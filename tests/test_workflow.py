from __future__ import annotations

import io
from copy import deepcopy

from meeting_to_crm.candidates import CandidateResolver
from meeting_to_crm.executor import Executor
from meeting_to_crm.journal import Journal
from meeting_to_crm.models import ProcessingStatus
from meeting_to_crm.observability import EventLogger
from meeting_to_crm.policy import PolicyEngine
from meeting_to_crm.workflow import Workflow
from tests.conftest import FakeDecisionEngine
from tests.decisions import fixture_decisions


def _workflow(config, client, decisions, *, max_attempts: int = 3):
    logger = EventLogger("DEBUG", io.StringIO())
    journal = Journal(config.state_path)
    engine = FakeDecisionEngine(decisions)
    executor = Executor(
        client,
        journal,
        logger,
        max_attempts=max_attempts,
        sleep=lambda _: None,
    )
    workflow = Workflow(
        config,
        client,
        CandidateResolver(client, config),
        engine,
        PolicyEngine(),
        journal,
        executor,
        logger,
    )
    return workflow, journal, engine


def test_internal_meeting_skips_model_and_crm_writes(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    workflow, journal, engine = _workflow(config_factory(), client, {})
    try:
        result = workflow.process(load_meeting("m04.json"), apply=True)
    finally:
        journal.close()

    assert result.status == ProcessingStatus.skipped
    assert not engine.calls
    assert store.write_count == 0


def test_non_sales_meeting_skips_writes(crm_factory, config_factory, load_meeting) -> None:
    store, client = crm_factory()
    decisions = fixture_decisions()
    workflow, journal, _ = _workflow(config_factory(), client, decisions)
    try:
        result = workflow.process(load_meeting("m05.json"), apply=True)
    finally:
        journal.close()

    assert result.status == ProcessingStatus.skipped
    assert store.write_count == 0


def test_existing_deal_is_updated_and_noted(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        result = workflow.process(load_meeting("m01.json"), apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert result.status == ProcessingStatus.completed
    assert deal["next_step"] == "Deeper product demo"
    assert deal["next_step_date"] == "2026-08-13"
    assert deal["competitor"] == "Clari"
    assert len(deal["notes"]) == 1


def test_duplicate_changed_payload_performs_no_second_writes(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        first = workflow.process(load_meeting("m01.json"), apply=True)
        writes_after_first = store.write_count
        duplicate = workflow.process(load_meeting("m14.json"), apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert first.status == ProcessingStatus.completed
    assert duplicate.status == ProcessingStatus.duplicate
    assert "DUPLICATE_ID_PAYLOAD_CHANGED" in duplicate.reason_codes
    assert store.write_count == writes_after_first
    assert len(deal["notes"]) == 1


def test_one_off_partial_failure_retries_safely(crm_factory, config_factory, load_meeting) -> None:
    store, client = crm_factory(fail_nth_write=2)
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        result = workflow.process(load_meeting("m01.json"), apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert result.status == ProcessingStatus.completed
    assert store.write_count == 3
    assert len(deal["notes"]) == 1
    assert result.operations[1].attempts == 2


def test_partial_run_resumes_only_failed_operation(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory(fail_nth_write=2)
    config = config_factory()
    workflow, journal, _ = _workflow(config, client, fixture_decisions(), max_attempts=1)
    try:
        first = workflow.process(load_meeting("m01.json"), apply=True)
        second = workflow.process(load_meeting("m01.json"), apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert first.status == ProcessingStatus.partial
    assert second.status == ProcessingStatus.completed
    assert store.write_count == 3
    assert second.operations[0].attempts == 1
    assert second.operations[1].attempts == 2
    assert len(deal["notes"]) == 1


def test_wrong_deals_and_contacts_remain_unchanged(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    config = config_factory()
    workflow, journal, _ = _workflow(config, client, fixture_decisions())
    renewal_before = deepcopy(client.get("deal", "deal_5"))
    glacier_before = deepcopy(client.get("deal", "deal_9"))
    contacts_before = {
        "contact_10": client.get("contact", "contact_10"),
        "contact_11": client.get("contact", "contact_11"),
    }
    try:
        cobalt = workflow.process(load_meeting("m08.json"), apply=True)
        keystone = workflow.process(load_meeting("m11.json"), apply=True)
    finally:
        journal.close()

    assert cobalt.status == ProcessingStatus.completed
    assert cobalt.deal_id == "deal_6"
    assert client.get("deal", "deal_5") == renewal_before
    assert keystone.status == ProcessingStatus.completed
    assert keystone.deal_id == "deal_10"
    assert client.get("deal", "deal_9") == glacier_before
    assert client.get("contact", "contact_10") == contacts_before["contact_10"]
    assert client.get("contact", "contact_11") == contacts_before["contact_11"]


def test_contractor_does_not_create_quill_deal(crm_factory, config_factory, load_meeting) -> None:
    store, client = crm_factory()
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    before = len(store.entities["deal"])
    try:
        result = workflow.process(load_meeting("m12.json"), apply=True)
    finally:
        journal.close()

    assert result.status == ProcessingStatus.completed
    assert result.deal_id == "deal_1"
    assert len(store.entities["deal"]) == before


def test_new_evaluation_reviews_without_resurrecting_closed_deal(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    before = deepcopy(client.get("deal", "deal_8"))
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        result = workflow.process(load_meeting("m13.json"), apply=True)
    finally:
        journal.close()

    assert result.status == ProcessingStatus.review
    assert client.get("deal", "deal_8") == before
    assert store.write_count == 0


def test_explicit_next_step_date_and_champion_are_written(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        result = workflow.process(load_meeting("m15.json"), apply=True)
        deal = client.get("deal", "deal_7")
    finally:
        journal.close()

    assert result.status == ProcessingStatus.completed
    assert deal["next_step"] == "Security working session"
    assert deal["next_step_date"] == "2026-09-03"
    assert deal["champion"] == "Jane Cho"


def test_apply_aborts_when_target_changed_after_plan(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        planned = workflow.process(meeting, apply=False)
        client.upsert("deal", {"id": "deal_1", "next_step": "External owner changed this"})
        result = workflow.process(meeting, apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert planned.status == ProcessingStatus.planned
    assert result.status == ProcessingStatus.review
    assert "PRECONDITION_CHANGED" in result.reason_codes
    assert deal["next_step"] == "External owner changed this"
    assert not deal["notes"]


def test_apply_refuses_all_writes_when_planned_deal_is_closed(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    meeting = load_meeting("m01.json")
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        planned = workflow.process(meeting, apply=False)
        client.upsert("deal", {"id": "deal_1", "status": "closed-lost"})
        writes_before_apply = store.write_count
        result = workflow.process(meeting, apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert planned.status == ProcessingStatus.planned
    assert result.status == ProcessingStatus.review
    assert "DEAL_NO_LONGER_OPEN" in result.reason_codes
    assert store.write_count == writes_before_apply
    assert "next_step" not in deal
    assert not deal["notes"]


def test_apply_refuses_note_only_plan_when_deal_is_closed(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    meeting = load_meeting("m06.json")
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        planned = workflow.process(meeting, apply=False)
        assert [operation.kind.value for operation in planned.operations] == ["add_note"]
        client.upsert("deal", {"id": "deal_1", "status": "closed-lost"})
        writes_before_apply = store.write_count
        result = workflow.process(meeting, apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert result.status == ProcessingStatus.review
    assert "DEAL_NO_LONGER_OPEN" in result.reason_codes
    assert store.write_count == writes_before_apply
    assert not deal["notes"]


def test_apply_refuses_note_only_plan_when_deal_pipeline_changes(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    meeting = load_meeting("m06.json")
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        planned = workflow.process(meeting, apply=False)
        assert [operation.kind.value for operation in planned.operations] == ["add_note"]
        client.upsert("deal", {"id": "deal_1", "pipeline": "enterprise"})
        writes_before_apply = store.write_count
        result = workflow.process(meeting, apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert result.status == ProcessingStatus.review
    assert "DEAL_PIPELINE_CHANGED" in result.reason_codes
    assert store.write_count == writes_before_apply
    assert not deal["notes"]


def test_pipeline_change_blocks_note_after_recovering_prior_upsert(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    meeting = load_meeting("m01.json")
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        workflow.process(meeting, apply=False)
        plan = journal.load_plan(meeting.id)
        assert plan is not None
        client.upsert(
            "deal",
            {
                "id": "deal_1",
                **plan.operations[0].desired,
                "pipeline": "enterprise",
            },
        )
        writes_before_apply = store.write_count
        result = workflow.process(meeting, apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert result.status == ProcessingStatus.review
    assert "DEAL_PIPELINE_CHANGED" in result.reason_codes
    assert store.write_count == writes_before_apply
    assert not deal["notes"]


def test_crash_gap_reconciles_target_state_without_duplicates(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    meeting = load_meeting("m01.json")
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
    try:
        workflow.process(meeting, apply=False)
        plan = journal.load_plan(meeting.id)
        assert plan is not None
        client.upsert("deal", {"id": "deal_1", **plan.operations[0].desired})
        client.add_note(
            {"type": "deal", "id": "deal_1"},
            str(plan.operations[1].desired["body"]),
        )
        writes_before_recovery = store.write_count
        result = workflow.process(meeting, apply=True)
        deal = client.get("deal", "deal_1")
    finally:
        journal.close()

    assert result.status == ProcessingStatus.completed
    assert store.write_count == writes_before_recovery
    assert len(deal["notes"]) == 1


def test_all_fifteen_fixtures_follow_the_supported_slice(
    crm_factory, config_factory, load_meeting
) -> None:
    store, client = crm_factory()
    initial_entity_counts = {kind: len(records) for kind, records in store.entities.items()}
    workflow, journal, _ = _workflow(config_factory(), client, fixture_decisions())
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
        actual = {
            name: workflow.process(load_meeting(name), apply=True).status for name in expected
        }
    finally:
        journal.close()

    assert actual == expected
    assert {kind: len(records) for kind, records in store.entities.items()} == (
        initial_entity_counts
    )
    assert store.write_count == 9
    assert len(client.get("deal", "deal_1")["notes"]) == 3
