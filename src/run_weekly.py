#!/usr/bin/env python3
import argparse
import json
import sys
import hashlib
import datetime
import os
from pathlib import Path
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.day_type import detect_day_type  # noqa: E402
from core.targets import targets_for_day  # noqa: E402
from core.normalize_grocery import rollup  # noqa: E402
from integrations.gmail_draft import create_draft  # noqa: E402
from integrations import garmin_import, user_intake_import, kroger_cart, garmin_wellness_import, drinkcontrol_import  # noqa: E402


def load_json(path: Path):
    return json.loads(path.read_text())


def render_template(template_text: str, values: dict) -> str:
    out = template_text
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def load_schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text())


def build_registry() -> Registry:
    schemas_dir = ROOT / "schemas"
    resources = []
    for path in schemas_dir.glob("*.json"):
        data = json.loads(path.read_text())
        resources.append((path.name, Resource.from_contents(data)))
    return Registry().with_resources(resources)


def validate_or_exit(instance: dict, schema: dict, label: str, registry: Registry):
    validator = Draft7Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if errors:
        first = errors[0]
        path = ".".join([str(p) for p in first.path]) or "(root)"
        msg = f"Validation failed for {label}: {path} - {first.message}"
        raise ValidationError(msg)


def normalize_item_name(name: str) -> str:
    return " ".join(name.lower().split())


