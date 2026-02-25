# HW2 — Mini-Benchmark
## MSIS 549 · Agentic AI for Real-World Impact

---

## Benchmark Setup

**What is being evaluated:** The Recipe Curator (Stage 2) — the primary Claude API component.
This stage is the most variable and most LLM-dependent component in the pipeline. All other stages are deterministic.

**Prompts/settings frozen during benchmark:** Yes — `SYSTEM_PROMPT` in `src/io/recipe_curator.py` was locked before running test cases. `model=claude-sonnet-4-6`, `max_tokens=8192`, `temperature=default (1.0)`.

**Baseline comparison:**
1. **Single-prompt baseline:** Ask Claude for 28 recipes in one unstructured prompt (no JSON format, no batching, no constraint instructions)
2. **Manual process baseline:** Author manually selects 28 meals from saved recipe bookmarks, checks constraints by hand, writes grocery list

---

## Scoring Rubric

| Dimension | Weight | 0 (Fail) | 1 (Partial) | 2 (Pass) | 3 (Excellent) |
|---|---|---|---|---|---|
| **Constraint adherence** | 30% | Avoid-list violation present | Near-miss (ingredient in name but not used) | Zero violations | Zero violations + substitution note offered |
| **Recipe URL quality** | 20% | >50% URLs broken/hallucinated | 25–50% broken | <25% broken (mostly simple_build for valid reasons) | All URLs real OR appropriate simple_build fallbacks |
| **Macro alignment** | 20% | >20% off daily target | 10–20% off | Within 10% | Within 5% |
| **Variety** | 15% | Same protein source every day | 2 proteins repeated | 3+ different protein sources, no consecutive repeats | 4+ protein sources, intelligent batch-cook grouping |
| **Ingredient completeness** | 15% | <50% meals have full ingredient objects | 50–80% complete | >80% complete with {name, quantity, unit} | All 28 meals have 4–8 ingredients with units |

**Total score:** Sum of (dimension score × weight) × (3/max_weight_adjusted) → normalized to 0–10

---

## Test Cases

### Test Case 1: Standard Week (Nominal)
**Input:** Nathan's standard profile + build-week training schedule (same as Run 1)
- 4 training days, 1 high day, 2 rest days
- Avoid: whole tomato, bell peppers, spicy food, raw white onion
- Mediterranean dietary preference

**Expected behavior:** 28 meals generated with real URLs; snacks may use simple_build; all avoids respected

**Result (System — v2 prompt):**

| Dimension | Score | Notes |
|---|---|---|
| Constraint adherence | 3/3 | Zero avoid-list violations; Claude correctly used Dijon mustard not spicy mustard, no tomato in any dish |
| Recipe URL quality | 2/3 | 8/28 = 29% simple_build (snacks — appropriate); 20/28 have real URLs |
| Macro alignment | 2/3 | Daily avg 2,773 kcal vs target 3,013 (−8% — within 10% threshold) |
| Variety | 2/3 | Salmon featured 3 days (batch-cook justified); 4 protein sources total (salmon, chicken, eggs, tuna) |
| Ingredient completeness | 3/3 | All 28 meals have 4–8 ingredients with {name, quantity, unit} objects |

**System score: (0.3×3 + 0.2×2 + 0.2×2 + 0.15×2 + 0.15×3) / 3 × 10 = 7.5/10**

**Result (Single-prompt baseline — v1 prompt):**

| Dimension | Score | Notes |
|---|---|---|
| Constraint adherence | 1/3 | "Spicy sriracha chicken" appeared in D4_Lunch — spicy food violation |
| Recipe URL quality | 0/3 | JSON truncated at token limit — 16/28 meals present, all URLs placeholder text |
| Macro alignment | 0/3 | Insufficient data — only 16 meals returned |
| Variety | 1/3 | Incomplete response makes evaluation partial |
| Ingredient completeness | 0/3 | Ingredients returned as strings ("chicken, rice, broccoli") — no quantities |

**Baseline score: (0.3×1 + 0.2×0 + 0.2×0 + 0.15×1 + 0.15×0) / 3 × 10 = 1.5/10**

---

### Test Case 2: Edge Case — Heavy Restriction Profile
**Input:** Modified profile with extensive restrictions:
- Avoid list extended: whole tomato, bell peppers, spicy food, raw white onion, shellfish, soy, pork, processed meats
- Dietary: pescatarian (fish + dairy + eggs; no poultry)
- Training: 6 days/week (5 training + 1 high + 0 rest — maximum load week)
- Budget: low

**Purpose:** Tests constraint-following under pressure (fewer protein sources, no rest days, budget constraint)

**Expected behavior:** Claude should use fish, eggs, legumes, dairy as protein sources; no chicken, turkey, or pork should appear; low-budget recipes preferred (Budget Bytes, AllRecipes over NYT Cooking)

**Result (System — v2 prompt):**

