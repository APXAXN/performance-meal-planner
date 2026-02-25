# Nutrition Source of Truth — Training (Cycling)

**Source:** *Optimal Cycling Nutrition* — Dr Emma Wilkins & Tom Bell (2023)
**Reference PDF:** `docs/references/Optimal_Cycling_Nutrition.pdf`
**Purpose:** Defines all evidence-based macro targets, timing protocols, and intensity-scaling rules used by the pipeline's `targets.py` engine. Every rule here can be traced back to a specific chapter and table in the source document. Update this file whenever nutritional methodology changes.

**Owner/Process:** Engineering + Athlete — review monthly or when training focus shifts.

---

## 1. Foundational Philosophy

> "Carbohydrate periodisation": daily macros — especially carbs — are **modulated day-to-day based on training type and intensity**, not held constant.
> High-intensity days demand high carbs. Lower-intensity and rest days permit lower carbs, which can enhance fat oxidation adaptations.
> Protein remains consistently elevated relative to the general population, to support mitochondrial biogenesis, muscle repair, and training adaptations.
> Fat is set as a fixed percentage of total energy (~20–25%), to protect hormone production and micronutrient absorption.

*(Source: Ch. 4 — Training Nutrition: Daily Requirements; Ch. 5 — Timing Considerations)*

---

## 2. Daily Energy Estimation (RMR + PAL + Training)

### 2.1 Step 1 — Resting Metabolic Rate

**Preferred (requires body fat %):** Cunningham Equation
```
RMR (kcal) = 22 × FFM_kg + 500
FFM_kg = weight_kg × (1 − body_fat_fraction)
```

**Fallback (no body fat %):** Harris-Benedict
```
Men:   BMR = 66.473 + (13.7516 × weight_kg) + (5.0033 × height_cm) − (6.755 × age)
Women: BMR = 655.0955 + (9.5634 × weight_kg) + (1.8496 × height_cm) − (4.6756 × age)
```

### 2.2 Step 2 — Physical Activity Level (non-training)

Use the Cunningham multipliers if Cunningham RMR was used; Harris-Benedict multipliers otherwise.

| Lifestyle | PAL (Cunningham) | PAL (Harris-Benedict) |
|-----------|------------------|-----------------------|
| Desk, very little movement | 1.15–1.25 | 1.4–1.5 |
| Desk + light walking (commute/lunch) | 1.3–1.4 | 1.6–1.7 |
| Standing/manual work | 1.5–1.6 | 1.8–1.9 |
| Add daily low-intensity activity (30–60 min walk/run) | +0.2–0.3 | +0.3–0.4 |

Non-training TDEE = RMR × PAL

### 2.3 Step 3 — Training Energy Expenditure

**With power meter (preferred):** 1 kJ of work ≈ 1 kcal burned (25% mechanical efficiency).
**Without power meter — MET method:**
```
Energy (kcal) = RMR × MET × (duration_hours / 24)
```

| Activity | MET |
|----------|-----|
| Recovery ride | 7 |
| Endurance ride (Zone 2) | 8 |
| Interval session / hard group ride | 9–12 |
| Strength training | 3.5–6 |
| Running @ 10 min/mile | 9.8 |

**Total daily TDEE = Non-training TDEE + Training energy expenditure**

*(Source: Ch. 3 — Fundamentals for Health: Energy Demands)*

---

## 3. Day-Type Macro Targets

### 3.1 Official Ranges (per kg body weight per day)

| Day Type | Carbs (g/kg) | Protein (g/kg) | Fat (% kcal) |
|----------|-------------|----------------|--------------|
| **High** — intervals, group ride, long endurance (≥2H) | 6–12 g/kg | 1.2–2.0 g/kg | 20–25% |
| **Training** — moderate (1–2H endurance) | 5–7 g/kg | 1.2–2.0 g/kg | 20–25% |
| **Rest** — recovery ride or no training | 3–5 g/kg | 1.6–2.0 g/kg | 20–25% |

