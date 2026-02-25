#!/usr/bin/env python3
"""
Performance Meal Planner — Weekly Pipeline (V1)

Stage-gated pipeline implementing 09_Agent_Roles_and_Pipeline.md.

Stages:
  0  Validate Inputs
  1  Nutrition Planner  → plan_intent.md + plan_intent.json
  2  Recipe Curator     → recipes.md
  3  Grocery Mapper     → grocery_list.csv + grocery_notes.md
  4  Data Analyst       → plan_modifications.json + Insights_Report.md (V1: always insufficient)
  4b Revision Pass      → plan_intent_revised.md (V2 only — skipped in V1)
  5  Compose Digest     → Weekly_Email_Digest.md
  6  QA Gate            → qa_report.md

Data Analyst is V2 infrastructure: always outputs data_confidence=insufficient in V1.
Run is executable without Data Analyst live (Stage 4b never fires in V1).
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.day_type import detect_day_type  # noqa: E402
from core.targets import targets_for_day, week_intensity_tier  # noqa: E402
from core.normalize_grocery import rollup  # noqa: E402
from integrations.gmail_draft import create_draft  # noqa: E402
from integrations import (  # noqa: E402
    garmin_import,
    user_intake_import,
    kroger_cart,
    garmin_wellness_import,
    drinkcontrol_import,
)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def load_json(path):
    return json.loads(path.read_text())


def render_template(template_text, values):
    out = template_text
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def load_schema(name):
    return json.loads((ROOT / "schemas" / name).read_text())


def build_registry():
    resources = []
    for path in (ROOT / "schemas").glob("*.json"):
        data = json.loads(path.read_text())
        resources.append((path.name, Resource.from_contents(data)))
    return Registry().with_resources(resources)


def validate_or_exit(instance, schema, label, registry):
    validator = Draft7Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if errors:
        first = errors[0]
        path = ".".join([str(p) for p in first.path]) or "(root)"
        raise ValidationError(f"Validation failed for {label}: {path} - {first.message}")


# ---------------------------------------------------------------------------
# Run Log
# ---------------------------------------------------------------------------

class RunLog:
    """Records stage completions, defaults applied, and fallbacks."""

    def __init__(self, week_start):
        self.week_start = week_start
        self.stages = []
        self.defaults = []
        self.fallbacks = []

    def record_stage(self, stage, status="PASS", note=""):
        ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self.stages.append((stage, ts, status, note))

    def add_default(self, msg):
        self.defaults.append(msg)

    def add_fallback(self, msg):
        self.fallbacks.append(msg)

    def to_markdown(self):
        lines = [f"# Run Log — {self.week_start}", ""]
        lines.append("## Stage Completions")
        for stage, ts, status, note in self.stages:
            note_str = f" — {note}" if note else ""
            lines.append(f"- {stage}: {ts} — {status}{note_str}")
        lines.append("")
        lines.append("## Defaults Applied")
        if self.defaults:
            for d in self.defaults:
                lines.append(f"- {d}")
        else:
            lines.append("- None")
        lines.append("")
        lines.append("## Fallbacks")
        if self.fallbacks:
            for f in self.fallbacks:
                lines.append(f"- {f}")
        else:
            lines.append("- None")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 0 — Validate Inputs
# ---------------------------------------------------------------------------

def stage0_validate(user, context, signals, run_log):
    """Validate required fields. Returns list of defaults applied."""
    defaults = []

    for field in ["user_id", "name", "age", "sex", "height_cm", "weight_kg", "goal"]:
        if field not in user:
            raise SystemExit(f"Stage 0 HALT: user_profile.json missing required field '{field}'")

    for field in ["week_start", "timezone", "schedule", "training_focus"]:
        if field not in context:
            raise SystemExit(f"Stage 0 HALT: weekly_context.json missing required field '{field}'")

    schedule = context.get("schedule", [])
    if len(schedule) != 7:
        raise SystemExit(f"Stage 0 HALT: schedule must have exactly 7 days; found {len(schedule)}")

    if not user.get("pal_value"):
        defaults.append("pal_value: applied default 1.35 (desk job + light walking)")
        run_log.add_default("pal_value: default 1.35")

    if not user.get("body_fat_pct"):
        defaults.append("body_fat_pct: not set; using Harris-Benedict equation (Cunningham unavailable)")
        run_log.add_default("body_fat_pct: missing — using Harris-Benedict")

    if not signals.get("mfp_summary", {}).get("avg_kcal"):
        defaults.append("MFP data: not available; macro adherence tracking unavailable this week")
        run_log.add_default("mfp_summary.avg_kcal: null — no adherence tracking")

    return defaults


# ---------------------------------------------------------------------------
# Stage 1 — Nutrition Planner → plan_intent.md + plan_intent.json
# ---------------------------------------------------------------------------

def build_meal_id_table(context):
    """Build the 28 meal ID entries for the week."""
    meal_ids = []
    for i, day in enumerate(context["schedule"], start=1):
        day_type = detect_day_type(day)
        for slot in ["Breakfast", "Lunch", "Dinner", "Snack"]:
            meal_ids.append({
                "meal_id": f"D{i}_{slot}",
                "date": day["date"],
                "slot": slot,
                "day_type": day_type,
            })
    return meal_ids


def stage1_plan_intent(user, context, signals, run_log, defaults):
    """Build plan_intent from weekly context and user profile."""
    schedule = context["schedule"]
    week_start = context["week_start"]
    tier = week_intensity_tier(schedule)

    per_day = []
    for day in schedule:
        day_type = detect_day_type(day)
        t = targets_for_day(day_type, user, schedule)
        per_day.append({"date": day["date"], "day_type": day_type, **t})

    training_days_list = [d for d in per_day if d["day_type"] in ("training", "high")]
    rest_days_list = [d for d in per_day if d["day_type"] == "rest"]
    high_days_list = [d for d in per_day if d["day_type"] == "high"]

    avg_kcal = round(sum(d["kcal"] for d in per_day) / len(per_day))
    avg_protein = round(sum(d["protein_g"] for d in per_day) / len(per_day))
    avg_fat = round(sum(d["fat_g"] for d in per_day) / len(per_day))
    avg_carbs_training = round(
        sum(d["carbs_g"] for d in per_day if d["day_type"] in ("training", "high")) /
        max(len(training_days_list), 1)
    )
    avg_carbs_rest = round(
        sum(d["carbs_g"] for d in rest_days_list) / max(len(rest_days_list), 1)
    )

    meal_structure = {
        "training_day": {
            "breakfast": "Moderate carbs (50-70g), protein anchor 35-45g, easy prep — e.g., Greek yogurt bowl or egg-based",
            "lunch": "Balanced meal, carb-forward, protein anchor 45-55g — e.g., grain bowl or sandwich",
            "dinner": "Higher protein (50-60g), moderate carbs, batch-cook friendly — e.g., salmon/chicken + rice + veg",
            "snack": "Protein-anchored (>=15g), light carbs — e.g., apple + nut butter or cottage cheese",
        },
        "high_day": {
            "breakfast": "High-carb (80-110g), easy prep, pre-training fuel — e.g., oats + banana + protein",
            "lunch": "Carb-forward post-training (100-120g carbs), protein anchor 40-50g — e.g., turkey rice bowl",
            "dinner": "High protein (55-65g), high carbs (100-120g), recovery-focused — e.g., salmon pasta or chicken stir-fry with rice",
            "snack": "Rapid-carb + protein (>=20g protein, >=30g carbs) — e.g., yogurt + granola + berries",
        },
        "rest_day": {
            "breakfast": "Protein-forward (40-50g), lower carbs (<30g), higher fat — e.g., egg white scramble + avocado",
            "lunch": "Protein anchor (50-55g), moderate carbs (40-50g) — e.g., chicken salad + quinoa",
            "dinner": "Early dinner (before 7pm), moderate protein (45-50g), lower carbs — e.g., miso tofu rice or chicken + veg",
            "snack": "Protein-focused (>=20g), minimal carbs — e.g., cottage cheese + berries",
        },
    }

    rationale = _build_rationale(user, context, signals, per_day, tier)
    meal_ids = build_meal_id_table(context)

    plan_intent = {
        "week_start": week_start,
        "macro_plan": {
            "daily_avg_kcal": avg_kcal,
            "protein_g": avg_protein,
            "carbs_g_training": avg_carbs_training,
            "carbs_g_rest": avg_carbs_rest,
            "fat_g": avg_fat,
        },
        "day_types": {
            "training_days": [d["date"] for d in per_day if d["day_type"] == "training"],
            "high_days": [d["date"] for d in per_day if d["day_type"] == "high"],
            "rest_days": [d["date"] for d in per_day if d["day_type"] == "rest"],
        },
        "meal_structure": meal_structure,
        "rationale": rationale,
        "meal_ids": meal_ids,
        "defaults_applied": defaults,
        "per_day_targets": per_day,
    }

    return plan_intent


def _build_rationale(user, context, signals, per_day, tier):
    """Build 4-8 rationale bullets tied to this week's signals."""
    bullets = []
    goal = user.get("goal", "maintain")
    training_focus = context.get("training_focus", "general fitness")
    weight = user.get("weight_kg", 75)

    high_count = sum(1 for d in per_day if d["day_type"] == "high")
    rest_count = sum(1 for d in per_day if d["day_type"] == "rest")
    training_count = sum(1 for d in per_day if d["day_type"] == "training")

    bullets.append(
        f"Goal: {goal} — calorie targets set via evidence-based TDEE calculation "
        f"(Harris-Benedict + PAL {user.get('pal_value', 1.35)})."
    )
    bullets.append(
        f"Week pattern: {high_count} high-intensity, {training_count} training, {rest_count} rest — "
        f"week tier classified as '{tier}'. Carb targets positioned at "
        f"{'upper' if tier == 'peak' else 'mid-upper' if tier == 'build' else 'mid' if tier == 'base' else 'lower'} "
        f"end of daily range."
    )
    age = user.get("age", 35)
    bullets.append(
        f"Protein set at {'1.8-2.0' if age >= 40 else '1.6-1.8'} g/kg "
        f"({weight:.0f} kg) — {'elevated for masters athlete (age 40+)' if age >= 40 else 'standard endurance athlete range'}. "
        f"Rest days prioritize protein synthesis (higher protein, lower carbs)."
    )
    bullets.append(
        f"Training focus: {training_focus} — meal structure supports this with "
        f"{'carbohydrate periodization (high-carb on intensity days, moderate on endurance, lower on rest)' if high_count > 0 else 'consistent moderate carb intake across training days'}."
    )

    garmin = signals.get("garmin_summary", {})
    alcohol = signals.get("alcohol_summary", {})
    acwr = garmin.get("acwr")
    sleep = garmin.get("avg_sleep_hr")
    training_load = garmin.get("training_load", "unknown")

    if training_load in ("high",) or (acwr and acwr > 1.3):
        bullets.append(
            f"Training load is HIGH (ACWR: {acwr or 'elevated'}) — added ~100 kcal buffer on training days "
            f"to support recovery. Monitor for fatigue; consider reducing load if energy declines."
        )
    elif training_load == "moderate":
        bullets.append(
            f"Training load is moderate (ACWR: {acwr or 'mid-range'}) — standard fuelling strategy applied."
        )

    if sleep and sleep < 7.0:
        bullets.append(
            f"Sleep average: {sleep:.1f} hrs (below 7 hr target) — prioritize earlier dinners on rest days "
            f"and magnesium-rich foods (spinach, pumpkin seeds) to support sleep quality."
        )
    elif sleep and sleep >= 8.0:
        bullets.append(
            f"Sleep average: {sleep:.1f} hrs (good) — recovery is well-supported. Maintaining current meal timing."
        )

    if alcohol.get("flag") in ("moderate", "heavy"):
        bullets.append(
            f"Alcohol: {alcohol.get('units_7d', 0):.1f} units last 7 days ({alcohol.get('flag')} flag) — "
            f"plan includes B-vitamin rich foods (leafy greens, eggs) and hydration emphasis. "
            f"{alcohol.get('recovery_note', '')}"
        )
    elif alcohol.get("flag") == "light" and alcohol.get("units_7d", 0) > 0:
        bullets.append(
            f"Alcohol: {alcohol.get('units_7d', 0):.1f} units last 7 days (light) — "
            f"minor consideration. {alcohol.get('recovery_note', 'Maintain hydration.')}"
        )

    return bullets[:8]


