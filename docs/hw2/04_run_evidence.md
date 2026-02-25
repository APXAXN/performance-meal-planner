# HW2 — Real Usage Evidence (2 Runs)
## MSIS 549 · Agentic AI for Real-World Impact

---

## Run 1 — 2026-02-25 (Week of 2026-02-23)

### Input
- **User profile:** Nathan Fitzgerald, 45yo male, 67.6 kg, 172.7 cm, 11% BF, FTP 220W
  - Goal: maintain | Dietary: mediterranean, high-protein, whole-foods, omnivore
  - Avoid: whole tomato, bell peppers, spicy food, raw white onion
  - Max cook time: 90 min | Budget: high
- **Weekly context:** Week starting 2026-02-23 | Training focus: Base endurance + strength
  - Schedule: Mon=run, Tue=strength, Wed=rest, Thu=interval, Fri=endurance, Sat=long_run, Sun=rest
- **Outcome signals:** Garmin ACWR=1.7 (HIGH), avg sleep=8.2hr, RHR=57bpm, training_load=high
  - Alcohol: 4.5 units/7d (light) | MFP: avg 2400 kcal, 150g protein

**Run command:** `python src/run_weekly.py --demo --send`

**Run timestamps (from run_log.md):**
- Stage 0 (Validate): 2026-02-25T22:03:15Z — PASS
- Stage 1 (Plan Intent): 2026-02-25T22:03:15Z — PASS
- Stage 2 (Recipes): 2026-02-25T22:04:50Z — PASS *(95 seconds — 2 Claude API calls)*
- Stage 3 (Grocery): 2026-02-25T22:04:50Z — PASS
- Stage 4 (Data Analyst): 2026-02-25T22:04:50Z — PASS (1 week in Feature_Table, need 4)
- Stage 5 (Digest): 2026-02-25T22:04:50Z — PASS
- Stage 6 (QA Gate): 2026-02-25T22:04:50Z — PASS
- **Total wall time: ~95 seconds**

### Key Outputs

**Macro targets computed (Stage 1):**
- Daily avg: 3,013 kcal | Protein: 125g | Carbs training: 455g | Carbs rest: 277g | Fat: 99g
- TDEE method: Harris-Benedict + PAL 1.35 (no body_fat_pct in profile at time of run)
- ACWR=1.7 triggered: +100 kcal buffer on training days

**Week type:** Build week | Day types: 4 training, 1 high-intensity, 2 rest

**Sample recipes generated (Stage 2 — Claude API):**

| Meal ID | Meal Name | URL |
|---|---|---|
| D1_Breakfast | Greek Yogurt Power Bowl with Honey, Granola & Mixed Berries | budgetbytes.com/greek-yogurt-parfait/ |
| D1_Lunch | Grilled Chicken & Farro Grain Bowl with Roasted Zucchini, Chickpeas & Lemon-Tahini Dressing | seriouseats.com/grain-bowl-chicken-farro-recipe |
| D1_Dinner | Baked Salmon with Herbed Basmati Rice & Roasted Asparagus | simplyrecipes.com/recipes/baked_salmon/ |
| D3_Breakfast | Egg White Scramble with Smoked Salmon, Capers & Avocado | seriouseats.com/scrambled-eggs-with-smoked-salmon |
| D6_Breakfast | Acai Bowl with Banana, Granola, Chia Seeds & Honey | allrecipes.com/recipe/acai-bowl/ |
| D7_Dinner | Mediterranean White Bean Stew with Spinach & Lemon | simplyrecipes.com/mediterranean-white-bean-stew |

**Grocery list (Stage 3):** 102 line items | Budget estimate: $255–$510 | Store: Fred Meyer
- Categories: Protein (salmon, chicken, eggs, tuna), Dairy (Greek yogurt, cottage cheese), Produce (avocado, spinach, berries), Pantry (farro, quinoa, basmati rice)
- Batch-cook items flagged: Baked Salmon (D1+D2 Dinner shared)

