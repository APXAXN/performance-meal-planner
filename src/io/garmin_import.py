"""Garmin data loader — unified interface for CSV exports and optional garth automation.

── Manual path (primary) ────────────────────────────────────────────────────
User exports CSV files from Garmin Connect and drops them into inputs/exports/:
  garmin_activities_*.csv   — from connect.garmin.com → Activities → Export CSV
  garmin_wellness_*.csv     — from connect.garmin.com → Health Stats → Export

── Automated path (optional) ────────────────────────────────────────────────
If GARMIN_EMAIL and GARMIN_PASSWORD are set in .env, the garth library will
attempt to authenticate via Garmin SSO and fetch data headlessly.
Install garth:  pip install garth
If garth is not installed or auth fails, falls back to the CSV path.

── Unified interface ─────────────────────────────────────────────────────────
    from src.io.garmin_import import load_garmin

    data = load_garmin(exports_dir="inputs/exports/", start_date="2025-03-03", end_date="2025-03-09")
    # data["activities"]    — list of activity dicts
    # data["wellness"]      — list of wellness/sleep dicts
    # data["source"]        — "garth_api" | "csv" | "none"
    # data["training_days"] — list of ISO date strings
    # data["day_type_map"]  — {date_str: "training"|"rest"|"recovery"}
    # data["training_load"] — "low"|"moderate"|"high"|"very_high"
    # data["avg_sleep_hours"]
    # data["avg_resting_hr"]
"""

import csv
import glob
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ── File finders ──────────────────────────────────────────────────────────────

def find_latest_export(exports_dir: str, kind: str = "activities") -> Optional[str]:
    """Scan exports_dir for the latest Garmin CSV of the given kind.

    Args:
        exports_dir: Directory to scan (e.g. "inputs/exports/").
        kind: "activities" → garmin_activities_*.csv
              "wellness"   → garmin_wellness_*.csv

    Returns:
        Absolute path to the most recent matching file, or None.
    """
    if kind == "activities":
        pattern = os.path.join(exports_dir, "garmin_activities_*.csv")
    elif kind == "wellness":
        pattern = os.path.join(exports_dir, "garmin_wellness_*.csv")
    else:
        raise ValueError(f"Unknown kind: {kind!r}. Use 'activities' or 'wellness'.")

    matches = glob.glob(pattern)
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


# ── CSV parsers ───────────────────────────────────────────────────────────────

def _parse_float(val: str) -> Optional[float]:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_date(val: str) -> Optional[date]:
    val = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def parse_activities(csv_path: str) -> list[dict]:
    """Parse a Garmin Connect activities CSV export.

    Expected columns (Garmin Connect Activities export):
      Activity Type, Date, Title, Distance, Calories, Time, Avg HR, Max HR, Aerobic TE

    Returns:
        List of activity dicts:
          {date, activity_type, duration_min, distance_km, calories, avg_hr, training_effect}
    """
    activities = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            d = _parse_date(row.get("Date", ""))
            if d is None:
                continue

            # Duration: "Total Time" or "Time" in HH:MM:SS or MM:SS
            duration_min = None
            time_raw = row.get("Total Time") or row.get("Time", "")
            if time_raw and time_raw != "--":
                parts = time_raw.split(":")
                try:
                    if len(parts) == 3:
                        duration_min = int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
                    elif len(parts) == 2:
                        duration_min = int(parts[0]) + int(parts[1]) / 60
                except (ValueError, IndexError):
                    pass

            activities.append({
                "date": d.isoformat(),
                "activity_type": row.get("Activity Type", "").lower().strip(),
                "duration_min": round(duration_min, 1) if duration_min else None,
                "distance_km": _parse_float(row.get("Distance", "")),
                "calories": _parse_float(row.get("Calories", "")),
                "avg_hr": _parse_float(row.get("Avg HR", "")),
                "training_effect": _parse_float(row.get("Aerobic TE", "")),
            })

    return activities


