from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_to_crm.models import MeetingWebhook

ROOT = Path(__file__).resolve().parents[1]


def _payload() -> dict:
    return json.loads((ROOT / "fixtures" / "m01.json").read_text(encoding="utf-8"))


def test_valid_fixture_has_stable_hash() -> None:
    first = MeetingWebhook.model_validate(_payload())
    second = MeetingWebhook.model_validate(_payload())

    assert first.payload_hash() == second.payload_hash()
    assert len(first.payload_hash()) == 64


def test_extra_webhook_field_is_rejected() -> None:
    payload = _payload()
    payload["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        MeetingWebhook.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("duration_seconds",), -1),
        (("transcript", 0, "timestamp"), -1),
    ],
)
def test_negative_values_are_rejected(path: tuple, value: int) -> None:
    payload = _payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        MeetingWebhook.model_validate(payload)


def test_timezone_naive_occurred_at_is_rejected() -> None:
    payload = _payload()
    payload["occurred_at"] = "2026-08-11T14:00:00"

    with pytest.raises(ValidationError, match="UTC offset"):
        MeetingWebhook.model_validate(payload)
