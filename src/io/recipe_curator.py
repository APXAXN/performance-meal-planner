"""Live Claude-powered Recipe Curator — Stage 2 replacement.

Replaces the static meal_buckets.json lookup with a real Claude API call
that generates concrete meal names + working recipe URLs tailored to the
user's profile, avoids, dietary preferences, and the week's plan intent.

Preferred recipe sources (in order):
  1. serious eats (seriouseats.com)
  2. NYT Cooking (cooking.nytimes.com)
  3. AllRecipes (allrecipes.com)
  4. BBC Good Food (bbcgoodfood.com)
  5. Simple build (no URL — ingredients + method described inline)

Usage:
    from src.io.recipe_curator import curate_recipes
    recipes = curate_recipes(plan_intent, user_profile)
"""

import json
import logging
import re
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Preferred recipe sources to cite in the prompt
PREFERRED_SOURCES = [
    "seriouseats.com",
    "cooking.nytimes.com",
    "allrecipes.com",
    "bbcgoodfood.com",
    "simplyrecipes.com",
    "budgetbytes.com",
]

SYSTEM_PROMPT = """You are a Recipe Curator for a performance nutrition meal planning system.

Your job: given a weekly meal plan structure, return exactly one concrete recipe per meal slot.

RULES:
1. Each recipe must be real and the URL must be a real, working link from a well-known recipe site.
   Preferred sources: Serious Eats, NYT Cooking, AllRecipes, BBC Good Food, Simply Recipes, Budget Bytes.
   If you cannot confirm a real URL, use "simple_build" as the url and describe the recipe inline.
2. Respect ALL items in avoid_list (hard constraint — never include these ingredients).
3. Respect dietary_preferences (e.g. mediterranean = olive oil, fish, legumes, whole grains).
4. Match meal energy to the day type: high days = more carbs, rest days = protein-forward + lower carbs.
5. Vary meals across the week — do not repeat the same recipe on consecutive days.
6. Batch-cook flag: if a dinner recipe is used on 2+ days, mark batch_cook=true.
7. Keep cooking_time_max_min in mind.
8. For each recipe, include 4-8 key ingredients with realistic quantities for 2 servings.

OUTPUT FORMAT — return a single JSON array. You may wrap it in ```json``` fences if you need to:
[
  {
    "meal_id": "D1_Breakfast",
    "date": "YYYY-MM-DD",
    "day_type": "training",
    "slot": "breakfast",
    "name": "Concrete meal name (e.g. 'Smashed Avocado & Poached Eggs on Sourdough')",
    "url": "https://www.seriouseats.com/... OR simple_build",
    "cook_time_min": 15,
    "batch_cook": false,
    "ingredients": [
      {"name": "egg", "quantity": 4, "unit": "whole"},
      {"name": "avocado", "quantity": 1, "unit": "whole"},
      {"name": "sourdough bread", "quantity": 2, "unit": "slice"},
      {"name": "olive oil", "quantity": 1, "unit": "tbsp"}
    ],
    "macros": {"kcal": 480, "protein_g": 28, "carbs_g": 38, "fat_g": 22},
    "substitution_note": ""
  }
]
"""


def _build_user_prompt(plan_intent: dict, user_profile: dict) -> str:
    avoid = user_profile.get("avoid_list", [])
    allergies = user_profile.get("allergies", [])
    prefs = user_profile.get("dietary_preferences", [])
    cook_time = user_profile.get("cooking_time_max_min", 45)
    budget = user_profile.get("budget_level", "medium")

    macro_plan = plan_intent.get("macro_plan", {})
    meal_structure = plan_intent.get("meal_structure", {})
    meal_ids = plan_intent.get("meal_ids", [])

    lines = [
        f"USER PROFILE:",
        f"  Dietary preferences: {', '.join(prefs) if prefs else 'omnivore'}",
        f"  Avoid (hard constraint): {', '.join(avoid) if avoid else 'none'}",
        f"  Allergies: {', '.join(allergies) if allergies else 'none'}",
        f"  Max cooking time: {cook_time} min",
        f"  Budget level: {budget}",
        "",
        f"MACRO TARGETS:",
        f"  Daily avg: {macro_plan.get('daily_avg_kcal', 0):.0f} kcal",
        f"  Protein: {macro_plan.get('protein_g', 0):.0f}g/day",
        f"  Carbs (training/high days): {macro_plan.get('carbs_g_training', 0):.0f}g",
        f"  Carbs (rest days): {macro_plan.get('carbs_g_rest', 0):.0f}g",
        f"  Fat: {macro_plan.get('fat_g', 0):.0f}g/day",
        "",
        "MEAL STRUCTURE GUIDANCE:",
    ]

    for day_type, structure in meal_structure.items():
        lines.append(f"  {day_type.title()} days:")
        for slot in ["breakfast", "lunch", "dinner", "snack"]:
            desc = structure.get(slot, "")
            if desc:
                lines.append(f"    {slot}: {desc}")

    lines += ["", "MEAL IDs TO FILL (return one recipe per row):"]
    for m in meal_ids:
        lines.append(
            f"  {m['meal_id']} | {m['date']} | {m['slot']} | {m['day_type']}"
        )

    return "\n".join(lines)


