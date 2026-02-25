"""Day type detection helpers."""

def detect_day_type(schedule_entry: dict) -> str:
    """
    Determine day type from weekly_context schedule entry.
    Falls back to simple note-based heuristics.
    """
    day_type = schedule_entry.get("day_type")
    if day_type in {"rest", "training", "high"}:
        return day_type

    notes = (schedule_entry.get("notes") or "").lower()
    if "long" in notes or "interval" in notes or "race" in notes:
        return "high"
    if "rest" in notes or "mobility" in notes:
        return "rest"
    return "training"
