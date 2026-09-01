from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    resend_api_key: str
    to_email: str
    from_email: str
    openai_model: str = "gpt-5-mini"
    timezone: str = "America/Phoenix"
    lookback_hours: int = 36
    max_candidates: int = 60

    @classmethod
    def from_env(cls, *, require_delivery: bool = True) -> Settings:
        values = {
            "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
            "resend_api_key": os.getenv("RESEND_API_KEY", ""),
            "to_email": os.getenv("BRIEF_TO_EMAIL", ""),
            "from_email": os.getenv("BRIEF_FROM_EMAIL", ""),
            "openai_model": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            "timezone": os.getenv("BRIEF_TIMEZONE", "America/Phoenix"),
            "lookback_hours": int(os.getenv("BRIEF_LOOKBACK_HOURS", "36")),
            "max_candidates": int(os.getenv("BRIEF_MAX_CANDIDATES", "60")),
        }
        required = ["openai_api_key"]
        if require_delivery:
            required += ["resend_api_key", "to_email", "from_email"]
        missing = [key for key in required if not values[key]]
        if missing:
            env_names = {
                "openai_api_key": "OPENAI_API_KEY",
                "resend_api_key": "RESEND_API_KEY",
                "to_email": "BRIEF_TO_EMAIL",
                "from_email": "BRIEF_FROM_EMAIL",
            }
            raise ValueError(
                "Missing required environment variables: "
                + ", ".join(env_names[x] for x in missing)
            )
        return cls(**values)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES_PATH = PROJECT_ROOT / "config" / "sources.yml"
DEFAULT_OUT_DIR = PROJECT_ROOT / "out"