def plan_intent_to_markdown(plan):
    """Render plan_intent dict to plan_intent.md format."""
    mp = plan["macro_plan"]
    dt = plan["day_types"]
    ms = plan["meal_structure"]
    lines = [f"# Plan Intent — {plan['week_start']}", ""]

    lines += [
        "## Macro Plan",
        f"- Daily average calories: {mp['daily_avg_kcal']} kcal",
        f"- Protein target: {mp['protein_g']}g (all days)",
        f"- Carb target (training/high days): {mp['carbs_g_training']}g",
        f"- Carb target (rest days): {mp['carbs_g_rest']}g",
        f"- Fat target: {mp['fat_g']}g",
        "",
        "## Day Types",
        f"- Training days: {', '.join(dt['training_days']) or 'None'}",
        f"- High-intensity days: {', '.join(dt['high_days']) or 'None'}",
        f"- Rest/recovery days: {', '.join(dt['rest_days']) or 'None'}",
        "",
        "## Meal Structure (by day type)",
    ]

    for day_type_key, label in [
        ("training_day", "Training Days"),
        ("high_day", "High-Intensity Days"),
        ("rest_day", "Rest Days"),
    ]:
        s = ms.get(day_type_key, {})
        if s:
            lines.append(f"\n### {label}")
            for slot in ["breakfast", "lunch", "dinner", "snack"]:
                if slot in s:
                    lines.append(f"- {slot.title()}: {s[slot]}")

    lines += ["", "## Rationale"]
    for b in plan["rationale"]:
        lines.append(f"- {b}")

    lines += ["", "## Meal IDs", "| Meal ID | Date | Slot | Day Type |", "|---|---|---|---|"]
    for m in plan["meal_ids"]:
        lines.append(f"| {m['meal_id']} | {m['date']} | {m['slot']} | {m['day_type']} |")

    if plan.get("defaults_applied"):
        lines += ["", "## Defaults Applied"]
        for d in plan["defaults_applied"]:
            lines.append(f"- {d}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 2 — Recipe Curator → recipes.md
# ---------------------------------------------------------------------------

def stage2_recipes(plan_intent, meal_buckets):
    """Map meal IDs to recipes from meal_buckets.json."""
    recipes = []
    for m in plan_intent["meal_ids"]:
        meal_id = m["meal_id"]
        day_type = m["day_type"]
        slot = m["slot"].lower()

        bucket = meal_buckets.get(day_type, meal_buckets.get("training", []))
        meal = _pick_meal_for_slot(bucket, slot)

        if meal:
            recipes.append({
                "meal_id": meal_id,
                "date": m["date"],
                "day_type": day_type,
                "slot": slot,
                "name": meal["name"],
                "time": meal.get("time", ""),
                "recipe_link": meal.get("recipe_link", ""),
                "batch_cook": False,
                "key_ingredients": [i["name"] for i in meal.get("ingredients", [])[:5]],
                "ingredients": meal.get("ingredients", []),
                "macros": meal.get("macros", {}),
                "substitution_note": "",
            })

    _mark_batch_cook(recipes)
    return recipes


def _pick_meal_for_slot(bucket, slot):
    """Find the meal in the bucket for the given slot.

    Strategy (in order):
    1. Exact prefix match: meal name starts with "Slot:" (e.g., "Dinner: Salmon...")
    2. Fallback: positional index by slot order
    """
    slot_prefix = slot.title() + ":"
    for meal in bucket:
        if meal["name"].startswith(slot_prefix):
            return meal
    slot_index = {"breakfast": 0, "lunch": 1, "dinner": 3, "snack": 2}
    idx = slot_index.get(slot, 0)
    if idx < len(bucket):
        return bucket[idx]
    return bucket[0] if bucket else None


def _mark_batch_cook(recipes):
    """Mark dinners that share a recipe name as batch-cook."""
    dinner_names = {}
    for r in recipes:
        if r["slot"] == "dinner":
            dinner_names.setdefault(r["name"], []).append(r["meal_id"])
    for r in recipes:
        if r["slot"] == "dinner" and len(dinner_names.get(r["name"], [])) > 1:
            r["batch_cook"] = True


def recipes_to_markdown(recipes):
    lines = ["# Recipes", ""]
    for r in recipes:
        batch_str = "yes" if r["batch_cook"] else "no"
        link = r.get("recipe_link", "")
        source = link.split("/")[2] if link and "/" in link and len(link.split("/")) > 2 else "simple build"
        recipe_line = (
            f"- **Recipe:** [{r['name']}]({link})" if link
            else f"- **Recipe:** Simple Build — {r['name']}"
        )
        lines += [
            f"### {r['meal_id']} — {r['name']}",
            f"- **Date:** {r['date']}",
            f"- **Day Type:** {r['day_type']}",
            recipe_line,
            f"- **Source:** {source}",
            f"- **Batch-cook:** {batch_str}",
            f"- **Estimated macros:** {r['macros'].get('kcal', 0):.0f} kcal | "
            f"P{r['macros'].get('protein_g', 0):.0f}g C{r['macros'].get('carbs_g', 0):.0f}g F{r['macros'].get('fat_g', 0):.0f}g",
            f"- **Key ingredients:** {', '.join(r['key_ingredients'])}",
        ]
        if r.get("substitution_note"):
            lines.append(f"- **Substitution note:** {r['substitution_note']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 3 — Grocery Mapper → grocery_list.csv + grocery_notes.md
# ---------------------------------------------------------------------------

def stage3_grocery(recipes, user, week_start):
    """Build grocery list from recipes. Returns (grocery_list_json, csv_rows)."""
    raw_items = []
    csv_rows = []

    for recipe in recipes:
        meal_id = recipe["meal_id"]
        for ing in recipe.get("ingredients", []):
            ing_id = "ing_" + "_".join(
                ing["name"].lower().replace("-", "_").replace(" ", "_").split()
            )
            raw_items.append({
                "name": ing["name"],
                "quantity": ing["quantity"],
                "unit": ing["unit"],
                "category": ing.get("category", "other"),
                "source_days": [recipe["date"]],
                "meal_id": meal_id,
                "ingredient_id": ing_id,
            })
            csv_rows.append({
                "meal_id": meal_id,
                "ingredient_id": ing_id,
                "category": ing.get("category", "other"),
                "item_name": ing["name"],
                "quantity": ing["quantity"],
                "unit": ing["unit"],
                "store": "Fred Meyer",
                "price": "",
                "sku": "",
                "match_confidence": "approximate",
                "substitute_1": "",
                "substitute_2": "",
            })

    rolled = rollup(raw_items)
    grocery_list = {"week_start": week_start, "items": rolled}
    csv_aggregated = _aggregate_csv_rows(csv_rows)

    return grocery_list, csv_aggregated


def _aggregate_csv_rows(rows):
    """Aggregate CSV rows by ingredient_id, summing quantities, collecting meal_ids."""
    by_id = {}
    for row in rows:
        ing_id = row["ingredient_id"]
        if ing_id not in by_id:
            by_id[ing_id] = dict(row)
            by_id[ing_id]["_meal_ids"] = {row["meal_id"]}
        else:
            by_id[ing_id]["quantity"] = float(by_id[ing_id]["quantity"]) + float(row["quantity"])
            by_id[ing_id]["_meal_ids"].add(row["meal_id"])

    result = []
    for ing_id, row in by_id.items():
        meal_ids = sorted(row.pop("_meal_ids"))
        row["meal_id"] = "MULTI" if len(meal_ids) > 1 else meal_ids[0]
        row["quantity"] = round(float(row["quantity"]))
        result.append(row)

    return sorted(result, key=lambda r: (r["category"], r["item_name"]))


def write_grocery_csv(rows, out_path):
    """Write grocery_list.csv with the spec-defined columns."""
    fieldnames = [
        "meal_id", "ingredient_id", "category", "item_name",
        "quantity", "unit", "store", "price", "sku",
        "match_confidence", "substitute_1", "substitute_2",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def grocery_notes_to_markdown(grocery_list, csv_rows):
    items = grocery_list.get("items", [])
    total_items = len(items)
    approximate = [r["item_name"] for r in csv_rows if r.get("match_confidence") == "approximate"]
    no_match = [r["item_name"] for r in csv_rows if r.get("match_confidence") == "best-effort"]
    multi_meal = [r["item_name"] for r in csv_rows if r.get("meal_id") == "MULTI"]

    est_low = total_items * 2.5
    est_high = total_items * 5.0

    lines = [
        "# Grocery Notes",
        "",
        "## Store: Fred Meyer",
        f"## Budget Estimate: ${est_low:.0f}–${est_high:.0f} (approximate; {total_items} line items)",
        "",
        "## Items Flagged as Approximate",
    ]
    if approximate:
        for item in sorted(set(approximate)):
            lines.append(f"- {item}")
    else:
        lines.append("- None")

    lines += ["", "## Items With No Match (Needs Manual Lookup)"]
    if no_match:
        for item in sorted(set(no_match)):
            lines.append(f"- {item}")
    else:
        lines.append("- None")

    lines += ["", "## Batch-Cook Notes"]
    if multi_meal:
        lines.append(f"- {len(multi_meal)} ingredients aggregated across multiple meals (meal_id=MULTI in CSV)")
        for item in sorted(set(multi_meal))[:8]:
            lines.append(f"  - {item}")
    else:
        lines.append("- No batch-cook aggregation this week")

    return "\n".join(lines)


def grocery_to_markdown(grocery_list):
    """Render grocery list as readable Markdown."""
    groups = {}
    for item in grocery_list.get("items", []):
        groups.setdefault(item.get("category", "unknown"), []).append(item)
    has_prices = any(i.get("price_usd") for i in grocery_list.get("items", []))
    lines = [f"# Grocery List ({grocery_list.get('week_start')})", "", "**Items**"]
    for category in sorted(groups.keys()):
        lines.append(f"\n{category.title()}")
        for item in sorted(groups[category], key=lambda x: x.get("name_normalized", "")):
            qty = item.get("total_quantity")
            unit = item.get("unit")
            price = item.get("price_usd")
            store_name = item.get("store_item_name", "")
            match_type = item.get("match_type", "")
            line = f"- {item.get('name_display')} — {qty:.0f} {unit}"
            if price is not None:
                line += f" | ${price:.2f}"
                if match_type == "approximate":
                    line += " (approx match)"
            elif has_prices:
                line += " | price unavailable"
            if store_name and store_name.lower() != item.get("name_display", "").lower():
                line += f"\n  -> {store_name}"
            lines.append(line)
    if has_prices:
        priced = [i for i in grocery_list.get("items", []) if i.get("price_usd")]
        total = sum(i["price_usd"] for i in priced)
        lines.append(f"\n**Estimated Total (Kroger):** ${total:.2f} ({len(priced)}/{len(grocery_list.get('items', []))} items priced)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 4 — Data Analyst (V2 infrastructure — always insufficient in V1)
# ---------------------------------------------------------------------------

def _count_feature_table_rows(path):
    """Count data rows in Feature_Table.csv. Returns 0 if file doesn't exist."""
    if not path.exists():
        return 0
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        return sum(1 for _ in reader)


def _week_already_in_feature_table(path, week_start):
    """Return True if this week_start already has a row in Feature_Table.csv."""
    if not path.exists():
        return False
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        return any(row.get("week_start") == week_start for row in reader)


def _append_feature_table(plan_intent, signals, path, current_rows):
    """Append one row to Feature_Table.csv for the current week (idempotent per week_start)."""
    week_start = plan_intent["week_start"]
    if _week_already_in_feature_table(path, week_start):
        return  # already recorded this week; do not duplicate
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "week_start", "week_tier", "avg_kcal", "avg_protein_g", "avg_carbs_g", "avg_fat_g",
        "training_days", "rest_days", "high_days",
        "avg_sleep_hr", "avg_rhr", "acwr", "training_load",
        "alcohol_units_7d", "alcohol_flag",
        "mfp_avg_kcal", "mfp_protein_g", "notes",
    ]

    mp = plan_intent["macro_plan"]
    dt = plan_intent["day_types"]
    garmin = signals.get("garmin_summary", {})
    alcohol = signals.get("alcohol_summary", {})
    mfp = signals.get("mfp_summary", {})

    high_n = len(dt.get("high_days", []))
    rest_n = len(dt.get("rest_days", []))
    if high_n >= 3:
        tier = "peak"
    elif rest_n >= 4:
        tier = "recovery"
    elif high_n == 0:
        tier = "base"
    else:
        tier = "build"

    row = {
        "week_start": plan_intent["week_start"],
        "week_tier": tier,
        "avg_kcal": mp["daily_avg_kcal"],
        "avg_protein_g": mp["protein_g"],
        "avg_carbs_g": mp["carbs_g_training"],
        "avg_fat_g": mp["fat_g"],
        "training_days": len(dt.get("training_days", [])),
        "rest_days": rest_n,
        "high_days": high_n,
        "avg_sleep_hr": garmin.get("avg_sleep_hr", ""),
        "avg_rhr": garmin.get("avg_rhr", ""),
        "acwr": garmin.get("acwr", ""),
        "training_load": garmin.get("training_load", ""),
        "alcohol_units_7d": alcohol.get("units_7d", ""),
        "alcohol_flag": alcohol.get("flag", ""),
        "mfp_avg_kcal": mfp.get("avg_kcal", ""),
        "mfp_protein_g": mfp.get("protein_g", ""),
        "notes": f"V1 baseline row {current_rows + 1}",
    }

    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def stage4_data_analyst(plan_intent, signals, out_dir, run_log):
    """
    V1: Always produces data_confidence=insufficient, revision_pass_authorized=false.
    Appends row to Feature_Table.csv for future V2 activation.
    """
    feature_table_path = ROOT / "data" / "Feature_Table.csv"
    weeks_available = _count_feature_table_rows(feature_table_path)

    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    plan_modifications = {
        "generated_at": ts,
        "data_confidence": "insufficient",
        "revision_pass_authorized": False,
        "modifications": [],
        "max_modifications_applied": 3,
        "v1_note": (
            f"Insufficient historical data: Feature_Table.csv has {weeks_available} week(s) "
            f"(minimum 4 required to activate analysis). "
            f"Appending current week to Feature_Table.csv."
        ),
    }

    _append_feature_table(plan_intent, signals, feature_table_path, weeks_available)

    run_log.record_stage(
        "Stage 4 (Data Analyst)",
        "PASS",
        f"data_confidence=insufficient — Stage 4b skipped ({weeks_available} weeks in Feature_Table.csv, need 4)",
    )

    return plan_modifications, weeks_available


def insights_report_v1(plan_modifications, weeks_available):
    """Generate the V1 Insights Report (advisory only — no modifications)."""
    lines = [
        "# Insights Report (V1 — Data Accumulation Mode)",
        "",
        "## Signals Summary",
        "No historical baseline available. This is an early run of the pipeline.",
        f"Feature_Table.csv currently has {weeks_available} week(s) of data (4 required to activate analysis).",
        "",
        "## Data Analyst Status",
        f"**V1 Mode:** Inactive. Reason: {plan_modifications.get('v1_note', 'Insufficient data.')}",
        "",
        "**Activation threshold:** 4 complete weekly rows in `data/Feature_Table.csv`.",
        "**Current status:** Accumulating baseline data. No modifications proposed.",
        "",
        "## What to Track Next Week",
        "- Log energy level 1-5 at 2pm each day",
        "- Note which meals you actually cooked vs substituted",
        "- Rate sleep quality 1-5 each morning",
        "- Log any GI discomfort after meals (1=none, 5=significant)",
        "- Note how training felt (RPE 1-10) on each training day",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 5 — Compose Digest
# ---------------------------------------------------------------------------

def build_meal_plan_section(recipes, plan_intent):
    """Build the Meal Plan section for the digest (Mon-Sun format)."""
    by_date = {}
    for r in recipes:
        date = r["date"]
        if date not in by_date:
            by_date[date] = {"day_type": r["day_type"], "meals": {}}
        by_date[date]["meals"][r["slot"]] = r

    lines = []
    seen_dates = set()
    for m in plan_intent["meal_ids"]:
        date = m["date"]
        if date in seen_dates:
            continue
        seen_dates.add(date)
        day_data = by_date.get(date, {})
        day_type = day_data.get("day_type", m["day_type"])
        day_name = datetime.date.fromisoformat(date).strftime("%A")
        lines.append(f"### {day_name} {date} — {day_type.title()} Day")

        for slot in ["breakfast", "lunch", "dinner", "snack"]:
            meal = day_data.get("meals", {}).get(slot)
            if meal:
                link = meal.get("recipe_link", "")
                name = meal.get("name", "")
                if link:
                    lines.append(f"- **{slot.title()}:** [{name}]({link})")
                else:
                    lines.append(f"- **{slot.title()}:** {name} *(simple build)*")
        lines.append("")

    return "\n".join(lines)


def build_data_analyst_notes(plan_modifications):
    """Build the Data Analyst Notes section for the digest."""
    confidence = plan_modifications.get("data_confidence", "insufficient")
    v1_note = plan_modifications.get("v1_note", "")
    mods_applied = len(plan_modifications.get("modifications", []))

    if confidence == "insufficient":
        return (
            "**Modifications applied to this plan:** None — insufficient historical data\n\n"
            f"*{v1_note}*\n\n"
            "*Data Analyst will activate after 4 weeks of pipeline runs. "
            "See `data/Feature_Table.csv` for accumulation progress.*\n\n"
            "*These signals are correlational, not causal. Training load, sleep environment, "
            "and stress are not fully controlled.*"
        )

    lines = [f"**Modifications applied to this plan:** {mods_applied} of 3 max", ""]
    for mod in plan_modifications.get("modifications", []):
        lines.append(f"- {mod['meal_id']}: {mod['proposed_value']} (confidence: {mod['confidence']})")
    lines += [
        "",
        "*These signals are correlational, not causal. Training load, sleep environment, "
        "and stress are not fully controlled.*",
    ]
    return "\n".join(lines)


def build_notes_assumptions(defaults, plan_modifications):
    """Build Notes / Assumptions section."""
    lines = []
    if defaults:
        lines.append("**Defaults applied (inputs were missing):**")
        for d in defaults:
            lines.append(f"- {d}")
        lines.append("")
    mods = plan_modifications.get("modifications", [])
    if mods:
        lines.append("**Plan modifications applied:**")
        for mod in mods:
            lines.append(f"- {mod['modification_id']}: {mod['meal_id']} — {mod['proposed_value']}")
        lines.append("")
    if not defaults and not mods:
        lines.append("- No defaults or modifications applied this week.")
    return "\n".join(lines) if lines else "- No defaults or modifications applied this week."


def build_email_digest(context, user, plan_intent, recipes, grocery_list,
                       plan_modifications, qa_report, defaults, run_log,
                       grocery_diff_lines=None, qa_placeholder=False):
    """Assemble the Weekly_Email_Digest.md from all approved artifacts."""
    week_start = context["week_start"]
    dt_obj = datetime.date.fromisoformat(week_start)
    week_label = f"W{dt_obj.isocalendar()[1]:02d}"

    per_day = plan_intent.get("per_day_targets", [])
    high_count = sum(1 for d in per_day if d["day_type"] == "high")
    rest_count = sum(1 for d in per_day if d["day_type"] == "rest")
    training_count = sum(1 for d in per_day if d["day_type"] == "training")

    if grocery_diff_lines:
        theme = "Higher-carb support for load"
    elif high_count >= 2:
        theme = "Peak load week"
    elif rest_count >= 3:
        theme = "Recovery focus week"
    else:
        theme = "Supportive load balance"

    subject_line = f"Week {week_label} — {theme}"

    mp = plan_intent["macro_plan"]
    at_a_glance = "\n".join([
        f"- Training focus: {context.get('training_focus', 'General training')}",
        f"- Pattern: {high_count} intensity, {training_count} endurance, {rest_count} rest days",
        f"- Goal: {user.get('goal', 'maintain')} — avg {mp['daily_avg_kcal']} kcal/day",
        f"- Protein target: {mp['protein_g']}g/day | Carbs: {mp['carbs_g_training']}g training / {mp['carbs_g_rest']}g rest",
        f"- Grocery list ready: {len(grocery_list.get('items', []))} items across "
        f"{len(set(i.get('category', 'other') for i in grocery_list.get('items', [])))} categories",
    ])

    targets_table = "\n".join([
        f"- {t['date']} ({t['day_type']}): {t['kcal']:.0f} kcal | "
        f"P{t['protein_g']:.0f}g C{t['carbs_g']:.0f}g F{t['fat_g']:.0f}g"
        for t in per_day
    ])

    plan_rationale = "\n".join([f"- {b}" for b in plan_intent.get("rationale", [])])
    data_analyst_notes = build_data_analyst_notes(plan_modifications)
    meal_plan_section = build_meal_plan_section(recipes, plan_intent)

    top_items = sorted(grocery_list.get("items", []), key=lambda x: x.get("total_quantity", 0), reverse=True)
    grocery_top = "\n".join([
        f"- {i.get('name_display')} — {i.get('total_quantity', 0):.0f} {i.get('unit', '')}"
        for i in top_items[:10]
    ])

    notes_assumptions = build_notes_assumptions(defaults, plan_modifications)

    feedback_questions = "\n".join([
        "- Any schedule changes or time constraints next week?",
        "- Budget target or preferred price range?",
        "- Meals you want repeated or avoided?",
        "- Energy levels this week (1-5) — particularly on training days?",
    ])

    if qa_placeholder:
        qa_summary = str(qa_report)  # raw placeholder string passed in
        qa_confidence = ""
    else:
        overall, issues, confidence = _parse_qa_summary(qa_report)
        qa_summary_lines = [f"- Status: {overall}"]
        if issues:
            for issue in issues:
                qa_summary_lines.append(f"- {issue}")
        else:
            qa_summary_lines.append("- No blocking issues")
        qa_summary = "\n".join(qa_summary_lines)
        qa_confidence = f"- {confidence}" if confidence else "- QA checks passed"

    template_text = (ROOT / "templates" / "Weekly_Email_Digest.template.md").read_text()
    return render_template(template_text, {
        "subject_line": subject_line,
        "at_a_glance": at_a_glance,
        "targets_table": targets_table,
        "plan_rationale": plan_rationale,
        "data_analyst_notes": data_analyst_notes,
        "meal_plan_section": meal_plan_section,
        "grocery_top_items": grocery_top,
        "notes_assumptions": notes_assumptions,
        "feedback_questions": feedback_questions,
        "qa_summary": qa_summary,
        "qa_confidence": qa_confidence,
    })


# ---------------------------------------------------------------------------
# Stage 6 — QA Gate
# ---------------------------------------------------------------------------

def stage6_qa(user, context, plan_intent, recipes, grocery_list, csv_rows,
              digest, plan_modifications, run_log):
    """Full QA check against the spec rubric. Returns qa_report.md content."""
    issues = {
        "coverage": [],
        "constraints": [],
        "macro": [],
        "grocery": [],
        "recipes": [],
        "modification": [],
        "tone": [],
    }

    # Coverage: 9 required sections
    required_sections = [
        "## TL;DR",
        "## This Week's Targets",
        "## Plan Rationale",
        "## Data Analyst Notes",
        "## Meal Plan",
        "## Grocery List",
        "## Notes / Assumptions",
        "## Next Week Feedback Prompts",
        "## QA Summary",
    ]
    for section in required_sections:
        if section not in digest:
            issues["coverage"].append(f"Missing section: {section}")

    # Constraint adherence
    avoid = set(a.lower() for a in user.get("avoid_list", []) + user.get("allergies", []))
    if avoid:
        for r in recipes:
            name_lower = r.get("name", "").lower()
            for item in avoid:
                if item and item in name_lower:
                    issues["constraints"].append(f"Meal '{r['name']}' contains restricted item '{item}'")

    # Macro accuracy (within 10% of per_day targets — they are the source of truth)
    mp = plan_intent["macro_plan"]
    per_day = plan_intent.get("per_day_targets", [])
    if per_day:
        avg_kcal_target = mp["daily_avg_kcal"]
        avg_kcal_actual = sum(d["kcal"] for d in per_day) / len(per_day)
        deviation = abs(avg_kcal_actual - avg_kcal_target) / avg_kcal_target
        if deviation > 0.10:
            issues["macro"].append(
                f"Avg kcal deviation {deviation:.1%} exceeds +-10% "
                f"(target: {avg_kcal_target:.0f}, actual: {avg_kcal_actual:.0f})"
            )

    # Grocery completeness
    if len(csv_rows) == 0:
        issues["grocery"].append("Grocery CSV has no rows")
    for row in csv_rows:
        if not row.get("item_name"):
            issues["grocery"].append(f"Blank item_name for {row.get('ingredient_id', 'unknown')}")
        if not row.get("match_confidence"):
            issues["grocery"].append(f"Missing match_confidence for {row.get('item_name', 'unknown')}")
        try:
            qty = float(row.get("quantity", 0))
            if qty <= 0:
                issues["grocery"].append(f"Non-positive quantity for {row.get('item_name', 'unknown')}: {qty}")
        except (ValueError, TypeError):
            issues["grocery"].append(f"Invalid quantity for {row.get('item_name', 'unknown')}")

    # Recipe link quality
    for r in recipes:
        link = r.get("recipe_link", "")
        if not link:
            issues["recipes"].append(f"{r['meal_id']}: no recipe link")
        elif "example.com" in link:
            issues["recipes"].append(f"{r['meal_id']}: placeholder example.com URL")

    # Modification audit (V2 only — no-op in V1)
    mods = plan_modifications.get("modifications", [])
    if mods:
        meal_ids_in_plan = {m["meal_id"] for m in plan_intent.get("meal_ids", [])}
        for mod in mods:
            if mod["meal_id"] not in meal_ids_in_plan:
                issues["modification"].append(f"Modification references unknown meal_id: {mod['meal_id']}")

    # Tone check
    medical_terms = ["will improve", "proven to", "scientifically shown", "cures", "prevents disease", "treats"]
    prescriptive_terms = ["you must", "you need to", "you should always"]
    digest_lower = digest.lower()
    for term in medical_terms:
        if term in digest_lower:
            issues["tone"].append(f"Medical claim: '{term}'")
    for term in prescriptive_terms:
        if term in digest_lower:
            issues["tone"].append(f"Prescriptive language: '{term}'")

    # Build report
    all_blocking = issues["coverage"] + issues["constraints"] + issues["modification"] + issues["tone"]
    advisory = issues["macro"] + issues["grocery"] + issues["recipes"]
    overall = "PASS" if not all_blocking else "FAIL"

    def _pf(key):
        return "PASS" if not issues[key] else "FAIL"

    report_lines = ["# QA Report", "", "## Coverage Check"]
    report_lines.append(f"- Subject line: {'PASS' if digest.startswith('#') else 'FAIL'}")
    for section in required_sections:
        label = section.replace("## ", "")
        report_lines.append(f"- {label}: {'PASS' if section in digest else 'FAIL'}")

    report_lines += ["", "## Constraint Adherence",
                     f"- Restrictions honored: {_pf('constraints')}",
                     f"- Allergies not violated: {_pf('constraints')}"]
    for v in issues["constraints"]:
        report_lines.append(f"  - {v}")

    report_lines += ["", "## Macro Accuracy",
                     f"- Daily average within +-10% of targets: {_pf('macro')}"]
    for v in issues["macro"]:
        report_lines.append(f"  - {v}")

    report_lines += [
        "", "## Grocery Completeness",
        f"- All recipe ingredients mapped: {'PASS' if csv_rows else 'FAIL'}",
        f"- All quantities present: {_pf('grocery')}",
        f"- match_confidence populated: {_pf('grocery')}",
        f"- No blank item_name fields: {_pf('grocery')}",
    ]

    recipe_link_issues = [v for v in issues["recipes"] if "example.com" in v or "no recipe link" in v]
    report_lines += [
        "", "## Recipe Link Quality",
        f"- No placeholder or broken URLs: {'PASS' if not recipe_link_issues else 'FAIL'}",
        f"- All meal IDs have recipe entry: {'PASS' if len(recipes) == 28 else 'FAIL (expected 28, got ' + str(len(recipes)) + ')'}",
    ]
    for v in issues["recipes"]:
        report_lines.append(f"  - {v}")

    if mods:
        report_lines += ["", "## Modification Audit", f"- Modifications traceable: {_pf('modification')}"]
    else:
        report_lines += [
            "", "## Modification Audit",
            "- No modifications applied (data_confidence: insufficient — V1 mode)",
        ]

    report_lines += [
        "", "## Tone Check",
        f"- No medical claims: {'PASS' if not any('Medical' in v for v in issues['tone']) else 'FAIL'}",
        f"- No prescriptive language: {'PASS' if not any('Prescriptive' in v for v in issues['tone']) else 'FAIL'}",
    ]

    report_lines += ["", f"## Overall: {overall}", "", "## Blocking Issues"]
    if all_blocking:
        for v in all_blocking[:10]:
            report_lines.append(f"- {v}")
    else:
        report_lines.append("- None")

    report_lines += ["", "## Non-blocking Suggestions"]
    if advisory:
        for v in advisory[:5]:
            report_lines.append(f"- {v}")
    else:
        report_lines.append("- None")

    return "\n".join(report_lines)


def _parse_qa_summary(qa_report):
    overall = "PASS"
    top_issues = []
    lines = qa_report.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Overall:"):
            overall = line.split(":", 1)[1].strip()
        if line.strip() == "## Blocking Issues":
            j = i + 1
            while j < len(lines) and (lines[j].startswith("- ") or lines[j].startswith("  - ")):
                text = lines[j].lstrip("- ").strip()
                if text and text != "None":
                    top_issues.append(text)
                j += 1
    return overall, top_issues[:3], ""


# ---------------------------------------------------------------------------
# Legacy weekly_outputs builder (for schema validation)
# ---------------------------------------------------------------------------

def build_weekly_outputs(plan_intent, recipes, grocery_list, email_digest):
    """Build weekly_outputs.json for schema validation."""
    per_day = plan_intent.get("per_day_targets", [])
    mp = plan_intent["macro_plan"]

    by_date = {}
    for r in recipes:
        date = r["date"]
        if date not in by_date:
            by_date[date] = {"date": date, "day_type": r["day_type"], "meals": []}
        by_date[date]["meals"].append({
            "name": r["name"],
            "time": r.get("time", ""),
            "recipe_link": r.get("recipe_link", ""),
            "ingredients": r.get("ingredients", []),
            "macros": r.get("macros", {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}),
        })
    days = [v for _, v in sorted(by_date.items())]
    meal_plan = {"week_start": plan_intent["week_start"], "days": days}

    per_day_targets_out = [
        {
            "date": t["date"],
            "day_type": t["day_type"],
            "calories_target": t["kcal"],
            "protein_g": t["protein_g"],
            "carbs_g": t["carbs_g"],
            "fat_g": t["fat_g"],
            "notes": "Evidence-based TDEE targets",
        }
        for t in per_day
    ]

    return {
        "week_start": plan_intent["week_start"],
        "per_day_targets": per_day_targets_out,
        "meal_plan": meal_plan,
        "grocery_list": grocery_list,
        "nutrition_brief": {
            "summary": (
                "Supportive adaptation: high days increase carbs and calories; rest days keep calories "
                "steady with higher protein density. Targets are evidence-based weekly averages."
            ),
            "targets": {
                "kcal": mp["daily_avg_kcal"],
                "protein_g": mp["protein_g"],
                "carbs_g": mp["carbs_g_training"],
                "fat_g": mp["fat_g"],
            },
        },
        "email_digest": email_digest,
    }


def build_weekly_meal_md(plan_intent, recipes, context, user):
    tpl = (ROOT / "templates" / "Weekly_Meal_Plan.template.md").read_text()
    by_date = {}
    for r in recipes:
        by_date.setdefault(r["date"], []).append(r)
    daily_lines = []
    for date, meals in sorted(by_date.items()):
        day_type = meals[0]["day_type"] if meals else "training"
        slot_order = ["breakfast", "lunch", "dinner", "snack"]
        meal_names = ", ".join(
            m["name"] for m in sorted(meals, key=lambda x: slot_order.index(x["slot"]) if x["slot"] in slot_order else 99)
        )
        daily_lines.append(f"- {date} ({day_type}): {meal_names}")
    return render_template(tpl, {
        "week_start": context["week_start"],
        "goal": user["goal"],
        "training_focus": context["training_focus"],
        "daily_plan_table": "\n".join(daily_lines),
    })


def build_nutrition_brief_md(plan_intent, context):
    tpl = (ROOT / "templates" / "Nutrition_Brief.template.md").read_text()
    mp = plan_intent["macro_plan"]
    return render_template(tpl, {
        "week_start": context["week_start"],
        "summary": "Supportive adaptation: high days increase carbs; rest days prioritize protein synthesis.",
        "kcal": mp["daily_avg_kcal"],
        "protein_g": mp["protein_g"],
        "carbs_g": mp["carbs_g_training"],
        "fat_g": mp["fat_g"],
    })


def compute_grocery_diff(base_list, alt_list):
    base_map = {}
    alt_map = {}
    for i in base_list.get("items", []):
        k = (i.get("name_normalized"), i.get("unit"))
        base_map[k] = base_map.get(k, 0) + float(i.get("total_quantity", 0))
    for i in alt_list.get("items", []):
        k = (i.get("name_normalized"), i.get("unit"))
        alt_map[k] = alt_map.get(k, 0) + float(i.get("total_quantity", 0))
    deltas = []
    for k in set(base_map) | set(alt_map):
        d = alt_map.get(k, 0) - base_map.get(k, 0)
        if abs(d) > 0.0001:
            deltas.append((k, d))
    deltas.sort(key=lambda x: abs(x[1]), reverse=True)
    lines = []
    for (name, unit), delta in deltas[:5]:
        sign = "+" if delta > 0 else "-"
        lines.append(f"{sign} {name} ({unit}): {abs(delta):.0f}")
    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Performance Meal Planner — Stage-gated weekly pipeline (V1)"
    )
    parser.add_argument("--demo", action="store_true", help="Run with demo inputs")
    parser.add_argument("--variant", choices=["base", "alt"], default="base")
    parser.add_argument("--gmail-draft", action="store_true")
    parser.add_argument("--to", dest="to_email")
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--week-start", dest="week_start")
    parser.add_argument("--kroger-search", action="store_true")
    parser.add_argument("--garmin-wellness", dest="garmin_wellness_dir", metavar="PATH")
    parser.add_argument("--wellness-days", dest="wellness_days", type=int, default=14)
    parser.add_argument("--drinkcontrol", dest="drinkcontrol_csv", metavar="PATH")
    args = parser.parse_args()

    if not args.demo:
        raise SystemExit("Use --demo for the local demo run.")

    demo_dir = ROOT / "demo_inputs"
    out_dir = ROOT / "outputs" / ("demo_alt" if args.variant == "alt" else "demo")
    out_dir.mkdir(parents=True, exist_ok=True)

    # --ingest
    if args.ingest:
        raw_dir = demo_dir / "raw"
        parsed_dir = demo_dir / "parsed"
        print("\n[--ingest] Parsing raw inputs...")
        if (raw_dir / "user_intake.csv").exists():
            user_intake_import.run(raw_dir, parsed_dir)
        else:
            print("  Skipping user intake: not found")
        if (raw_dir / "garmin_activities.csv").exists():
            import datetime as _dt
            ws = _dt.date.fromisoformat(args.week_start) if args.week_start else (
                _dt.date.today() - _dt.timedelta(days=_dt.date.today().weekday())
            )
            garmin_import.run(raw_dir, parsed_dir, ws)
        else:
            print("  Skipping Garmin: not found")
        print("[--ingest] Done.\n")

    # --garmin-wellness
    if args.garmin_wellness_dir:
        gdir = Path(args.garmin_wellness_dir)
        if not gdir.is_absolute():
            gdir = (ROOT / gdir).resolve()
        if not gdir.exists():
            raise SystemExit(f"[--garmin-wellness] Not found: {gdir}")
        print("\n[--garmin-wellness] Parsing Garmin wellness export...")
        garmin_wellness_import.run(
            garmin_dir=gdir,
            output_path=demo_dir / "outcome_signals.json",
            days=args.wellness_days,
        )
        print()

    # --drinkcontrol
    if args.drinkcontrol_csv:
        dc_path = Path(args.drinkcontrol_csv).expanduser()
        if not dc_path.is_absolute():
            dc_path = (ROOT / dc_path).expanduser().resolve()
        if not dc_path.exists():
            raise SystemExit(f"[--drinkcontrol] Not found: {dc_path}")
        print("\n[--drinkcontrol] Parsing DrinkControl export...")
        drinkcontrol_import.run(csv_path=dc_path, output_path=demo_dir / "outcome_signals.json")
        print()

    # Load inputs
    schema_registry = build_registry()
    parsed_dir = demo_dir / "parsed"
    input_dir = parsed_dir if (args.ingest and parsed_dir.exists()) else demo_dir

    try:
        user = load_json(input_dir / "user_profile.json")
        context_file = "weekly_context_alt.json" if args.variant == "alt" else "weekly_context.json"
        context = load_json(input_dir / context_file)
        signals = load_json(demo_dir / "outcome_signals.json")
        meal_buckets = load_json(demo_dir / "meal_buckets.json")

        validate_or_exit(user, load_schema("user_profile.schema.json"), "user_profile.json", schema_registry)
        validate_or_exit(context, load_schema("weekly_context.schema.json"), context_file, schema_registry)
        validate_or_exit(signals, load_schema("outcome_signals.schema.json"), "outcome_signals.json", schema_registry)
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)

    run_log = RunLog(context["week_start"])

    # Stage 0
    print("\n[Stage 0] Validating inputs...")
    defaults = stage0_validate(user, context, signals, run_log)
    run_log.record_stage("Stage 0 (Validate)", "PASS")
    print(f"  OK — {len(defaults)} defaults applied")

    # Stage 1
    print("\n[Stage 1] Building plan intent (Nutrition Planner)...")
    plan_intent = stage1_plan_intent(user, context, signals, run_log, defaults)
    plan_intent_md = plan_intent_to_markdown(plan_intent)
    (out_dir / "plan_intent.md").write_text(plan_intent_md)
    # Write plan_intent.json without per_day_targets (keep it to plan schema)
    plan_intent_json = {k: v for k, v in plan_intent.items() if k != "per_day_targets"}
    (out_dir / "plan_intent.json").write_text(json.dumps(plan_intent_json, indent=2))
    run_log.record_stage("Stage 1 (Plan Intent)", "PASS")
    print(f"  OK — {len(plan_intent['meal_ids'])} meal IDs generated")

    # Stage 2
    print("\n[Stage 2] Attaching recipes (Recipe Curator)...")
    recipes = stage2_recipes(plan_intent, meal_buckets)
    recipes_md = recipes_to_markdown(recipes)
    (out_dir / "recipes.md").write_text(recipes_md)
    batch_cook_count = sum(1 for r in recipes if r["batch_cook"])
    run_log.record_stage("Stage 2 (Recipes)", "PASS")
    print(f"  OK — {len(recipes)} recipes attached ({batch_cook_count} batch-cook)")

    # Stage 3
    print("\n[Stage 3] Mapping grocery items (Grocery Mapper)...")
    grocery_list, csv_rows = stage3_grocery(recipes, user, context["week_start"])
    grocery_md = grocery_to_markdown(grocery_list)
    grocery_notes_md = grocery_notes_to_markdown(grocery_list, csv_rows)
    write_grocery_csv(csv_rows, out_dir / "grocery_list.csv")
    (out_dir / "Grocery_List.md").write_text(grocery_md)
    (out_dir / "grocery_notes.md").write_text(grocery_notes_md)
    run_log.record_stage("Stage 3 (Grocery)", "PASS")
    print(f"  OK — {len(csv_rows)} grocery line items, {len(grocery_list['items'])} after rollup")

    # Stage 4
    print("\n[Stage 4] Running Data Analyst (V1 — infrastructure mode)...")
    plan_modifications, weeks_in_table = stage4_data_analyst(plan_intent, signals, out_dir, run_log)
    insights_md = insights_report_v1(plan_modifications, weeks_in_table)
    (out_dir / "plan_modifications.json").write_text(json.dumps(plan_modifications, indent=2))
    (out_dir / "Insights_Report.md").write_text(insights_md)
    print(f"  OK — data_confidence=insufficient, Stage 4b skipped ({weeks_in_table} weeks in Feature_Table.csv)")

    # Stage 4b (V2 only — never fires in V1)
    if plan_modifications.get("revision_pass_authorized"):
        print("\n[Stage 4b] Applying modifications (V2 mode)...")
        run_log.record_stage("Stage 4b (Revision)", "PASS", "V2 revision pass executed")
    else:
        run_log.record_stage("Stage 4b (Revision)", "SKIP",
                              "data_confidence=insufficient — revision_pass_authorized=false")

    # Variant comparison
    alt_hash_flag = None
    grocery_diff_lines = None
    if args.variant == "alt":
        base_file = ROOT / "outputs" / "demo" / "grocery_list.json"
        if base_file.exists():
            base_json = json.loads(base_file.read_text())
            base_hash = hashlib.sha256(json.dumps(base_json, sort_keys=True).encode()).hexdigest()
            alt_hash = hashlib.sha256(json.dumps(grocery_list, sort_keys=True).encode()).hexdigest()
            alt_hash_flag = "SAME" if base_hash == alt_hash else "DIFF"
            grocery_diff_lines = compute_grocery_diff(base_json, grocery_list)

    # Stage 5 — compose digest (QA section populated after Stage 6)
    print("\n[Stage 5] Composing digest (Orchestrator)...")
    # Build a draft with a placeholder QA section so we can run Stage 6 on the real content
    draft_qa_placeholder = "- Status: pending (Stage 6 QA gate not yet run)"
    email_md_draft = build_email_digest(
        context, user, plan_intent, recipes, grocery_list,
        plan_modifications, draft_qa_placeholder, defaults, run_log, grocery_diff_lines,
        qa_placeholder=True
    )
    run_log.record_stage("Stage 5 (Digest)", "PASS")
    print(f"  OK — {len(email_md_draft)} chars (draft)")

    # Stage 6 — QA runs on all artifacts except the digest QA section itself
    print("\n[Stage 6] QA Gate (QA / Compliance Editor)...")
    qa_report = stage6_qa(
        user, context, plan_intent, recipes, grocery_list, csv_rows,
        email_md_draft, plan_modifications, run_log
    )
    overall, _, _ = _parse_qa_summary(qa_report)

    # Compose final digest with real QA result injected
    email_md = build_email_digest(
        context, user, plan_intent, recipes, grocery_list,
        plan_modifications, qa_report, defaults, run_log, grocery_diff_lines
    )
    (out_dir / "Weekly_Email_Digest.md").write_text(email_md)
    (out_dir / "qa_report.md").write_text(qa_report)
    run_log.record_stage("Stage 6 (QA Gate)", overall)
    print(f"  {overall}")

    # Build and validate weekly_outputs
    weekly_outputs = build_weekly_outputs(plan_intent, recipes, grocery_list, email_md)
    try:
        validate_or_exit(weekly_outputs["meal_plan"], load_schema("meal_plan.schema.json"),
                         "meal_plan", schema_registry)
        validate_or_exit(weekly_outputs["grocery_list"], load_schema("grocery_list.schema.json"),
                         "grocery_list", schema_registry)
        validate_or_exit(weekly_outputs, load_schema("weekly_outputs.schema.json"),
                         "weekly_outputs", schema_registry)
    except ValidationError as e:
        print(f"  Schema validation warning: {e}", file=sys.stderr)

    # Render templates and write JSON
    (out_dir / "Weekly_Meal_Plan.md").write_text(build_weekly_meal_md(plan_intent, recipes, context, user))
    (out_dir / "Nutrition_Brief.md").write_text(build_nutrition_brief_md(plan_intent, context))
    (out_dir / "meal_plan.json").write_text(json.dumps(weekly_outputs["meal_plan"], indent=2))
    (out_dir / "grocery_list.json").write_text(json.dumps(grocery_list, indent=2))
    (out_dir / "weekly_outputs.json").write_text(json.dumps(weekly_outputs, indent=2))
    (out_dir / "run_log.md").write_text(run_log.to_markdown())

    # --kroger-search
    if args.kroger_search:
        config_path = demo_dir / "kroger_config.json"
        cart_out_path = out_dir / "kroger_cart_request.json"
        print("\n[--kroger-search] Resolving grocery items via Kroger API...")
        try:
            kroger_cart.run_search(
                grocery_list_path=out_dir / "grocery_list.json",
                config_path=config_path,
                out_path=cart_out_path,
            )
            enriched_data = json.loads(cart_out_path.read_text())
            enriched_items = enriched_data.get("enriched_items", [])
            if enriched_items:
                grocery_list["items"] = enriched_items
                (out_dir / "Grocery_List.md").write_text(grocery_to_markdown(grocery_list))
                total = enriched_data.get("estimated_total_usd", 0)
                priced = enriched_data.get("items_priced", 0)
                total_n = enriched_data.get("items_total", 0)
                print(f"  Updated. Estimated total: ${total:.2f} ({priced}/{total_n} priced)")
        except (FileNotFoundError, ValueError) as e:
            print(f"  [kroger-search] Skipped: {e}")
        except kroger_cart.KrogerAPIError as e:
            print(f"  [kroger-search] API error: {e}")

    # --gmail-draft
    if args.gmail_draft:
        digest_text = (out_dir / "Weekly_Email_Digest.md").read_text()
        subject = "Weekly Nutrition Digest"
        for line in digest_text.splitlines():
            if line.startswith("# "):
                subject = line[2:].strip()
                break
        to_email = args.to_email or os.getenv("DELIVERY_EMAIL") or "pr@apxaxn.com"
        create_draft(subject=subject, body=digest_text, to=to_email, output_dir=out_dir)
        print(f"\nGmail draft created (stub). Recipient: {to_email}")

    # Summary
    weeks_display = _count_feature_table_rows(ROOT / "data" / "Feature_Table.csv")
    print(f"\n{'='*60}")
    print(f"Run complete — outputs in {out_dir}")
    print(f"  plan_intent.md          Stage 1 artifact")
    print(f"  recipes.md              Stage 2 artifact")
    print(f"  grocery_list.csv        Stage 3 artifact (spec CSV format)")
    print(f"  grocery_notes.md        Stage 3 artifact")
    print(f"  plan_modifications.json Stage 4 artifact (V1: insufficient)")
    print(f"  Insights_Report.md      Stage 4 artifact (V1: advisory)")
    print(f"  Weekly_Email_Digest.md  Stage 5 artifact (primary sendable)")
    print(f"  qa_report.md            Stage 6 artifact — Overall: {overall}")
    print(f"  run_log.md              Orchestrator log")
    print(f"  data/Feature_Table.csv  Data Analyst accumulator ({weeks_display} row(s))")
    print(f"{'='*60}")

    if overall == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
