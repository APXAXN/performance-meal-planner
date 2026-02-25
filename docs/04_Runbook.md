# Runbook

**Purpose:** Step-by-step guide to running the Performance Meal Planner pipeline locally.

**Owner/Process:** Engineering — update when CLI flags or workflow changes.

---

## Pre-Run Checklist

Before running the agent pipeline each week:

1. Drop Nutritionix CSV export into `inputs/exports/` (export from app: Profile → Logs → Export)
2. Drop Garmin CSV export into `inputs/exports/` (connect.garmin.com → Activities → Export)
   OR: ensure `GARMIN_EMAIL` + `GARMIN_PASSWORD` are set in `.env` for automated fetch via garth
3. Optional: confirm `STRAVA_REFRESH_TOKEN` is valid (auto-refreshes if set)
4. Run: `python scripts/ingest.py --week YYYY-MM-DD --exports inputs/exports/`
5. Confirm ingestion summary shows all sources ✓ or note which are absent
6. Run: `python src/run_weekly.py --demo`

---

## Prerequisites

- Python 3.11+
- Install dependencies:

```bash
pip install -r requirements.txt
```

Dependencies: `jsonschema>=4.18`, `referencing>=0.28`, `requests`, `python-dotenv`, `garth` (optional — Garmin automation), `pytest`

Copy `.env.example` to `.env` and fill in your credentials before running:

```bash
cp .env.example .env
# Edit .env and add ANTHROPIC_API_KEY at minimum
```

---

## Directory Layout (Quick Reference)

```
performance-meal-planner/
├── demo_inputs/
│   ├── user_profile.json          # Demo user profile (schema-compliant)
│   ├── weekly_context.json        # Demo base week schedule
│   ├── weekly_context_alt.json    # Demo alt week schedule
│   ├── outcome_signals.json       # Prior-week wearable/nutrition signals
│   ├── meal_buckets.json          # Hardcoded demo meals by day type
│   ├── kroger_config.json         # Kroger API credentials (gitignored)
│   ├── raw/                       # Raw export files (your actual data)
│   │   ├── garmin_activities.csv  # Export from Garmin Connect
│   │   └── user_intake.csv        # User profile intake form
│   └── parsed/                    # Parser outputs (auto-generated)
│       ├── user_profile.json
│       └── weekly_context.json
├── outputs/
│   ├── demo/                      # Base demo outputs
│   └── demo_alt/                  # Alt-variant demo outputs
└── src/
    └── run_weekly.py              # Main entry point
```

---

## Run Modes

### A. Demo run (no real data required)

Uses hardcoded demo inputs in `demo_inputs/`.

```bash
python src/run_weekly.py --demo
```

**Outputs written to** `outputs/demo/`:
- `Weekly_Email_Digest.md` — primary sendable artifact
- `Weekly_Meal_Plan.md`
- `Grocery_List.md`
- `Nutrition_Brief.md`
- `meal_plan.json`, `grocery_list.json`, `weekly_outputs.json`
- `qa_report.md`

---

### B. Alt-variant demo run

Uses `weekly_context_alt.json` (higher-intensity week). Includes grocery diff vs base.

```bash
python src/run_weekly.py --demo --variant alt
```

**Run base first**, then alt — the diff requires `outputs/demo/grocery_list.json` to exist.

---

### C. Ingest raw data, then run

Parse your real Garmin export and user intake CSV before running the pipeline.

**Step 1: Export Garmin data**
1. Go to https://connect.garmin.com/modern/activities
2. Click the download icon (top-right) → Export to CSV
3. Save as `demo_inputs/raw/garmin_activities.csv`

**Step 2: Fill in user intake**
Edit `demo_inputs/raw/user_intake.csv` with your profile details (one row).

**Step 3: Parse and run**

```bash
# Parse raw data → demo_inputs/parsed/, then run pipeline with demo meal buckets
python src/run_weekly.py --demo --ingest

# Specify week start explicitly (defaults to current Monday)
python src/run_weekly.py --demo --ingest --week-start 2026-02-23
```

**Review parsed outputs** before trusting the plan:
- `demo_inputs/parsed/weekly_context.json` — check day_type assignments
- `demo_inputs/parsed/user_profile.json` — check profile fields

**Note:** `--ingest` currently populates `demo_inputs/parsed/` but the pipeline still reads from `demo_inputs/` (the demo JSON files). To use parsed files in the pipeline, copy them:

```bash
cp demo_inputs/parsed/user_profile.json demo_inputs/user_profile.json
cp demo_inputs/parsed/weekly_context.json demo_inputs/weekly_context.json
python src/run_weekly.py --demo
```

---

### D. Kroger product search

Resolve grocery items against the Kroger/Fred Meyer product catalog and get real prices.

**Setup (one-time):**
1. Register at https://developer.kroger.com
2. Create an application (set redirect URI to `http://localhost:8080/callback`)
3. Copy your `client_id` and `client_secret` into `demo_inputs/kroger_config.json`
4. Set `location_id` for your nearest Fred Meyer:
   - Find it: `GET /v1/locations?filter.chain=Fred%20Meyer&filter.zipCode=YOUR_ZIP`
   - Or use the default `70100159` (Portland, OR Fred Meyer) for testing

**Run:**

```bash
# Run demo first (generates grocery_list.json), then search Kroger
python src/run_weekly.py --demo --kroger-search
```

**Outputs added/updated:**
- `outputs/demo/kroger_cart_request.json` — enriched grocery list with product IDs, prices, match scores, and cart payload
- `outputs/demo/Grocery_List.md` — updated with price column and estimated total

**What `--kroger-search` does NOT do (V1):**
- Does not push to your Kroger cart (that requires user OAuth, coming in V1.5)
- Does not place an order

---

### E. Gmail draft

Write the Weekly Email Digest to a Gmail draft (does not send — creates a draft payload file).

```bash
python src/run_weekly.py --demo --gmail-draft --to you@example.com
```

Or set the recipient via environment variable:

```bash
export DELIVERY_EMAIL=you@example.com
python src/run_weekly.py --demo --gmail-draft
```

**Output:** `outputs/demo/draft_request.json` — Gmail API-ready payload.

Note: V1 writes the payload file. Actual Gmail API integration (OAuth + draft creation) is a Day 3 deliverable.

---

### F. Combined run (all flags)

```bash
python src/run_weekly.py --demo --ingest --week-start 2026-02-23 --kroger-search --gmail-draft --to you@example.com
```

---

## Expected QA Output

Both demo variants should produce `qa_report.md` with:

```
**Overall**: PASS

**Checklist**
- Completeness: PASS
- Constraints: PASS
- Grocery sanity: PASS
- Macro plausibility: PASS
- Per-day targets: PASS
```

If QA fails, check:
1. Schema validation errors printed to stderr
2. Macro math: per_day_targets calories should be within 15% of macro-derived kcal
3. Grocery items: all must have source_days referencing valid meal plan dates
4. Completeness: exactly 7 days, all meals have recipe_link

---

## Deprecation Warnings

None expected. The pipeline uses `referencing.Registry` (not the deprecated `jsonschema.RefResolver`).

Verify with:

```bash
python -W error src/run_weekly.py --demo
```

Should exit 0 with no warnings.

---

## Adding a New Week

1. Update `demo_inputs/outcome_signals.json` with last week's actuals
2. Export Garmin CSV for the new week → `demo_inputs/raw/garmin_activities.csv`
3. Run with `--ingest --week-start YYYY-MM-DD`
4. Review parsed `weekly_context.json`; adjust day_types if needed
5. Copy parsed files to `demo_inputs/` and run `--demo`
6. Run `--kroger-search` to refresh prices
7. Open `outputs/demo/Weekly_Email_Digest.md` and send

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: referencing` | Dependencies not installed | `pip install -r requirements.txt` |
| `Validation failed for ...` | Input JSON doesn't match schema | Check field types and required fields in docs/02_Data_Contracts.md |
| `Garmin CSV not found` | Missing export file | Export from Garmin Connect → save to demo_inputs/raw/ |
| `Kroger credentials not configured` | kroger_config.json has placeholder values | Register at developer.kroger.com, fill in client_id and client_secret |
| `KrogerAPIError: Unauthorized` | Wrong credentials or expired token | Re-check client_id/secret; token auto-refreshes on next run |
| `QA FAIL: Grocery: too few items` | Meal plan has fewer than 15 ingredients | Check meal_buckets.json — ensure each day has 4+ meals with ingredients |
| `QA FAIL: Macros: protein target low` | User weight is low or targets are off | Check weight_kg in user_profile.json; protein floor = weight * 1.6 g/kg |