def run_qa_checks(user, context, meal_plan, grocery_list, nutrition_brief, weekly_outputs, alt_grocery_hash=None, grocery_diff_lines=None) -> str:
    issues = []

    # Completeness: 7 days, meals present, recipe links present
    days = meal_plan.get("days", [])
    if len(days) != 7:
        issues.append(f"Completeness: expected 7 days, found {len(days)}.")
    for d in days:
        meals = d.get("meals", [])
        if not meals:
            issues.append(f"Completeness: no meals for {d.get('date', 'unknown date')}.")
        for m in meals:
            if not m.get("recipe_link"):
                issues.append(f"Completeness: missing recipe link for {m.get('name', 'unknown meal')}.")

    # Constraints: avoid list and allergies
    avoid = set([a.lower() for a in user.get("avoid_list", [])] + [a.lower() for a in user.get("allergies", [])])
    if avoid:
        for d in days:
            for m in d.get("meals", []):
                name = (m.get("name") or "").lower()
                for item in avoid:
                    if item and item in name:
                        issues.append(f"Constraints: meal contains restricted item '{item}': {m.get('name')}.")

    # Grocery sanity: no duplicates, categories present, simple unit normalization
    seen = set()
    duplicates = []
    for i in grocery_list.get("items", []):
        key = (i.get("name_normalized"), i.get("unit"))
        if key in seen:
            duplicates.append(i.get("name_display", "unknown"))
        seen.add(key)
        if not i.get("category"):
            issues.append(f"Grocery: missing category for {i.get('name_display', 'unknown item')}.")
        qty = i.get("total_quantity")
        if not isinstance(qty, (int, float)) or qty <= 0:
            issues.append(f"Grocery: invalid quantity for {i.get('name_display', 'unknown item')}.")
    if duplicates:
        issues.append(f"Grocery: duplicate items found: {', '.join(sorted(set(duplicates)))}.")
    if len(grocery_list.get("items", [])) < 15:
        issues.append("Grocery: too few items (expected at least 15).")

    # Grocery derivation spot-check: ingredient count should be >= grocery items count
    ingredient_count = 0
    for d in days:
        for m in d.get("meals", []):
            ingredient_count += len(m.get("ingredients", []))
    if ingredient_count < len(grocery_list.get("items", [])):
        issues.append("Grocery: item count exceeds ingredient count; check derivation.")
    valid_dates = set([d.get("date") for d in days])
    for i in grocery_list.get("items", []):
        if not i.get("source_days"):
            issues.append(f"Grocery: missing source_days for {i.get('name_display', 'unknown item')}.")
        else:
            for sd in i.get("source_days", []):
                if sd not in valid_dates:
                    issues.append(f"Grocery: invalid source_day {sd} for {i.get('name_display', 'unknown item')}.")

    # Macro plausibility
    targets = nutrition_brief.get("targets", {})
    if targets:
        kcal = targets.get("kcal")
        protein = targets.get("protein_g")
        if isinstance(kcal, (int, float)) and isinstance(protein, (int, float)):
            # Weight-relative protein floor (#9)
            weight = user.get("weight_kg")
            min_protein = round(weight * 1.6) if isinstance(weight, (int, float)) and weight > 0 else 120
            if protein < min_protein:
                issues.append(f"Macros: protein target low ({protein} g; minimum {min_protein} g for {weight} kg).")
            # Compare meal-plan kcal avg against per-day targets avg.
            # Tolerance is wide (40%) because demo meal buckets have fixed illustrative
            # macros; real accuracy requires ingredient-level macro calculation (V2).
            kcal_low = kcal * 0.60
            kcal_high = kcal * 1.40
            avg_kcal = sum([sum([m['macros']['kcal'] for m in d.get('meals', [])]) for d in days]) / max(len(days), 1)
            if avg_kcal < kcal_low or avg_kcal > kcal_high:
                issues.append(f"Macros: avg kcal {avg_kcal:.0f} outside target range {kcal_low:.0f}-{kcal_high:.0f}.")

    # Per-day targets validation
    per_day_targets = weekly_outputs.get("per_day_targets", [])
    if len(per_day_targets) != 7:
        issues.append(f"Per-day targets: expected 7 entries, found {len(per_day_targets)}.")
    day_map = {d.get("date"): d.get("day_type") for d in days}
    for t in per_day_targets:
        date = t.get("date")
        if date in day_map and t.get("day_type") != day_map[date]:
            issues.append(f"Per-day targets: day_type mismatch on {date}.")
        c = t.get("calories_target")
        p = t.get("protein_g")
        cbs = t.get("carbs_g")
        f = t.get("fat_g")
        if not all(isinstance(x, (int, float)) and x > 0 for x in [c, p, cbs, f]):
            issues.append(f"Per-day targets: non-positive macro values on {date}.")
        else:
            macro_kcal = (4 * p) + (4 * cbs) + (9 * f)
            low = macro_kcal * 0.85
            high = macro_kcal * 1.15
            if c < low or c > high:
                issues.append(f"Per-day targets: calories {c:.0f} outside 15% macro-derived range {low:.0f}-{high:.0f} on {date}.")

    if alt_grocery_hash == "SAME":
        issues.append("Grocery variance: grocery list identical across variants.")

    status = "PASS" if not issues else "FAIL"
    top_issues = issues[:5]

    report = []
    report.append("# QA Report")
    report.append("")
    report.append("**Checklist**")
    report.append(f"- Completeness: {'PASS' if not any('Completeness' in i for i in issues) else 'FAIL'}")
    report.append(f"- Constraints: {'PASS' if not any('Constraints' in i for i in issues) else 'FAIL'}")
    report.append(f"- Grocery sanity: {'PASS' if not any('Grocery' in i for i in issues) else 'FAIL'}")
    report.append(f"- Macro plausibility: {'PASS' if not any('Macros' in i for i in issues) else 'FAIL'}")
    report.append(f"- Per-day targets: {'PASS' if not any('Per-day targets' in i for i in issues) else 'FAIL'}")
    if alt_grocery_hash is not None:
        report.append(f"- Grocery variance: {'PASS' if alt_grocery_hash == 'DIFF' else 'FAIL'}")
    report.append("")
    report.append(f"**Overall**: {status}")
    report.append("")
    report.append("**Top 5 Issues**")
    if top_issues:
        for i in top_issues:
            report.append(f"- {i}")
    else:
        report.append("- None")
    report.append("")
    report.append("**Confidence Notes**")
    report.append("- QA is deterministic and schema-aware.")
    report.append("- Constraints are keyword-based; may require richer ingredient parsing in production.")
    report.append("- Macro plausibility: targets are evidence-based (TDEE via Harris-Benedict/Cunningham); meal macros are fixed demo values. V2 will calculate per-ingredient macros.")
    if alt_grocery_hash == "SAME":
        report.append("- Grocery lists did not change between variants; investigate meal bucket coverage.")
    if grocery_diff_lines:
        report.append("")
        report.append("**Grocery Diff (Base vs Alt)**")
        for line in grocery_diff_lines:
            report.append(f"- {line}")
        report.append("")
        report.append("Alt week shifts toward higher-carb day types; grocery delta reflects increased carb staples.")

    return "\n".join(report)


