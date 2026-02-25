# HW2 — System Design
## MSIS 549 · Agentic AI for Real-World Impact

**Implementation Path: Path B — SDK Workflow**
Built with: Python · Anthropic Claude API (`claude-sonnet-4-6`) · Gmail SMTP · launchd automation

---

## Architecture Overview

This is a **stage-gated agentic pipeline** with 6 sequential processing stages plus a conditional revision pass. Each stage has a single responsibility, defined inputs/outputs, and a schema-validated contract.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INPUTS (provided once/weekly)                    │
│  user_profile.json │ weekly_context.json │ outcome_signals.json     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Stage 0: Validate  │
                    │  (schema check +    │
                    │   apply defaults)   │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Stage 1: Nutrition  │  → plan_intent.json
                    │   Planner           │  → plan_intent.md
                    │ (macro engine +     │
                    │  28 meal IDs)       │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Stage 2: Recipe     │  → recipes.md
                    │  Curator            │
                    │ [CLAUDE API CALL]   │
                    │ (28 real recipes,   │
                    │  batched 2×14)      │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Stage 3: Grocery    │  → grocery_list.csv
                    │  Mapper             │  → grocery_list.json
                    │ (ingredient rollup  │  → grocery_notes.md
                    │  + categorization)  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Stage 4: Data       │  → plan_modifications.json
                    │  Analyst            │  → Insights_Report.md
                    │ (V1: always insuff.)│  → Feature_Table.csv (append)
                    │ (V2: ≥4 rows → AI) │
                    └──────────┬──────────┘
                               │
                   ┌───────────┴────────────┐
                   │  data_confidence=       │
                   │  "emerging"/"strong"?   │
                   │  + authorized=true?     │
                   └───┬───────────────┬────┘
                      YES              NO
                       │               │
          ┌────────────▼───┐           │
          │ Stage 4b:      │           │
          │ Revision Pass  │ (V1: SKIP)│
          │ (amend plan)   │           │
          └────────────┬───┘           │
                       └───────────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Stage 5: Compose    │  → Weekly_Email_Digest.md
                    │  Digest             │    (draft, QA placeholder)
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Stage 6: QA Gate    │  → qa_report.md
                    │ (7-dimension check) │
                    │ [BLOCKS on FAIL]    │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Stage 5 re-run:     │  → Weekly_Email_Digest.md
                    │ Inject real QA      │    (final, sent via Gmail)
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Gmail SMTP Send     │
                    │ (--send flag)       │
                    └─────────────────────┘
