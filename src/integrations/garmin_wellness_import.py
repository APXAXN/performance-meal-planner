"""Garmin Connect full export → outcome_signals.json parser.

Reads a Garmin Connect data export directory (the kind you download from
garmin.com/account → Privacy → Export Data) and extracts wellness signals
used by the weekly meal-plan pipeline.

Export directory layout expected:
    <garmin_dir>/
        DI_CONNECT/
            DI-Connect-Wellness/
                *_heartRateZones.json          # static RHR, LTHR, MaxHR
                *_sleepData.json               # nightly sleep stages (multiple files)
                *_healthStatusData.json        # daily steps, RHR time-series (multiple files)
            DI-Connect-Metrics/
                MetricsAcuteTrainingLoad_*.json  # ACWR training load (multiple files)
            DI-Connect-Aggregator/
                UDSFile_*.json                  # daily step / calorie summaries (multiple files)

Output written to demo_inputs/outcome_signals.json:
    {
        "week_start": "YYYY-MM-DD",         # Monday of the look-back window
        "generated_at": "ISO timestamp",
        "garmin_summary": {
            "avg_sleep_hr":    float,        # avg nightly sleep hours (last 14 nights)
            "avg_rhr":         int,          # avg resting HR (last 14 days)
            "avg_steps":       int,          # avg daily steps (last 14 days)
            "training_load":   str,          # "low" | "moderate" | "high" (ACWR status)
            "acwr":            float,        # most recent acute:chronic workload ratio
            "vo2max":          int | null,   # from userBioMetricProfileData
            "ftp_w":           int | null,   # from powerZones / bioMetrics_latest
        },
        "mfp_summary": { ... }              # unchanged from manual input
    }

Usage:
    python src/integrations/garmin_wellness_import.py --garmin-dir Garmin02242026 --days 14

    Or called programmatically via run():
        from src.integrations.garmin_wellness_import import run
        run(garmin_dir=Path("Garmin02242026"), output_path=Path("demo_inputs/outcome_signals.json"))

Notes:
    - All date arithmetic is naive UTC (Garmin exports in local-equivalent strings).
    - MFP section is preserved from the existing outcome_signals.json if present;
      only the garmin_summary block is overwritten.
    - Garmin export directory is never modified.
"""

import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> object:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _glob_sorted(directory: Path, pattern: str) -> list[Path]:
    """Return sorted list of files matching glob pattern (newest-last by name)."""
    return sorted(directory.glob(pattern))


def _parse_date(s: str) -> Optional[date]:
    """Parse 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS.f' → date. Returns None on failure."""
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _epoch_ms_to_date(ms: int) -> Optional[date]:
    """Convert Unix epoch milliseconds → UTC date."""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Individual signal extractors
# ---------------------------------------------------------------------------

def _extract_sleep(wellness_dir: Path, days: int) -> Optional[float]:
    """Average total sleep hours over the most recent `days` nights.

    Reads all *_sleepData.json files, merges entries, picks most recent `days`
    nights, computes (deepSleepSeconds + lightSleepSeconds + remSleepSeconds) / 3600.
    Returns None if no data found.
    """
    sleep_files = _glob_sorted(wellness_dir, "*_sleepData.json")
    if not sleep_files:
        return None

    entries_by_date: dict[date, dict] = {}
    for sf in sleep_files:
        try:
            data = _load_json(sf)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            d = _parse_date(entry.get("calendarDate", ""))
            if d:
                entries_by_date[d] = entry   # later file wins for same date

    if not entries_by_date:
        return None

    cutoff = max(entries_by_date.keys()) - timedelta(days=days - 1)
    recent = {d: e for d, e in entries_by_date.items() if d >= cutoff}
    if not recent:
        recent = entries_by_date  # fall back to all data

    total_hours = []
    for entry in recent.values():
        sleep_sec = (
            (entry.get("deepSleepSeconds") or 0)
            + (entry.get("lightSleepSeconds") or 0)
            + (entry.get("remSleepSeconds") or 0)
        )
        if sleep_sec > 0:
            total_hours.append(sleep_sec / 3600.0)

    if not total_hours:
        return None
    return round(sum(total_hours) / len(total_hours), 1)


def _extract_rhr(wellness_dir: Path, days: int) -> Optional[int]:
    """Average resting heart rate over the most recent `days` days.

    Reads *_healthStatusData.json files (which contain `restingHeartRate` per day).
    Falls back to the static value in *_heartRateZones.json if time-series unavailable.
    """
    health_files = _glob_sorted(wellness_dir, "*_healthStatusData.json")
    rhr_by_date: dict[date, int] = {}

    for hf in health_files:
        try:
            data = _load_json(hf)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            d = _parse_date(entry.get("calendarDate", ""))
            rhr = entry.get("restingHeartRate") or entry.get("currentDayRestingHeartRate")
            if d and isinstance(rhr, (int, float)) and 30 < rhr < 120:
                rhr_by_date[d] = int(rhr)

    if rhr_by_date:
        cutoff = max(rhr_by_date.keys()) - timedelta(days=days - 1)
        recent = {d: v for d, v in rhr_by_date.items() if d >= cutoff}
        if not recent:
            recent = rhr_by_date
        return round(sum(recent.values()) / len(recent))

    # Fall back to static HR zones file
    zone_files = list(wellness_dir.glob("*_heartRateZones.json"))
    for zf in zone_files:
        try:
            data = _load_json(zf)
        except Exception:
            continue
        if isinstance(data, list):
            for entry in data:
                rhr = entry.get("restingHeartRateUsed")
                if isinstance(rhr, (int, float)) and 30 < rhr < 120:
                    return int(rhr)

    return None