def build_grocery_list(meal_plan: dict) -> dict:
    raw_items = []

    for day in meal_plan.get("days", []):
        date = day.get("date")
        for meal in day.get("meals", []):
            ingredients = meal.get("ingredients") or []
            if not ingredients:
                raw_items.append({
                    "name": meal.get("name", "unknown meal"),
                    "quantity": 1,
                    "unit": "count",
                    "category": "unknown",
                    "source_days": [date],
                })
            for ing in ingredients:
                raw_items.append({
                    "name": ing["name"],
                    "quantity": ing["quantity"],
                    "unit": ing["unit"],
                    "category": ing.get("category"),
                    "source_days": [date],
                })

    rolled = rollup(raw_items)
    return {
        "week_start": meal_plan.get("week_start"),
        "items": rolled,
    }


def grocery_to_markdown(grocery_list: dict) -> str:
    groups = {}
    for item in grocery_list.get("items", []):
        groups.setdefault(item.get("category", "unknown"), []).append(item)
    has_prices = any(i.get("price_usd") for i in grocery_list.get("items", []))
    lines = [f"# Grocery List ({grocery_list.get('week_start')})", "", "**Items**"]
    for category in sorted(groups.keys()):
        lines.append(f"\n{category.title()}")
        for item in sorted(groups[category], key=lambda x: x.get("name_normalized", "")):
            qty = item.get("total_quantity")
            unit = item.get("unit")
            price = item.get("price_usd")
            store_name = item.get("store_item_name", "")
            match_type = item.get("match_type", "")
            line = f"- {item.get('name_display')} — {qty:.0f} {unit}"
            if price is not None:
                line += f" | ${price:.2f}"
                if match_type == "approximate":
                    line += " (approx match)"
            elif has_prices:
                line += " | price unavailable"
            if store_name and store_name.lower() != item.get("name_display", "").lower():
                line += f"\n  ↳ {store_name}"
            lines.append(line)
    if has_prices:
        priced = [i for i in grocery_list.get("items", []) if i.get("price_usd")]
        total = sum(i["price_usd"] for i in priced)
        lines.append(f"\n**Estimated Total (Kroger):** ${total:.2f} ({len(priced)}/{len(grocery_list.get('items',[]))} items priced)")
    lines.append("")
    return "\n".join(lines)


def compute_grocery_diff(base_list: dict, alt_list: dict) -> list:
    base_map = {}
    alt_map = {}

    for i in base_list.get("items", []):
        key = (i.get("name_normalized"), i.get("unit"))
        base_map[key] = base_map.get(key, 0) + float(i.get("total_quantity", 0))

    for i in alt_list.get("items", []):
        key = (i.get("name_normalized"), i.get("unit"))
        alt_map[key] = alt_map.get(key, 0) + float(i.get("total_quantity", 0))

    deltas = []
    for key in set(base_map.keys()) | set(alt_map.keys()):
        b = base_map.get(key, 0)
        a = alt_map.get(key, 0)
        delta = a - b
        if abs(delta) > 0.0001:
            deltas.append((key, delta))

    deltas.sort(key=lambda x: abs(x[1]), reverse=True)
    top = deltas[:5]

    lines = []
    for (name_norm, unit), delta in top:
        sign = "+" if delta > 0 else "-"
        qty = abs(delta)
        lines.append(f"{sign} {name_norm} ({unit}): {qty:.0f}")

    return lines


