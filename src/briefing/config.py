"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_LOOKBACK_HOURS = 36
DEFAULT_MAX_CANDIDATES = 60


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    resend_api_key: str
    to_email: str
    from_email: str
    openai_model: str = DEFAULT_MODEL
    timezone: str = DEFAULT_TIMEZONE
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS
    max_candidates: int = DEFAULT_MAX_CANDIDATES

    @classmethod
    def from_env(cls, *, require_delivery: bool = True) -> Settings:
        values = {
            "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
            "resend_api_key": os.getenv("RESEND_API_KEY", ""),
            "to_email": os.getenv("BRIEF_TO_EMAIL", ""),
            "from_email": os.getenv("BRIEF_FROM_EMAIL", ""),
            "openai_model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            "timezone": os.getenv("BRIEF_TIMEZONE", DEFAULT_TIMEZONE),
            "lookback_hours": _env_int("BRIEF_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS),
            "max_candidates": _env_int("BRIEF_MAX_CANDIDATES", DEFAULT_MAX_CANDIDATES),
        }

        required = ["openai_api_key"]
        if require_delivery:
            required.extend(("resend_api_key", "to_email", "from_email"))
        missing = [key for key in required if not values[key]]
        if missing:
            env_names = {
                "openai_api_key": "OPENAI_API_KEY",
                "resend_api_key": "RESEND_API_KEY",
                "to_email": "BRIEF_TO_EMAIL",
                "from_email": "BRIEF_FROM_EMAIL",
            }
            names = ", ".join(env_names[key] for key in missing)
            raise ValueError(f"Missing required environment variables: {names}")
        return cls(**values)


PROJECT_ROOT = Path.cwd()
DEFAULT_SOURCES_PATH = PROJECT_ROOT / "config" / "sources.yml"
DEFAULT_OUT_DIR = PROJECT_ROOT / "out"
