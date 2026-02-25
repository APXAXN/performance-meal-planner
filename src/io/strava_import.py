"""Strava Official API integration — activity fetch for weekly meal planning.

Strava's API is free for personal/non-commercial use and fully automatable
once the one-time OAuth setup is complete.

── One-time setup ────────────────────────────────────────────────────────────
1. Create an app at https://www.strava.com/settings/api
   - Set "Authorization Callback Domain" to localhost
   - Note your Client ID and Client Secret

2. Get an authorization code via browser:
   https://www.strava.com/oauth/authorize?client_id=CLIENT_ID&response_type=code
     &redirect_uri=http://localhost&scope=activity:read&approval_prompt=force

   After authorizing, you'll be redirected to:
     http://localhost/?state=&code=AUTHORIZATION_CODE&scope=...
   Copy the AUTHORIZATION_CODE from the URL.

3. Exchange code for refresh token (run once in terminal):
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=CLIENT_ID \
     -d client_secret=CLIENT_SECRET \
     -d code=AUTHORIZATION_CODE \
     -d grant_type=authorization_code
   Copy the "refresh_token" from the response.

4. Add to .env:
   STRAVA_CLIENT_ID=12345
   STRAVA_CLIENT_SECRET=abc123...
   STRAVA_REFRESH_TOKEN=def456...

After that, the script refreshes the access token automatically on each run.
── ──────────────────────────────────────────────────────────────────────────
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"

# Local cache file for the access token (avoids re-fetching on every run)
_TOKEN_CACHE = Path(".strava_token")

# Internal type mapping: Strava sport type → our internal type
_TYPE_MAP = {
    "run": "run",
    "virtualrun": "run",
    "trailrun": "run",
    "ride": "cycling",
    "virtualride": "cycling",
    "mountainbikeride": "cycling",
    "gravel_ride": "cycling",
    "weighttraining": "strength",
    "workout": "strength",
    "crossfit": "strength",
    "swim": "swimming",
    "openwatersswim": "swimming",
    "walk": "walk",
    "hike": "hike",
    "yoga": "mobility",
    "pilates": "mobility",
    "rowing": "rowing",
    "inlineskate": "cardio",
    "elliptical": "cardio",
    "stairstepper": "cardio",
    "iceskate": "cardio",
    "nordicski": "cardio",
    "alpineski": "cardio",
    "snowboard": "cardio",
    "soccer": "cardio",
    "golf": "mobility",
}


def _map_type(strava_type: str) -> str:
    return _TYPE_MAP.get(strava_type.lower().replace(" ", ""), "other")


def get_strava_token() -> Optional[str]:
    """Get a valid Strava access token using the refresh token flow.

    Reads STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN from env.
    Caches the access token + expiry in .strava_token (JSON) to avoid
    re-fetching on every run.

    Returns:
        Access token string, or None if any credential is missing.
    """
    client_id = os.environ.get("STRAVA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("STRAVA_REFRESH_TOKEN", "").strip()

    if not (client_id and client_secret and refresh_token):
        logger.info(
            "Strava not configured — add STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, "
            "STRAVA_REFRESH_TOKEN to .env. "
            "One-time setup: https://www.strava.com/settings/api"
        )
        return None

    # Check cache
    if _TOKEN_CACHE.exists():
        try:
            cached = json.loads(_TOKEN_CACHE.read_text())
            if cached.get("expires_at", 0) > time.time() + 60:
                return cached["access_token"]
        except Exception:
            pass

    # Refresh
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(
        STRAVA_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("Strava token refresh failed (%s): %s", e.code, e.read().decode()[:200])
        return None
    except Exception as exc:
        logger.warning("Strava token refresh error: %s", exc)
        return None

    access_token = body.get("access_token")
    expires_at = body.get("expires_at", 0)

    if access_token:
        try:
            _TOKEN_CACHE.write_text(json.dumps({
                "access_token": access_token,
                "expires_at": expires_at,
            }))
        except Exception:
            pass  # cache write failure is non-fatal

    return access_token


def fetch_activities(start_date: str, end_date: str) -> list[dict]:
    """Fetch Strava activities within a date range.

    Args:
        start_date: ISO date string (YYYY-MM-DD) — inclusive.
        end_date:   ISO date string (YYYY-MM-DD) — inclusive.

    Returns:
        List of activity dicts:
          {date, type, name, duration_min, distance_km, avg_hr, suffer_score, calories}
        Returns empty list if Strava is not configured or fetch fails.
    """
    token = get_strava_token()
    if not token:
        return []

    start_dt = datetime.combine(date.fromisoformat(start_date),
                                 datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(date.fromisoformat(end_date),
                               datetime.max.time().replace(microsecond=0)).replace(tzinfo=timezone.utc)

    after = int(start_dt.timestamp())
    before = int(end_dt.timestamp())

    params = urllib.parse.urlencode({
        "after": after,
        "before": before,
        "per_page": 30,
    })
    url = f"{STRAVA_ACTIVITIES_URL}?{params}"

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_activities = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("Strava activities fetch failed (%s): %s", e.code, e.read().decode()[:200])
        return []
    except Exception as exc:
        logger.warning("Strava activities fetch error: %s", exc)
        return []

    activities = []
    for act in raw_activities:
        start_str = act.get("start_date_local", "")[:10]  # "YYYY-MM-DD"
        sport_type = act.get("sport_type") or act.get("type", "")

        distance_m = act.get("distance", 0) or 0
        elapsed_sec = act.get("elapsed_time", 0) or 0

        activities.append({
            "date": start_str,
            "type": _map_type(sport_type),
            "name": act.get("name", ""),
            "duration_min": round(elapsed_sec / 60, 1) if elapsed_sec else None,
            "distance_km": round(distance_m / 1000, 2) if distance_m else None,
            "avg_hr": act.get("average_heartrate"),
            "suffer_score": act.get("suffer_score"),
            "calories": act.get("calories"),
        })

    return activities
