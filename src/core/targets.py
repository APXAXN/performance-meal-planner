"""Evidence-based macro target engine for cyclists.

Implements the methodology from:
  docs/nutrition_source_of_truth_training.md
  Source: Optimal Cycling Nutrition — Dr Emma Wilkins & Tom Bell (2023)
  PDF: docs/references/Optimal_Cycling_Nutrition.pdf

Key principles (Chapter 4 of source):
  1. Total daily kcal derived from RMR × PAL + training expenditure.
  2. Fat allocated first at 25% of kcal (floor 20%).
  3. Protein set by body weight and day-type/goal factor (g/kg).
  4. Carbs fill remaining energy budget; validated against g/kg ranges.
  5. Carbs drop on rest days; protein rises on rest days (muscle synthesis priority).
  6. Week-level intensity scaling: strictness and carb levels scale with
     how many high-intensity days are in the week (§4 of training doc).

User profile fields consumed:
  weight_kg      — required for all macro math
  height_cm      — for Harris-Benedict BMR (fallback)
  age            — for Harris-Benedict BMR (fallback)
  sex            — "male" | "female" | other (for Harris-Benedict)
  goal           — "maintain" | "gain" | "cut"
  pal_value      — optional float 1.1–2.0; Physical Activity Level (non-training)
  body_fat_pct   — optional float 5–40; enables Cunningham equation for RMR
"""

from typing import Optional


# ---------------------------------------------------------------------------
# Constants from source document
# ---------------------------------------------------------------------------

# Fat allocation as fraction of total kcal (Ch. 4 — Balancing Macronutrients)
FAT_FRACTION_DEFAULT = 0.25
FAT_FRACTION_FLOOR = 0.20   # used on high days if carb budget is tight

# Carbohydrate g/kg ranges by day type (Ch. 4 Table 3)
CARB_RANGE_G_PER_KG = {
    "high":     (6.0, 12.0),
    "training": (5.0, 7.0),
    "rest":     (3.0, 5.0),
}

# Protein factors g/kg by day type and goal (Ch. 4, Ch. 6)
PROTEIN_FACTOR = {
    # (day_type, goal) → g/kg
    ("high",     "maintain"): 1.4,
    ("high",     "gain"):     1.8,
    ("high",     "cut"):      2.0,
    ("training", "maintain"): 1.6,
    ("training", "gain"):     1.8,
    ("training", "cut"):      2.0,
    ("rest",     "maintain"): 1.8,
    ("rest",     "gain"):     1.8,
    ("rest",     "cut"):      2.0,
}

# Absolute protein floor regardless of g/kg calculation (g/day)
PROTEIN_ABS_FLOOR_G_PER_KG = 1.6

# Caloric density constants (kcal per gram)
KCAL_PER_G_FAT = 9
KCAL_PER_G_CARB = 4
KCAL_PER_G_PROTEIN = 4

# Energy deficit applied when goal == "cut" (kcal/day) — Ch. 6
DEFICIT_CUT_KCAL = 300       # ~0.3 kg/week rate, conservative and sustainable

# Energy surplus applied when goal == "gain" (kcal/day)
SURPLUS_GAIN_KCAL = 200

# Default PAL if not specified in user profile (desk job + light walking)
DEFAULT_PAL = 1.55

# Week intensity tiers — drive carb target positioning within range
# (§4 Intensity Scaling in training doc)
WEEK_TIER_HIGH_THRESHOLD = 3     # ≥3 high days → Peak Week
WEEK_TIER_RECOVERY_THRESHOLD = 4  # ≥4 rest days → Recovery Week


# ---------------------------------------------------------------------------
# RMR / BMR Estimation
# ---------------------------------------------------------------------------

def _rmr_cunningham(weight_kg: float, body_fat_pct: float) -> float:
    """Cunningham Equation (preferred when body fat % is known).
    RMR = 22 × FFM + 500  (Source: Ch. 3, citing Cunningham 1980)
    """
    fat_fraction = body_fat_pct / 100.0
    ffm = weight_kg * (1.0 - fat_fraction)
    return 22.0 * ffm + 500.0


def _bmr_harris_benedict(weight_kg: float, height_cm: float,
                          age: float, sex: str) -> float:
    """Harris-Benedict Equation (fallback when body fat % unknown).
    (Source: Ch. 3, citing Harris & Benedict 1919)
    """
    if sex == "female":
        return 655.0955 + (9.5634 * weight_kg) + (1.8496 * height_cm) - (4.6756 * age)
    else:  # male, nonbinary, unspecified — use male equation as conservative default
        return 66.473 + (13.7516 * weight_kg) + (5.0033 * height_cm) - (6.755 * age)


