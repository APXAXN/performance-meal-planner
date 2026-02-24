"""Normalize and roll up grocery items."""

ALIASES = {
    "capsicum": "bell pepper",
    "bell peppers": "bell pepper",
    "olive oil extra virgin": "olive oil",
    "extra virgin olive oil": "olive oil",
    "ev olive oil": "olive oil",
}

# Ingredient-domain plural → singular map.
# Replaces the naive endswith("s") strip that broke words like
# "hummus" → "hummu" or "asparagus" → "asparagaru".
PLURAL_MAP = {
    "oats": "oat",
    "berries": "berry",
    "greens": "green",
    "whites": "white",
    "fillets": "fillet",
    "eggs": "egg",
    "bananas": "banana",
    "peppers": "pepper",
    "tomatoes": "tomato",
    "potatoes": "potato",
    "onions": "onion",
    "carrots": "carrot",
    "olives": "olive",
    "grapes": "grape",
    "nuts": "nut",
    "almonds": "almond",
    "cashews": "cashew",
    "walnuts": "walnut",
    "strawberries": "strawberry",
    "blueberries": "blueberry",
    "raspberries": "raspberry",
    "cherries": "cherry",
    "peaches": "peach",
    "apples": "apple",
    "oranges": "orange",
    "lemons": "lemon",
    "limes": "lime",
    "mushrooms": "mushroom",
    "zucchinis": "zucchini",
    "cucumbers": "cucumber",
    "lentils": "lentil",
    "beans": "bean",
    "chickpeas": "chickpea",
    "shrimps": "shrimp",
    "sardines": "sardine",
    "anchovies": "anchovy",
    "herbs": "herb",
    "spices": "spice",
    "seeds": "seed",
}

UNIT_ALIASES = {
    "grams": "g",
    "gram": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "milliliter": "ml",
    "milliliters": "ml",
    "liter": "l",
    "liters": "l",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "count": "count",
}

CONVERSIONS = {
    ("kg", "g"): 1000.0,
    ("g", "kg"): 1.0 / 1000.0,
    ("l", "ml"): 1000.0,
    ("ml", "l"): 1.0 / 1000.0,
    ("tbsp", "ml"): 15.0,
    ("tsp", "ml"): 5.0,
}


def normalize_name(name: str) -> str:
    n = " ".join(name.lower().split())
    n = ALIASES.get(n, n)
    n = PLURAL_MAP.get(n, n)
    return n


def normalize_unit(unit: str) -> str:
    u = " ".join(unit.lower().split())
    return UNIT_ALIASES.get(u, u)


def convert(quantity: float, from_unit: str, to_unit: str):
    if from_unit == to_unit:
        return quantity
    key = (from_unit, to_unit)
    if key in CONVERSIONS:
        return quantity * CONVERSIONS[key]
    return None


def rollup(items: list) -> list:
    """
    items: list of dicts with name, quantity, unit, category, source_days
    returns rolled up list with name_display, name_normalized, total_quantity, unit, category, source_days, notes
    """
    buckets = {}
    notes = {}

    for it in items:
        name_norm = normalize_name(it["name"])
        unit_norm = normalize_unit(it["unit"])
        key = (name_norm, unit_norm)
        if key not in buckets:
            buckets[key] = {
                "name_display": it["name"],
                "name_normalized": name_norm,
                "total_quantity": float(it["quantity"]),
                "unit": unit_norm,
                "category": it.get("category") or "unknown",
                "source_days": sorted(set(it.get("source_days", []))),
                "notes": "",
            }
        else:
            buckets[key]["total_quantity"] += float(it["quantity"])
            buckets[key]["source_days"] = sorted(set(buckets[key]["source_days"]) | set(it.get("source_days", [])))

    # Attempt conversion for same name with different units
    name_groups = {}
    for (name_norm, unit), item in buckets.items():
        name_groups.setdefault(name_norm, []).append(item)

    rolled = []
    for name_norm, group in name_groups.items():
        if len(group) == 1:
            rolled.append(group[0])
            continue

        # Copy to avoid mutating the original bucket entries (fix #4)
        base = dict(group[0])
        base["source_days"] = list(group[0]["source_days"])
        merged = True
        for other in group[1:]:
            conv = convert(other["total_quantity"], other["unit"], base["unit"])
            if conv is None:
                merged = False
                break
            base["total_quantity"] += conv
            base["source_days"] = sorted(set(base["source_days"]) | set(other["source_days"]))
            if other.get("notes"):
                notes[name_norm] = "Unit conversion applied"
        if merged:
            base["notes"] = notes.get(name_norm, "")
            rolled.append(base)
        else:
            rolled.extend(group)

    return rolled
