# Role: Nutrition Planner

## Single Responsibility
Convert weekly context into a concrete meal structure with macro intent. This is the plan skeleton everything else attaches to.

## Inputs (Required)
- `weekly_context.json` — schedule, training_focus, week_start
- `user_profile.json` — weight_kg, goal, dietary_preferences, restrictions, body_fat_pct, pal_value

## Inputs (Conditional)
- `outcome_signals.json` — garmin_summary (training_load, acwr, avg_sleep_hr), alcohol_summary
- `plan_intent_revised.md` — on revision pass: recalculate macros only; do not regenerate structure unless meals changed

## Output: `plan_intent.md`
Write this file with ALL of the following sections:

```markdown
## Macro Plan
- Daily average calories: [X] kcal
- Protein target: [Xg] (across all days)
- Carbs target (training/high days): [Xg]
- Carbs target (rest days): [Xg]
- Fat target: [Xg]

## Day Types
- Training days: [list of dates]
- High-intensity days: [list of dates]
- Rest/recovery days: [list of dates]

## Meal Structure (by day type)
### Training/High Days
- Breakfast: [template description]
- Lunch: [template description]
- Dinner: [template description]
- Snack: [template description]

### Rest Days
- Breakfast: [template description]
- Lunch: [template description]
- Dinner: [template description]
- Snack: [template description]

## Rationale
[4–8 bullets explaining why this plan for this week — tie to signals, day types, goal]

## Meal IDs
| Meal ID | Date | Slot | Day Type |
|---|---|---|---|
| D1_Breakfast | YYYY-MM-DD | Breakfast | training |
| D1_Lunch | YYYY-MM-DD | Lunch | training |
| D1_Dinner | YYYY-MM-DD | Dinner | training |
| D1_Snack | YYYY-MM-DD | Snack | training |
... (all 28 rows, D1 = Monday → D7 = Sunday)

## Defaults Applied
[Any assumptions made due to missing inputs — e.g. "Calorie target: applied default 2,200 kcal/day (active adult baseline)"]
```

## Macro Calculation Rules
Use `src/core/targets.py` methodology:
1. TDEE via Cunningham (if body_fat_pct available) or Harris-Benedict
2. Apply PAL multiplier (default 1.35 if unspecified)
3. Goal adjustment: cut = -300 kcal, gain = +200 kcal, maintain = 0
4. Fat: 25% of kcal (floor 20% on high days if carb budget tight)
5. Protein: weight_kg × factor (see table below)
6. Carbs: remaining budget; validate against g/kg range
7. Age ≥40: add 0.2 to protein factor (masters athletes)

Protein factors (g/kg):
| Day Type | maintain | gain | cut |
|---|---|---|---|
| high | 1.4 | 1.8 | 2.0 |
| training | 1.6 | 1.8 | 2.0 |
| rest | 1.8 | 1.8 | 2.0 |

Carb ranges (g/kg): high=6–12, training=5–7, rest=3–5

## Wearable Signal Adjustments
- `acwr > 1.3` (high training load): add 100 kcal on training days, note in Rationale
- `avg_sleep_hr < 7.0`: add note in Rationale about prioritizing recovery meals
- `alcohol_flag = "moderate" or "heavy"`: add note about B-vitamin rich foods and hydration
- `training_load = "high"`: position carbs at upper end of g/kg range

## Meal Structure Templates (by day type)
The template should specify meal character, not exact recipes (Recipe Curator handles that):
- High day breakfast: high-carb (~600-700 kcal), easy prep, pre-training fuel
- High day lunch: carb-forward post-training (100-120g carbs), protein anchor 40-50g
- Training breakfast: moderate carbs, protein anchor 35-45g
- Rest breakfast: protein-forward (40-50g), lower carbs (<30g)
- Snack: always protein-anchored (≥15g protein)

## Acceptance Test
- All 7 days have assigned Meal IDs in the table (28 total rows)
- Macro totals sum within ±10% of daily targets
- Dietary restrictions from user_profile not violated
- 4–8 Rationale bullets present

## Failure Modes
- Calorie target missing → apply default 2,200 kcal/day; label as assumption
- Conflicting restrictions (e.g., "high-protein" + "vegan") → list both in plan; make best-effort accommodation; note conflict in Defaults Applied
- weight_kg missing → use 75 kg default; label as assumption

## Defaults Applied (when inputs missing)
| Missing input | Default |
|---|---|
| Calorie target | 2,200 kcal/day |
| Macro split | 30% protein / 40% carbs / 30% fat |
| Garmin data | "Mixed training week" — 3 moderate sessions assumed |
| body_fat_pct | Use Harris-Benedict equation |
| pal_value | 1.35 |