def _extract_steps(wellness_dir: Path, aggregator_dir: Path, days: int) -> Optional[int]:
    """Average daily step count over the most recent `days` days.

    Prefers the health status time-series (totalSteps per day).
    Falls back to UDSFile aggregator if health status has no step data.
    """
    # Primary: healthStatusData
    health_files = _glob_sorted(wellness_dir, "*_healthStatusData.json")
    steps_by_date: dict[date, int] = {}

    for hf in health_files:
        try:
            data = _load_json(hf)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            d = _parse_date(entry.get("calendarDate", ""))
            steps = entry.get("totalSteps")
            if d and isinstance(steps, (int, float)) and steps >= 0:
                steps_by_date[d] = int(steps)

    # Fallback: UDSFile aggregator
    if not steps_by_date and aggregator_dir.exists():
        uds_files = _glob_sorted(aggregator_dir, "UDSFile_*.json")
        for uf in uds_files:
            try:
                data = _load_json(uf)
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for entry in data:
                d = _parse_date(entry.get("calendarDate", ""))
                steps = entry.get("totalSteps")
                if d and isinstance(steps, (int, float)) and steps >= 0:
                    steps_by_date[d] = int(steps)

    if not steps_by_date:
        return None

    cutoff = max(steps_by_date.keys()) - timedelta(days=days - 1)
    recent = {d: v for d, v in steps_by_date.items() if d >= cutoff}
    if not recent:
        recent = steps_by_date
    return round(sum(recent.values()) / len(recent))


def _extract_training_load(metrics_dir: Path, days: int) -> tuple[str, Optional[float]]:
    """Most recent ACWR training load status and ratio.

    Reads all MetricsAcuteTrainingLoad_*.json files, picks the most recent
    entry within the look-back window.

    Returns:
        (load_label, acwr_ratio) where load_label is "low" | "moderate" | "high" | "unknown"
        and acwr_ratio is the float ratio (or None if unavailable).

    ACWR status mapping:
        LOW      → "low"       (undertrained / detraining)
        OPTIMAL  → "moderate"  (well-trained / productive load)
        HIGH     → "high"      (overreaching risk)
    """
    STATUS_MAP = {
        "LOW": "low",
        "OPTIMAL": "moderate",
        "HIGH": "high",
    }

    load_files = _glob_sorted(metrics_dir, "MetricsAcuteTrainingLoad_*.json")
    entries_by_date: dict[date, dict] = {}

    for lf in load_files:
        try:
            data = _load_json(lf)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            # calendarDate may be epoch ms (int) or string
            cal = entry.get("calendarDate")
            if isinstance(cal, int):
                d = _epoch_ms_to_date(cal)
            else:
                d = _parse_date(str(cal)) if cal else None
            if d:
                entries_by_date[d] = entry

    if not entries_by_date:
        return "unknown", None

    cutoff = max(entries_by_date.keys()) - timedelta(days=days - 1)
    recent = {d: e for d, e in entries_by_date.items() if d >= cutoff}
    if not recent:
        recent = entries_by_date

    # Use the most recent entry
    latest_entry = recent[max(recent.keys())]
    status_raw = latest_entry.get("acwrStatus", "")
    acwr_ratio = latest_entry.get("dailyAcuteChronicWorkloadRatio")
    if isinstance(acwr_ratio, (int, float)):
        acwr_ratio = round(float(acwr_ratio), 2)
    else:
        acwr_ratio = None

    load_label = STATUS_MAP.get(status_raw.upper(), "unknown")
    return load_label, acwr_ratio


def _extract_biometrics(wellness_dir: Path) -> dict:
    """Extract VO2max and FTP from static biometric profile files.

    Returns dict with optional keys: vo2max, ftp_w.
    """
    result: dict = {}

    # Primary source: userBioMetricProfileData (may be a list with one entry)
    profile_files = list(wellness_dir.glob("*_userBioMetricProfileData.json"))
    for pf in profile_files:
        try:
            data = _load_json(pf)
        except Exception:
            continue
        # Garmin exports this as a list of one record
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            vo2 = data.get("vo2MaxCycling") or data.get("vo2Max")
            if isinstance(vo2, (int, float)) and vo2 > 0:
                result["vo2max"] = int(round(vo2))
            ftp = data.get("functionalThresholdPower")
            if isinstance(ftp, (int, float)) and ftp > 0:
                result["ftp_w"] = int(round(ftp))

    # Supplement from bioMetrics_latest (has FTP)
    latest_files = list(wellness_dir.glob("*_bioMetrics_latest.json"))
    for lf in latest_files:
        try:
            data = _load_json(lf)
        except Exception:
            continue
        if isinstance(data, dict):
            ftp = data.get("functionalThresholdPower")
            if isinstance(ftp, (int, float)) and ftp > 0 and "ftp_w" not in result:
                result["ftp_w"] = int(round(ftp))

    # Supplement from power zones (FTP field)
    zone_files = list(wellness_dir.glob("*_powerZones.json"))
    for zf in zone_files:
        try:
            data = _load_json(zf)
        except Exception:
            continue
        if isinstance(data, list):
            for entry in data:
                ftp = entry.get("functionalThresholdPower")
                if isinstance(ftp, (int, float)) and ftp > 0 and "ftp_w" not in result:
                    result["ftp_w"] = int(round(ftp))

    return result


