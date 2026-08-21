from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _domains(value: str) -> frozenset[str]:
    return frozenset(item.strip().casefold() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    crm_url: str
    openai_api_key: str | None
    openai_model: str
    state_path: Path
    internal_domains: frozenset[str]
    personal_domains: frozenset[str]
    log_level: str

    @classmethod
    def from_env(cls) -> Config:
        key = os.environ.get("OPENAI_API_KEY")
        return cls(
            crm_url=(os.environ.get("ERGO_CRM_URL") or "http://127.0.0.1:8787").rstrip("/"),
            openai_api_key=key if key else None,
            openai_model=os.environ.get("OPENAI_MODEL") or "gpt-5.6-luna",
            state_path=Path(
                os.environ.get("MEETING_CRM_STATE_PATH") or ".meeting_to_crm/state.sqlite3"
            ),
            internal_domains=_domains(
                os.environ.get("HELIOS_INTERNAL_DOMAINS") or "helios.example"
            ),
            personal_domains=_domains(
                os.environ.get("PERSONAL_EMAIL_DOMAINS")
                or "gmail.com,yahoo.com,outlook.com,hotmail.com,icloud.com"
            ),
            log_level=(os.environ.get("LOG_LEVEL") or "INFO").upper(),
        )
