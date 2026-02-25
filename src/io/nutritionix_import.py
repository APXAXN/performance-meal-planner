"""Nutritionix manual CSV export parser.

The user exports their food log from the Nutritionix mobile app:
  Profile → Logs → Export → CSV

Drop the exported file into inputs/exports/ with a filename matching:
  nutritionix_*.csv

This module finds the most recent such file, parses daily totals, and
returns a summary dict for inclusion in weekly_context.json.

No API credentials required — manual CSV export only.
"""

import csv
import glob
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Expected column names (Nutritionix may vary slightly — handled gracefully)
_COL_DATE = ["date", "Date"]
_COL_NAME = ["food name", "Food Name", "name", "Name", "item", "Item"]
_COL_KCAL = ["calories", "Calories", "energy", "Energy", "kcal"]
_COL_FAT = ["total fat (g)", "Total Fat (g)", "fat (g)", "Fat (g)", "fat_g", "fat"]
_COL_CARBS = ["total carbohydrate (g)", "Total Carbohydrate (g)", "carbs (g)", "Carbs (g)", "carbs_g", "carbs"]
_COL_PROTEIN = ["protein (g)", "Protein (g)", "protein_g", "protein"]


def _find_col(headers: list[str], candidates: list[str]) -> Optional[str]:
    """Return the first candidate that exists in headers (case-insensitive)."""
    lower_headers = {h.lower(): h for h in headers}
    for c in candidates:
        match = lower_headers.get(c.lower())
        if match is not None:
            return match
    return None


def _parse_float(val: str) -> Optional[float]:
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_date(val: str) -> Optional[date]:
    val = str(val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def find_latest_export(exports_dir: str) -> Optional[str]:
    """Scan exports_dir for nutritionix_*.csv; return the most recent by mtime.

    Returns absolute path string or None if no file found.
    """
    pattern = os.path.join(exports_dir, "nutritionix_*.csv")
    matches = glob.glob(pattern)
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


def parse_nutrition_log(csv_path: str) -> list[dict]:
    """Parse a Nutritionix CSV export into daily summary dicts.

    Args:
        csv_path: Path to a nutritionix_*.csv file.

    Returns:
        List of daily dicts:
          {date, calories, protein_g, carbs_g, fat_g, foods: [str]}
        Sorted ascending by date.
    """
    by_date: dict[date, dict] = {}

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        col_date = _find_col(headers, _COL_DATE)
        col_name = _find_col(headers, _COL_NAME)
        col_kcal = _find_col(headers, _COL_KCAL)
        col_fat = _find_col(headers, _COL_FAT)
        col_carbs = _find_col(headers, _COL_CARBS)
        col_protein = _find_col(headers, _COL_PROTEIN)

        if col_date is None:
            logger.warning("Nutritionix CSV: no date column found in %s", csv_path)
            return []

        for row in reader:
            d = _parse_date(row.get(col_date, ""))
            if d is None:
                continue

            kcal = _parse_float(row.get(col_kcal, "")) if col_kcal else None
            fat = _parse_float(row.get(col_fat, "")) if col_fat else None
            carbs = _parse_float(row.get(col_carbs, "")) if col_carbs else None
            protein = _parse_float(row.get(col_protein, "")) if col_protein else None
            food_name = row.get(col_name, "").strip() if col_name else ""

            if d not in by_date:
                by_date[d] = {
                    "date": d.isoformat(),
                    "calories": 0.0,
                    "protein_g": 0.0,
                    "carbs_g": 0.0,
                    "fat_g": 0.0,
                    "foods": [],
                }

            entry = by_date[d]
            entry["calories"] += kcal or 0.0
            entry["protein_g"] += protein or 0.0
            entry["carbs_g"] += carbs or 0.0
            entry["fat_g"] += fat or 0.0
            if food_name:
                entry["foods"].append(food_name)

    # Round aggregates
    for entry in by_date.values():
        entry["calories"] = round(entry["calories"])
        entry["protein_g"] = round(entry["protein_g"], 1)
        entry["carbs_g"] = round(entry["carbs_g"], 1)
        entry["fat_g"] = round(entry["fat_g"], 1)

    return sorted(by_date.values(), key=lambda x: x["date"])


def summarize_week(daily_logs: list[dict]) -> dict:
    """Aggregate daily logs into a weekly summary.

    Args:
        daily_logs: Output of parse_nutrition_log().

    Returns:
        {avg_calories, avg_protein_g, avg_carbs_g, avg_fat_g, days_logged, adherence_pct}
        adherence_pct = days_logged / 7 * 100
    """
    if not daily_logs:
        return {
            "avg_calories": None,
            "avg_protein_g": None,
            "avg_carbs_g": None,
            "avg_fat_g": None,
            "days_logged": 0,
            "adherence_pct": 0.0,
        }

    n = len(daily_logs)
    return {
        "avg_calories": round(sum(d["calories"] for d in daily_logs) / n),
        "avg_protein_g": round(sum(d["protein_g"] for d in daily_logs) / n, 1),
        "avg_carbs_g": round(sum(d["carbs_g"] for d in daily_logs) / n, 1),
        "avg_fat_g": round(sum(d["fat_g"] for d in daily_logs) / n, 1),
        "days_logged": n,
        "adherence_pct": round(n / 7 * 100, 1),
    }


def load_nutritionix(exports_dir: str) -> Optional[dict]:
    """Top-level loader: find latest export, parse it, return weekly summary.

    Returns:
        Weekly summary dict, or None if no export found.
    """
    csv_path = find_latest_export(exports_dir)
    if csv_path is None:
        logger.info(
            "No Nutritionix export found — food log data will be absent from weekly_context. "
            "Drop nutritionix_*.csv into inputs/exports/ before next run."
        )
        return None

    logger.info("Nutritionix: parsing %s", csv_path)
    daily_logs = parse_nutrition_log(csv_path)
    if not daily_logs:
        logger.warning("Nutritionix: CSV parsed but no rows found in %s", csv_path)
        return None

    summary = summarize_week(daily_logs)
    summary["source_file"] = os.path.basename(csv_path)
    summary["daily_logs"] = daily_logs
    return summary