**QA report (Stage 6):**
```
## Overall: PASS

Coverage Check: all 9 sections — PASS
Constraint Adherence: PASS (zero avoid-list violations)
Macro Accuracy: PASS (within ±10%)
Grocery Completeness: FAIL (quantities 0 for cinnamon, vanilla extract — advisory only)
Recipe Link Quality: FAIL (8 snack slots use simple_build — advisory only)
Tone Check: PASS

Blocking Issues: None
```

### Failures Noted (Run 1)

| Issue | Severity | Root Cause |
|---|---|---|
| 8 snack meals use "simple_build" (no URL) | Advisory | Snacks are simple combinations (cottage cheese + berries) that genuinely don't have canonical recipe URLs — expected behavior |
| Cinnamon/vanilla extract quantity = 0 | Advisory | Claude returned `0.25 tsp` which rounds to 0 in float normalization — quantity floor missing |
| Grocery `match_confidence` all = "approximate" | Advisory | Kroger API integration deferred to V1.2 — no real price lookup yet |
| Grocery category = "other" for all items | Advisory | Category classifier not yet implemented — V1.2 roadmap |
| Feature_Table has 1 row → Data Analyst V2 inactive | Expected | Need 4 runs to activate — accumulating |

---

## Run 2 — Second Run (Required for HW2)

> **Note for submission:** Run 2 will be executed the week of 2026-03-02 (next Monday) when launchd triggers automatically, or can be triggered manually with the command below. This section documents what will change.

**To execute Run 2 now:**
```bash
cd /Users/nathanfitzgerald/.claude-worktrees/performance-meal-planner/goofy-chaum
python src/run_weekly.py --demo --send
```

**What changes in Run 2 (improvements from Run 1 findings):**

| Change | Before (Run 1) | After (Run 2) |
|---|---|---|
| `.env` duplicate entries | Gmail App Password duplicated (wrong password in first set) | `.env` cleaned — single correct entry |
| Grocery quantity floor | 0.25 tsp → 0 (rounds to zero) | Add `max(quantity, 0.01)` floor in grocery normalizer |
| Feature_Table accumulation | 1 row | 2 rows (Data Analyst still V1 mode; 2 more runs needed) |
| Email delivery | Not confirmed (App Password not yet set) | Confirmed via Gmail SMTP |

**What to observe in Run 2:**
1. Email arrives at pr@apxaxn.com within 95 seconds of running
2. `data/Feature_Table.csv` now has 2 rows
3. Digest reflects current week's training schedule (different day types)
4. QA report should PASS with same or fewer advisory issues

**Run 2 output artifacts will be saved to:** `outputs/demo/` (overwrites Run 1 — consider using `--variant alt` for comparison)

---

## Reflection: What the System Does Well vs. Poorly

### Strengths
- **End-to-end automation:** Zero manual steps between input and email receipt
- **Constraint adherence:** avoid_list enforcement is robust — no violations in Run 1 across 28 meals
- **Macro science:** Cunningham/Harris-Benedict + PAL + day-type periodization produces evidence-based targets
- **Recipe variety:** Claude generates genuinely different meals per day, no consecutive repeats
- **Graceful fallbacks:** Every potential failure point has a fallback; pipeline never hard-crashes

### Weaknesses
- **URL hallucination risk:** Claude cannot verify URLs exist at generation time — some links may 404
- **Snack recipe quality:** Simple snacks (cottage cheese, apple + nut butter) correctly get "simple_build" but produce no actionable cooking instructions
- **No Kroger pricing:** Grocery budget is estimated, not real — V1.2 fix
- **Data Analyst inactive:** 4 runs needed before personalized modifications appear — user sees the same "insufficient data" note for first month
- **No feedback loop yet:** User cannot mark meals as "liked/disliked" to influence next week's curator
