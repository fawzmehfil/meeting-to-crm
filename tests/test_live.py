from __future__ import annotations

import os

import pytest

from meeting_to_crm.candidates import CandidateResolver
from meeting_to_crm.decision import LunaDecisionEngine
from meeting_to_crm.models import MeetingDecision


@pytest.mark.live
def test_luna_returns_structured_decision(crm_factory, config_factory, load_meeting) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY is not set")
    _, client = crm_factory()
    config = config_factory(openai_api_key=api_key)
    meeting = load_meeting("m08.json")
    candidates = CandidateResolver(client, config).resolve(meeting)

    result = LunaDecisionEngine(api_key).decide(meeting, candidates, client.field_schema())

    assert isinstance(result.decision, MeetingDecision)
    assert result.decision.deal_id in {deal.id for deal in candidates.deals} | {None}
