from __future__ import annotations

from typing import Any

from ergo_crm import ErgoCRMClient
from meeting_to_crm.candidates import CandidateResolver, all_attendees_internal
from meeting_to_crm.config import Config
from meeting_to_crm.decision import DecisionEngine
from meeting_to_crm.executor import Executor
from meeting_to_crm.journal import Journal
from meeting_to_crm.models import (
    CandidateContext,
    MutationPlan,
    OperationResult,
    PlanDisposition,
    ProcessingResult,
    ProcessingStatus,
)
from meeting_to_crm.observability import EventLogger
from meeting_to_crm.policy import PolicyEngine, internal_skip_decision


class Workflow:
    def __init__(
        self,
        config: Config,
        client: ErgoCRMClient,
        resolver: CandidateResolver,
        decision_engine: DecisionEngine,
        policy: PolicyEngine,
        journal: Journal,
        executor: Executor,
        logger: EventLogger,
    ) -> None:
        self.config = config
        self.client = client
        self.resolver = resolver
        self.decision_engine = decision_engine
        self.policy = policy
        self.journal = journal
        self.executor = executor
        self.logger = logger

    def process(self, meeting: Any, *, apply: bool) -> ProcessingResult:
        payload_hash = meeting.payload_hash()
        self.logger.emit(
            "meeting_received",
            meeting_id=meeting.id,
            payload_hash=payload_hash,
            mode="apply" if apply else "plan",
        )
        existing = self.journal.get_meeting(meeting.id)
        if existing:
            prior = self.journal.load_plan(meeting.id)
            if existing["payload_hash"] != payload_hash:
                if existing["status"] in {"completed", "skipped", "review"}:
                    self.logger.emit(
                        "duplicate_detected",
                        level="WARNING",
                        meeting_id=meeting.id,
                        payload_changed=True,
                    )
                    return self._duplicate_result(meeting, prior, changed=True)
                self.journal.mark_meeting(
                    meeting.id,
                    "review",
                    "payload changed while a prior plan was in progress",
                )
                return self._review_changed_payload(meeting, prior)

            if prior is not None:
                if existing["status"] == "completed":
                    self.logger.emit(
                        "duplicate_detected", meeting_id=meeting.id, payload_changed=False
                    )
                    return self._duplicate_result(meeting, prior, changed=False)
                if existing["status"] == "skipped":
                    return self._result_from_plan(meeting.name, prior, ProcessingStatus.skipped)
                if existing["status"] == "review":
                    return self._result_from_plan(meeting.name, prior, ProcessingStatus.review)
                if not apply:
                    status = (
                        ProcessingStatus.partial
                        if existing["status"] == "partial"
                        else ProcessingStatus.planned
                    )
                    return self._result_from_plan(meeting.name, prior, status)
                return self.executor.apply(prior, meeting.name)

        self.journal.start_meeting(meeting.id, payload_hash)
        try:
            if all_attendees_internal(meeting, self.config.internal_domains):
                context = CandidateContext()
                decision = internal_skip_decision(meeting)
                schema: dict[str, Any] = {}
            else:
                context = self.resolver.resolve(meeting)
                self.logger.emit(
                    "candidates_resolved",
                    meeting_id=meeting.id,
                    companies=[item.id for item in context.companies],
                    contacts=[item.id for item in context.contacts],
                    deals=[item.id for item in context.deals],
                )
                schema = self.client.field_schema()
                decision = self.decision_engine.decide(meeting, context, schema)
                self.logger.emit(
                    "decision_completed",
                    meeting_id=meeting.id,
                    outcome=decision.decision.outcome.value,
                    classification=decision.decision.classification.value,
                    company_id=decision.decision.company_id,
                    deal_id=decision.decision.deal_id,
                    model=decision.model,
                    response_id=decision.response_id,
                    duration_ms=decision.duration_ms,
                    input_tokens=decision.input_tokens,
                    output_tokens=decision.output_tokens,
                )
            plan = self.policy.build_plan(meeting, context, decision, schema)
        except Exception as exc:
            self.journal.mark_meeting(meeting.id, "partial", str(exc))
            self.logger.emit(
                "decision_rejected",
                level="ERROR",
                meeting_id=meeting.id,
                error=str(exc),
            )
            return ProcessingResult(
                meeting_id=meeting.id,
                meeting_name=meeting.name,
                status=ProcessingStatus.error,
                reason_codes=["PLANNING_FAILED"],
                error=str(exc),
            )

        if plan.disposition == PlanDisposition.skip:
            self.journal.save_plan(plan, "skipped")
            self.logger.emit(
                "meeting_skipped", meeting_id=meeting.id, reason_codes=plan.reason_codes
            )
            return self._result_from_plan(meeting.name, plan, ProcessingStatus.skipped)
        if plan.disposition == PlanDisposition.review:
            self.journal.save_plan(plan, "review")
            self.logger.emit(
                "meeting_review_required",
                level="WARNING",
                meeting_id=meeting.id,
                reason_codes=plan.reason_codes,
            )
            return self._result_from_plan(meeting.name, plan, ProcessingStatus.review)

        self.journal.save_plan(plan, "planned")
        self.logger.emit(
            "plan_created",
            meeting_id=meeting.id,
            deal_id=plan.deal_id,
            operations=[operation.kind.value for operation in plan.operations],
        )
        if not apply:
            return self._result_from_plan(meeting.name, plan, ProcessingStatus.planned)
        return self.executor.apply(plan, meeting.name)

    def _result_from_plan(
        self, meeting_name: str, plan: MutationPlan, status: ProcessingStatus
    ) -> ProcessingResult:
        states = {
            row["operation_id"]: row for row in self.journal.operation_states(plan.meeting_id)
        }
        operations = [
            OperationResult(
                operation_id=operation.operation_id,
                kind=operation.kind,
                target_id=operation.target_id,
                status=str(states.get(operation.operation_id, {}).get("status") or "pending"),
                attempts=int(states.get(operation.operation_id, {}).get("attempts") or 0),
                error=states.get(operation.operation_id, {}).get("last_error"),
            )
            for operation in plan.operations
        ]
        return ProcessingResult(
            meeting_id=plan.meeting_id,
            meeting_name=meeting_name,
            status=status,
            classification=plan.decision.classification,
            company_id=plan.company_id,
            contact_ids=plan.contact_ids,
            deal_id=plan.deal_id,
            reason_codes=plan.reason_codes,
            evidence=plan.decision.all_evidence(),
            operations=operations,
        )

    def _duplicate_result(
        self, meeting: Any, plan: MutationPlan | None, *, changed: bool
    ) -> ProcessingResult:
        if plan is None:
            return ProcessingResult(
                meeting_id=meeting.id,
                meeting_name=meeting.name,
                status=ProcessingStatus.duplicate,
                reason_codes=[
                    "DUPLICATE_ID_PAYLOAD_CHANGED" if changed else "DUPLICATE_MEETING_ID"
                ],
            )
        result = self._result_from_plan(meeting.name, plan, ProcessingStatus.duplicate)
        result.reason_codes.append(
            "DUPLICATE_ID_PAYLOAD_CHANGED" if changed else "DUPLICATE_MEETING_ID"
        )
        return result

    def _review_changed_payload(self, meeting: Any, plan: MutationPlan | None) -> ProcessingResult:
        if plan is None:
            return ProcessingResult(
                meeting_id=meeting.id,
                meeting_name=meeting.name,
                status=ProcessingStatus.review,
                reason_codes=["PAYLOAD_CHANGED_DURING_RETRY"],
            )
        result = self._result_from_plan(meeting.name, plan, ProcessingStatus.review)
        result.reason_codes.append("PAYLOAD_CHANGED_DURING_RETRY")
        return result
