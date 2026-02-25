"""Kroger OAuth2 client credentials token manager.

Handles the app-level (client_credentials) token used for product search.
Token is cached in .kroger_token (JSON) to avoid re-fetching on every run.

Credentials are read from environment variables (or .env via config.py):
  KROGER_CLIENT_ID
  KROGER_CLIENT_SECRET
  KROGER_LOCATION_ID   (default: 02400688 — Fred Meyer Seattle)

Register for credentials at: https://developer.kroger.com
"""

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KROGER_TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"

_TOKEN_CACHE = Path(".kroger_token")


def get_token() -> Optional[str]:
    """Fetch (or return cached) Kroger app-level access token.

    Uses client_credentials flow with scope=product.compact.
    Caches token + expiry in .kroger_token.
    Refreshes automatically when within 60 seconds of expiry.

    Returns:
        Access token string, or None if credentials are missing.
    """
    client_id = os.environ.get("KROGER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("KROGER_CLIENT_SECRET", "").strip()

    if not (client_id and client_secret):
        logger.info(
            "Kroger credentials not configured — set KROGER_CLIENT_ID and "
            "KROGER_CLIENT_SECRET in .env. Register at: https://developer.kroger.com"
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

    # Fetch new token
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "product.compact",
    }).encode()

    req = urllib.request.Request(
        KROGER_TOKEN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("Kroger token fetch failed (%s): %s", e.code, e.read().decode()[:200])
        return None
    except Exception as exc:
        logger.warning("Kroger token fetch error: %s", exc)
        return None

    access_token = body.get("access_token")
    expires_in = body.get("expires_in", 1800)
    expires_at = time.time() + expires_in

    if access_token:
        try:
            _TOKEN_CACHE.write_text(json.dumps({
                "access_token": access_token,
                "expires_at": expires_at,
            }))
        except Exception:
            pass

    return access_token
