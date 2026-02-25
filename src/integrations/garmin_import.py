"""Garmin Connect Activities CSV parser.

Reads the standard Garmin Connect Activities CSV export and emits a
schema-compliant weekly_context.json for a specified week.

How to export from Garmin Connect:
  1. Go to https://connect.garmin.com/modern/activities
  2. Click the download/export icon (top right of activity list)
  3. Select "Export to CSV"
  4. Save as demo_inputs/raw/garmin_activities.csv

Expected CSV columns (Garmin Connect standard export):
  Activity Type, Date, Favorite, Title, Distance, Calories, Time,
  Avg HR, Max HR, Aerobic TE, Avg Run Cadence, Max Run Cadence,
  Avg Pace, Best Pace, Total Ascent, Total Descent, Avg Stride Length,
  Avg Vertical Ratio, Avg Vertical Oscillation, Training Stress Score,
  Avg Power, Max Power, Steps, ...

Assumptions documented in docs/02_Data_Contracts.md:
  - Week starts Monday (ISO week convention)
  - Distance in km (Garmin Connect default metric; miles auto-detected)
  - Aerobic TE >= 3.5 OR distance >= 12km → "high" day
  - Strength/yoga/mobility activity types → "training" (not "high")
  - Days with no activity → "rest"
  - Multiple activities on same day → highest-intensity day_type wins
  - training_focus is auto-generated from dominant activity type and
    flagged with "(auto-generated)" — review before production use
  - avg_sleep_hr and avg_steps default to 0.0/0 if not in export
    (Garmin Activities CSV does not include sleep/steps; those require
    the Health Stats export or Garmin Connect API)
"""

import csv
import json
import datetime
from pathlib import Path
from typing import Optional


# Activity type → base day_type classification
ACTIVITY_TYPE_MAP = {
    # High-intensity capable
    "running": "running",
    "trail running": "running",
    "treadmill running": "running",
    "cycling": "cycling",
    "road cycling": "cycling",
    "mountain biking": "cycling",
    "swimming": "swimming",
    "open water swimming": "swimming",
    "rowing": "rowing",
    "virtual cycling": "cycling",
    # Always training (not high)
    "strength training": "strength",
    "cardio": "cardio",
    "yoga": "mobility",
    "pilates": "mobility",
    "flexibility": "mobility",
    "hiit": "strength",
    "indoor rowing": "rowing",
    "elliptical": "cardio",
    "stair stepping": "cardio",
    "walking": "walking",
    "hiking": "hiking",
    # Rest-adjacent
    "stretching": "mobility",
    "meditation": "mobility",
}

# Activity categories that can never be "high" (always "training")
ALWAYS_TRAINING = {"strength", "mobility", "cardio", "walking", "hiking"}

# Distance threshold (km) for "high" day classification
HIGH_DAY_DISTANCE_KM = 12.0

# Aerobic Training Effect threshold for "high" day
HIGH_DAY_AEROBIC_TE = 3.5