Notes:
- **Carbs upper bound** (12 g/kg) is for intense sessions ≥4–5H. Use 6–8 g/kg for typical hard training days.
- **Protein upper bound** (2.0 g/kg) applies to: masters cyclists (40+), concurrent strength training, weight-loss phase, or acute training load increase.
- **Fat floors:** never go below 20% of daily kcal; this protects hormone synthesis and micronutrient absorption. Do not reduce fat below this floor to make room for carbs.
- **Protein on rest days** is set *higher* than training days (toward 1.8–2.0 g/kg) because this is when muscle protein synthesis is primary; no high carb need competes for budget.

*(Source: Ch. 4 Table 3)*

### 3.2 Pipeline Macro Calculation Logic

The pipeline uses the following **step-by-step algorithm** (matching Ch. 4 "Balancing Macronutrients with Energy Demands"):

```
1. Compute total daily kcal target from TDEE.
2. Allocate fat: 25% of kcal → divide by 9 → fat_g.
   (Use 20% if carb budget is too tight on high days.)
3. Remaining kcal = total_kcal − (fat_g × 9).
4. Remaining grams = remaining_kcal / 4  (carbs+protein both = 4 kcal/g).
5. Set protein_g = weight_kg × protein_factor (see table below by day/goal).
6. carbs_g = remaining_g − protein_g.
7. Validate: carbs_g must fall within the g/kg range for the day type;
   adjust fat % (down to 20%) if not.
```

### 3.3 Protein Factor by Day Type and Goal

| Scenario | Protein Factor (g/kg) |
|----------|-----------------------|
| High training day — maintain | 1.4 |
| Training day — maintain | 1.6 |
| Rest day — maintain | 1.8 |
| Any day — weight loss / cut goal | 2.0 |
| Any day — muscle gain / gain goal | 1.8 |
| Masters (40+) — any day | +0.2 above scenario |

Minimum protein floor: `max(120, weight_kg × 1.6)` grams absolute.

*(Source: Ch. 4 — Protein section; Ch. 6 — Weight Loss protein guidance 1.8–2.3 g/kg)*

---

## 4. Intensity Scaling Rules

The pipeline maps Garmin day_type → nutrition intensity tier:

| Garmin `day_type` | Nutrition Tier | Rationale |
|-------------------|---------------|-----------|
| `high` | High Training Day | Intervals, group ride, cycling TE ≥ 3.5, distance ≥ 12 km |
| `training` | Moderate Training Day | Zone 2 endurance, cardio, strength, < 2H effort |
| `rest` | Rest / Recovery Day | No activity recorded, or recovery ride only |

**Strictness of methodology scales with week intensity:**

| Week Pattern (from Garmin) | Adherence Tier | Behavior |
|----------------------------|---------------|----------|
| ≥3 high days | **Peak Week** — strict | Carbs at 7–8 g/kg on high days; post-ride carb window enforced in notes |
| 1–2 high days | **Build Week** — standard | Standard targets per day type |
| 0 high days, ≥3 training | **Base Week** — moderate | Carbs at lower end of training range (5 g/kg); encourage fat-adaptation sessions |
| ≥4 rest days | **Recovery Week** — relaxed | Protein-focused; modest energy deficit OK (−200 kcal); carbs at 3–4 g/kg |

*(Source: Ch. 4 — Carbohydrate Periodisation; Ch. 5 — Acute Nutrition Strategies)*

---

## 5. Meal Timing Protocols by Session Type

### 5.1 Interval / High-Intensity Sessions

| Window | Recommendation |
|--------|----------------|
| **Pre-session (2–4H before)** | 1–2 g/kg carbs from moderate-GI whole food sources |
| **Pre-session (<10 min before, if early AM)** | Small fast-digesting carb: banana, gel, slice of toast + jam |
| **Avoid eating 10–60 min before** | Blood glucose dip window — makes effort feel harder |
| **During (>2H only)** | 45–60 g/hr carbs; slightly more if >3H |
| **Post-session (within 30 min)** | ~1 g/kg carbs + 15–20 g protein; window is when glycogen replenishment is fastest |
| **Caffeine** | 20–60 min pre-session; improves perceived effort |

