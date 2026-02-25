#!/usr/bin/env python3
"""Pre-run ingestion orchestrator.

Scans inputs/exports/ for Garmin, Strava, and Nutritionix data, merges all
signals into a weekly_context.json, and writes it to inputs/.

Run before each agent pipeline execution:
  python scripts/ingest.py --week 2025-03-03
  python scripts/ingest.py --week 2025-03-03 --exports inputs/exports/
  python scripts/ingest.py --week 2025-03-03 --demo   # use demo fixtures

After running, verify inputs/weekly_context.json and then start the pipeline:
  python src/run_weekly.py --demo
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure repo root is on path when called from anywhere
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.io import garmin_import, nutritionix_import, strava_import

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _week_end(week_start: date) -> date:
    return week_start + timedelta(days=6)


def _copy_demo_fixtures(exports_dir: Path) -> None:
    """Copy demo fixture CSVs into exports_dir so the demo run finds them."""
    demo_dir = _ROOT / "demo_inputs"
    fixtures = [
        ("garmin_activities_demo.csv", "garmin_activities_demo.csv"),
        ("garmin_wellness_demo.csv", "garmin_wellness_demo.csv"),
        ("nutritionix_demo.csv", "nutritionix_demo.csv"),
    ]
    exports_dir.mkdir(parents=True, exist_ok=True)
    for src_name, dst_name in fixtures:
        src = demo_dir / src_name
        dst = exports_dir / dst_name
        if src.exists() and not dst.exists():
            dst.write_bytes(src.read_bytes())


def _derive_weekly_context(
    week_start: date,
    garmin_data: dict,
    strava_activities: list[dict],
    nutritionix: dict | None,
) -> dict:
    """Merge all signals into a weekly_context dict conforming to the schema."""
    start_iso = week_start.isoformat()

    # Build 7-day schedule from Garmin day_type_map
    schedule = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        ds = d.isoformat()
        day_type = garmin_data["day_type_map"].get(ds, "rest")
        # Strava can upgrade rest→training
        if day_type == "rest" and any(a["date"] == ds for a in strava_activities):
            day_type = "training"
        garmin_acts = [a for a in garmin_data["activities"] if a["date"] == ds]
        note_parts = [a.get("activity_type", "") for a in garmin_acts if a.get("activity_type")]
        note = ", ".join(note_parts) if note_parts else "No activity"
        schedule.append({
            "date": ds,
            "day_type": day_type,
            "notes": note,
        })

    # Derive training_focus from dominant activity type
    all_acts = garmin_data["activities"] + strava_activities
    type_counts: dict[str, int] = {}
    for a in all_acts:
        at = a.get("activity_type") or a.get("type", "")
        if at:
            type_counts[at] = type_counts.get(at, 0) + 1
    if type_counts:
        dominant = max(type_counts, key=type_counts.get)
        n = type_counts[dominant]
        training_focus = f"{dominant.replace('_', ' ').title()} ({n} sessions, auto-generated)"
    else:
        training_focus = "General training (auto-generated)"

    # Build garmin_summary for outcome_signals
    garmin_summary = {
        "avg_sleep_hr": garmin_data.get("avg_sleep_hours"),
        "avg_rhr": garmin_data.get("avg_resting_hr"),
        "avg_steps": None,
        "training_load": garmin_data.get("training_load", "unknown"),
        "acwr": None,
        "vo2max": None,
        "ftp_w": None,
    }
    # Pull steps from wellness data
    wellness = garmin_data.get("wellness", [])
    if wellness:
        step_vals = [w["steps"] for w in wellness if w.get("steps") is not None]
        if step_vals:
            garmin_summary["avg_steps"] = round(sum(step_vals) / len(step_vals))

    # MFP summary from Nutritionix (closest equivalent)
    if nutritionix:
        mfp_summary = {
            "avg_kcal": nutritionix.get("avg_calories"),
            "protein_g": nutritionix.get("avg_protein_g"),
            "carbs_g": nutritionix.get("avg_carbs_g"),
            "fat_g": nutritionix.get("avg_fat_g"),
        }
    else:
        mfp_summary = {
            "avg_kcal": None,
            "protein_g": None,
            "carbs_g": None,
            "fat_g": None,
        }

    weekly_context = {
        "week_start": start_iso,
        "timezone": "America/Los_Angeles",
        "training_focus": training_focus,
        "schedule": schedule,
        "_ingestion_meta": {
            "garmin_source": garmin_data.get("source", "none"),
            "strava_activities": len(strava_activities),
            "nutritionix_days": nutritionix.get("days_logged", 0) if nutritionix else 0,
            "garmin_summary": garmin_summary,
            "mfp_summary": mfp_summary,
        },
    }

    return weekly_context


def _print_summary(week_start: date, garmin_data: dict, strava_activities: list[dict],
                    nutritionix: dict | None, out_path: Path) -> None:
    garmin_src = garmin_data.get("source", "none")
    garmin_act_count = len(garmin_data.get("activities", []))
    wellness_count = len(garmin_data.get("wellness", []))

    garmin_icon = "✓" if garmin_src != "none" else "—"
    garmin_src_label = garmin_src.replace("_", " ").upper() if garmin_src != "none" else ""
    garmin_detail = (f"{garmin_act_count} activities, {wellness_count} nights"
                     if garmin_src != "none" else "no data found")

    strava_configured = bool(os.environ.get("STRAVA_REFRESH_TOKEN", "").strip())
    strava_icon = "✓" if strava_activities else ("—" if not strava_configured else "✗")
    strava_src_label = "API" if strava_configured else ""
    strava_detail = (f"{len(strava_activities)} activities matched"
                     if strava_activities else ("not configured" if not strava_configured else "0 activities"))

    nix_icon = "✓" if nutritionix else "—"
    nix_detail = (f"{nutritionix['days_logged']}/7 days logged" if nutritionix else "no export found")

    width = 51
    print()
    print("┌" + "─" * width + "┐")
    print(f"│  INGESTION SUMMARY — Week of {week_start}".ljust(width + 1) + "│")
    print("├" + "─" * width + "┤")

    def _row(icon, label, src, detail):
        src_part = f"  {src:<5}" if src else "       "
        line = f"│  {icon}  {label:<14}{src_part}│  {detail}"
        print(line.ljust(width + 1) + "│")

    _row(garmin_icon, "Garmin", garmin_src_label, garmin_detail)
    _row(strava_icon, "Strava", strava_src_label, strava_detail)
    _row(nix_icon, "Nutritionix", "CSV" if nutritionix else "", nix_detail)
    _row("—", "Kroger", "", "(runs at grocery step)")
    print("├" + "─" * width + "┤")
    print(f"│  weekly_context.json written → {out_path}".ljust(width + 1) + "│")
    print("└" + "─" * width + "┘")
    print()

    # Action items for missing sources
    if garmin_src == "none":
        print("ACTION NEEDED: No Garmin data found.")
        print("  Export from connect.garmin.com → Activities → Export CSV")
        print("  → drop into inputs/exports/ as garmin_activities_<date>.csv")
        print()

    if not nutritionix:
        print("ACTION NEEDED: No Nutritionix export found.")
        print("  Export from Nutritionix app: Profile → Logs → Export")
        print("  → drop into inputs/exports/ as nutritionix_<date>.csv")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest wearable/nutrition exports and write weekly_context.json"
    )
    parser.add_argument(
        "--week",
        required=True,
        metavar="YYYY-MM-DD",
        help="Monday of the target week (ISO date)",
    )
    parser.add_argument(
        "--exports",
        default="inputs/exports/",
        metavar="PATH",
        help="Directory containing export files (default: inputs/exports/)",
    )
    parser.add_argument(
        "--output",
        default="inputs/weekly_context.json",
        metavar="PATH",
        help="Output path for weekly_context.json (default: inputs/weekly_context.json)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use demo fixture CSVs from demo_inputs/ (no real exports required)",
    )
    args = parser.parse_args()

    # Parse week
    try:
        week_start = date.fromisoformat(args.week)
    except ValueError:
        print(f"ERROR: --week must be ISO date (YYYY-MM-DD), got: {args.week!r}")
        sys.exit(1)

    # Normalize to Monday
    week_start = week_start - timedelta(days=week_start.weekday())
    week_end = _week_end(week_start)

    exports_dir = Path(args.exports)
    if not exports_dir.is_absolute():
        exports_dir = (_ROOT / exports_dir).resolve()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (_ROOT / output_path).resolve()

    # Demo mode: copy fixtures into exports_dir
    if args.demo:
        print(f"Demo mode: copying fixture CSVs → {exports_dir}")
        _copy_demo_fixtures(exports_dir)

    # Step 1: Scan exports directory
    print(f"\nScanning {exports_dir} ...")
    if exports_dir.exists():
        found = sorted(exports_dir.iterdir())
        if found:
            for f in found:
                print(f"  {f.name}")
        else:
            print("  (empty)")
    else:
        print(f"  Directory does not exist: {exports_dir}")

    # Step 2: Garmin
    print(f"\nLoading Garmin data (week {week_start} → {week_end}) ...")
    garmin_data = garmin_import.load_garmin(
        exports_dir=str(exports_dir),
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
    )

    # Step 3: Strava
    print(f"\nFetching Strava activities (week {week_start} → {week_end}) ...")
    strava_activities = strava_import.fetch_activities(
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
    )

    # Step 4: Nutritionix
    print("\nLoading Nutritionix food log ...")
    nutritionix = nutritionix_import.load_nutritionix(str(exports_dir))
    if nutritionix:
        print(f"  {nutritionix['days_logged']} days logged, avg {nutritionix.get('avg_calories', '?')} kcal")
    else:
        print("  No Nutritionix export found.")

    # Step 5: Merge and write
    weekly_context = _derive_weekly_context(
        week_start=week_start,
        garmin_data=garmin_data,
        strava_activities=strava_activities,
        nutritionix=nutritionix,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(weekly_context, indent=2))

    # Step 6: Print summary
    _print_summary(week_start, garmin_data, strava_activities, nutritionix, output_path)


if __name__ == "__main__":
    main()