| Dimension | Score | Notes |
|---|---|---|
| Constraint adherence | 2/3 | Zero violations; however, "fish sauce" used in one recipe — borderline (fish sauce is fermented fish, not whole fish — acceptable) |
| Recipe URL quality | 2/3 | 22/28 have URLs; 6 simple_build (snacks + 2 complex pescatarian dinners where Claude couldn't confirm URL) |
| Macro alignment | 2/3 | High carb week (all training/high days) — protein targets harder to hit without poultry; avg −9% on protein |
| Variety | 3/3 | Salmon, cod, tuna, eggs, Greek yogurt, legumes — excellent rotation for pescatarian |
| Ingredient completeness | 3/3 | All 28 meals complete with ingredient objects |

**System score: (0.3×2 + 0.2×2 + 0.2×2 + 0.15×3 + 0.15×3) / 3 × 10 = 7.5/10**

---

### Test Case 3: Ambiguous Case — Conflicting Signals
**Input:** Standard profile BUT:
- `acwr = 2.1` (dangerously high — overtraining risk)
- `avg_sleep_hr = 5.5` (severely under-slept)
- `goal = cut` (calorie deficit)
- Training schedule shows 6-day week (no rest days)

**Purpose:** Tests handling of conflicting signals — cutting calories while overtraining and sleep-deprived is nutritionally contradictory. The system must handle this gracefully without medical advice.

**Expected behavior:** Stage 1 (Nutrition Planner) applies the cut goal (−300 kcal) but triggers ACWR warning; rationale should flag both risks; QA tone check should catch any prescriptive language; recipes should be achievable with the lower calorie budget

**Result (System):**

| Behavior | Observed |
|---|---|
| ACWR warning in rationale | ✓ — "Training load is VERY HIGH (ACWR: 2.1) — strong caution: this level of load carries injury risk. Added 100 kcal buffer but recommend reviewing training volume." |
| Sleep warning in rationale | ✓ — "Sleep average 5.5 hrs (below optimal) — recovery is impaired. Prioritizing anti-inflammatory foods and magnesium-rich options." |
| Prescriptive language flagged | ✓ QA PASS — rationale uses "recommend reviewing" not "you must rest" |
| Calorie target | Correct — cut applied (−300 kcal from TDEE); ACWR +100 kcal buffer partially offsets |
| Recipes at lower calorie budget | ✓ — smaller portions, no batch-cook added complexity |
| Tone check | PASS — no medical claims |

**Ambiguity handling score:** The system correctly surfaces the conflict in rationale without resolving it (not the system's role) and doesn't prescribe medical guidance. The macros reflect the cut goal mechanically.

**System score: 8.0/10** (full marks on constraint, tone, completeness; −0.5 macro because conflicting signals make target somewhat arbitrary)

---

## Benchmark Results Summary

| Test Case | System Score | Baseline Score | Delta |
|---|---|---|---|
| TC1: Standard week (nominal) | 7.5/10 | 1.5/10 | **+6.0** |
| TC2: Edge case (heavy restrictions) | 7.5/10 | N/A (baseline failed to handle) | — |
| TC3: Ambiguous (conflicting signals) | 8.0/10 | N/A | — |
| **Average** | **7.7/10** | **1.5/10** | **+6.2** |

---

## Worst Failure

**TC1 — Macro alignment:** The system produced an average of 2,773 kcal/day vs a target of 3,013 kcal (−8%). This is within the ±10% QA threshold (PASS) but represents a systematic undercount.

**Root cause analysis:**
1. Claude's macro estimates per meal are approximate (training data based, not Nutritionix-verified)
2. Snacks (8 simple_build items) had lower macro estimates than the meal structure template suggested
3. The grocery mapper captures all ingredients but doesn't back-calculate macros from quantities — it trusts Claude's estimates

**Impact:** The email digest reports slightly lower calories than the system-calculated targets. User may under-eat relative to prescription on rest days.

**Fix:** Post-process each recipe's macros using ingredient × quantity × known USDA values (requires Nutritionix integration — planned for V1.2).

---

## Baseline Comparison

**Manual process baseline:**
- Time to complete manually: ~2.5 hours (recipe selection: 60 min, grocery merge: 45 min, macro check: 30 min, email draft: 15 min)
- Time with system: ~3 min run + 10 min review = 13 minutes
- **Time saved: ~2.25 hours/week = 117 hours/year**

**Single-prompt LLM baseline:**
- Score: 1.5/10 — unusable output (truncated JSON, no quantities, constraint violation)
- The multi-stage architecture with batching and explicit format instructions produces **5× better output** than a single prompt

---

## Reproducibility

All test cases can be reproduced:

```bash
# TC1 (nominal):
python src/run_weekly.py --demo

# TC2 (heavy restrictions) — requires editing demo_inputs/user_profile.json:
# Set: avoid_list += ["shellfish", "soy", "pork", "processed meats"]
# Set: dietary_preferences = ["pescatarian"]
python src/run_weekly.py --demo

# TC3 (conflicting signals) — requires editing demo_inputs/outcome_signals.json:
# Set: garmin_summary.acwr = 2.1, avg_sleep_hr = 5.5
# And demo_inputs/user_profile.json: goal = "cut"
python src/run_weekly.py --demo
```

**Prompt settings during benchmark:** `model=claude-sonnet-4-6`, `BATCH_SIZE=14`, `max_tokens=8192`, `temperature=default`
Results may vary across runs due to LLM non-determinism; re-running TC1 10× showed ±0.5 score variance on URL quality and variety dimensions.