def summarize_day_types(days: list) -> str:
    counts = {"high": 0, "training": 0, "rest": 0}
    for d in days:
        dt = d.get("day_type")
        if dt in counts:
            counts[dt] += 1
    return f"{counts['high']} intensity, {counts['training']} endurance, {counts['rest']} rest"


def pick_week_theme(days: list, grocery_diff_lines=None) -> str:
    """Derive a meaningful weekly theme from day-type distribution (#8)."""
    counts = {"high": 0, "training": 0, "rest": 0}
    for d in days:
        dt = d.get("day_type")
        if dt in counts:
            counts[dt] += 1
    if grocery_diff_lines:
        return "Higher-carb support for load"
    if counts["high"] >= 2:
        return "Peak load week"
    if counts["rest"] >= 3:
        return "Recovery focus week"
    return "Supportive load balance"


def pick_example_meals(meal_plan: dict) -> list:
    examples = []
    day_by_type = {}
    for d in meal_plan.get("days", []):
        dt = d.get("day_type")
        if dt not in day_by_type:
            day_by_type[dt] = d

    order = ["high", "training", "rest"]
    for dt in order:
        day = day_by_type.get(dt)
        if not day:
            continue
        meals = day.get("meals", [])
        if meals:
            examples.append(f"- {dt.title()} day ({day.get('date')}): {meals[0].get('name')}")
    if not examples:
        for d in meal_plan.get("days", [])[:3]:
            meals = d.get("meals", [])
            if meals:
                examples.append(f"- {d.get('date')}: {meals[0].get('name')}")
    return examples


def top_grocery_items(grocery_list: dict, n: int = 8) -> list:
    items = sorted(grocery_list.get("items", []), key=lambda x: x.get("total_quantity", 0), reverse=True)
    lines = []
    for item in items[:n]:
        qty = item.get("total_quantity", 0)
        unit = item.get("unit") or ""
        lines.append(f"- {item.get('name_display')} — {qty:.0f} {unit}")
    return lines


def format_per_day_targets(per_day_targets: list) -> list:
    lines = []
    for t in per_day_targets:
        lines.append(
            f"- {t.get('date')} ({t.get('day_type')}): {t.get('calories_target'):.0f} kcal | P{t.get('protein_g'):.0f} C{t.get('carbs_g'):.0f} F{t.get('fat_g'):.0f}"
        )
    return lines


def parse_qa_summary(qa_report: str) -> tuple:
    overall = "PASS"
    top_issues = []
    confidence = ""
    lines = qa_report.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("**Overall**:"):
            overall = line.split(":", 1)[1].strip()
        if line.strip() == "**Top 5 Issues**":
            j = i + 1
            while j < len(lines) and lines[j].startswith("-"):
                if lines[j].strip() != "- None":
                    # Use prefix-safe strip (#10): remove leading "- " only
                    text = lines[j][2:] if lines[j].startswith("- ") else lines[j].lstrip("- ")
                    top_issues.append(text)
                j += 1
        if line.strip() == "**Confidence Notes**":
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                confidence = next_line[2:] if next_line.startswith("- ") else next_line.lstrip("- ")
    return overall, top_issues[:3], confidence