def curate_recipes(plan_intent: dict, user_profile: dict) -> list[dict]:
    """Call Claude to generate real recipes for all 28 meal slots.

    Args:
        plan_intent:  Output of stage1_plan_intent (dict with meal_ids, macro_plan, etc.)
        user_profile: User profile dict (dietary_preferences, avoid_list, etc.)

    Returns:
        List of recipe dicts with real names + URLs, ready to drop into the pipeline.
        Falls back to a structured placeholder on any error.
    """
    # Load .env if not already loaded
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
    except ImportError:
        pass

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — falling back to placeholder recipes.")
        return _fallback_recipes(plan_intent)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    all_meal_ids = plan_intent.get("meal_ids", [])
    logger.info("Recipe Curator: calling Claude for %d meal slots (batched)...", len(all_meal_ids))

    # Batch into groups of 14 to stay well within token limits
    BATCH_SIZE = 14
    recipes_raw = []

    for batch_start in range(0, len(all_meal_ids), BATCH_SIZE):
        batch_ids = all_meal_ids[batch_start:batch_start + BATCH_SIZE]
        batch_intent = dict(plan_intent)
        batch_intent["meal_ids"] = batch_ids

        user_prompt = _build_user_prompt(batch_intent, user_profile)
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(all_meal_ids) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info("  Batch %d/%d (%d meals)...", batch_num, total_batches, len(batch_ids))

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown fences using line-based approach
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

            batch_recipes = json.loads(raw)
            recipes_raw.extend(batch_recipes)

        except json.JSONDecodeError as e:
            logger.warning("Recipe Curator batch %d: JSON parse failed (%s) — using fallback for batch.", batch_num, e)
            recipes_raw.extend(_fallback_recipes({"meal_ids": batch_ids}))
        except Exception as e:
            logger.warning("Recipe Curator batch %d: Claude call failed (%s) — using fallback.", batch_num, e)
            recipes_raw.extend(_fallback_recipes({"meal_ids": batch_ids}))

    # Normalise into pipeline format
    recipes = []
    for r in recipes_raw:
        url = r.get("url", "") or ""
        is_simple_build = not url or url == "simple_build"

        # Use full ingredient objects if provided, else derive from key_ingredients list
        raw_ingredients = r.get("ingredients", [])
        if raw_ingredients and isinstance(raw_ingredients[0], dict):
            ingredients = raw_ingredients
        else:
            # Older format: list of strings
            key_ings = r.get("key_ingredients", []) or raw_ingredients
            ingredients = [{"name": i, "quantity": 1, "unit": "serving"}
                           for i in key_ings if isinstance(i, str)]

        key_ingredients = [i["name"] for i in ingredients]

        recipes.append({
            "meal_id": r.get("meal_id", ""),
            "date": r.get("date", ""),
            "day_type": r.get("day_type", "training"),
            "slot": r.get("slot", ""),
            "name": r.get("name", ""),
            "time": "",
            "recipe_link": "" if is_simple_build else url,
            "batch_cook": r.get("batch_cook", False),
            "key_ingredients": key_ingredients,
            "ingredients": ingredients,
            "macros": r.get("macros", {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}),
            "substitution_note": r.get("substitution_note", ""),
        })

    logger.info("Recipe Curator: %d recipes generated.", len(recipes))
    return recipes


def _fallback_recipes(plan_intent: dict) -> list[dict]:
    """Return structured placeholders if Claude call fails."""
    SLOT_DEFAULTS = {
        "breakfast": ("Oats with banana and protein powder", "https://www.allrecipes.com/recipe/19142/basic-oatmeal/"),
        "lunch": ("Grilled chicken quinoa bowl", "https://www.allrecipes.com/recipe/244392/quinoa-bowl/"),
        "dinner": ("Salmon with roasted vegetables and rice", "https://www.seriouseats.com/easy-baked-salmon/"),
        "snack": ("Greek yogurt with berries", ""),
    }
    recipes = []
    for m in plan_intent.get("meal_ids", []):
        slot = m["slot"].lower()
        name, url = SLOT_DEFAULTS.get(slot, ("Balanced meal", ""))
        recipes.append({
            "meal_id": m["meal_id"],
            "date": m["date"],
            "day_type": m["day_type"],
            "slot": slot,
            "name": name,
            "time": "",
            "recipe_link": url,
            "batch_cook": False,
            "key_ingredients": [],
            "ingredients": [],
            "macros": {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
            "substitution_note": "Fallback — Claude unavailable",
        })
    return recipes
