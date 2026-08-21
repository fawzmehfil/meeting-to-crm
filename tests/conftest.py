from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ergo_crm import ErgoCRMClient
from ergo_crm.server import make_server
from ergo_crm.store import Store
from meeting_to_crm.config import Config
from meeting_to_crm.models import DecisionResult, MeetingDecision, MeetingWebhook

ROOT = Path(__file__).resolve().parents[1]


class FakeDecisionEngine:
    def __init__(self, decisions: dict[str, MeetingDecision]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    def decide(
        self,
        meeting: MeetingWebhook,
        candidates: Any,
        field_schema: dict[str, Any],
    ) -> DecisionResult:
        self.calls.append(meeting.id)
        return DecisionResult(
            decision=self.decisions[meeting.id],
            model="fake-luna",
            prompt_version="test.v1",
        )


@pytest.fixture
def load_meeting() -> Callable[[str], MeetingWebhook]:
    def load(name: str) -> MeetingWebhook:
        payload = json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))
        return MeetingWebhook.model_validate(payload)

    return load


@pytest.fixture
def config_factory(tmp_path: Path) -> Callable[..., Config]:
    def make(**overrides: Any) -> Config:
        values: dict[str, Any] = {
            "crm_url": "http://unused",
            "openai_api_key": "test-key",
            "openai_model": "gpt-5.6-luna",
            "state_path": tmp_path / "state.sqlite3",
            "internal_domains": frozenset({"helios.example"}),
            "personal_domains": frozenset(
                {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"}
            ),
            "log_level": "ERROR",
        }
        values.update(overrides)
        return Config(**values)

    return make


@pytest.fixture
def crm_factory(monkeypatch: pytest.MonkeyPatch):
    servers: list[tuple[Any, threading.Thread]] = []
    monkeypatch.setenv("ERGO_CRM_QUIET", "1")

    def make(fail_nth_write: int = 0) -> tuple[Store, ErgoCRMClient]:
        store = Store.from_seed_file(fail_nth_write=fail_nth_write)
        server = make_server("127.0.0.1", 0, store=store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        client = ErgoCRMClient(f"http://127.0.0.1:{server.server_port}")
        return store, client

    yield make

    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()