def parse_wellness(csv_path: str) -> list[dict]:
    """Parse a Garmin Connect wellness/health-stats CSV export.

    Expected columns (Garmin wellness export):
      Date, Sleep Duration (h), Avg Stress Level, Body Battery High, Resting HR, Steps

    Column names vary by export version — handled gracefully.

    Returns:
        List of wellness dicts:
          {date, sleep_hours, stress_score, body_battery, resting_hr, steps}
    """
    # Column name aliases
    COL_MAP = {
        "date": ["date", "Date", "calendar date", "Calendar Date"],
        "sleep_hours": ["sleep duration (h)", "Sleep Duration (h)", "sleep (h)", "Sleep (h)",
                        "total sleep", "Total Sleep", "sleep_hours"],
        "stress": ["avg stress level", "Avg Stress Level", "stress", "Stress",
                   "average stress", "Average Stress"],
        "body_battery": ["body battery high", "Body Battery High", "body_battery_high",
                         "Body Battery", "body battery"],
        "resting_hr": ["resting hr", "Resting HR", "resting heart rate", "Resting Heart Rate",
                       "avg resting hr", "rhr"],
        "steps": ["steps", "Steps", "total steps", "Total Steps", "step count"],
    }

    def _find(headers_lower: dict, candidates: list[str]) -> Optional[str]:
        for c in candidates:
            match = headers_lower.get(c.lower())
            if match is not None:
                return match
        return None

    wellness = []

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        headers_lower = {h.lower(): h for h in headers}

        col_date = _find(headers_lower, COL_MAP["date"])
        col_sleep = _find(headers_lower, COL_MAP["sleep_hours"])
        col_stress = _find(headers_lower, COL_MAP["stress"])
        col_battery = _find(headers_lower, COL_MAP["body_battery"])
        col_rhr = _find(headers_lower, COL_MAP["resting_hr"])
        col_steps = _find(headers_lower, COL_MAP["steps"])

        if col_date is None:
            logger.warning("Garmin wellness CSV: no date column found in %s", csv_path)
            return []

        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            d = _parse_date(row.get(col_date, ""))
            if d is None:
                continue

            wellness.append({
                "date": d.isoformat(),
                "sleep_hours": _parse_float(row.get(col_sleep, "")) if col_sleep else None,
                "stress_score": _parse_float(row.get(col_stress, "")) if col_stress else None,
                "body_battery": _parse_float(row.get(col_battery, "")) if col_battery else None,
                "resting_hr": _parse_float(row.get(col_rhr, "")) if col_rhr else None,
                "steps": _parse_float(row.get(col_steps, "")) if col_steps else None,
            })

    return sorted(wellness, key=lambda x: x["date"])


# ── Automated path (garth) ───────────────────────────────────────────────────

def fetch_garmin_data(start_date: str, end_date: str,
                      email: str, password: str) -> Optional[dict]:
    """Fetch Garmin activities and wellness data via garth SSO.

    Args:
        start_date: ISO date string (YYYY-MM-DD).
        end_date:   ISO date string (YYYY-MM-DD).
        email:      Garmin account email.
        password:   Garmin account password.

    Returns:
        Dict with keys "activities" and "wellness" (same shape as CSV parsers),
        or None if garth is unavailable or auth fails.
    """
    try:
        import garth  # noqa: F401 — optional dependency
    except ImportError:
        logger.warning(
            "garth is not installed — Garmin automated path unavailable. "
            "Install with: pip install garth"
        )
        return None

    try:
        garth.login(email, password)
    except Exception as exc:
        logger.warning("Garmin garth auth failed: %s — falling back to CSV.", exc)
        return None

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        activities = []
        wellness = []

        # Fetch activities day by day
        current = start
        while current <= end:
            day_str = current.isoformat()
            try:
                acts = garth.connectapi(
                    f"/activitylist-service/activities/search/activities",
                    params={"startDate": day_str, "endDate": day_str, "limit": 20},
                )
                for act in (acts or []):
                    activities.append({
                        "date": day_str,
                        "activity_type": act.get("activityType", {}).get("typeKey", "").lower(),
                        "duration_min": round(act.get("duration", 0) / 60, 1),
                        "distance_km": round(act.get("distance", 0) / 1000, 2),
                        "calories": act.get("calories"),
                        "avg_hr": act.get("averageHR"),
                        "training_effect": act.get("aerobicTrainingEffect"),
                    })
            except Exception:
                pass

            try:
                day_data = garth.connectapi(
                    f"/wellness-service/wellness/dailySummary/{day_str}"
                )
                if day_data:
                    wellness.append({
                        "date": day_str,
                        "sleep_hours": round(day_data.get("sleepingSeconds", 0) / 3600, 1),
                        "stress_score": day_data.get("averageStressLevel"),
                        "body_battery": day_data.get("bodyBatteryHighestValue"),
                        "resting_hr": day_data.get("restingHeartRate"),
                        "steps": day_data.get("totalSteps"),
                    })
            except Exception:
                pass

            current += timedelta(days=1)

        return {"activities": activities, "wellness": wellness}

    except Exception as exc:
        logger.warning("Garmin garth data fetch failed: %s — falling back to CSV.", exc)
        return None


# ── Day-type derivation ───────────────────────────────────────────────────────

_HIGH_ACTIVITY_TYPES = {"running", "cycling", "road cycling", "trail running",
                         "swimming", "open water swimming", "rowing", "virtual cycling"}
_REST_ACTIVITY_TYPES = {"stretching", "meditation", "mobility", "flexibility"}
_TRAINING_ONLY = {"strength training", "strength", "cardio", "yoga", "pilates",
                   "hiit", "walking", "hiking", "elliptical"}

def _derive_day_type(activity_type: str, distance_km: Optional[float],
                      training_effect: Optional[float]) -> str:
    atype = activity_type.lower().strip()
    if atype in _REST_ACTIVITY_TYPES:
        return "rest"
    if atype in _TRAINING_ONLY:
        return "training"
    if atype in _HIGH_ACTIVITY_TYPES:
        if (distance_km and distance_km >= 12.0) or (training_effect and training_effect >= 3.5):
            return "high"
        return "training"
    return "training"