```

---

## Components (≥ 3 Required — This System Has 7)

### Component 1: Input Validator (Stage 0)
- **Purpose:** Ensure all required fields are present; apply safe defaults for optional fields
- **Inputs:** user_profile.json, weekly_context.json, outcome_signals.json
- **Outputs:** Validated dicts; list of defaults applied (logged to run_log.md)
- **Failure mode:** Hard exit if required fields missing (user_id, week_start, etc.)
- **Code:** Embedded in `src/run_weekly.py` → `stage0_validate()`

### Component 2: Nutrition Planner (Stage 1)
- **Purpose:** Compute individualized macro targets and generate 28 meal slot IDs
- **Inputs:** Validated user profile + weekly context + signals
- **Outputs:** `plan_intent.json` (schema-validated), `plan_intent.md`
- **Key logic:**
  - TDEE via Cunningham formula (if body_fat_pct known) or Harris-Benedict + PAL
  - `week_intensity_tier()` classifies week as base/build/peak/recovery
  - Carb ranges: high=6–12 g/kg, training=5–7, rest=3–5 (evidence-based)
  - Protein: 1.8–2.0 g/kg + 0.2 g/kg bonus for age ≥40 (masters athlete)
  - Training load HIGH (ACWR ≥1.5) → +100 kcal buffer on training days
- **Failure mode:** Falls back to Harris-Benedict if body_fat_pct absent; applies default PAL=1.35
- **Code:** `src/core/targets.py` + Stage 1 section of `run_weekly.py`

### Component 3: Recipe Curator — Claude API (Stage 2)
- **Purpose:** Attach one real, concrete recipe per meal slot using live Claude API
- **Inputs:** plan_intent (meal_ids, macro_plan, meal_structure) + user_profile
- **Outputs:** `recipes.md` — 28 meals with real URLs, macros, full ingredient objects
- **Key logic:**
  - System prompt instructs Claude to use real URLs from preferred sources (Serious Eats, AllRecipes, etc.) or `simple_build`
  - Batched 2×14 API calls (`max_tokens=8192`) to stay within token limits
  - Line-based JSON fence stripping (regex approach failed on multiline)
  - Full ingredient objects `{name, quantity, unit}` required for grocery mapper
  - Falls back to `_fallback_recipes()` on parse failure (never silently drops meals)
- **Failure mode:** JSON parse failure → structured fallback per batch; API failure → fallback; never crashes pipeline
- **Code:** `src/io/recipe_curator.py`

### Component 4: Grocery Mapper (Stage 3)
- **Purpose:** Extract and roll up all recipe ingredients into a purchasable, categorized grocery list
- **Inputs:** recipes list (28 meals × 4–8 ingredients each)
- **Outputs:** `grocery_list.csv` (spec columns), `grocery_list.json`, `grocery_notes.md`
- **Key logic:**
  - `normalize_grocery.py` rolls up quantities by ingredient ID (e.g., all chicken across 28 meals)
  - Assigns categories (Produce, Protein, Dairy, Pantry, etc.)
  - match_confidence field (exact/approximate/fallback) — used by QA gate
- **Failure mode:** Missing ingredient fields → logged; pipeline continues with partial list
- **Code:** `src/core/normalize_grocery.py` + Stage 3 section of `run_weekly.py`

### Component 5: Data Analyst (Stage 4)
- **Purpose:** Accumulate weekly performance data and (V2) propose plan modifications
- **Inputs:** plan_intent + outcome_signals + `data/Feature_Table.csv`
- **Outputs:** `plan_modifications.json`, `Insights_Report.md`, updated `Feature_Table.csv`
- **Key logic:**
  - **V1 (current):** Always `data_confidence="insufficient"`; appends row to Feature_Table; no modifications
  - **V2 (activated at 4 rows):** Analyzes trends; proposes ≤3 evidence-grounded modifications
  - `_week_already_in_feature_table()` prevents duplicate rows (idempotent per week_start)
- **Failure mode:** Missing Feature_Table → creates fresh; corrupt row → skip append, log warning
- **Code:** Stage 4 section of `run_weekly.py`

### Component 6: QA Gate (Stage 6)
- **Purpose:** 7-dimension compliance check; blocks email send if blocking issues found
- **Inputs:** All upstream artifacts (digest draft, recipes, grocery, plan_intent)
- **Outputs:** `qa_report.md` with PASS/FAIL per dimension; PASS/FAIL overall
- **Dimensions checked:**
  1. **Coverage** — 9 required digest sections present (BLOCKING)
  2. **Constraint adherence** — no avoid_list or allergy violations (BLOCKING)
  3. **Macro accuracy** — daily avg within ±10% of targets (advisory)
  4. **Grocery completeness** — quantities, match_confidence, no blank item names (advisory)
  5. **Recipe link quality** — no broken/placeholder URLs; all 28 meals present (advisory)
  6. **Modification audit** — if V2, applied mods traceable in digest
  7. **Tone check** — no medical claims, no prescriptive language (BLOCKING)
- **Failure mode:** Blocking issue → logs error, writes FAIL report; email not sent
- **Code:** Stage 6 section of `run_weekly.py`

### Component 7: Gmail SMTP Sender
- **Purpose:** Deliver the final Weekly Email Digest to the user automatically
- **Inputs:** Subject line, body (markdown), recipient (from .env)
- **Outputs:** Email sent to `GMAIL_RECIPIENT`
- **Key logic:** smtplib SMTP port 587, STARTTLS, App Password auth; no OAuth complexity
- **Failure mode:** Auth failure → warning logged, returns False; pipeline completes, email skipped
- **Code:** `src/io/gmail_sender.py`

---

## Orchestration

```python
# src/run_weekly.py — main() orchestration
stage0_validate(user, context, signals, run_log)
plan_intent = stage1_plan_intent(user, context, signals, run_log, defaults)
recipes = curate_recipes(plan_intent, user)          # Component 3 — Claude API
grocery = stage3_grocery(recipes, user, week_start)
plan_mods = stage4_data_analyst(plan_intent, signals, out_dir, run_log)
if plan_mods["revision_pass_authorized"]:            # V2 only — currently SKIPPED
    plan_intent = stage4b_revision(plan_intent, plan_mods)
digest_draft = build_email_digest(..., qa_placeholder=True)   # Stage 5 draft
qa = stage6_qa(user, context, plan_intent, recipes, grocery, digest_draft, run_log)
digest_final = build_email_digest(..., qa_report=qa)          # Stage 5 final
if args.send:
    send_digest(subject, digest_final)               # Component 7
```

**Routing/Sequencing Rules:**
- Stages 0→1→2→3→4 always run in order
- Stage 4b fires only if `revision_pass_authorized=True` (currently never in V1)
- Stage 5 runs twice: once with placeholder QA, once with real QA injected
- Stage 6 result gates whether `--send` fires

---

## Error Handling + Fallbacks

| Scenario | Handling |
|---|---|
| Missing optional .env key | Warning logged; feature disabled gracefully |
| Claude API timeout/failure | `_fallback_recipes()` — structured placeholder meals, never crashes |
| JSON parse failure (Claude response) | Batch-level fallback; other batches unaffected |
| Schema validation failure | Hard exit with descriptive error message |
| Gmail auth failure | Warning logged; run completes without email |
| Feature_Table duplicate | `_week_already_in_feature_table()` skips append |
| Missing ingredient quantity | Logged as advisory QA issue; pipeline continues |

---

## Automation (launchd — macOS)

The pipeline runs every Monday at 6:00 AM without manual intervention:

```
~/Library/LaunchAgents/com.apxaxn.meal-planner.plist
→ Runs: scripts/run_weekly.sh
→ Logs: ~/Library/Logs/meal-planner.log
→ Schedule: Weekday=1, Hour=6, Minute=0
```

`scripts/run_weekly.sh` safely loads `.env` (stripping inline comments that break `source`) before invoking `python src/run_weekly.py --demo --send`.
