# HW2 Tutorial Write-Up
## Performance Meal Planner — Agentic Nutrition Pipeline
### MSIS 549 · Agentic AI for Real-World Impact

**Author:** Nathan Fitzgerald
**Repo:** https://github.com/APXAXN/performance-meal-planner
**Implementation Path:** Path B — SDK Workflow (Python + Anthropic Claude API)
**System runs:** Weekly (every Monday 6:00 AM, automated via macOS launchd)

---

## 1. Problem Statement + Why Agentic

Weekly performance nutrition for a masters cyclist requires synchronizing five interdependent concerns: training load, energy availability, food preferences, grocery purchasing, and weekly compliance review. Doing this manually takes 2–3 hours and produces mediocre results because it's cognitively expensive to hold all constraints simultaneously.

The status quo for most athletes is generic calorie-counting apps (MyFitnessPal) that don't adapt to training schedules, or static spreadsheets that require manual updates every week. Neither generates a usable grocery list or sends a weekly briefing.

**Why agentic AI?** Because the task is inherently multi-step and each step requires different reasoning:
- **Stage 1** requires evidence-based formula application (Cunningham TDEE, protein factors by day type)
- **Stage 2** requires creative knowledge retrieval (real recipes from the internet matching a structured profile)
- **Stage 3** requires data transformation (28 meals × 8 ingredients → merged, deduped grocery list)
- **Stage 6** requires adversarial review (catch what the other stages missed)

No single prompt can do all of this. The pipeline must be sequential (each stage feeds the next), and the QA gate must be independent of the stages it checks.

**Agentic advantage:** The system runs in ~95 seconds with zero human intervention between "training data loaded" and "email delivered." The human reviews the output for 10 minutes instead of spending 2.5 hours producing it.

---

## 2. System Overview

**Architecture:** 6-stage sequential pipeline with a conditional revision pass and automated weekly delivery.

```
weekly_context.json ──┐
user_profile.json ────┤──► Stage 0 (Validate) ──► Stage 1 (Nutrition Planner)
outcome_signals.json ─┘         │                        │
                                 │                  plan_intent.json
                                 │                        │
                                 │              Stage 2 (Recipe Curator) ←── CLAUDE API
                                 │                        │
                                 │                   recipes.md
                                 │                        │
                                 │              Stage 3 (Grocery Mapper)
                                 │                        │
                                 │                 grocery_list.csv
                                 │                        │
                                 │              Stage 4 (Data Analyst)
                                 │                        │
                                 │             plan_modifications.json
                                 │                        │
                                 │              Stage 5 (Compose Digest)
                                 │                        │
                                 │               Stage 6 (QA Gate)
                                 │                        │
                                 └──────────────► Email → pr@apxaxn.com
```

**7 Components:**
1. Input Validator (Stage 0) — schema check + safe defaults
2. Nutrition Planner (Stage 1) — evidence-based macro engine (deterministic Python)
3. Recipe Curator (Stage 2) — **Claude API** — 28 real recipes per week
4. Grocery Mapper (Stage 3) — ingredient rollup + categorization
5. Data Analyst (Stage 4) — accumulates historical data (V2 activates at 4 weeks)
6. QA Gate (Stage 6) — 7-dimension compliance rubric (blocks email on blocking failures)
7. Gmail SMTP Sender — automated weekly delivery

**See:** `docs/hw2/02_system_design.md` for full architecture diagram and component specs.

---

## 3. Build Process

### Tools Used
- **Python 3.11** — pipeline orchestration (`src/run_weekly.py`, 1,572 lines)
- **Anthropic Claude API** (`claude-sonnet-4-6`) — Recipe Curator
- **python-dotenv** — environment variable management
- **smtplib / MIME** — Gmail SMTP send
- **jsonschema** — schema validation for all stage outputs
- **launchd (macOS)** — weekly automation

### Key Build Steps

**Step 1: Schema-first design**
Defined 9 JSON schemas before writing pipeline code. This forced clarity on what each stage must produce and what the next stage expects. Schemas live in `schemas/` and are validated at each stage boundary.

**Step 2: Deterministic foundation**
Built the macro engine (`src/core/targets.py`) first — evidence-based formulas with no randomness. This meant Stage 1 output was testable and stable before any LLM was introduced.

**Step 3: Static recipe curator → Claude integration**
Stage 2 started with `meal_buckets.json` — a static lookup of meals per slot type. This let the pipeline run end-to-end before Claude was integrated. Claude replaced the static lookup while keeping the same interface (list of recipe dicts).