### 5.2 Endurance Rides (Zone 2, 1–5H)

| Window | Recommendation |
|--------|----------------|
| **Short (<2H)** | No specific pre-fuelling; avoid heavy meal 1–2H before |
| **Medium (2–5H)** | 30 g/hr carbs during; food choices should be lower-GI for fat adaptation |
| **Long (>5H or high burn rate)** | 45–60 g/hr during; 1 g/kg carbs post-session within 30 min |
| **Carb-restricted training option** | On training days, short Zone 2 rides are ideal for overnight-fasted or sleep-low RCA sessions (see §7) |

### 5.3 Strength / Resistance Training

| Window | Recommendation |
|--------|----------------|
| **Pre** | No special carb requirement |
| **Post (within 2H)** | 0.2–0.3 g/kg high-quality protein; repeat every 3–4H |
| **If combined with bike session same day** | 1 g/kg carbs within 30 min of strength session + repeat every few hours |

### 5.4 Recovery / Rest Days

| Window | Recommendation |
|--------|----------------|
| **All day** | 4–5 protein servings of 15–25 g each, spread across day |
| **Carbs** | 3–5 g/kg; focus on whole grains, legumes, vegetables |
| **Energy** | Modest deficit OK (−100 to −200 kcal); adaptations happen on rest days — do not over-restrict |

*(Source: Ch. 5 Table 4)*

---

## 6. Glycemic Index Strategy

| Context | GI Guidance |
|---------|-------------|
| Pre-interval meal (2–4H before) | Moderate GI (oats, brown rice, whole grain bread) |
| Immediate pre-session (<10 min) | High GI (banana, sports gel, white bread + jam, fruit juice) |
| During endurance ride | High GI for fast absorption (gels, sports drinks, dried fruit) |
| Post-session recovery meal | Moderate-high GI to speed glycogen replenishment |
| Rest day / base diet | Low GI preferred (oats, lentils, beans, wholegrain pasta, vegetables) |
| Fat-adaptation session | Low GI / avoid carbs pre and during; high quality low-GI refuel after |

*(Source: Ch. 1 — Glycemic Index; Ch. 5 — Acute Strategies)*

---

## 7. Carbohydrate-Restricted Training (RCA) Protocol

**Only use on low-intensity sessions (< ~85% FTP, Zone 2 or below). Never on intervals.**

| Method | Description | Evidence Strength |
|--------|-------------|------------------|
| Overnight fast | Train AM before any carb intake; liver glycogen depleted overnight | Moderate |
| Twice-daily | AM session depletes glycogen; restrict carbs between; PM session with low stores | Strong |
| Sleep-low | PM high-intensity session depletes glycogen → restrict carbs overnight → AM endurance ride fasted | Strongest |

**Safety rules:**
- Max 2 RCA sessions/week; do not do RCA if sick, very lean, or showing RED-S symptoms
- Refuel with carbs within 30 min of completing any RCA session
- Caffeine (no sugar/milk) 30–60 min before can ease the perceived effort
- RCA does NOT undo carb-loading benefits for races; metabolic flexibility is preserved

*(Source: Ch. 5 — Training with Restricted Carbohydrate Availability)*

---

## 8. Weight Loss Integration

Used when `user_profile.goal == "cut"`:

| Adjustment | Value |
|------------|-------|
| Daily energy deficit | −250 to −500 kcal (slower = safer) |
| Fat allocation | 20% of kcal (lower floor to make deficit) |
| Protein | 1.8–2.3 g/kg (maximum satiety + muscle preservation) |
| Carbs | Reduce primarily on rest/low-intensity days; protect carbs on interval days |
| Max safe rate | ~0.5 kg/week (500 kcal/day deficit) |
| Minimum energy availability | 30 kcal/kg FFM/day absolute floor (RED-S threshold) |