def _estimate_rmr(user: dict) -> Optional[float]:
    """Estimate RMR/BMR from user profile. Returns None if insufficient data."""
    weight = user.get("weight_kg")
    if not isinstance(weight, (int, float)) or weight <= 0:
        return None

    body_fat = user.get("body_fat_pct")
    if isinstance(body_fat, (int, float)) and 3 < body_fat < 60:
        return _rmr_cunningham(weight, body_fat)

    height = user.get("height_cm")
    age = user.get("age")
    sex = user.get("sex", "male")
    if isinstance(height, (int, float)) and isinstance(age, (int, float)):
        return _bmr_harris_benedict(weight, height, age, sex)

    return None


# ---------------------------------------------------------------------------
# TDEE Estimation
# ---------------------------------------------------------------------------

def _estimate_tdee(user: dict, day_type: str) -> Optional[float]:
    """Estimate total daily energy expenditure for a given day type.

    TDEE = RMR × PAL (non-training component) + training energy expenditure.
    Training energy is estimated from day_type using MET values (Ch. 3 Table 2).

    Returns None if RMR cannot be estimated (missing height/age/weight).
    """
    rmr = _estimate_rmr(user)
    if rmr is None:
        return None

    pal = user.get("pal_value", DEFAULT_PAL)
    if not isinstance(pal, (int, float)) or not (1.0 <= pal <= 3.0):
        pal = DEFAULT_PAL

    non_training_tdee = rmr * pal

    # Training energy by day_type using MET × duration heuristic
    # (Ch. 3: Energy expenditure = RMR × MET × (duration_hours / 24))
    if day_type == "high":
        # Interval or long endurance: ~2H at MET 10 (midpoint interval/group ride)
        training_kcal = rmr * 10 * (2.0 / 24)
    elif day_type == "training":
        # Zone 2 endurance ~1.5H at MET 8
        training_kcal = rmr * 8 * (1.5 / 24)
    else:  # rest
        training_kcal = 0.0

    return round(non_training_tdee + training_kcal)


# ---------------------------------------------------------------------------
# Week-Level Intensity Tier
# ---------------------------------------------------------------------------

def week_intensity_tier(schedule: list) -> str:
    """Determine week intensity tier from 7-day schedule.

    Returns: "peak" | "build" | "base" | "recovery"
    (§4 Intensity Scaling, training source of truth doc)
    """
    if not schedule:
        return "build"
    high_count = sum(1 for d in schedule if d.get("day_type") == "high")
    rest_count = sum(1 for d in schedule if d.get("day_type") == "rest")

    if high_count >= WEEK_TIER_HIGH_THRESHOLD:
        return "peak"
    if rest_count >= WEEK_TIER_RECOVERY_THRESHOLD:
        return "recovery"
    if high_count == 0:
        return "base"
    return "build"


def _carb_position(day_type: str, tier: str) -> float:
    """Choose carb g/kg target within the allowed range based on week tier.

    Peak weeks push toward upper bound; recovery weeks toward lower bound.
    """
    lo, hi = CARB_RANGE_G_PER_KG.get(day_type, (5.0, 7.0))
    position = {
        "peak":     0.75,   # upper portion of range — fuelling is critical
        "build":    0.55,   # slightly above midpoint
        "base":     0.45,   # near midpoint — emphasise fat adaptation
        "recovery": 0.30,   # lower portion — modest carbs, protein-forward
    }.get(tier, 0.55)
    return lo + (hi - lo) * position


# ---------------------------------------------------------------------------
# Macro Target Builder
# ---------------------------------------------------------------------------

