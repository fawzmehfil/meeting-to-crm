from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Attendee(StrictModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)

    @field_validator("name", "email")
    @classmethod
    def strip_value(cls, value: str) -> str:
        return value.strip()


class ActionItem(StrictModel):
    text: str = Field(min_length=1)
    owner_email: str = Field(min_length=3)

    @field_validator("text", "owner_email")
    @classmethod
    def strip_value(cls, value: str) -> str:
        return value.strip()


class TranscriptTurn(StrictModel):
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)
    timestamp: int = Field(ge=0)

    @field_validator("speaker", "text")
    @classmethod
    def strip_value(cls, value: str) -> str:
        return value.strip()


class MeetingWebhook(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    occurred_at: datetime
    duration_seconds: int = Field(ge=0)
    attendees: list[Attendee]
    notes: str
    action_items: list[ActionItem]
    transcript: list[TranscriptTurn]

    @field_validator("id", "name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return value.strip()

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a UTC offset")
        return value

    def canonical_json(self) -> str:
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def payload_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def evidence_text(self) -> str:
        parts = [self.name, self.notes]
        parts.extend(f"{a.name} {a.email}" for a in self.attendees)
        parts.extend(f"{a.text} {a.owner_email}" for a in self.action_items)
        parts.extend(f"{t.speaker} {t.text}" for t in self.transcript)
        return "\n".join(parts)


class EntityRef(StrictModel):
    type: Literal["company", "contact", "deal"]
    id: str


class CandidateEntity(StrictModel):
    type: Literal["company", "contact", "deal"]
    id: str
    record: dict[str, Any]
    discovered_by: list[str] = Field(default_factory=list)
    associations: list[EntityRef] = Field(default_factory=list)


class CandidateContext(StrictModel):
    companies: list[CandidateEntity] = Field(default_factory=list)
    contacts: list[CandidateEntity] = Field(default_factory=list)
    deals: list[CandidateEntity] = Field(default_factory=list)

    def ids(self, entity_type: str) -> set[str]:
        mapping = {
            "company": self.companies,
            "contact": self.contacts,
            "deal": self.deals,
        }
        return {entity.id for entity in mapping[entity_type]}

    def find(self, entity_type: str, entity_id: str) -> CandidateEntity | None:
        mapping = {
            "company": self.companies,
            "contact": self.contacts,
            "deal": self.deals,
        }
        return next((item for item in mapping[entity_type] if item.id == entity_id), None)


class EvidenceSource(str, Enum):
    attendee = "attendee"
    title = "title"
    notes = "notes"
    action_item = "action_item"
    transcript = "transcript"


class Evidence(StrictModel):
    source: EvidenceSource
    quote: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "Verbatim substring from the declared source. For attendees, use the plain name or "
            "email value, never a serialized JSON object."
        ),
    )
    supports: str = Field(
        min_length=1,
        max_length=100,
        description="Canonical policy support label required by the system prompt.",
    )


class ProposedFieldChange(StrictModel):
    entity_type: Literal["deal"]
    entity_id: str
    field: str
    value: str
    evidence: list[Evidence] = Field(min_length=1)


class MeetingClassification(str, Enum):
    customer_sales = "customer_sales"
    internal = "internal"
    non_sales = "non_sales"
    unclear = "unclear"


class DecisionOutcome(str, Enum):
    update_existing = "update_existing"
    review = "review"
    skip = "skip"


class DealDisposition(str, Enum):
    existing = "existing"
    new = "new"
    none = "none"
    unclear = "unclear"


class Certainty(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class MeetingDecision(StrictModel):
    classification: MeetingClassification
    outcome: DecisionOutcome
    company_id: str | None
    contact_ids: list[str]
    deal_id: str | None
    deal_disposition: DealDisposition
    certainty: Certainty
    proposed_changes: list[ProposedFieldChange]
    evidence: list[Evidence] = Field(min_length=1)
    reason_codes: list[str]
    ambiguities: list[str] = Field(
        description=(
            "Only unresolved uncertainty that makes the selected entities or a proposed value "
            "unsafe. Must be empty when uncertainty was resolved, is merely contextual, or affects "
            "a field omitted from proposed_changes."
        )
    )

    def all_evidence(self) -> list[Evidence]:
        combined = list(self.evidence)
        seen = {(item.source.value, item.quote, item.supports) for item in combined}
        for change in self.proposed_changes:
            for item in change.evidence:
                key = (item.source.value, item.quote, item.supports)
                if key not in seen:
                    combined.append(item)
                    seen.add(key)
        return combined


class DecisionResult(StrictModel):
    decision: MeetingDecision
    response_id: str | None = None
    model: str
    prompt_version: str
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class OperationKind(str, Enum):
    upsert_deal = "upsert_deal"
    add_note = "add_note"


class MutationOperation(StrictModel):
    operation_id: str
    sequence: int
    kind: OperationKind
    target_id: str
    expected_before: dict[str, Any]
    desired: dict[str, Any]


class PlanDisposition(str, Enum):
    apply = "apply"
    skip = "skip"
    review = "review"


class MutationPlan(StrictModel):
    meeting_id: str
    payload_hash: str
    disposition: PlanDisposition
    decision: MeetingDecision
    company_id: str | None
    contact_ids: list[str]
    deal_id: str | None
    operations: list[MutationOperation]
    reason_codes: list[str]
    policy_notes: list[str]
    response_id: str | None = None
    model: str
    prompt_version: str
    duration_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProcessingStatus(str, Enum):
    planned = "planned"
    completed = "completed"
    skipped = "skipped"
    review = "review"
    duplicate = "duplicate"
    partial = "partial"
    error = "error"


class OperationResult(StrictModel):
    operation_id: str
    kind: OperationKind
    target_id: str
    status: str
    attempts: int = 0
    error: str | None = None


class ProcessingResult(StrictModel):
    meeting_id: str
    meeting_name: str
    status: ProcessingStatus
    classification: MeetingClassification | None = None
    company_id: str | None = None
    contact_ids: list[str] = Field(default_factory=list)
    deal_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    operations: list[OperationResult] = Field(default_factory=list)
    error: str | None = None
