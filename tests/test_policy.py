from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from meeting_to_crm.candidates import CandidateResolver
from meeting_to_crm.models import (
    Certainty,
    DecisionResult,
    Evidence,
    EvidenceSource,
    PlanDisposition,
    ProposedFieldChange,
)
from meeting_to_crm.policy import PolicyEngine
from tests.decisions import fixture_decisions


def _plan(crm_factory, config_factory, load_meeting, fixture: str, decision_key: str | None = None):
    _, client = crm_factory()
    meeting = load_meeting(fixture)
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[decision_key or meeting.id]
    result = DecisionResult(
        decision=decision,
        model="fake-luna",
        prompt_version="test.v1",
    )
    return PolicyEngine().build_plan(meeting, context, result, client.field_schema())


def test_policy_canonicalizes_enum_and_builds_two_operations(
    crm_factory, config_factory, load_meeting
) -> None:
    plan = _plan(crm_factory, config_factory, load_meeting, "m01.json")

    assert plan.disposition == PlanDisposition.apply
    assert [operation.kind.value for operation in plan.operations] == [
        "upsert_deal",
        "add_note",
    ]
    assert plan.operations[0].desired["competitor"] == "Clari"
    assert plan.operations[0].desired["next_step_date"] == "2026-08-13"
    assert plan.operations[0].expected_before["pipeline"] == "default"
    assert plan.operations[1].expected_before["pipeline"] == "default"
    assert plan.operations[1].desired["body"].count("[meeting-to-crm:") == 1


def test_positional_field_evidence_is_rejected_by_the_decision_schema() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        ProposedFieldChange.model_validate(
            {
                "entity_type": "deal",
                "entity_id": "deal_1",
                "field": "competitor",
                "value": "Clari",
                "evidence_indices": [4],
            }
        )


def test_field_requires_correctly_tagged_embedded_evidence(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    next_step = next(change for change in decision.proposed_changes if change.field == "next_step")
    next_step.evidence = [decision.evidence[1]]

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "FIELD_EVIDENCE_INVALID" in plan.reason_codes


def test_hallucinated_embedded_field_evidence_forces_review(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    next_step = next(change for change in decision.proposed_changes if change.field == "next_step")
    next_step.evidence = [
        next_step.evidence[0].model_copy(
            update={"quote": "This field evidence never appeared in the meeting."}
        )
    ]

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "FIELD_EVIDENCE_INVALID" in plan.reason_codes


def test_closed_lost_deal_cannot_be_selected(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m13.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(
        update={
            "outcome": "update_existing",
            "deal_disposition": "existing",
            "deal_id": "deal_8",
        }
    )
    result = DecisionResult(decision=decision, model="fake", prompt_version="test")

    plan = PolicyEngine().build_plan(meeting, context, result, client.field_schema())

    assert plan.disposition == PlanDisposition.review
    assert "DEAL_NOT_OPEN" in plan.reason_codes
    assert not plan.operations


def test_shared_contact_cannot_authorize_deal_from_wrong_selected_company(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m07.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    decision.company_id = "company_2"
    decision.contact_ids = ["contact_2"]
    decision.deal_id = "deal_1"
    decision.evidence = [
        Evidence(source=EvidenceSource.notes, quote=meeting.notes, supports=target)
        for target in (
            "classification",
            "company:company_2",
            "contact:contact_2",
            "deal:deal_1",
            "deal_disposition",
        )
    ]

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "DEAL_NOT_ASSOCIATED_WITH_COMPANY" in plan.reason_codes
    assert not plan.operations


def test_selected_entities_require_exact_in_payload_evidence(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    decision.proposed_changes = []
    decision.evidence = []

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert {
        "CLASSIFICATION_EVIDENCE_MISSING",
        "COMPANY_EVIDENCE_MISSING",
        "CONTACT_EVIDENCE_MISSING",
        "DEAL_EVIDENCE_MISSING",
        "DEAL_DISPOSITION_EVIDENCE_MISSING",
    }.issubset(plan.reason_codes)
    assert not plan.operations


def test_hallucinated_evidence_forces_review(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = deepcopy(fixture_decisions()[meeting.id])
    decision.evidence[0].quote = "This sentence never appeared in the meeting."

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "EVIDENCE_NOT_IN_PAYLOAD" in plan.reason_codes


def test_evidence_must_match_its_declared_source(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    decision.evidence[0].source = EvidenceSource.transcript

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "EVIDENCE_SOURCE_MISMATCH" in plan.reason_codes


def test_serialized_attendee_object_is_not_verbatim_attendee_evidence(
    crm_factory, config_factory, load_meeting
) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    contact_evidence = next(
        item for item in decision.evidence if item.supports == "contact:contact_1"
    )
    contact_evidence.quote = '{"email":"nina@apexrobotics.com","name":"Nina Volk"}'

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "EVIDENCE_NOT_IN_PAYLOAD" in plan.reason_codes


def test_protected_field_forces_review(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    decision.proposed_changes = [
        ProposedFieldChange(
            entity_type="deal",
            entity_id="deal_1",
            field="status",
            value="closed-won",
            evidence=[decision.evidence[0].model_copy(update={"supports": "field:deal.status"})],
        )
    ]

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "PROTECTED_FIELD_PROPOSED" in plan.reason_codes


def test_low_certainty_forces_review(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(update={"certainty": Certainty.medium})

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "CERTAINTY_BELOW_HIGH" in plan.reason_codes


@pytest.mark.parametrize("value", ["2026-08-10", "2028-01-01", "Thursday"])
def test_invalid_dates_force_review(crm_factory, config_factory, load_meeting, value: str) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m01.json")
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id].model_copy(deep=True)
    next_step_date = next(
        change for change in decision.proposed_changes if change.field == "next_step_date"
    )
    next_step_date.value = value

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.review
    assert "INVALID_FIELD_VALUE" in plan.reason_codes


def test_existing_write_once_value_is_preserved(crm_factory, config_factory, load_meeting) -> None:
    _, client = crm_factory()
    meeting = load_meeting("m15.json")
    client.upsert("deal", {"id": "deal_7", "champion": "Existing Champion"})
    context = CandidateResolver(client, config_factory()).resolve(meeting)
    decision = fixture_decisions()[meeting.id]

    plan = PolicyEngine().build_plan(
        meeting,
        context,
        DecisionResult(decision=decision, model="fake", prompt_version="test"),
        client.field_schema(),
    )

    assert plan.disposition == PlanDisposition.apply
    assert plan.operations[0].desired.get("champion") is None
    assert any("write-once" in note for note in plan.policy_notes)
