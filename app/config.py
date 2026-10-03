"""Application settings loaded from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    policy_file: Path
    openai_api_key: str | None
    openai_model: str
    llm_rephrase: bool
    http_timeout: float
    alerts_file: Path | None
    forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)


def get_settings() -> Settings:
    alerts = os.getenv("ALERTS_FILE")
    return Settings(
        policy_file=Path(os.getenv("POLICY_FILE", ROOT_DIR / "policies" / "sops.yaml")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        llm_rephrase=os.getenv("LLM_REPHRASE", "false").lower() == "true",
        http_timeout=float(os.getenv("HTTP_TIMEOUT", "8")),
        alerts_file=Path(alerts) if alerts else None,
    )