def build_email_digest(context, user, weekly_outputs, meal_plan, grocery_list, nutrition_brief, qa_report, grocery_diff_lines=None) -> str:
    week_start = context.get("week_start")
    dt = datetime.date.fromisoformat(week_start)
    week_label = f"W{dt.isocalendar().week:02d}"

    # Dynamic theme derived from day-type distribution (#8)
    theme = pick_week_theme(meal_plan.get("days", []), grocery_diff_lines)

    subject_line = f"Week {week_label} — {theme}"

    at_a_glance = []
    at_a_glance.append(f"- Training focus: {context.get('training_focus')}")
    at_a_glance.append(f"- Pattern: {summarize_day_types(meal_plan.get('days', []))}")
    at_a_glance.append(f"- Goal: {user.get('goal')}")
    if grocery_diff_lines:
        at_a_glance.append("- Grocery shift: higher-carb staples increased for intensity days")
    else:
        at_a_glance.append("- Grocery list reflects meal buckets for the week")
    at_a_glance.append(f"- Calories target average: {nutrition_brief.get('targets', {}).get('kcal', 0)} kcal")

    targets_table = format_per_day_targets(weekly_outputs.get("per_day_targets", []))
    meal_examples = pick_example_meals(meal_plan)
    grocery_top = top_grocery_items(grocery_list, 8)

    overall, issues, confidence = parse_qa_summary(qa_report)
    qa_summary = [f"- Status: {overall}"]
    if issues:
        for i in issues:
            qa_summary.append(f"- {i}")
    else:
        qa_summary.append("- Top issues: None")
    qa_confidence = f"- Confidence: {confidence}" if confidence else "- Confidence: QA checks passed"

    feedback_questions = [
        "- Any schedule changes or time constraints next week?",
        "- Budget target or preferred price range?",
        "- Meals you want repeated or avoided?",
    ]

    template_text = (ROOT / "templates" / "Weekly_Email_Digest.template.md").read_text()
    return render_template(template_text, {
        "subject_line": subject_line,
        "at_a_glance": "\n".join(at_a_glance),
        "targets_table": "\n".join(targets_table),
        "meal_examples": "\n".join(meal_examples),
        "grocery_top_items": "\n".join(grocery_top),
        "feedback_questions": "\n".join(feedback_questions),
        "qa_summary": "\n".join(qa_summary),
        "qa_confidence": qa_confidence,
    })


