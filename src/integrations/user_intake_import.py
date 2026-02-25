"""User intake CSV parser.

Reads a single-row CSV from demo_inputs/raw/user_intake.csv and emits
a schema-compliant user_profile.json.

CSV columns (all required unless noted):
    user_id, name, age, sex, height_cm, weight_kg, goal,
    dietary_preferences (comma-separated in quotes),
    allergies (comma-separated in quotes),
    avoid_list (comma-separated in quotes, optional),
    cooking_time_max_min (integer, optional),
    budget_level (low|medium|high, optional)

Assumptions documented in docs/02_Data_Contracts.md.
"""

import csv
import json
from pathlib import Path


def _split_list(value: str) -> list:
    """Split a quoted comma-separated string into a cleaned list."""
    if not value or not value.strip():
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_user_intake(csv_path: Path) -> dict:
    """Parse user_intake.csv → user_profile dict (schema-compliant)."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        raise ValueError(f"No data rows found in {csv_path}")
    if len(rows) > 1:
        print(f"Warning: {csv_path} has {len(rows)} rows; using first row only.")

    row = rows[0]

    profile = {
        "user_id": row["user_id"].strip(),
        "name": row["name"].strip(),
        "age": int(row["age"].strip()),
        "sex": row["sex"].strip().lower(),
        "height_cm": float(row["height_cm"].strip()),
        "weight_kg": float(row["weight_kg"].strip()),
        "goal": row["goal"].strip().lower(),
        "dietary_preferences": _split_list(row.get("dietary_preferences", "")),
        "allergies": _split_list(row.get("allergies", "")),
        "avoid_list": _split_list(row.get("avoid_list", "")),
    }

    cooking_time = row.get("cooking_time_max_min", "").strip()
    if cooking_time:
        profile["cooking_time_max_min"] = int(cooking_time)

    budget = row.get("budget_level", "").strip().lower()
    if budget:
        profile["budget_level"] = budget

    return profile


def run(raw_dir: Path, parsed_dir: Path) -> Path:
    """Parse user_intake.csv and write user_profile.json to parsed_dir."""
    csv_path = raw_dir / "user_intake.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"User intake CSV not found at {csv_path}.\n"
            "Create it from the template at demo_inputs/raw/user_intake.csv."
        )

    profile = parse_user_intake(csv_path)
    out_path = parsed_dir / "user_profile.json"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(profile, indent=2))
    print(f"  user_profile.json written → {out_path}")
    return out_path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    run(root / "demo_inputs" / "raw", root / "demo_inputs" / "parsed")
