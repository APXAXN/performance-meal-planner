#!/usr/bin/env python3
"""Onboarding questionnaire — Claude-powered conversational profile builder.

Claude asks the user questions one at a time, validates answers, fills gaps
with smart defaults, and writes a schema-compliant user_profile.json to
demo_inputs/user_profile.json.

Usage:
    python src/onboarding.py
    python src/onboarding.py --output demo_inputs/user_profile.json
"""

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# Load .env before importing config
try:
    from dotenv import load_dotenv
    env_path = _ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
except ImportError:
    pass

import anthropic


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a friendly, knowledgeable performance nutrition onboarding assistant for a weekly meal planning system. Your job is to collect the user's profile information through natural conversation.

You need to collect the following fields to build their profile:
- name (str) — first name or preferred name
- age (int) — years
- sex (str) — "female", "male", "nonbinary", or "unspecified"
- height_cm (float) — height in cm (accept feet/inches and convert)
- weight_kg (float) — weight in kg (accept lbs and convert: lbs × 0.4536)
- goal (str) — one of: "maintain", "gain", "cut"
- dietary_preferences (list of strings) — e.g. ["omnivore"], ["vegetarian"], ["vegan"], ["gluten-free", "dairy-free"]
- avoid_list (list of strings) — ingredients they dislike or avoid (not allergies)
- allergies (list of strings) — true food allergies (will be treated as hard constraints)
- cooking_time_max_min (int) — max minutes willing to spend cooking a meal (15, 30, 45, 60, 90)
- budget_level (str) — "low", "medium", or "high" (weekly grocery budget)
- body_fat_pct (float or null) — optional, if they know it
- ftp_w (int or null) — optional, functional threshold power in watts (cyclists only)

RULES:
1. Ask questions conversationally — don't list all fields at once. Group related questions naturally.
2. Accept natural language and convert to the correct format (e.g. "5'10" → 177.8 cm, "175 lbs" → 79.4 kg).
3. When you have collected ALL required fields, output a JSON block in this exact format:

```json
{
  "PROFILE_COMPLETE": true,
  "profile": {
    "user_id": "user_001",
    "name": "...",
    "age": 0,
    "sex": "...",
    "height_cm": 0.0,
    "weight_kg": 0.0,
    "goal": "...",
    "dietary_preferences": [],
    "avoid_list": [],
    "allergies": [],
    "cooking_time_max_min": 30,
    "budget_level": "medium",
    "body_fat_pct": null,
    "ftp_w": null
  }
}
```

4. Only output the JSON block when ALL required fields are collected. Required fields: name, age, sex, height_cm, weight_kg, goal, dietary_preferences.
5. For optional fields (avoid_list, allergies, cooking_time_max_min, budget_level, body_fat_pct, ftp_w) — if not provided, use sensible defaults: avoid_list=[], allergies=[], cooking_time_max_min=30, budget_level="medium", body_fat_pct=null, ftp_w=null.
6. Be warm and encouraging. If they give an unusual answer, ask a gentle clarifying question.
7. Start by introducing yourself briefly and asking for their name."""


# ── Conversation loop ─────────────────────────────────────────────────────────

def run_onboarding(output_path: Path) -> None:
    client = anthropic.Anthropic()

    print("\n" + "─" * 60)
    print("  Performance Meal Planner — Profile Setup")
    print("  Powered by Claude · Type 'quit' to exit")
    print("─" * 60 + "\n")

    messages = []

    # Kick off with Claude's opening message
    opening = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "Hi, I'm ready to set up my profile."}],
    )
    assistant_text = opening.content[0].text
    messages.append({"role": "user", "content": "Hi, I'm ready to set up my profile."})
    messages.append({"role": "assistant", "content": assistant_text})
    print(f"Claude: {assistant_text}\n")

    # Conversation loop
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nOnboarding cancelled.")
            sys.exit(0)

        if user_input.lower() in ("quit", "exit", "q"):
            print("\nOnboarding cancelled. Run again to restart.")
            sys.exit(0)

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        assistant_text = response.content[0].text
        messages.append({"role": "assistant", "content": assistant_text})

        print(f"\nClaude: {assistant_text}\n")

        # Check if profile is complete
        if "PROFILE_COMPLETE" in assistant_text and "```json" in assistant_text:
            profile = _extract_profile(assistant_text)
            if profile:
                _save_profile(profile, output_path)
                break


def _extract_profile(text: str) -> dict | None:
    """Extract the profile JSON block from Claude's response."""
    import re
    # Find ```json ... ``` block
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        if data.get("PROFILE_COMPLETE") and "profile" in data:
            return data["profile"]
    except json.JSONDecodeError:
        pass
    return None


def _save_profile(profile: dict, output_path: Path) -> None:
    """Validate and save the profile JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, indent=2))

    print("\n" + "─" * 60)
    print("  ✓  Profile saved!")
    print(f"     → {output_path}")
    print("─" * 60)
    print(f"\n  Name:   {profile.get('name', '?')}")
    print(f"  Goal:   {profile.get('goal', '?')}")
    print(f"  Weight: {profile.get('weight_kg', '?')} kg")
    print(f"  Diet:   {', '.join(profile.get('dietary_preferences', []))}")
    if profile.get('allergies'):
        print(f"  Allergies: {', '.join(profile['allergies'])}")
    if profile.get('avoid_list'):
        print(f"  Avoids: {', '.join(profile['avoid_list'])}")
    print()
    print("  Next step: run the pipeline with your new profile:")
    print("    python src/run_weekly.py --demo")
    print("─" * 60 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude-powered onboarding questionnaire — builds user_profile.json"
    )
    parser.add_argument(
        "--output",
        default="demo_inputs/user_profile.json",
        metavar="PATH",
        help="Output path for user_profile.json (default: demo_inputs/user_profile.json)",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = (_ROOT / output_path).resolve()

    # Verify API key is available
    import os
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("\n[✗] ANTHROPIC_API_KEY not set.")
        print("    Add it to your .env file:")
        print("    ANTHROPIC_API_KEY=sk-ant-api03-...")
        print("    Get one at: https://console.anthropic.com/\n")
        sys.exit(1)

    run_onboarding(output_path)


if __name__ == "__main__":
    main()