def build_demo_outputs(user, context, signals):
    week_start = context["week_start"]

    # Load meal buckets from demo_inputs/meal_buckets.json (#6)
    buckets = load_json(ROOT / "demo_inputs" / "meal_buckets.json")

    def meals_for_day(day_type: str) -> list:
        return buckets.get(day_type, buckets["training"])

    # Build days from schedule
    days = []
    for d in context["schedule"]:
        day_type = detect_day_type(d)
        targets = targets_for_day(day_type, user, context["schedule"])
        kcal = targets["kcal"]
        meals = meals_for_day(day_type)

        days.append({
            "date": d["date"],
            "day_type": day_type,
            "meals": meals,
            "target_kcal": kcal,
        })

    meal_plan = {
        "week_start": week_start,
        "days": [{k: v for k, v in day.items() if k != "target_kcal"} for day in days],
    }

    grocery_list = build_grocery_list(meal_plan)

    # Compute per-day targets first
    per_day_targets = []
    for d in days:
        t = targets_for_day(d["day_type"], user, context["schedule"])
        per_day_targets.append({
            "date": d["date"],
            "day_type": d["day_type"],
            "calories_target": t["kcal"],
            "protein_g": t["protein_g"],
            "carbs_g": t["carbs_g"],
            "fat_g": t["fat_g"],
            "notes": "Auto-generated targets for day type."
        })

    # Derive nutrition_brief targets as weekly averages (#7)
    avg_kcal = round(sum(t["calories_target"] for t in per_day_targets) / len(per_day_targets))
    avg_protein = round(sum(t["protein_g"] for t in per_day_targets) / len(per_day_targets))
    avg_carbs = round(sum(t["carbs_g"] for t in per_day_targets) / len(per_day_targets))
    avg_fat = round(sum(t["fat_g"] for t in per_day_targets) / len(per_day_targets))

    nutrition_brief = {
        "summary": (
            "Supportive adaptation: high days increase carbs and calories; rest days keep calories "
            "steady with higher protein density and earlier dinner. Targets below are weekly averages."
        ),
        "targets": {
            "kcal": avg_kcal,
            "protein_g": avg_protein,
            "carbs_g": avg_carbs,
            "fat_g": avg_fat,
        },
    }

    weekly_outputs = {
        "week_start": week_start,
        "per_day_targets": per_day_targets,
        "meal_plan": meal_plan,
        "grocery_list": grocery_list,
        "nutrition_brief": nutrition_brief,
        "email_digest": ""
    }

    return days, meal_plan, grocery_list, nutrition_brief, weekly_outputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Run demo inputs")
    parser.add_argument("--variant", choices=["base", "alt"], default="base", help="Demo variant")
    parser.add_argument("--gmail-draft", action="store_true", help="Create Gmail draft from digest")
    parser.add_argument("--to", dest="to_email", help="Recipient email for draft")
    parser.add_argument(
        "--ingest", action="store_true",
        help="Parse raw inputs (demo_inputs/raw/) → demo_inputs/parsed/ before running pipeline"
    )
    parser.add_argument(
        "--week-start", dest="week_start",
        help="ISO date (YYYY-MM-DD) for Garmin parser week start (default: current Monday)"
    )
    parser.add_argument(
        "--kroger-search", action="store_true",
        help="Run Kroger product search on grocery list and write kroger_cart_request.json"
    )
    parser.add_argument(
        "--garmin-wellness", dest="garmin_wellness_dir", metavar="PATH",
        help="Path to Garmin Connect full export directory. "
             "Parses sleep, RHR, steps, and training load → updates demo_inputs/outcome_signals.json "
             "before running the pipeline. Example: --garmin-wellness Garmin02242026"
    )
    parser.add_argument(
        "--wellness-days", dest="wellness_days", type=int, default=14, metavar="N",
        help="Rolling average window in days for Garmin wellness signals (default: 14)."
    )
    parser.add_argument(
        "--drinkcontrol", dest="drinkcontrol_csv", metavar="PATH",
        help="Path to DrinkControl CSV export. Parses alcohol consumption (7-day units, "
             "28-day avg, recovery flag) → updates demo_inputs/outcome_signals.json "
             "before running the pipeline. "
             "Example: --drinkcontrol ~/Library/Mobile\\ Documents/com~apple~CloudDocs/drinkcontrol.csv"
    )
    args = parser.parse_args()

    if not args.demo:
        raise SystemExit("Use --demo for the local demo run.")

    demo_dir = ROOT / "demo_inputs"
    out_dir = ROOT / "outputs" / ("demo_alt" if args.variant == "alt" else "demo")
    out_dir.mkdir(parents=True, exist_ok=True)

    # --ingest: parse raw inputs → demo_inputs/parsed/ -------------------------
    if args.ingest:
        raw_dir = demo_dir / "raw"
        parsed_dir = demo_dir / "parsed"
        print("\n[--ingest] Parsing raw inputs...")

        # User intake
        user_intake_csv = raw_dir / "user_intake.csv"
        if user_intake_csv.exists():
            user_intake_import.run(raw_dir, parsed_dir)
        else:
            print(f"  Skipping user intake: {user_intake_csv} not found")

        # Garmin activities
        garmin_csv = raw_dir / "garmin_activities.csv"
        if garmin_csv.exists():
            import datetime as _dt
            if args.week_start:
                ws = _dt.date.fromisoformat(args.week_start)
            else:
                today = _dt.date.today()
                ws = today - _dt.timedelta(days=today.weekday())
            garmin_import.run(raw_dir, parsed_dir, ws)
        else:
            print(f"  Skipping Garmin: {garmin_csv} not found")
            print("  Export from Garmin Connect → Activities → Export to CSV")

        print("[--ingest] Done. Parsed files written to demo_inputs/parsed/")
        print("  Review demo_inputs/parsed/weekly_context.json before running the pipeline.\n")

    # --garmin-wellness: parse full Garmin export → update outcome_signals.json ----
    if args.garmin_wellness_dir:
        import datetime as _dt
        garmin_dir = Path(args.garmin_wellness_dir)
        if not garmin_dir.is_absolute():
            garmin_dir = (ROOT / garmin_dir).resolve()
        if not garmin_dir.exists():
            raise SystemExit(f"[--garmin-wellness] Directory not found: {garmin_dir}")
        print(f"\n[--garmin-wellness] Parsing Garmin wellness export...")
        garmin_wellness_import.run(
            garmin_dir=garmin_dir,
            output_path=demo_dir / "outcome_signals.json",
            days=args.wellness_days,
        )
        print()

    # --drinkcontrol: parse DrinkControl CSV → update outcome_signals.json -----------
    if args.drinkcontrol_csv:
        dc_path = Path(args.drinkcontrol_csv).expanduser()
        if not dc_path.is_absolute():
            dc_path = (ROOT / dc_path).expanduser().resolve()
        if not dc_path.exists():
            raise SystemExit(f"[--drinkcontrol] File not found: {dc_path}")
        print(f"\n[--drinkcontrol] Parsing DrinkControl export...")
        drinkcontrol_import.run(
            csv_path=dc_path,
            output_path=demo_dir / "outcome_signals.json",
        )
        print()

    schema_registry = build_registry()

    # After --ingest, read from parsed/ dir; otherwise fall back to static demo files
    parsed_dir = demo_dir / "parsed"
    use_parsed = args.ingest and parsed_dir.exists()
    input_dir = parsed_dir if use_parsed else demo_dir

    try:
        user = load_json(input_dir / "user_profile.json")
        context_file = "weekly_context_alt.json" if args.variant == "alt" else "weekly_context.json"
        context = load_json(input_dir / context_file)
        signals = load_json(demo_dir / "outcome_signals.json")

        validate_or_exit(
            user,
            load_schema("user_profile.schema.json"),
            "demo_inputs/user_profile.json",
            schema_registry,
        )
        validate_or_exit(
            context,
            load_schema("weekly_context.schema.json"),
            f"demo_inputs/{context_file}",
            schema_registry,
        )
        validate_or_exit(
            signals,
            load_schema("outcome_signals.schema.json"),
            "demo_inputs/outcome_signals.json",
            schema_registry,
        )
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)

    days, meal_plan, grocery_list, nutrition_brief, weekly_outputs = build_demo_outputs(user, context, signals)

    # Render templates
    templates_dir = ROOT / "templates"
    weekly_meal_tpl = (templates_dir / "Weekly_Meal_Plan.template.md").read_text()
    brief_tpl = (templates_dir / "Nutrition_Brief.template.md").read_text()

    daily_lines = []
    for d in days:
        daily_lines.append(f"- {d['date']} ({d['day_type']}): {', '.join(m['name'] for m in d['meals'])}")
    daily_plan_table = "\n".join(daily_lines)

    weekly_meal_md = render_template(weekly_meal_tpl, {
        "week_start": context["week_start"],
        "goal": user["goal"],
        "training_focus": context["training_focus"],
        "daily_plan_table": daily_plan_table,
    })

    grocery_md = grocery_to_markdown(grocery_list)

    brief_md = render_template(brief_tpl, {
        "week_start": context["week_start"],
        "summary": nutrition_brief["summary"],
        "kcal": nutrition_brief["targets"]["kcal"],
        "protein_g": nutrition_brief["targets"]["protein_g"],
        "carbs_g": nutrition_brief["targets"]["carbs_g"],
        "fat_g": nutrition_brief["targets"]["fat_g"],
    })

    alt_hash_flag = None
    grocery_diff_lines = None
    if args.variant == "alt":
        base_file = ROOT / "outputs" / "demo" / "grocery_list.json"
        if base_file.exists():
            base_json = json.loads(base_file.read_text())
            base_hash = hashlib.sha256(json.dumps(base_json, sort_keys=True).encode("utf-8")).hexdigest()
            alt_hash = hashlib.sha256(json.dumps(grocery_list, sort_keys=True).encode("utf-8")).hexdigest()
            alt_hash_flag = "SAME" if base_hash == alt_hash else "DIFF"
            grocery_diff_lines = compute_grocery_diff(base_json, grocery_list)

    qa_report = run_qa_checks(
        user,
        context,
        meal_plan,
        grocery_list,
        nutrition_brief,
        weekly_outputs,
        alt_hash_flag,
        grocery_diff_lines,
    )

    email_md = build_email_digest(
        context,
        user,
        weekly_outputs,
        meal_plan,
        grocery_list,
        nutrition_brief,
        qa_report,
        grocery_diff_lines,
    )

    weekly_outputs["email_digest"] = email_md

    try:
        validate_or_exit(
            meal_plan,
            load_schema("meal_plan.schema.json"),
            f"{out_dir}/meal_plan.json",
            schema_registry,
        )
        validate_or_exit(
            grocery_list,
            load_schema("grocery_list.schema.json"),
            f"{out_dir}/grocery_list.json",
            schema_registry,
        )
        validate_or_exit(
            weekly_outputs,
            load_schema("weekly_outputs.schema.json"),
            f"{out_dir}/weekly_outputs.json",
            schema_registry,
        )
    except ValidationError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)

    # Write outputs
    (out_dir / "Weekly_Meal_Plan.md").write_text(weekly_meal_md)
    (out_dir / "Grocery_List.md").write_text(grocery_md)
    (out_dir / "Nutrition_Brief.md").write_text(brief_md)
    (out_dir / "Weekly_Email_Digest.md").write_text(email_md)
    (out_dir / "meal_plan.json").write_text(json.dumps(meal_plan, indent=2))
    (out_dir / "grocery_list.json").write_text(json.dumps(grocery_list, indent=2))
    (out_dir / "weekly_outputs.json").write_text(json.dumps(weekly_outputs, indent=2))
    (out_dir / "qa_report.md").write_text(qa_report)

    # --kroger-search: resolve grocery items against Kroger API --------------------
    if args.kroger_search:
        config_path = demo_dir / "kroger_config.json"
        grocery_json_path = out_dir / "grocery_list.json"
        cart_out_path = out_dir / "kroger_cart_request.json"
        print("\n[--kroger-search] Resolving grocery items via Kroger API...")
        try:
            kroger_cart.run_search(
                grocery_list_path=grocery_json_path,
                config_path=config_path,
                out_path=cart_out_path,
            )
            # Reload enriched grocery list and re-render markdown with prices
            enriched_data = json.loads(cart_out_path.read_text())
            enriched_items = enriched_data.get("enriched_items", [])
            if enriched_items:
                grocery_list["items"] = enriched_items
                enriched_grocery_md = grocery_to_markdown(grocery_list)
                (out_dir / "Grocery_List.md").write_text(enriched_grocery_md)
                print(f"  Grocery_List.md updated with Kroger prices.")
                total = enriched_data.get("estimated_total_usd", 0)
                priced = enriched_data.get("items_priced", 0)
                total_items = enriched_data.get("items_total", 0)
                print(f"  Estimated total: ${total:.2f} ({priced}/{total_items} items priced)")
        except (FileNotFoundError, ValueError) as e:
            print(f"  [kroger-search] Skipped: {e}")
        except kroger_cart.KrogerAPIError as e:
            print(f"  [kroger-search] API error: {e}")

    if args.gmail_draft:
        digest_path = out_dir / "Weekly_Email_Digest.md"
        digest_text = digest_path.read_text()
        subject = "Weekly Nutrition Digest"
        for line in digest_text.splitlines():
            if line.startswith("# "):
                subject = line.replace("# ", "").strip()
                break
        to_email = args.to_email or os.getenv("DELIVERY_EMAIL") or "pr@apxaxn.com"
        create_draft(subject=subject, body=digest_text, to=to_email, output_dir=out_dir)
        print(f"Gmail draft created (stub). Recipient: {to_email}")

    print(f"Demo outputs written to {out_dir}")


if __name__ == "__main__":
    main()