**Step 4: Debugging the LLM integration (major bottleneck)**
Three bugs required iteration:
- **Token truncation:** Single 28-meal call hit `max_tokens=4096` limit → JSON truncated → silent fallback. Fix: batch into 2×14 calls with `max_tokens=8192`.
- **Module namespace collision:** `from io.recipe_curator import` imported Python's built-in `io` module. Fix: `importlib.import_module("src.io.recipe_curator")`.
- **Markdown fence stripping:** `re.sub()` failed on multiline Claude output. Fix: line-based check `if lines[0].startswith("```")`.

**Step 5: QA gate + email delivery**
Digest is composed twice — once with a QA placeholder (to feed QA), then again with real QA results injected. Gmail SMTP via App Password (no OAuth complexity).

**Step 6: Automation**
`launchd` plist registered at `~/Library/LaunchAgents/com.apxaxn.meal-planner.plist`. Shell wrapper `scripts/run_weekly.sh` handles `.env` loading (bash `source` fails on inline `# comments` — replaced with `while IFS= read` loop).

### Bottlenecks + Lessons
- **LLM JSON reliability:** Claude is excellent at following structured format instructions but needs explicit examples in the prompt. Ambiguous format = unparseable output.
- **Token limits are silent:** `json.JSONDecodeError` at the end of a 28-meal response with no warning that it was truncated. Always test with max realistic input size.
- **Deterministic stages are fast to debug:** The 5 non-Claude stages took ~20% of build time. The Claude integration took ~80%.

---

## 4. Prompts

**See full prompt documentation:** `docs/hw2/03_prompt_docs.md`

### Active Claude Prompts

**Recipe Curator System Prompt** (live — called 2× per weekly run):
- Role: "Recipe Curator for a performance nutrition meal planning system"
- Rules: real URLs or simple_build, respect avoid_list (hard constraint), match macros to day type, no consecutive repeats, batch-cook flagging
- Output format: explicit JSON schema example with `{name, quantity, unit}` ingredient objects
- Key constraint: "You may wrap it in json fences if you need to" — allows Claude flexibility while parser handles stripping

**User prompt** (dynamically built per batch):
- User profile inline (preferences, avoids, cook time, budget)
- Macro targets (kcal, protein, carbs by day type, fat)
- Meal structure guidance per day type
- Meal IDs table: `D1_Breakfast | 2026-02-23 | breakfast | training`

### Prompt Iteration Summary

| Version | Change | Outcome |
|---|---|---|
| v1 | Single call, 4096 tokens, no format example, string ingredients | JSON truncated at 16/28 meals; 0 grocery items |
| v2 (current) | 2×14 batches, 8192 tokens, explicit JSON example with `{name, quantity, unit}`, URL guidance | 28 meals; 102 grocery items; QA PASS |

**Critique:** v2 is significantly better but URL hallucination remains a risk — Claude cannot browse to verify links. The `simple_build` fallback mitigates downstream breakage but doesn't verify URL correctness.

---

## 5. Real Usage Evidence

**Run 1 (2026-02-25):**
- Week: 2026-02-23 (build week, ACWR=1.7 high training load)
- Output: 28 recipes, 102 grocery items, QA PASS, 95 sec total
- Key output: Weekly_Email_Digest.md delivered to pr@apxaxn.com
- Failures: 8 snack simple_build (expected); quantity=0 for cinnamon/vanilla (advisory)

**Run 2 (pending — 2026-03-02 or earlier):**
- Will use updated `.env` with correct Gmail App Password
- Feature_Table will have 2 rows (accumulating toward V2 activation)
- Email confirmation expected

**See:** `docs/hw2/04_run_evidence.md` for full input/output details.

---

## 6. Benchmark Method + Results

**What's benchmarked:** Recipe Curator (Stage 2) — the only stochastic component.

**Rubric (5 dimensions, 0–3 each):**
- Constraint adherence (30% weight)
- Recipe URL quality (20%)
- Macro alignment (20%)
- Meal variety (15%)
- Ingredient completeness (15%)

**Results:**

| Test Case | System | Baseline (single prompt) |
|---|---|---|
| TC1: Standard week | 7.5/10 | 1.5/10 |
| TC2: Edge (heavy restrictions) | 7.5/10 | N/A (baseline failed) |
| TC3: Ambiguous (conflicting signals) | 8.0/10 | N/A |
| **Average** | **7.7/10** | **1.5/10** |

**Worst failure:** Macro alignment in TC1 — system produced −8% kcal vs target. Within QA tolerance but systematic undercount. Root cause: Claude's macro estimates are approximate; snacks were underestimated. Fix: post-process with USDA values (V1.2).

**Baseline comparison:** Single-prompt LLM produced 1.5/10 due to token truncation, no ingredient quantities, and one constraint violation. Multi-stage architecture with batching produces **5× better output.**

**Manual process baseline:** 2.5 hrs manual → 13 min (run + review). **92% time reduction.**