def _derive_training_load(activities: list[dict], wellness: list[dict]) -> str:
    """Estimate training load tier from activity count and wellness signals."""
    high_days = sum(
        1 for a in activities
        if _derive_day_type(a["activity_type"], a.get("distance_km"), a.get("training_effect")) == "high"
    )
    training_days = sum(
        1 for a in activities
        if _derive_day_type(a["activity_type"], a.get("distance_km"), a.get("training_effect")) == "training"
    )
    total_active = high_days + training_days

    if high_days >= 3:
        return "very_high"
    if high_days >= 1 or total_active >= 4:
        return "high"
    if total_active >= 2:
        return "moderate"
    return "low"


# ── Unified loader ────────────────────────────────────────────────────────────

def load_garmin(exports_dir: str, start_date: str, end_date: str) -> dict:
    """Load Garmin data — tries garth automation first, falls back to CSV.

    Args:
        exports_dir: Directory containing garmin_activities_*.csv / garmin_wellness_*.csv
        start_date:  ISO date string (YYYY-MM-DD) — Monday of the target week.
        end_date:    ISO date string (YYYY-MM-DD) — Sunday of the target week.

    Returns:
        {
            activities: list[dict],
            wellness:   list[dict],
            source:     "garth_api" | "csv" | "none",
            training_days: [ISO date str, ...],
            day_type_map:  {date_str: "training"|"rest"|"high"},
            training_load: "low"|"moderate"|"high"|"very_high",
            avg_sleep_hours: float|None,
            avg_resting_hr:  float|None,
        }
    """
    activities: list[dict] = []
    wellness: list[dict] = []
    source = "none"

    # Try automated garth path if credentials present
    from src.io.config import cfg
    garmin_email = os.environ.get("GARMIN_EMAIL", "").strip()
    garmin_password = os.environ.get("GARMIN_PASSWORD", "").strip()

    if garmin_email and garmin_password:
        result = fetch_garmin_data(start_date, end_date, garmin_email, garmin_password)
        if result:
            activities = result["activities"]
            wellness = result["wellness"]
            source = "garth_api"

    # Fall back to CSV
    if source == "none":
        act_path = find_latest_export(exports_dir, kind="activities")
        if act_path:
            try:
                activities = parse_activities(act_path)
                source = "csv"
                logger.info("Garmin: loaded activities from %s", act_path)
            except Exception as exc:
                logger.warning("Garmin activities CSV parse failed: %s", exc)

        well_path = find_latest_export(exports_dir, kind="wellness")
        if well_path:
            try:
                wellness = parse_wellness(well_path)
                if source == "none":
                    source = "csv"
                logger.info("Garmin: loaded wellness from %s", well_path)
            except Exception as exc:
                logger.warning("Garmin wellness CSV parse failed: %s", exc)

    # Filter to the target week
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    activities = [a for a in activities
                  if start <= date.fromisoformat(a["date"]) <= end]
    wellness = [w for w in wellness
                if start <= date.fromisoformat(w["date"]) <= end]

    # Derive day-type map
    day_type_map: dict[str, str] = {}
    for a in activities:
        day_type = _derive_day_type(a["activity_type"], a.get("distance_km"), a.get("training_effect"))
        existing = day_type_map.get(a["date"], "rest")
        # Higher-intensity wins
        priority = {"high": 2, "training": 1, "rest": 0}
        if priority.get(day_type, 0) > priority.get(existing, 0):
            day_type_map[a["date"]] = day_type

    # Fill rest days for days with no activity
    current = start
    while current <= end:
        ds = current.isoformat()
        if ds not in day_type_map:
            day_type_map[ds] = "rest"
        current += timedelta(days=1)

    training_days = [d for d, t in day_type_map.items() if t in ("training", "high")]
    training_load = _derive_training_load(activities, wellness)

    avg_sleep = None
    if wellness:
        sleep_vals = [w["sleep_hours"] for w in wellness if w.get("sleep_hours")]
        avg_sleep = round(sum(sleep_vals) / len(sleep_vals), 1) if sleep_vals else None

    avg_rhr = None
    if wellness:
        rhr_vals = [w["resting_hr"] for w in wellness if w.get("resting_hr")]
        avg_rhr = round(sum(rhr_vals) / len(rhr_vals), 1) if rhr_vals else None

    if source == "none":
        logger.warning(
            "No Garmin data found (neither garth nor CSV). "
            "Export from connect.garmin.com → Activities → Export CSV → "
            "drop into inputs/exports/ as garmin_activities_<date>.csv"
        )

    return {
        "activities": activities,
        "wellness": wellness,
        "source": source,
        "training_days": sorted(training_days),
        "day_type_map": day_type_map,
        "training_load": training_load,
        "avg_sleep_hours": avg_sleep,
        "avg_resting_hr": avg_rhr,
    }