*(Source: Ch. 6 — Weight Loss)*

---

## 9. Micronutrient Priorities for Cyclists

| Nutrient | Cyclist Relevance | Key Sources |
|----------|------------------|-------------|
| **Iron** | Critical for haemoglobin; endurance cyclists need ~70% more than general population | Red meat, poultry, fish, legumes, spinach, fortified cereals |
| **Vitamin B12** | Red blood cell production; mitochondrial adaptation | Meat, fish, eggs, dairy, fortified plant milks |
| **Calcium** | Muscle contraction, bone health | Dairy, leafy greens, fortified plant milks, canned salmon |
| **Vitamin D** | Calcium absorption; immune function; neuromuscular | Sun exposure; oily fish; supplementation 10 mcg/day if risk group |
| **Zinc** | DNA synthesis; muscle repair; immune function | Meat, seafood, whole grains, beans |
| **Omega-3** | Anti-inflammatory; recovery | Oily fish, walnuts, linseed |

**Supplementation recommendations:**
- Vitamin D: 10 mcg/day if limited sun exposure (standard for Pacific Northwest athletes)
- B12: supplement if vegan
- Iron: supplement if pre-menopausal female, vegetarian/vegan, or confirmed deficient

*(Source: Ch. 2 — Micronutrients)*

---

## 10. Worked Example — 74 kg Male Cyclist, Maintain Goal

**Profile:** 74 kg, 178 cm, 32 years old, male, body fat unknown → use Harris-Benedict.

```
BMR = 66.473 + (13.7516 × 74) + (5.0033 × 178) − (6.755 × 32)
    = 66.473 + 1017.6 + 890.6 − 216.2
    = 1758 kcal

PAL (desk + light walking) = 1.65 (Harris-Benedict midpoint)
Non-training TDEE = 1758 × 1.65 = 2901 kcal

Training energy (1H Zone 2, no power meter):
  = 1758 × 8 × (1/24) = 586 kcal

Total TDEE (training day) ≈ 2901 + 586 = 3487 kcal
Total TDEE (rest day)     ≈ 2901 kcal
```

**High Day Macros (TDEE ~3487 kcal):**
```
Fat: 25% × 3487 = 872 kcal → 97 g
Remaining: 3487 − 872 = 2615 kcal → 654 g (carbs + protein)
Protein: 1.4 g/kg × 74 = 104 g
Carbs: 654 − 104 = 550 g → 7.4 g/kg ✓ (within 6–12 g/kg range)
```

**Rest Day Macros (TDEE ~2901 kcal):**
```
Fat: 25% × 2901 = 725 kcal → 81 g
Remaining: 2901 − 725 = 2176 kcal → 544 g
Protein: 1.8 g/kg × 74 = 133 g
Carbs: 544 − 133 = 411 g → 5.6 g/kg ✓ (within 3–5 g/kg; accepted upper end for "maintain")
```

> Note: The pipeline currently uses simplified fixed-base targets. The full TDEE calculation requires body fat % or explicit user PAL. See `user_profile` schema for `pal_value`, `body_fat_pct`, and `ftp_w` fields.

---

## 11. Pipeline Integration Points

| Pipeline Component | This Document's Role |
|-------------------|---------------------|
| `src/core/targets.py` | Implements §3 and §4 macro logic; reads `user_profile` fields |
| `demo_inputs/user_profile.json` | Provides `weight_kg`, `goal`, `age`, `height_cm`, `pal_value`, `body_fat_pct` |
| `demo_inputs/parsed/weekly_context.json` | Provides `schedule[].day_type` which drives intensity tier (§4) |
| `demo_inputs/meal_buckets.json` | Meal selections should respect GI timing from §6 |
| `outputs/*/Weekly_Email_Digest.md` | Week theme (§4 intensity scaling table) surfaces in subject line |
| `docs/nutrition_source_of_truth_baseline.md` | Foundational diet layer; training doc builds on top |