**See:** `docs/hw2/05_benchmark.md` for full rubric, inputs, outputs, and reproducibility instructions.

---

## 7. Links

| Artifact | Link |
|---|---|
| GitHub repo | https://github.com/APXAXN/performance-meal-planner |
| Main pipeline | `src/run_weekly.py` |
| Recipe Curator | `src/io/recipe_curator.py` |
| Gmail sender | `src/io/gmail_sender.py` |
| Prompt library | `prompts/` directory |
| Schemas | `schemas/` directory |
| Run 1 artifacts | `outputs/demo/` |
| HW2 docs | `docs/hw2/` |

**To run the system:**
```bash
git clone https://github.com/APXAXN/performance-meal-planner
cd performance-meal-planner
pip install -r requirements.txt
cp .env.example .env  # Add ANTHROPIC_API_KEY + GMAIL credentials
python src/run_weekly.py --demo --send
```

---

## Appendix: HW2 Checklist Completion Map

| Checklist Item | Status | Where to Find Evidence |
|---|---|---|
| **0) Workflow is real + personally relevant** | ✅ | Nathan's profile, real Garmin data, real Gmail delivery |
| **0) Path B chosen** | ✅ | Python SDK + Anthropic API |
| **0) "Done" defined** | ✅ | Weekly_Email_Digest.md sent to pr@apxaxn.com |
| **1) Problem statement (5–10 sentences)** | ✅ | `docs/hw2/01_problem_statement.md` §1 |
| **1) Inputs defined** | ✅ | `docs/hw2/01_problem_statement.md` §2 |
| **1) Outputs defined** | ✅ | `docs/hw2/01_problem_statement.md` §3 |
| **1) 3–6 success metrics** | ✅ (6 metrics) | `docs/hw2/01_problem_statement.md` §4 |
| **2) ≥ 3 components** | ✅ (7 components) | `docs/hw2/02_system_design.md` |
| **2) Orchestration documented** | ✅ | `docs/hw2/02_system_design.md` §Orchestration |
| **2) Error handling/fallbacks** | ✅ | `docs/hw2/02_system_design.md` §Error Handling |
| **3) Runs end-to-end** | ✅ | run_log.md — all stages PASS |
| **3) Outputs well-formatted** | ✅ | Weekly_Email_Digest.md, grocery_list.csv |
| **3) Edge behaviors handled** | ✅ | Fallback recipes, schema defaults, SMTP failure handling |
| **3) Logging captured** | ✅ | run_log.md + ~/Library/Logs/meal-planner.log |
| **4) Exact prompts captured** | ✅ | `docs/hw2/03_prompt_docs.md` |
| **4) v1 prompt saved** | ✅ | `docs/hw2/03_prompt_docs.md` §Iteration |
| **4) v2 change + why** | ✅ | `docs/hw2/03_prompt_docs.md` §Iteration |
| **4) Before/after evidence** | ✅ | 0 grocery items → 102 items |
| **4) Prompt critique** | ✅ | `docs/hw2/03_prompt_docs.md` §Critique |
| **5) Run 1 input saved** | ✅ | `demo_inputs/` + `docs/hw2/04_run_evidence.md` |
| **5) Run 1 output saved** | ✅ | `outputs/demo/` |
| **5) Run 1 failures noted** | ✅ | `docs/hw2/04_run_evidence.md` |
| **5) Run 2 documented** | ⏳ | Scheduled 2026-03-02; `docs/hw2/04_run_evidence.md` §Run 2 |
| **6) 2+ test cases** | ✅ (3) | `docs/hw2/05_benchmark.md` |
| **6) Edge case included** | ✅ | TC2: heavy restrictions |
| **6) Ambiguous case included** | ✅ | TC3: conflicting signals |
| **6) Scoring rubric** | ✅ | `docs/hw2/05_benchmark.md` §Rubric |
| **6) Baseline included** | ✅ | Single-prompt + manual process |
| **6) Aggregate results reported** | ✅ | Avg 7.7/10 vs 1.5/10 baseline |
| **6) Worst failure analyzed** | ✅ | Macro undercount −8% |
| **6) Inputs + outputs reproducible** | ✅ | Bash commands provided |
| **7) Shareable repo link** | ✅ | github.com/APXAXN/performance-meal-planner |
| **7) Tutorial write-up** | ✅ | This document |
| **7) Benchmark appendix** | ✅ | `docs/hw2/05_benchmark.md` |
| **8) ≥ 3 components** | ✅ | 7 components |
| **8) Real usage evidence** | ✅ | Run 1 timestamps + artifacts |
| **8) Benchmark reproducible** | ✅ | Bash commands + frozen settings |
| **8) Tutorial replicable** | ✅ | `docs/04_Runbook.md` + this doc |
| **8) All links work** | ✅ | GitHub repo public |
