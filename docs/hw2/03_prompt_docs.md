# HW2 — Prompt Documentation + Iteration
## MSIS 549 · Agentic AI for Real-World Impact

---

## Overview

This system uses **two primary Claude API prompt calls** — the Recipe Curator (Stage 2) and future Data Analyst (Stage 4 V2). All other stages are deterministic Python using evidence-based formulas. This design intentionally limits LLM calls to where they add unique value: generating real-world recipe content and analyzing longitudinal patterns.

---

## Prompt 1: Recipe Curator (Stage 2)

**File:** `src/io/recipe_curator.py` → `SYSTEM_PROMPT` + `_build_user_prompt()`
**Model:** `claude-sonnet-4-6`
**max_tokens:** 8192
**Invocations per run:** 2 (batched — 14 meals per call)

### System Prompt (exact, current v2)

```
You are a Recipe Curator for a performance nutrition meal planning system.

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
```

### User Prompt Structure (dynamically built per batch)

```
USER PROFILE:
  Dietary preferences: mediterranean, high-protein, whole-foods, omnivore
  Avoid (hard constraint): whole tomato, bell peppers, spicy food, raw white onion
  Allergies: none
  Max cooking time: 90 min
  Budget level: high

MACRO TARGETS:
  Daily avg: 3013 kcal
  Protein: 125g/day
  Carbs (training/high days): 455g
  Carbs (rest days): 277g
  Fat: 75g/day

MEAL STRUCTURE GUIDANCE:
  Training days:
    breakfast: High-carb, protein-anchored pre-training (600-700 kcal)
    lunch: Post-training carb restore + lean protein (700-800 kcal)
    dinner: Balanced protein + complex carbs + vegetables (700-800 kcal)
    snack: Protein-forward recovery snack (300-400 kcal)
  Rest days:
    breakfast: Protein-forward, lower carbs (450-550 kcal)
    ...

MEAL IDs TO FILL (return one recipe per row):
  D1_Breakfast | 2026-02-23 | breakfast | training
  D1_Lunch | 2026-02-23 | lunch | training
  ...
```

---

## Prompt Iteration — Recipe Curator

### v1 Prompt (initial — failed)

**What it was:** Single API call for all 28 meals, `max_tokens=4096`, system prompt without explicit JSON format.

```
# v1 system prompt (abbreviated)
You are a recipe curator. Return a JSON list of recipes for these meal slots.
Include: meal_id, name, url, ingredients (list of strings), macros.
```

**What failed:**
- Response truncated at 4096 tokens → JSON malformed → parse failed silently → pipeline fell back to static placeholders
- Ingredient list was strings, not objects → grocery mapper got zero quantity/unit data → grocery list was empty
- No guidance on URL sources → Claude hallucinated plausible-sounding but non-existent URLs

**Before (v1 output excerpt):**
```json
[
  {"meal_id": "D1_Breakfast", "name": "Oatmeal with berries", "url": "https://www.seriouseats.com/oatmeal-berries-recipe-12345",
   "ingredients": ["oats", "berries", "honey"], "macros": {"kcal": 400}}
  // ... JSON truncated at token limit
```

**Grocery list result:** 0 items (fallback recipes have empty ingredient arrays)

---

### v2 Prompt (current — working)

**Changes made:**
1. **Batched 2×14 calls** with `max_tokens=8192` each → no truncation
2. **Explicit JSON format** with full example including `{name, quantity, unit}` objects
3. **URL guidance** — "simple_build" fallback instead of hallucinated URLs
4. **Avoid list enforcement** highlighted as "hard constraint"
5. **Line-based fence stripping** instead of regex (multiline Claude responses broke regex)

**After (v2 output excerpt):**
```json
[
  {
    "meal_id": "D1_Breakfast",
    "date": "2026-02-23",
    "day_type": "training",
    "slot": "breakfast",
    "name": "Greek Yogurt Power Bowl with Honey, Granola & Mixed Berries",
    "url": "https://www.budgetbytes.com/greek-yogurt-parfait/",
    "cook_time_min": 5,
    "batch_cook": false,
    "ingredients": [
      {"name": "Greek yogurt", "quantity": 300, "unit": "g"},
      {"name": "granola", "quantity": 60, "unit": "g"},
      {"name": "mixed berries", "quantity": 120, "unit": "g"},
      {"name": "honey", "quantity": 2, "unit": "tbsp"}
    ],
    "macros": {"kcal": 520, "protein_g": 38, "carbs_g": 72, "fat_g": 8}
  }
]
```

**Grocery list result:** 102 items with quantities (versus 0 in v1)

---

## Prompt Critique

| Dimension | Assessment |
|---|---|
| **Clarity** | Strong — JSON schema example in prompt eliminates ambiguity about format. "simple_build" fallback is explicit. |
| **Constraints** | Good — avoid_list called "hard constraint" in caps; dietary preferences listed inline. Could be stronger: no explicit test that Claude will refuse to use avoid items even in compound ingredients. |
| **Tone** | Appropriate — instructional/professional; no unnecessary padding. |
| **Eval criteria** | Partial — system prompt states rules but doesn't give Claude self-evaluation instructions ("before submitting, verify..."). QA is external (Stage 6). |
| **Weakness** | URL hallucination risk — Claude cannot actually browse URLs to verify they work; relies on training data knowledge of recipe sites. "simple_build" fallback mitigates downstream breakage but some generated URLs may 404. |
| **Token efficiency** | Batched 2×14 is conservative; could batch 21+7 without hitting limits, but current split is safe margin. |

---

## Prompt 2: Nutrition Planner (Stage 1 — deterministic)

**Note:** Stage 1 is entirely deterministic Python (`src/core/targets.py`). The `prompts/nutrition_planner.md` file is a **role specification prompt** for when this stage is invoked as a Claude tool (V2 roadmap), not an active API call in V1.

The key formulas are:
- Cunningham RMR = 500 + (22 × LBM_kg) where LBM = weight × (1 − body_fat_pct)
- Harris-Benedict RMR = 88.4 + (13.4 × weight_kg) + (4.8 × height_cm) − (5.68 × age) [male]
- TDEE = RMR × PAL
- Goal adjustment: maintain=0, gain=+200, cut=−300 kcal

---

## Prompt 3: QA Gate (Stage 6 — rule-based)

**Note:** Stage 6 is also deterministic Python, not a Claude API call. The `prompts/qa_compliance_editor.md` is a specification for a future LLM-powered QA reviewer.

The rubric is implemented as Python checks:
```python
# Example: constraint adherence check
for item in avoid_list:
    for recipe in recipes:
        if item.lower() in recipe["name"].lower():
            violations.append(f"Avoid item '{item}' found in {recipe['meal_id']}")
```

This design choice (rule-based QA over LLM QA) is intentional: deterministic checks are reproducible and cannot be "argued out of" by creative LLM output.
