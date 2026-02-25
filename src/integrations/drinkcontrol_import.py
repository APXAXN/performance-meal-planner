"""DrinkControl CSV export → alcohol_summary block in outcome_signals.json.

DrinkControl (iOS/Android) exports a semicolon-delimited CSV with one row per
drinking session.  This parser aggregates those sessions into weekly/monthly
signals and writes an ``alcohol_summary`` block to ``outcome_signals.json``.

The summary is used by the meal-plan pipeline to:
  - Flag recovery nutrition on high-alcohol days (electrolytes, B-vitamins)
  - Adjust sleep quality interpretation (alcohol suppresses REM)
  - Surface caloric displacement (alcohol kcal that crowd out nutrients)
  - Note any acute performance impact on the next training day

CSV format (semicolon-delimited, first row is header):
    AccountedForDate;RegisteredDate;Name;Serving;DrinkSizeInMl;
    AlcoholVolumePercentage;NumberOfDrinks;PriceForSingleDrink;TotalPrice;
    TotalAlcoholInGrams;TotalUnits(USA);TotalAlcoholCalories;TotalCalories

Output added to outcome_signals.json:
    {
        "alcohol_summary": {
            "units_7d":          float,   # USA units in the last 7 days
            "units_28d_avg_wk":  float,   # avg USA units/week over last 28 days
            "kcal_7d":           int,     # alcohol kcal in last 7 days
            "drink_days_7d":     int,     # number of days with any alcohol in last 7
            "heaviest_day_7d":   float,   # peak units on a single day in last 7
            "last_drink_date":   str,     # ISO date of most recent drink (or null)
            "days_since_drink":  int,     # calendar days since last drink (0 = today)
            "flag":              str,     # "none" | "light" | "moderate" | "heavy"
            "recovery_note":     str,     # human-readable context for the planner
        }
    }

Flag thresholds (USA units/week):
    none     ≤ 2
    light    2 < x ≤ 7
    moderate 7 < x ≤ 14
    heavy    > 14

Recovery notes are generated automatically based on flag + recency.

Usage (CLI):
    python src/integrations/drinkcontrol_import.py \\
        --csv ~/Library/Mobile\\ Documents/com~apple~CloudDocs/drinkcontrol.csv

Usage (programmatic):
    from src.integrations.drinkcontrol_import import run
    run(csv_path=Path("~/..."), output_path=Path("demo_inputs/outcome_signals.json"))
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Flag thresholds (USA units / 7-day window)
# ---------------------------------------------------------------------------
_FLAG_THRESHOLDS = [
    (14.0, "heavy"),
    (7.0,  "moderate"),
    (2.0,  "light"),
    (0.0,  "none"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> Optional[date]:
    """Parse 'YYYY-MM-DD ...' → date.  Returns None on failure."""
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _flag(units_7d: float) -> str:
    for threshold, label in _FLAG_THRESHOLDS:
        if units_7d > threshold:
            return label
    return "none"


def _recovery_note(flag: str, days_since: int, units_7d: float, heaviest: float) -> str:
    """Generate a plain-English recovery context string for the meal planner."""
    if flag == "none":
        return "No notable alcohol in the past 7 days — no recovery adjustments needed."

    recency = ""
    if days_since == 0:
        recency = "Drinking occurred today."
    elif days_since == 1:
        recency = "Last drink was yesterday."
    elif days_since <= 3:
        recency = f"Last drink was {days_since} days ago."
    else:
        recency = f"Last drink was {days_since} days ago — acute effects resolved."

    if flag == "light":
        advice = (
            f"{recency} Light week ({units_7d:.1f} units). "
            "Minor REM suppression possible; ensure adequate hydration and B-vitamins."
        )
    elif flag == "moderate":
        advice = (
            f"{recency} Moderate week ({units_7d:.1f} units, peak day {heaviest:.1f} units). "
            "Prioritise electrolytes, B12/folate, and extra carbs on next training day. "
            "Sleep quality may be reduced — treat today's sleep score conservatively."
        )
    else:  # heavy
        advice = (
            f"{recency} Heavy week ({units_7d:.1f} units, peak day {heaviest:.1f} units). "
            "Significant glycogen and protein synthesis impairment likely. "
            "Prioritise recovery nutrition: 1.2–1.6 g/kg protein, high-GI carbs post-ride, "
            "electrolytes, and B-complex. Avoid high-intensity training if acute."
        )
    return advice


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

def parse_drinkcontrol(csv_path: Path, ref_date: Optional[date] = None) -> dict:
    """Parse DrinkControl CSV and return an alcohol_summary dict.

    Args:
        csv_path:  Path to the DrinkControl export CSV.
        ref_date:  Reference date for window calculations (default: today).

    Returns:
        dict with keys matching the alcohol_summary schema block.
    """
    ref_date = ref_date or date.today()
    window_7d_start  = ref_date - timedelta(days=6)   # last 7 days inclusive
    window_28d_start = ref_date - timedelta(days=27)  # last 28 days inclusive

    # Aggregate by calendar date
    by_day: dict[date, dict] = defaultdict(lambda: {"units": 0.0, "kcal": 0.0})

    csv_path = Path(csv_path).expanduser()
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            d = _parse_date(row.get("AccountedForDate", ""))
            if d is None:
                continue
            try:
                units = float(row.get("TotalUnits(USA)", 0) or 0)
                kcal  = float(row.get("TotalAlcoholCalories", 0) or 0)
            except ValueError:
                continue
            by_day[d]["units"] += units
            by_day[d]["kcal"]  += kcal

    if not by_day:
        return {
            "units_7d":         0.0,
            "units_28d_avg_wk": 0.0,
            "kcal_7d":          0,
            "drink_days_7d":    0,
            "heaviest_day_7d":  0.0,
            "last_drink_date":  None,
            "days_since_drink": None,
            "flag":             "none",
            "recovery_note":    "No DrinkControl data found.",
        }

    # --- 7-day window ---
    days_7d = {d: v for d, v in by_day.items() if window_7d_start <= d <= ref_date}
    units_7d      = round(sum(v["units"] for v in days_7d.values()), 2)
    kcal_7d       = round(sum(v["kcal"]  for v in days_7d.values()))
    drink_days_7d = len(days_7d)
    heaviest_7d   = round(max((v["units"] for v in days_7d.values()), default=0.0), 2)

    # --- 28-day window → avg units/week ---
    days_28d = {d: v for d, v in by_day.items() if window_28d_start <= d <= ref_date}
    units_28d = sum(v["units"] for v in days_28d.values())
    units_28d_avg_wk = round(units_28d / 4.0, 2)  # 28 days = 4 weeks

    # --- Last drink ---
    all_drink_dates = sorted(by_day.keys())
    last_drink = all_drink_dates[-1] if all_drink_dates else None
    days_since = (ref_date - last_drink).days if last_drink else None

    # --- Flag and note ---
    flag = _flag(units_7d)
    note = _recovery_note(flag, days_since if days_since is not None else 999,
                          units_7d, heaviest_7d)

    return {
        "units_7d":         units_7d,
        "units_28d_avg_wk": units_28d_avg_wk,
        "kcal_7d":          kcal_7d,
        "drink_days_7d":    drink_days_7d,
        "heaviest_day_7d":  heaviest_7d,
        "last_drink_date":  str(last_drink) if last_drink else None,
        "days_since_drink": days_since,
        "flag":             flag,
        "recovery_note":    note,
    }


# ---------------------------------------------------------------------------
# run() — read/write outcome_signals.json
# ---------------------------------------------------------------------------

def run(csv_path: Path, output_path: Path, ref_date: Optional[date] = None) -> Path:
    """Parse DrinkControl CSV and write/update alcohol_summary in outcome_signals.json.

    Preserves all existing blocks (garmin_summary, mfp_summary, etc.).
    Only the alcohol_summary block is overwritten.

    Args:
        csv_path:     Path to the DrinkControl export CSV.
        output_path:  Path to outcome_signals.json to update.
        ref_date:     Reference date for window calculations (default: today).

    Returns:
        output_path after writing.
    """
    print(f"  Parsing DrinkControl CSV: {csv_path}")

    summary = parse_drinkcontrol(csv_path, ref_date)

    # Pretty-print what we found
    print(f"    units (7d):        {summary['units_7d']}")
    print(f"    units/wk (28d):    {summary['units_28d_avg_wk']}")
    print(f"    kcal (7d):         {summary['kcal_7d']}")
    print(f"    drink days (7d):   {summary['drink_days_7d']}")
    print(f"    heaviest day (7d): {summary['heaviest_day_7d']} units")
    print(f"    last drink:        {summary['last_drink_date']} ({summary['days_since_drink']}d ago)")
    print(f"    flag:              {summary['flag'].upper()}")
    print(f"    note:              {summary['recovery_note'][:80]}...")

    # Load existing outcome_signals to preserve other blocks
    existing: dict = {}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing["alcohol_summary"] = summary

    # Refresh generated_at timestamp
    existing["generated_at"] = (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"  outcome_signals.json updated → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse DrinkControl CSV → alcohol_summary in outcome_signals.json"
    )
    parser.add_argument(
        "--csv",
        required=True,
        metavar="PATH",
        help="Path to DrinkControl export CSV (semicolon-delimited).",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Output path for outcome_signals.json. "
             "Defaults to demo_inputs/outcome_signals.json relative to repo root.",
    )
    parser.add_argument(
        "--ref-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Reference date for window calculations (default: today).",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    csv_path = Path(args.csv).expanduser()
    output_path = (
        Path(args.output) if args.output
        else root / "demo_inputs" / "outcome_signals.json"
    )
    ref_date = date.fromisoformat(args.ref_date) if args.ref_date else None

    run(csv_path=csv_path, output_path=output_path, ref_date=ref_date)
