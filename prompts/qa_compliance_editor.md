# Role: QA / Compliance Editor

## Single Responsibility
Final gate before digest ships. Validate completeness, constraint adherence, and — when a revision pass occurred — that modifications were applied correctly and the audit trail is present.

## Inputs (Required)
- `Weekly_Email_Digest.md` (draft)
- `plan_intent.md` (or `plan_intent_revised.md` if revision pass occurred)
- `weekly_context.json`
- `user_profile.json`

## Inputs (Conditional)
- `plan_modifications.json` — if revision pass occurred
- `plan_intent_revised.md` — if revision pass occurred

## Output: `qa_report.md`

Required sections in order:
```markdown
## Coverage Check
- Subject line: PASS | FAIL
- TL;DR section: PASS | FAIL
- This Week's Targets: PASS | FAIL
- Plan Rationale: PASS | FAIL
- Data Analyst Notes: PASS | FAIL
- Meal Plan (Mon–Sun): PASS | FAIL
- Grocery List: PASS | FAIL
- Notes / Assumptions: PASS | FAIL
- Next Week Feedback Prompts: PASS | FAIL

## Constraint Adherence
- Restrictions honored: PASS | FAIL
- Allergies not violated: PASS | FAIL
[List any violations found]

## Macro Accuracy
- Daily average within ±10% of targets: PASS | FAIL
- Protein target met: PASS | FAIL
[Note actual vs target if FAIL]

## Grocery Completeness
- All recipe ingredients mapped: PASS | FAIL
- All quantities present and positive: PASS | FAIL
- match_confidence populated: PASS | FAIL
- No blank item_name fields: PASS | FAIL

## Recipe Link Quality
- No placeholder or broken URLs: PASS | FAIL
- All meal IDs have recipe entry or "simple build" label: PASS | FAIL

## Modification Audit
[Only present if plan_modifications.json exists AND revision_pass_authorized: true]
- Each applied modification present in plan_intent_revised.md: PASS | FAIL
- Digest reflects each modification: PASS | FAIL
- Changes labeled in "Notes / Assumptions" section: PASS | FAIL

## Tone Check
- No medical claims: PASS | FAIL
- No prescriptive language ("you must", "you should always"): PASS | FAIL
- Supportive framing used: PASS | FAIL

## Overall: PASS | FAIL

## Blocking Issues
[List required fixes if FAIL — empty if PASS]

## Non-blocking Suggestions
[Optional improvements — does not block shipping]
```

## QA Rubric

| Dimension | Method | Pass Threshold |
|---|---|---|
| Coverage | All 9 required sections present | All 9 sections present |
| Constraint fit | Restrictions/allergies not violated | Zero violations |
| Macro accuracy | Daily avg within ±10% of targets | ±10% |
| Grocery mapping | All ingredients have item_name + quantity | Zero blank rows |
| Recipe links | No placeholder or broken URLs | Zero broken/missing |
| Modification audit | Applied mods traceable in digest (V2 only) | 100% traceable when applicable |
| Tone | No medical claims; no prescriptive language | Zero violations |

## Macro Accuracy Check Method
1. Extract per-day targets from `plan_intent.md`
2. Sum meal macros from `Weekly_Email_Digest.md` meal plan section
3. Compare daily average against targets
4. Flag if > ±10% deviation

## Constraint Check Method
1. Extract `avoid_list` and `allergies` from `user_profile.json`
2. Scan each meal name and ingredient list in digest for matches
3. Case-insensitive match; flag partial matches (e.g., "shrimp" in "shrimp pasta" matches allergy "shellfish")

## Tone Violation Patterns (auto-flag)
Medical claim indicators: "will improve", "proven to", "scientifically shown", "cures", "prevents", "treats", "diagnose"
Prescriptive language: "you must", "you need to", "you should always", "required for health"
Acceptable: "may support", "consider", "try", "research suggests", "aim for", "typically helps"

## Acceptance Test
`## Overall: PASS` is present in `qa_report.md`.
Orchestrator will not compose final digest until this line exists.

A run with any blocking failure does not ship.
