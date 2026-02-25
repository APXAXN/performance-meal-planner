"""Environment variable loader for the Performance Meal Planner.

Loads all credentials and settings from the .env file (or real environment).
Two tiers:
  REQUIRED  — raises ConfigError with signup URL if missing
  OPTIONAL  — logs a warning; automated path unavailable, manual CSV fallback used

Usage:
    from src.io.config import cfg

    api_key = cfg.anthropic_api_key          # raises ConfigError if missing
    garmin_email = cfg.garmin_email          # None if not set (no crash)
"""

import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _dotenv_available = True
except ImportError:
    _dotenv_available = False

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _load_env() -> None:
    """Load .env from repo root (silently skips if file absent or dotenv not installed)."""
    if not _dotenv_available:
        return
    root = Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


_load_env()


def _require(name: str, signup_url: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ConfigError(
            f"Required environment variable {name!r} is not set.\n"
            f"  Get it at: {signup_url}\n"
            f"  Then add it to your .env file:  {name}=<your-value>"
        )
    return val


def _optional(name: str, description: str) -> str | None:
    val = os.environ.get(name, "").strip()
    if not val:
        logger.warning(
            "%s is not set — %s automated path unavailable; manual CSV fallback will be used.",
            name,
            description,
        )
        return None
    return val


class _Config:
    # ── Required ──────────────────────────────────────────────────────────────
    @property
    def anthropic_api_key(self) -> str:
        return _require("ANTHROPIC_API_KEY", "https://console.anthropic.com/")

    @property
    def kroger_client_id(self) -> str:
        return _require("KROGER_CLIENT_ID", "https://developer.kroger.com")

    @property
    def kroger_client_secret(self) -> str:
        return _require("KROGER_CLIENT_SECRET", "https://developer.kroger.com")

    # ── Optional ──────────────────────────────────────────────────────────────
    @property
    def kroger_location_id(self) -> str:
        val = os.environ.get("KROGER_LOCATION_ID", "").strip()
        return val if val else "02400688"  # Fred Meyer Seattle default

    @property
    def garmin_email(self) -> str | None:
        return _optional("GARMIN_EMAIL", "Garmin")

    @property
    def garmin_password(self) -> str | None:
        return _optional("GARMIN_PASSWORD", "Garmin")

    @property
    def strava_client_id(self) -> str | None:
        return _optional("STRAVA_CLIENT_ID", "Strava")

    @property
    def strava_client_secret(self) -> str | None:
        return _optional("STRAVA_CLIENT_SECRET", "Strava")

    @property
    def strava_refresh_token(self) -> str | None:
        return _optional("STRAVA_REFRESH_TOKEN", "Strava")

    @property
    def default_store(self) -> str:
        return os.environ.get("DEFAULT_STORE", "fred_meyer").strip()

    @property
    def default_servings(self) -> int:
        try:
            return int(os.environ.get("DEFAULT_SERVINGS", "2"))
        except ValueError:
            return 2


cfg = _Config()
