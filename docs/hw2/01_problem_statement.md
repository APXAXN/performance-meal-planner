# HW2 — Problem Statement + Success Criteria
## MSIS 549 · Agentic AI for Real-World Impact

---

## Problem Statement

Weekly performance nutrition is a high-stakes, high-effort manual process. A masters cyclist (40+) training 8–12 hours per week must synchronize calorie and macronutrient targets with a fluctuating training schedule, personal food restrictions, and real grocery purchasing — every single week.

The status quo is a combination of guesswork, static spreadsheets, and generic meal apps that ignore training context entirely. The result: under-fueling on hard training days, over-eating on rest days, and a grocery list that drifts away from what was planned. Doing this correctly by hand — consulting nutrition science, calculating individualized TDEE, matching meals to day type, building an ingredient-merged grocery list, and QA-checking for constraint violations — takes 2–3 hours per week.

An agentic system solves this because the task is inherently multi-step and context-dependent: each stage depends on the prior stage's output, the logic is rule-governed but nuanced, and the final artifact (a weekly email digest + grocery list) must be immediately usable without further editing. No single LLM prompt can do this reliably — it requires specialized roles with distinct responsibilities, validation gates, and the ability to accumulate data across runs to improve over time.

---

## Inputs (What the User Provides Each Run)

| Input | File | Format | Notes |
|---|---|---|---|
| User profile | `demo_inputs/user_profile.json` | JSON | Age, weight, height, BF%, FTP, dietary preferences, avoid list — set once via onboarding |
| Weekly context | `demo_inputs/weekly_context.json` | JSON | Week of, training schedule (7 days × activity type), training focus, timezone |
| Outcome signals | `demo_inputs/outcome_signals.json` | JSON | Garmin summary (ACWR, training load, sleep, RHR), alcohol log, MFP nutrition log |

**Run command:** `python src/run_weekly.py --demo --send`

The `--demo` flag uses the pre-populated demo inputs above. In production, signals are fetched automatically from Garmin/Strava APIs.

---

## Outputs (Final Artifacts Per Run)

| Artifact | Format | Primary Use |
|---|---|---|
| `Weekly_Email_Digest.md` | Markdown | **Primary output** — sent to user via Gmail; contains all actionable information |
| `grocery_list.csv` | CSV | Import-ready shopping list with categories, quantities, match confidence |
| `plan_intent.json` | JSON | Macro targets + 28 meal slot assignments (validated against schema) |
| `recipes.md` | Markdown | 28 meals with real recipe URLs, macros, batch-cook flags |
| `qa_report.md` | Markdown | 7-dimension QA rubric result; PASS/FAIL with blocking issues listed |
| `run_log.md` | Markdown | Stage-by-stage completions, defaults applied, fallbacks used |
| `data/Feature_Table.csv` | CSV | Accumulating weekly history (activates V2 analytics at 4 weeks) |

---

## Success Metrics

| # | Metric | Target | How Measured |
|---|---|---|---|
| 1 | **End-to-end run time** | < 3 minutes | `run_log.md` timestamps (Stage 0 → Stage 6) |
| 2 | **QA gate pass rate** | ≥ 90% of checks PASS | `qa_report.md` overall verdict; count of blocking issues |
| 3 | **Macro accuracy** | Daily avg within ±10% of evidence-based TDEE target | QA Stage 6 macro check; target computed via Cunningham or Harris-Benedict |
| 4 | **Constraint adherence** | 0 avoid-list or allergy violations across 28 meals | QA Stage 6 constraint check; cross-referenced against user_profile.json |
| 5 | **Recipe URL quality** | ≥ 80% of meals have a real (non-simple_build) URL | Count in `recipes.md` of meals with `recipe_link != ""` |
| 6 | **Time saved vs manual** | ≥ 90 min saved per week | Baseline: timed manual process (2–3 hrs); system: run time + review time (~15 min) |
