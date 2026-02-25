# Performance Meal Planner

Agentic workflow framework for weekly nutrition planning: ingest signals, set targets, draft plans, generate grocery lists, and produce a stakeholder digest.

**Quick Start**
1. Start with `docs/00_PRD.md` to define outcomes, users, and constraints.
2. Capture contracts in `docs/02_Data_Contracts.md` and implement schemas in `schemas/`.
3. Author prompts in `prompts/` and templates in `templates/`.
4. Orchestrate the weekly run in `src/run_weekly.py`.
5. Validate outputs against `schemas/weekly_outputs.schema.json`.

**Repository Layout**
- `docs/`: PRD, architecture, contracts, prompt library, runbook, test plan, risks, demo script.
- `prompts/`: Agent prompts (writer roles, QA critic, email digest).
- `schemas/`: JSON schemas for inputs, outputs, and signals.
- `templates/`: Output templates for plans, lists, and summaries.
- `src/`: Core logic and IO integrations.
- `outputs/`: Generated weekly artifacts.

**Workflow Outline**
1. Import weekly context and signals.
2. Compute targets and day types.
3. Draft meal plan and grocery list.
4. QA and normalize outputs.
5. Package weekly digest and export.

**Notes**
- See `docs/04_Runbook.md` for execution steps.
- Extend integrations under `src/io/` (Garmin, MFP, Notion, Drive, Gmail).

## Agent Skills
See `docs/08_Agent_Skills.md` for the agent’s capabilities, input/output contracts, and definition of done.
