"""Macro target heuristics by day type."""


def _base_targets(user_profile: dict | None) -> dict:
    # Minimal demo heuristic: adjust protein slightly by body weight if present
    weight = None if not user_profile else user_profile.get("weight_kg")
    if isinstance(weight, (int, float)) and weight > 0:
        protein = max(140, round(weight * 2.0))
    else:
        protein = 150

    return {
        "kcal": 2500,
        "protein_g": protein,
        "carbs_g": 300,
        "fat_g": 70,
    }


def targets_for_day(day_type: str, user_profile: dict | None = None) -> dict:
    base = _base_targets(user_profile)

    if day_type == "high":
        return {
            "kcal": base["kcal"] + 300,
            "protein_g": base["protein_g"] + 5,
            "carbs_g": base["carbs_g"] + 80,
            "fat_g": base["fat_g"] + 5,
        }
    if day_type == "rest":
        return {
            "kcal": base["kcal"] - 300,
            "protein_g": base["protein_g"] + 15,
            "carbs_g": base["carbs_g"] - 100,
            "fat_g": base["fat_g"],
        }
    return base
