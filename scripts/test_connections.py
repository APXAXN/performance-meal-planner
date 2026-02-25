#!/usr/bin/env python3
"""Integration smoke test.

Runs each integration and prints a status table showing what's working.

Usage:
    python scripts/test_connections.py
"""

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# Load .env if present
try:
    from dotenv import load_dotenv
    env_path = _ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
except ImportError:
    pass


def _check(label: str, status: str, detail: str) -> None:
    icons = {"ok": "[✓]", "warn": "[~]", "fail": "[✗]", "skip": "[-]"}
    icon = icons.get(status, "[ ]")
    print(f"  {icon} {label:<22} — {detail}")


def test_anthropic() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        _check("Anthropic", "fail",
               "Missing: ANTHROPIC_API_KEY — get it at https://console.anthropic.com/")
        return

    try:
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "ping"}],
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            model = body.get("model", "?")
            _check("Anthropic", "ok", f"model ping successful ({model})")
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:100]
        _check("Anthropic", "fail", f"HTTP {e.code}: {err}")
    except Exception as exc:
        _check("Anthropic", "fail", f"Error: {exc}")


def test_nutritionix() -> None:
    demo_csv = _ROOT / "demo_inputs" / "nutritionix_demo.csv"
    if not demo_csv.exists():
        _check("Nutritionix", "fail", "demo CSV not found: demo_inputs/nutritionix_demo.csv")
        return

    try:
        from src.io.nutritionix_import import parse_nutrition_log, summarize_week
        daily = parse_nutrition_log(str(demo_csv))
        summary = summarize_week(daily)
        n = summary["days_logged"]
        avg_kcal = summary.get("avg_calories", "?")
        _check("Nutritionix", "ok", f"demo CSV parsed: {n} days, avg {avg_kcal:,} kcal")
    except Exception as exc:
        _check("Nutritionix", "fail", f"Parse error: {exc}")


def test_garmin() -> None:
    demo_act = _ROOT / "demo_inputs" / "garmin_activities_demo.csv"
    demo_well = _ROOT / "demo_inputs" / "garmin_wellness_demo.csv"

    if not demo_act.exists():
        _check("Garmin", "fail", "demo CSV not found: demo_inputs/garmin_activities_demo.csv")
        return

    try:
        from src.io.garmin_import import parse_activities, parse_wellness
        activities = parse_activities(str(demo_act))
        wellness = parse_wellness(str(demo_well)) if demo_well.exists() else []

        training_days = sum(1 for a in activities
                            if a.get("activity_type") not in ("", None))
        sleep_vals = [w["sleep_hours"] for w in wellness if w.get("sleep_hours")]
        avg_sleep = round(sum(sleep_vals) / len(sleep_vals), 1) if sleep_vals else None

        detail = f"demo CSV parsed: {training_days} training days"
        if avg_sleep:
            detail += f", avg {avg_sleep}h sleep"
        _check("Garmin", "ok", detail)
    except Exception as exc:
        _check("Garmin", "fail", f"Parse error: {exc}")


def test_strava() -> None:
    client_id = os.environ.get("STRAVA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("STRAVA_REFRESH_TOKEN", "").strip()

    if not (client_id and client_secret and refresh_token):
        _check("Strava", "skip", "not configured — add credentials to .env")
        return

    try:
        from src.io.strava_import import get_strava_token, fetch_activities
        token = get_strava_token()
        if not token:
            _check("Strava", "fail", "token fetch failed — check credentials")
            return

        # Fetch last 7 days
        today = date.today()
        week_start = today - timedelta(days=7)
        activities = fetch_activities(week_start.isoformat(), today.isoformat())
        n = len(activities)
        _check("Strava", "ok", f"token fetch: OK | {n} activities in last 7 days")
    except Exception as exc:
        _check("Strava", "fail", f"Error: {exc}")


def test_kroger_auth() -> None:
    client_id = os.environ.get("KROGER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("KROGER_CLIENT_SECRET", "").strip()

    if not (client_id and client_secret):
        _check("Kroger auth", "fail",
               "Missing: KROGER_CLIENT_ID / KROGER_CLIENT_SECRET — get it at https://developer.kroger.com")
        return

    try:
        from src.io.kroger_auth import get_token, _TOKEN_CACHE
        import json as _json
        token = get_token()
        if not token:
            _check("Kroger auth", "fail", "token fetch returned None — check credentials")
            return

        expires_at = None
        if _TOKEN_CACHE.exists():
            try:
                cached = _json.loads(_TOKEN_CACHE.read_text())
                expires_at = cached.get("expires_at")
            except Exception:
                pass

        if expires_at:
            import datetime as _dt
            exp_str = _dt.datetime.fromtimestamp(expires_at).strftime("%H:%M:%S")
            _check("Kroger auth", "ok", f"token fetched, expires at {exp_str}")
        else:
            _check("Kroger auth", "ok", "token fetched")
    except Exception as exc:
        _check("Kroger auth", "fail", f"Error: {exc}")


def test_kroger_cart() -> None:
    _check("Kroger cart", "warn", "stub only (user OAuth not yet wired)")


def test_ingest() -> None:
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "ingest.py"),
             "--week", "2025-03-03", "--demo"],
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
            timeout=30,
        )
        if result.returncode == 0:
            out_path = _ROOT / "inputs" / "weekly_context.json"
            if out_path.exists():
                ctx = json.loads(out_path.read_text())
                week = ctx.get("week_start", "?")
                n_days = len(ctx.get("schedule", []))
                _check("Ingestion script", "ok",
                       f"weekly_context.json written → inputs/ (week {week}, {n_days} days)")
            else:
                _check("Ingestion script", "fail", "script ran but weekly_context.json not found")
        else:
            err = result.stderr.strip()[-150:] if result.stderr else result.stdout.strip()[-150:]
            _check("Ingestion script", "fail", f"exit {result.returncode}: {err}")
    except Exception as exc:
        _check("Ingestion script", "fail", f"Error: {exc}")


def main() -> None:
    print()
    print("  Performance Meal Planner — Connection Smoke Test")
    print("  " + "─" * 50)
    test_anthropic()
    test_nutritionix()
    test_garmin()
    test_strava()
    test_kroger_auth()
    test_kroger_cart()
    test_ingest()
    print()


if __name__ == "__main__":
    main()