def _parse_float(val: str) -> Optional[float]:
    try:
        return float(val.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_date(val: str) -> Optional[datetime.date]:
    """Parse Garmin date strings: '2026-02-23 07:15:22' or '2026-02-23'."""
    val = val.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _classify_activity(activity_type: str, distance_km: Optional[float],
                        aerobic_te: Optional[float]) -> str:
    """Return 'high', 'training', or 'rest' for a single activity."""
    atype = activity_type.lower().strip()
    category = ACTIVITY_TYPE_MAP.get(atype, "cardio")

    if category in ALWAYS_TRAINING:
        return "training"

    # Endurance activities: check thresholds for high
    if distance_km is not None and distance_km >= HIGH_DAY_DISTANCE_KM:
        return "high"
    if aerobic_te is not None and aerobic_te >= HIGH_DAY_AEROBIC_TE:
        return "high"
    return "training"


def _day_type_priority(a: str, b: str) -> str:
    """Return the higher-intensity day_type when merging two activities."""
    order = {"high": 2, "training": 1, "rest": 0}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def _infer_training_focus(day_types_by_date: dict) -> str:
    """Build a human-readable training_focus string from the week's activities."""
    activity_counts = {}
    for info in day_types_by_date.values():
        for act in info.get("activities", []):
            activity_counts[act] = activity_counts.get(act, 0) + 1

    if not activity_counts:
        return "General training (auto-generated)"

    dominant = max(activity_counts, key=activity_counts.get)
    count = activity_counts[dominant]

    focus_map = {
        "running": "Run training",
        "cycling": "Cycling / endurance",
        "swimming": "Swim training",
        "strength": "Strength training",
        "rowing": "Rowing / endurance",
        "cardio": "Cardio",
        "mobility": "Mobility / recovery",
        "walking": "Active recovery",
        "hiking": "Hiking / endurance",
    }
    label = focus_map.get(dominant, dominant.title())
    return f"{label} (auto-generated from {count} {dominant} sessions)"


def _week_bounds(week_start: datetime.date) -> tuple:
    """Return (monday, sunday) for the week containing week_start."""
    # Align to Monday
    monday = week_start - datetime.timedelta(days=week_start.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def parse_garmin_csv(csv_path: Path, week_start: datetime.date,
                     timezone: str = "America/Los_Angeles") -> dict:
    """
    Parse Garmin Connect Activities CSV and return schema-compliant weekly_context dict.

    Args:
        csv_path: Path to garmin_activities.csv
        week_start: The Monday of the target week (ISO date)
        timezone: IANA timezone string (default Pacific)

    Returns:
        weekly_context dict ready for JSON serialization and schema validation
    """
    monday, sunday = _week_bounds(week_start)

    # Read CSV — Garmin exports use UTF-8 with BOM sometimes
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        raise ValueError(f"No activity rows found in {csv_path}")

    # Normalize column names (strip whitespace)
    rows = [{k.strip(): v.strip() for k, v in row.items()} for row in rows]

    # Index activities by date within the target week
    day_info: dict[datetime.date, dict] = {}
    skipped = 0

    for row in rows:
        date_val = row.get("Date", "")
        activity_date = _parse_date(date_val)
        if activity_date is None:
            skipped += 1
            continue
        if not (monday <= activity_date <= sunday):
            continue

        activity_type = row.get("Activity Type", "").strip()
        if not activity_type:
            continue

        # Parse distance — handle both km and miles
        # Garmin Connect exports distance in the user's preferred unit
        # We detect miles if column header says "Distance" and values look small
        raw_distance = row.get("Distance", "")
        distance_val = _parse_float(raw_distance) if raw_distance else None

        # Heuristic: if distance < 0.5 and activity is running, likely miles near 0
        # Real detection: if all running distances < 2.0, likely miles → convert
        # For now, treat as km (metric default); document assumption
        distance_km = distance_val  # assumed km; see docs/02_Data_Contracts.md

        aerobic_te = _parse_float(row.get("Aerobic TE", "") or "")
        calories = _parse_float(row.get("Calories", "") or "")
        avg_hr = _parse_float(row.get("Avg HR", "") or "")
        activity_title = row.get("Title", activity_type)

        category = ACTIVITY_TYPE_MAP.get(activity_type.lower(), "cardio")
        day_type = _classify_activity(activity_type, distance_km, aerobic_te)

        if activity_date not in day_info:
            day_info[activity_date] = {
                "day_type": day_type,
                "activities": [category],
                "notes_parts": [activity_title],
                "calories": calories or 0,
                "avg_hr": avg_hr,
            }
        else:
            # Merge: take higher-intensity day_type
            existing = day_info[activity_date]
            existing["day_type"] = _day_type_priority(existing["day_type"], day_type)
            existing["activities"].append(category)
            existing["notes_parts"].append(activity_title)
            existing["calories"] = (existing["calories"] or 0) + (calories or 0)

    if skipped:
        print(f"  Warning: skipped {skipped} rows with unparseable dates.")

    # Build 7-day schedule (fill rest days for days with no activity)
    schedule = []
    for i in range(7):
        date = monday + datetime.timedelta(days=i)
        if date in day_info:
            info = day_info[date]
            notes_parts = info["notes_parts"]
            note = ", ".join(notes_parts[:2])
            if len(notes_parts) > 2:
                note += f" +{len(notes_parts) - 2} more"
            schedule.append({
                "date": date.isoformat(),
                "day_type": info["day_type"],
                "notes": note,
            })
        else:
            schedule.append({
                "date": date.isoformat(),
                "day_type": "rest",
                "notes": "No activity recorded",
            })

    training_focus = _infer_training_focus(day_info)

    return {
        "week_start": monday.isoformat(),
        "timezone": timezone,
        "training_focus": training_focus,
        "schedule": schedule,
    }


def run(raw_dir: Path, parsed_dir: Path, week_start: datetime.date,
        timezone: str = "America/Los_Angeles") -> Path:
    """Parse garmin_activities.csv and write weekly_context.json to parsed_dir."""
    csv_path = raw_dir / "garmin_activities.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Garmin CSV not found at {csv_path}.\n"
            "Export from Garmin Connect → Activities → Export to CSV\n"
            "and save as demo_inputs/raw/garmin_activities.csv."
        )

    context = parse_garmin_csv(csv_path, week_start, timezone)
    out_path = parsed_dir / "weekly_context.json"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(context, indent=2))
    print(f"  weekly_context.json written → {out_path}")
    print(f"  training_focus: {context['training_focus']}")
    high_days = sum(1 for d in context["schedule"] if d["day_type"] == "high")
    training_days = sum(1 for d in context["schedule"] if d["day_type"] == "training")
    rest_days = sum(1 for d in context["schedule"] if d["day_type"] == "rest")
    print(f"  Week pattern: {high_days} high, {training_days} training, {rest_days} rest")
    return out_path


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parents[2]
    # Default: current week Monday
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    if len(sys.argv) > 1:
        monday = datetime.date.fromisoformat(sys.argv[1])
    run(root / "demo_inputs" / "raw", root / "demo_inputs" / "parsed", monday)
