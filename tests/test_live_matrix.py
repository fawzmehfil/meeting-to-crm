from __future__ import annotations

import io
import json
import os

import pytest

from meeting_to_crm.candidates import CandidateResolver
from meeting_to_crm.decision import LunaDecisionEngine
from meeting_to_crm.executor import Executor
from meeting_to_crm.journal import Journal
from meeting_to_crm.models import ProcessingStatus
from meeting_to_crm.observability import EventLogger
from meeting_to_crm.policy import PolicyEngine
from meeting_to_crm.workflow import Workflow


@pytest.mark.live_matrix
def test_luna_full_fixture_matrix(crm_factory, config_factory, load_meeting) -> None:
    """Opt-in semantic evaluation: up to 13 paid Luna calls against all fixtures."""
    if os.environ.get("RUN_LIVE_FIXTURE_MATRIX") != "1":
        pytest.skip("set RUN_LIVE_FIXTURE_MATRIX=1 to confirm the paid full-matrix run")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY is not set")

    store, client = crm_factory()
    initial_entity_counts = {kind: len(records) for kind, records in store.entities.items()}
    config = config_factory(openai_api_key=api_key)
    journal = Journal(config.state_path)
    logger = EventLogger("ERROR", io.StringIO())
    workflow = Workflow(
        config,
        client,
        CandidateResolver(client, config),
        LunaDecisionEngine(api_key, config.openai_model),
        PolicyEngine(),
        journal,
        Executor(client, journal, logger),
        logger,
    )
    expected = {
        "m01.json": ProcessingStatus.completed,
        "m02.json": ProcessingStatus.review,
        "m03.json": ProcessingStatus.review,
        "m04.json": ProcessingStatus.skipped,
        "m05.json": ProcessingStatus.skipped,
        "m06.json": ProcessingStatus.completed,
        "m07.json": ProcessingStatus.completed,
        "m08.json": ProcessingStatus.completed,
        "m09.json": ProcessingStatus.review,
        "m10.json": ProcessingStatus.review,
        "m11.json": ProcessingStatus.completed,
        "m12.json": ProcessingStatus.completed,
        "m13.json": ProcessingStatus.review,
        "m14.json": ProcessingStatus.duplicate,
        "m15.json": ProcessingStatus.completed,
    }
    try:
        results = {name: workflow.process(load_meeting(name), apply=True) for name in expected}
    finally:
        journal.close()

    mismatches = {
        name: {
            "expected": expected[name].value,
            "actual": result.status.value,
            "reason_codes": result.reason_codes,
            "company_id": result.company_id,
            "contact_ids": result.contact_ids,
            "deal_id": result.deal_id,
            "error": result.error,
        }
        for name, result in results.items()
        if result.status != expected[name]
    }
    assert not mismatches, json.dumps(mismatches, indent=2, sort_keys=True)
    assert {kind: len(records) for kind, records in store.entities.items()} == (
        initial_entity_counts
    )