def targets_for_day(day_type: str, user_profile: Optional[dict] = None,
                    week_schedule: Optional[list] = None) -> dict:
    """Compute evidence-based daily macro targets for a given day type.

    Args:
        day_type:      "high" | "training" | "rest"
        user_profile:  UserProfile dict (weight_kg required; others optional)
        week_schedule: List of schedule day dicts (for week intensity tier)

    Returns:
        dict with keys: kcal, protein_g, carbs_g, fat_g

    Calculation chain (Ch. 4 — Balancing Macronutrients with Energy Demands):
        1. Estimate TDEE (or use fixed base if TDEE unavailable)
        2. Apply goal-based caloric adjustment (deficit/surplus)
        3. Allocate fat at 25% of kcal → fat_g
        4. Set protein_g from weight × factor
        5. carbs_g = remaining budget / 4 - protein_g
        6. Validate carbs against g/kg range; adjust fat % down if needed
    """
    user = user_profile or {}

    weight = user.get("weight_kg")
    if not isinstance(weight, (int, float)) or weight <= 0:
        weight = 75.0   # fallback to reasonable default

    goal = user.get("goal", "maintain")
    tier = week_intensity_tier(week_schedule or [])

    # --- Step 1: Total daily kcal ---
    tdee = _estimate_tdee(user, day_type)

    if tdee is None:
        # TDEE estimation not possible → use simplified heuristic base
        # Base is 33 kcal/kg/day (reasonable maintenance estimate for active adult)
        base_kcal = round(weight * 33)
        if day_type == "high":
            base_kcal += 400
        elif day_type == "rest":
            base_kcal -= 200
        tdee = base_kcal

    # --- Step 2: Goal adjustment ---
    if goal == "cut":
        tdee = max(tdee - DEFICIT_CUT_KCAL, round(weight * 28))  # floor: 28 kcal/kg
    elif goal == "gain":
        tdee += SURPLUS_GAIN_KCAL

    total_kcal = round(tdee)

    # --- Step 3: Fat allocation ---
    fat_kcal = total_kcal * FAT_FRACTION_DEFAULT
    fat_g = round(fat_kcal / KCAL_PER_G_FAT)

    # --- Step 4: Protein ---
    key = (day_type, goal)
    protein_factor = PROTEIN_FACTOR.get(key, PROTEIN_FACTOR.get((day_type, "maintain"), 1.6))
    # Age adjustment: masters cyclists (40+) shift factor up by 0.2 (Ch. 4)
    age = user.get("age")
    if isinstance(age, (int, float)) and age >= 40:
        protein_factor = min(protein_factor + 0.2, 2.3)
    protein_g = round(weight * protein_factor)
    # Apply absolute floor
    protein_floor = round(weight * PROTEIN_ABS_FLOOR_G_PER_KG)
    protein_g = max(protein_g, protein_floor, 120)

    # --- Step 5: Carbs from remaining budget ---
    remaining_kcal = total_kcal - fat_kcal
    remaining_g = remaining_kcal / KCAL_PER_G_CARB
    carbs_g = round(remaining_g - protein_g)

    # --- Step 6: Validate carbs against g/kg range; tighten fat if needed ---
    lo_g, hi_g = CARB_RANGE_G_PER_KG.get(day_type, (5.0, 7.0))
    lo_carbs = round(weight * lo_g)
    hi_carbs = round(weight * hi_g)
    target_carbs = round(weight * _carb_position(day_type, tier))

    if carbs_g < lo_carbs:
        # Not enough carbs — reduce fat to 20% floor to free up budget
        fat_kcal = total_kcal * FAT_FRACTION_FLOOR
        fat_g = round(fat_kcal / KCAL_PER_G_FAT)
        remaining_kcal = total_kcal - fat_kcal
        remaining_g = remaining_kcal / KCAL_PER_G_CARB
        carbs_g = max(round(remaining_g - protein_g), lo_carbs)
    elif carbs_g > hi_carbs:
        # More budget than needed — cap carbs, redirect excess to fat slightly
        carbs_g = hi_carbs

    # Snap to tier-appropriate target if carbs_g is in range
    if lo_carbs <= target_carbs <= hi_carbs:
        carbs_g = target_carbs

    # Final recompute of kcal to be accurate to the actual macros
    actual_kcal = round(
        carbs_g * KCAL_PER_G_CARB +
        protein_g * KCAL_PER_G_PROTEIN +
        fat_g * KCAL_PER_G_FAT
    )

    return {
        "kcal": actual_kcal,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }


# ---------------------------------------------------------------------------
# Convenience / backward-compat wrapper (used by run_weekly.py)
# ---------------------------------------------------------------------------

def targets_for_week(schedule: list, user_profile: Optional[dict] = None) -> list:
    """Compute targets for all 7 days given a weekly schedule list.

    Returns list of per-day target dicts, each with: date, day_type + macro keys.
    """
    results = []
    for day in schedule:
        day_type = day.get("day_type", "rest")
        t = targets_for_day(day_type, user_profile, schedule)
        results.append({
            "date": day.get("date", ""),
            "day_type": day_type,
            "kcal": t["kcal"],
            "protein_g": t["protein_g"],
            "carbs_g": t["carbs_g"],
            "fat_g": t["fat_g"],
        })
    return results