# ---------------------------------------------------------------------------
# Week-start helper
# ---------------------------------------------------------------------------

def _last_monday(ref: date) -> date:
    """Return the most recent Monday on or before `ref`."""
    return ref - timedelta(days=ref.weekday())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_garmin_wellness(garmin_dir: Path, days: int = 14) -> dict:
    """Parse Garmin export directory and return garmin_summary dict.

    Args:
        garmin_dir:  Root of the Garmin Connect export (contains DI_CONNECT/).
        days:        Look-back window in days for rolling averages (default 14).

    Returns:
        dict with keys: avg_sleep_hr, avg_rhr, avg_steps, training_load, acwr,
                        vo2max, ftp_w (all may be None if data not found).
    """
    di_connect = garmin_dir / "DI_CONNECT"
    wellness_dir = di_connect / "DI-Connect-Wellness"
    metrics_dir = di_connect / "DI-Connect-Metrics"
    aggregator_dir = di_connect / "DI-Connect-Aggregator"

    if not wellness_dir.exists():
        raise FileNotFoundError(
            f"Expected DI-Connect-Wellness directory not found at {wellness_dir}.\n"
            "Ensure garmin_dir points to the root of the Garmin Connect export."
        )

    avg_sleep = _extract_sleep(wellness_dir, days)
    avg_rhr = _extract_rhr(wellness_dir, days)
    avg_steps = _extract_steps(wellness_dir, aggregator_dir, days)
    training_load, acwr = _extract_training_load(metrics_dir, days)
    biometrics = _extract_biometrics(wellness_dir)

    summary = {
        "avg_sleep_hr": avg_sleep,
        "avg_rhr": avg_rhr,
        "avg_steps": avg_steps,
        "training_load": training_load,
        "acwr": acwr,
        "vo2max": biometrics.get("vo2max"),
        "ftp_w": biometrics.get("ftp_w"),
    }
    return summary


def run(garmin_dir: Path, output_path: Path, days: int = 14) -> Path:
    """Parse Garmin export and write/update outcome_signals.json.

    Preserves the existing mfp_summary block if output_path already exists.
    Only the garmin_summary block and metadata are overwritten.

    Args:
        garmin_dir:   Root of the Garmin Connect export.
        output_path:  Path to outcome_signals.json to write.
        days:         Rolling average window in days.

    Returns:
        output_path after writing.
    """
    print(f"  Parsing Garmin export: {garmin_dir} (last {days} days)")

    garmin_summary = parse_garmin_wellness(garmin_dir, days)

    # Print what we found
    print(f"    sleep:         {garmin_summary['avg_sleep_hr']} hr/night (avg)")
    print(f"    rhr:           {garmin_summary['avg_rhr']} bpm (avg)")
    print(f"    steps:         {garmin_summary['avg_steps']:,} steps/day (avg)" if garmin_summary['avg_steps'] else "    steps:         n/a")
    print(f"    training_load: {garmin_summary['training_load']}  (ACWR {garmin_summary['acwr']})")
    print(f"    vo2max:        {garmin_summary['vo2max']} ml/kg/min")
    print(f"    ftp_w:         {garmin_summary['ftp_w']} W")

    # Load existing outcome_signals to preserve mfp_summary
    existing: dict = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    today = date.today()
    week_start = _last_monday(today)

    output = {
        "week_start": str(week_start),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "garmin_summary": garmin_summary,
        "mfp_summary": existing.get("mfp_summary", {
            "avg_kcal": None,
            "protein_g": None,
            "carbs_g": None,
            "fat_g": None,
        }),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"  outcome_signals.json written → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse Garmin Connect export → outcome_signals.json"
    )
    parser.add_argument(
        "--garmin-dir",
        required=True,
        metavar="PATH",
        help="Root directory of the Garmin Connect data export (contains DI_CONNECT/).",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Output path for outcome_signals.json. "
             "Defaults to demo_inputs/outcome_signals.json relative to repo root.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        metavar="N",
        help="Rolling average window in days (default: 14).",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    garmin_dir = Path(args.garmin_dir)
    if not garmin_dir.is_absolute():
        garmin_dir = (root / garmin_dir).resolve()

    output_path = Path(args.output) if args.output else root / "demo_inputs" / "outcome_signals.json"

    run(garmin_dir=garmin_dir, output_path=output_path, days=args.days)
