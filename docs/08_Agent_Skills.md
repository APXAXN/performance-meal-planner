# Agent Skills (V1 → V2)

This document defines the **capabilities, inputs, output contracts, and operating rules** for the Performance Meal Planner + Grocery Concierge agent.

**Primary deliverable:** a single weekly sendable artifact: `Weekly_Email_Digest.md` (email-ready content).

---

## 1) Role

### V1 (Execution Engine)
Produce a weekly plan that combines:
- nutrition targets + preferences
- wearable-derived context (training load / recovery / sleep)
- meals + recipes
- a mapped grocery list with **real items + prices** (store-dependent)
- a supportive nutrition brief and rationale

### V2 (Adaptive Coach)
Learn from user outcomes and performance signals to:
- suggest experiments (food timing, macro shifts, recovery-focused meals)
- track which meals correlate with better recovery/performance
- personalize recommendations over time

---

## 2) Skills Modules

### Skill A — Intake & Normalization
**Goal:** turn raw inputs into a consistent “Weekly Context” object.

Inputs may include:
- Nutrition targets (calories/macros) and preferences/restrictions
- Garmin metrics (sleep, recovery, training load, workouts)
- Store preference (Fred Meyer / Safeway) and budget constraints

Behaviors:
- Normalize units (grams, calories), timezones, date ranges (week start/end)
- Detect missing inputs and proceed with best-effort defaults (see “Missing Data Rules”)

Outputs:
- `weekly_context.json` (optional internal artifact; not necessarily user-facing)

---

### Skill B — Supportive Performance Reasoning
**Goal:** convert context into *supportive* nutrition guidance (not medical claims).

Behaviors:
- Emphasize experimentation and recovery basics over certainty
- Avoid overstating causality (wearable metrics are noisy)
- Provide “why this plan” in 4–8 bullet points (high signal, low fluff)

Example reasoning topics:
- carbs around training vs rest days
- protein consistency and timing
- hydration/electrolytes suggestions
- easy-to-execute meal structure

---

### Skill C — Meal Plan Synthesis
**Goal:** generate a practical weekly plan that matches targets and constraints.

Behaviors:
- Produce meals in a repeatable template (Breakfast/Lunch/Dinner/Snacks)
- Keep complexity low (reuse ingredients, batch cook options)
- Ensure plan fits:
  - calorie target (±5–10% unless user specifies tighter)
  - protein minimum (if provided)
  - restrictions (allergies, dislikes, dietary pattern)

Outputs:
- meal plan sections inside `Weekly_Email_Digest.md`
- optional: `Meal_Plan.md` (if repo uses separate artifacts)

---

### Skill D — Recipe Linking & Selection
**Goal:** attach recipes that are realistic and aligned.

Behaviors:
- Provide 1 link per meal (or per dinner if batching)
- Prefer common, stable sources; avoid broken/low-quality links
- Offer substitutions if ingredients are niche

Output:
- recipe URLs embedded in the meal plan portion of `Weekly_Email_Digest.md`

---

### Skill E — Grocery Mapping (Store Items + Prices)
**Goal:** turn ingredients into purchasable items for the chosen store.

Behaviors:
- Map each ingredient to:
  - store item name
  - size/quantity
  - price (if available)
  - SKU/item ID (if available)
  - substitution options (2 max)

If exact SKU/price cannot be reliably fetched:
- provide “best-effort” product matches
- label them clearly as “approximate match”
- prioritize usability over perfection (user can swap brands)

Outputs:
- grocery section inside `Weekly_Email_Digest.md`
- optional: `Grocery_List.csv` for import/use

---

### Skill F — Weekly Digest Composition (Primary Artifact)
**Goal:** produce one email-ready document that contains everything needed.

Required output:
- `outputs/<YYYY-MM-DD>/Weekly_Email_Digest.md` (preferred)
  - or `outputs/demo/Weekly_Email_Digest.md` for demo mode

---

### Skill G — QA / Validation
**Goal:** catch the common failures before sending.

Checks:
- Does the digest include all required sections?
- Are targets/preferences respected?
- Are recipes linked?
- Does grocery list include quantities?
- Are assumptions labeled when data is missing?
- Tone: supportive, not prescriptive

Output:
- QA checklist section appended to the end of `Weekly_Email_Digest.md` (optional)
- or internal log line items

---

## 3) Input Contract

### Required Inputs (minimum viable)
- Weekly calorie target (or baseline goal)
- Dietary restrictions/allergies (or “none”)
- Store selection (Fred Meyer or Safeway)
- Number of servings (1 / 2 / family)

### Optional Inputs (improve quality)
- Macro targets (protein/carb/fat)
- Cooking time constraints (e.g., “<20 min weeknights”)
- Preferences (foods to include/avoid)
- Garmin summary (sleep, recovery, training load, planned workouts)
- Budget preference (value / balanced / premium)

---

## 4) Missing Data Rules (Non-blocking Defaults)

When a key input is missing, proceed with a conservative assumption and **label it** in the digest.

Defaults:
- Macros missing → set protein-forward baseline (e.g., 25–35% protein) and balanced carbs/fats
- Garmin missing → assume “mixed training week” and avoid aggressive deficits
- Store missing → default to Fred Meyer (or most common) and label assumption
- Preferences missing → choose broadly tolerable meals (simple, low-ingredient count)

---

## 5) Output Contract (Strict)

### Primary Artifact: `Weekly_Email_Digest.md`
Must include these sections in this order:

1. **Subject Line** (single line)
2. **TL;DR (5 bullets max)**
3. **This Week’s Targets**
   - calories (daily average)
   - protein target (if known; otherwise stated assumption)
4. **Plan Rationale (Supportive)**
   - 4–8 bullets tying plan to inputs
5. **Meal Plan**
   - Day-by-day or template-by-day (Mon–Sun)
   - include recipe link(s)
6. **Grocery List (Store-Mapped)**
   - grouped by category (produce, protein, pantry, dairy, frozen, etc.)
   - quantities
   - price + SKU/item ID when available
   - substitutions (optional)
7. **Notes / Assumptions**
   - anything guessed due to missing data
8. **Next Week Feedback Prompts**
   - 3 quick questions for the user (e.g., “Which 2 meals did you actually cook?”)

### Optional Artifacts
- `Grocery_List.csv` (columns: category, item_name, quantity, unit, store, price, sku, substitute_1, substitute_2)
- `weekly_context.json` (internal)

---

## 6) Tone & Safety Constraints

- Supportive, experimental language (“try,” “consider,” “if it feels good”)
- Avoid medical claims, diagnosis, or guaranteed outcomes
- Acknowledge wearable noise and confounders
- Prioritize sustainability and recovery

---

## 7) Definition of Done (Acceptance Checklist)

A weekly run is “done” when:
- `Weekly_Email_Digest.md` exists in the correct outputs path
- Includes all required sections
- Meal plan respects restrictions and target constraints
- Every meal block has a recipe link (or a clearly stated “simple build” without a link)
- Grocery list includes quantities; prices/SKUs included when available
- Assumptions are explicitly labeled
- Feedback prompts are present (to fuel learning loop)

---

## 8) V2 Hooks (Future Extensions)

When V2 is activated, add:
- “Meal Outcome Tags” (energy, sleep quality, GI comfort, performance)
- A weekly experiment suggestion (single variable change)
- A lightweight model/log that relates meals → outcomes → future recommendations
- A “known good meals” library with reusable templates
