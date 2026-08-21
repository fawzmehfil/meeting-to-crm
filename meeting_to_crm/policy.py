from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import timedelta
from typing import Any

from meeting_to_crm.models import (
    CandidateContext,
    Certainty,
    DealDisposition,
    DecisionOutcome,
    DecisionResult,
    Evidence,
    EvidenceSource,
    MeetingClassification,
    MeetingDecision,
    MeetingWebhook,
    MutationOperation,
    MutationPlan,
    OperationKind,
    PlanDisposition,
)

MISSING = "__meeting_to_crm_missing__"
PROTECTED_FIELDS = {"id", "name", "email", "domain", "status", "pipeline", "stage", "amount"}
_WHITESPACE = re.compile(r"\s+")


def _normalized(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def internal_skip_decision(meeting: MeetingWebhook) -> DecisionResult:
    quote = ", ".join(attendee.email for attendee in meeting.attendees)
    decision = MeetingDecision(
        classification=MeetingClassification.internal,
        outcome=DecisionOutcome.skip,
        company_id=None,
        contact_ids=[],
        deal_id=None,
        deal_disposition=DealDisposition.none,
        certainty=Certainty.high,
        proposed_changes=[],
        evidence=[
            Evidence(
                source=EvidenceSource.attendee,
                quote=quote,
                supports="classification",
            )
        ],
        reason_codes=["ALL_ATTENDEES_INTERNAL"],
        ambiguities=[],
    )
    return DecisionResult(
        decision=decision,
        model="deterministic",
        prompt_version="deterministic.internal.v1",
    )


class PolicyEngine:
    def build_plan(
        self,
        meeting: MeetingWebhook,
        candidates: CandidateContext,
        result: DecisionResult,
        field_schema: dict[str, Any],
    ) -> MutationPlan:
        decision = result.decision
        reasons = list(decision.reason_codes)
        policy_notes: list[str] = []

        if decision.outcome == DecisionOutcome.skip or decision.classification in {
            MeetingClassification.internal,
            MeetingClassification.non_sales,
        }:
            reasons.append("SAFE_SKIP")
            return self._plan(
                meeting,
                result,
                PlanDisposition.skip,
                [],
                _unique(reasons),
                policy_notes,
            )

        fatal: list[str] = []
        if decision.outcome != DecisionOutcome.update_existing:
            fatal.append("MODEL_REQUESTED_REVIEW")
        if decision.classification != MeetingClassification.customer_sales:
            fatal.append("NOT_CONFIRMED_CUSTOMER_SALES")
        if decision.deal_disposition != DealDisposition.existing:
            fatal.append("EXISTING_DEAL_NOT_CONFIRMED")
        if decision.certainty != Certainty.high:
            fatal.append("CERTAINTY_BELOW_HIGH")
        if decision.ambiguities:
            fatal.append("UNRESOLVED_AMBIGUITY")

        if decision.company_id not in candidates.ids("company"):
            fatal.append("COMPANY_NOT_IN_CANDIDATES")
        if not decision.contact_ids:
            fatal.append("NO_RELEVANT_CONTACT")
        elif any(
            contact_id not in candidates.ids("contact") for contact_id in decision.contact_ids
        ):
            fatal.append("CONTACT_NOT_IN_CANDIDATES")
        if decision.deal_id not in candidates.ids("deal"):
            fatal.append("DEAL_NOT_IN_CANDIDATES")

        if decision.outcome == DecisionOutcome.update_existing:
            evidence_supports = {item.supports for item in decision.evidence}
            if "classification" not in evidence_supports:
                fatal.append("CLASSIFICATION_EVIDENCE_MISSING")
            if decision.company_id and f"company:{decision.company_id}" not in evidence_supports:
                fatal.append("COMPANY_EVIDENCE_MISSING")
            if any(
                f"contact:{contact_id}" not in evidence_supports
                for contact_id in decision.contact_ids
            ):
                fatal.append("CONTACT_EVIDENCE_MISSING")
            if decision.deal_id and f"deal:{decision.deal_id}" not in evidence_supports:
                fatal.append("DEAL_EVIDENCE_MISSING")
            if "deal_disposition" not in evidence_supports:
                fatal.append("DEAL_DISPOSITION_EVIDENCE_MISSING")

        deal = candidates.find("deal", decision.deal_id) if decision.deal_id is not None else None
        if deal is not None:
            if str(deal.record.get("status") or "").casefold() != "open":
                fatal.append("DEAL_NOT_OPEN")
            associated = {(ref.type, ref.id) for ref in deal.associations}
            if decision.company_id and ("company", decision.company_id) not in associated:
                fatal.append("DEAL_NOT_ASSOCIATED_WITH_COMPANY")
            if decision.contact_ids and not any(
                ("contact", contact_id) in associated for contact_id in decision.contact_ids
            ):
                fatal.append("DEAL_NOT_ASSOCIATED_WITH_ANY_SELECTED_CONTACT")

        for evidence in decision.evidence:
            if _normalized(evidence.quote) not in _normalized(meeting.evidence_text()):
                fatal.append("EVIDENCE_NOT_IN_PAYLOAD")
                break
            if not self._evidence_matches_declared_source(meeting, evidence):
                fatal.append("EVIDENCE_SOURCE_MISMATCH")
                break

        if fatal:
            reasons.extend(fatal)
            return self._plan(
                meeting,
                result,
                PlanDisposition.review,
                [],
                _unique(reasons),
                policy_notes,
            )

        assert deal is not None
        assert decision.deal_id is not None
        desired: dict[str, str] = {}
        deal_preconditions = {"pipeline": deal.record.get("pipeline", MISSING)}
        expected: dict[str, Any] = dict(deal_preconditions)
        seen_fields: set[str] = set()
        schema = field_schema.get("deal", {})

        for change in decision.proposed_changes:
            if change.entity_id != decision.deal_id or change.entity_type != "deal":
                fatal.append("CHANGE_TARGET_MISMATCH")
                continue
            if change.field in seen_fields:
                fatal.append("DUPLICATE_FIELD_CHANGE")
                continue
            seen_fields.add(change.field)
            if change.field in PROTECTED_FIELDS:
                fatal.append("PROTECTED_FIELD_PROPOSED")
                continue
            spec = schema.get(change.field)
            if not isinstance(spec, dict):
                fatal.append("UNSUPPORTED_FIELD")
                continue
            if not self._field_evidence_is_valid(
                change.field,
                change.evidence,
                meeting,
            ):
                fatal.append("FIELD_EVIDENCE_INVALID")
                continue

            try:
                value = self._validate_value(
                    change.field,
                    change.value,
                    spec,
                    meeting,
                    change.evidence,
                )
            except ValueError as exc:
                fatal.append("INVALID_FIELD_VALUE")
                policy_notes.append(f"{change.field}: {exc}")
                continue

            current = deal.record.get(change.field, MISSING)
            if spec.get("write") == "write-once" and current not in {MISSING, None}:
                policy_notes.append(f"{change.field}: existing write-once value preserved")
                continue
            if current == value:
                policy_notes.append(f"{change.field}: already has desired value")
                continue
            expected[change.field] = current
            desired[change.field] = value

        if fatal:
            reasons.extend(fatal)
            return self._plan(
                meeting,
                result,
                PlanDisposition.review,
                [],
                _unique(reasons),
                policy_notes,
            )

        operations: list[MutationOperation] = []
        if desired:
            operations.append(
                self._operation(
                    meeting.id,
                    1,
                    OperationKind.upsert_deal,
                    decision.deal_id,
                    expected,
                    desired,
                )
            )
        operations.append(
            self._operation(
                meeting.id,
                len(operations) + 1,
                OperationKind.add_note,
                decision.deal_id,
                deal_preconditions,
                {"body": self._note_body(meeting)},
            )
        )
        reasons.append("POLICY_APPROVED")
        return self._plan(
            meeting,
            result,
            PlanDisposition.apply,
            operations,
            _unique(reasons),
            policy_notes,
        )

    def _validate_value(
        self,
        field: str,
        raw: str,
        spec: dict[str, Any],
        meeting: MeetingWebhook,
        field_evidence: list[Evidence],
    ) -> str:
        value = raw.strip()
        if not value:
            raise ValueError("value must not be empty")
        if len(value) > 500:
            raise ValueError("value exceeds 500 characters")

        field_type = spec.get("type")
        if field_type == "enum":
            values = [str(item) for item in spec.get("values", [])]
            matches = [item for item in values if item.casefold() == value.casefold()]
            if not matches:
                raise ValueError(f"{value!r} is not an allowed enum value")
            value = matches[0]
        elif field_type == "date":
            try:
                parsed = meeting.occurred_at.date().fromisoformat(value)
            except ValueError as exc:
                raise ValueError("date must be ISO YYYY-MM-DD") from exc
            meeting_date = meeting.occurred_at.date()
            if parsed < meeting_date or parsed > meeting_date + timedelta(days=365):
                raise ValueError("date must be between the meeting date and one year later")
            value = parsed.isoformat()
        elif field_type != "string":
            raise ValueError(f"unsupported CRM field type: {field_type}")

        if field == "champion":
            participant_names = {attendee.name.casefold() for attendee in meeting.attendees}
            participant_names.update(turn.speaker.casefold() for turn in meeting.transcript)
            if value.casefold() not in participant_names:
                raise ValueError("champion must match an attendee or transcript speaker")
        if field == "risk":
            evidence = " ".join(item.quote for item in field_evidence).casefold()
            phrases = {f"{value.casefold()} risk", f"risk is {value.casefold()}"}
            if not any(phrase in evidence for phrase in phrases):
                raise ValueError("risk level must be explicit in cited evidence")
        return value

    def _field_evidence_is_valid(
        self,
        field: str,
        evidence: list[Evidence],
        meeting: MeetingWebhook,
    ) -> bool:
        if not evidence:
            return False
        expected_support = f"field:deal.{field}"
        for item in evidence:
            if item.supports != expected_support:
                return False
            if not self._evidence_matches_declared_source(meeting, item):
                return False
        return True

    @staticmethod
    def _evidence_matches_declared_source(
        meeting: MeetingWebhook,
        evidence: Evidence,
    ) -> bool:
        source_text = {
            EvidenceSource.title: meeting.name,
            EvidenceSource.notes: meeting.notes,
            EvidenceSource.attendee: "\n".join(
                f"{attendee.name} {attendee.email}" for attendee in meeting.attendees
            ),
            EvidenceSource.action_item: "\n".join(
                f"{item.text} {item.owner_email}" for item in meeting.action_items
            ),
            EvidenceSource.transcript: "\n".join(
                f"{turn.speaker} {turn.text}" for turn in meeting.transcript
            ),
        }[evidence.source]
        return _normalized(evidence.quote) in _normalized(source_text)

    def _plan(
        self,
        meeting: MeetingWebhook,
        result: DecisionResult,
        disposition: PlanDisposition,
        operations: list[MutationOperation],
        reasons: list[str],
        notes: list[str],
    ) -> MutationPlan:
        decision = result.decision
        return MutationPlan(
            meeting_id=meeting.id,
            payload_hash=meeting.payload_hash(),
            disposition=disposition,
            decision=decision,
            company_id=decision.company_id,
            contact_ids=decision.contact_ids,
            deal_id=decision.deal_id,
            operations=operations,
            reason_codes=reasons,
            policy_notes=notes,
            response_id=result.response_id,
            model=result.model,
            prompt_version=result.prompt_version,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    def _operation(
        self,
        meeting_id: str,
        sequence: int,
        kind: OperationKind,
        target_id: str,
        expected: dict[str, Any],
        desired: dict[str, Any],
    ) -> MutationOperation:
        identity = json.dumps(
            {
                "meeting_id": meeting_id,
                "kind": kind.value,
                "target_id": target_id,
                "desired": desired,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        operation_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return MutationOperation(
            operation_id=operation_id,
            sequence=sequence,
            kind=kind,
            target_id=target_id,
            expected_before=expected,
            desired=desired,
        )

    def _note_body(self, meeting: MeetingWebhook) -> str:
        actions = "\n".join(
            f"- {item.text} (owner: {item.owner_email})" for item in meeting.action_items
        )
        if not actions:
            actions = "- None"
        return (
            f"Meeting: {meeting.name}\n"
            f"Occurred: {meeting.occurred_at.isoformat()}\n"
            f"Summary: {meeting.notes}\n"
            f"Action items:\n{actions}\n"
            f"Source meeting: {meeting.id}\n"
            f"[meeting-to-crm:{meeting.id}]"
        )
