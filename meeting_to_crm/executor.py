from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ergo_crm import ErgoCRMClient, ErgoCRMError
from meeting_to_crm.journal import Journal
from meeting_to_crm.models import (
    MutationOperation,
    MutationPlan,
    OperationKind,
    OperationResult,
    ProcessingResult,
    ProcessingStatus,
)
from meeting_to_crm.observability import EventLogger
from meeting_to_crm.policy import MISSING

_RETRYABLE_STATUS = {0, 429, 500, 502, 503, 504}


class PreconditionChanged(RuntimeError):
    pass


class DealNoLongerOpen(PreconditionChanged):
    pass


class DealPipelineChanged(PreconditionChanged):
    pass


class Executor:
    def __init__(
        self,
        client: ErgoCRMClient,
        journal: Journal,
        logger: EventLogger,
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.journal = journal
        self.logger = logger
        self.max_attempts = max_attempts
        self.sleep = sleep

    def apply(self, plan: MutationPlan, meeting_name: str) -> ProcessingResult:
        self.journal.mark_meeting(plan.meeting_id, "applying")
        states = {
            row["operation_id"]: row for row in self.journal.operation_states(plan.meeting_id)
        }

        for operation in plan.operations:
            if states.get(operation.operation_id, {}).get("status") == "succeeded":
                continue
            failure = self._apply_operation(plan, operation)
            if failure is None:
                continue
            if isinstance(failure, DealNoLongerOpen):
                self.journal.mark_meeting(plan.meeting_id, "review", str(failure))
                return self._result(
                    plan,
                    meeting_name,
                    ProcessingStatus.review,
                    ["DEAL_NO_LONGER_OPEN"],
                    str(failure),
                )
            if isinstance(failure, DealPipelineChanged):
                self.journal.mark_meeting(plan.meeting_id, "review", str(failure))
                return self._result(
                    plan,
                    meeting_name,
                    ProcessingStatus.review,
                    ["DEAL_PIPELINE_CHANGED"],
                    str(failure),
                )
            if isinstance(failure, PreconditionChanged):
                self.journal.mark_meeting(plan.meeting_id, "review", str(failure))
                return self._result(
                    plan,
                    meeting_name,
                    ProcessingStatus.review,
                    ["PRECONDITION_CHANGED"],
                    str(failure),
                )
            self.journal.mark_meeting(plan.meeting_id, "partial", str(failure))
            return self._result(
                plan,
                meeting_name,
                ProcessingStatus.partial,
                ["OPERATION_FAILED"],
                str(failure),
            )

        self.journal.mark_meeting(plan.meeting_id, "completed")
        self.logger.emit(
            "meeting_completed",
            meeting_id=plan.meeting_id,
            deal_id=plan.deal_id,
            operations=len(plan.operations),
        )
        return self._result(plan, meeting_name, ProcessingStatus.completed)

    def _apply_operation(
        self, plan: MutationPlan, operation: MutationOperation
    ) -> Exception | None:
        for retry_index in range(self.max_attempts):
            attempt = self.journal.mark_operation_attempt(operation.operation_id)
            self.logger.emit(
                "operation_attempted",
                meeting_id=plan.meeting_id,
                operation_id=operation.operation_id,
                operation=operation.kind.value,
                target_id=operation.target_id,
                attempt=attempt,
            )
            try:
                response = self._execute(plan, operation)
            except PreconditionChanged as exc:
                self.journal.mark_operation_error(operation.operation_id, str(exc), final=True)
                self.logger.emit(
                    "operation_failed",
                    level="WARNING",
                    meeting_id=plan.meeting_id,
                    operation_id=operation.operation_id,
                    error=str(exc),
                )
                return exc
            except ErgoCRMError as exc:
                retryable = exc.status in _RETRYABLE_STATUS
                final = not retryable or retry_index + 1 >= self.max_attempts
                self.journal.mark_operation_error(operation.operation_id, str(exc), final=final)
                self.logger.emit(
                    "operation_failed",
                    level="ERROR" if final else "WARNING",
                    meeting_id=plan.meeting_id,
                    operation_id=operation.operation_id,
                    crm_status=exc.status,
                    attempt=attempt,
                    retrying=not final,
                    error=str(exc),
                )
                if final:
                    return exc
                self.sleep(0.2 * (2**retry_index))
                continue
            except Exception as exc:
                self.journal.mark_operation_error(operation.operation_id, str(exc), final=True)
                self.logger.emit(
                    "operation_failed",
                    level="ERROR",
                    meeting_id=plan.meeting_id,
                    operation_id=operation.operation_id,
                    error=str(exc),
                )
                return exc

            self.journal.mark_operation_succeeded(operation.operation_id, response)
            self.logger.emit(
                "operation_succeeded",
                meeting_id=plan.meeting_id,
                operation_id=operation.operation_id,
                operation=operation.kind.value,
                target_id=operation.target_id,
                attempt=attempt,
                recovered=bool(response.get("recovered")),
            )
            return None
        return RuntimeError("operation attempts exhausted")

    def _execute(self, plan: MutationPlan, operation: MutationOperation) -> dict[str, Any]:
        if operation.kind == OperationKind.upsert_deal:
            current = self.client.get("deal", operation.target_id)
            if all(current.get(key, MISSING) == value for key, value in operation.desired.items()):
                return {"record": current, "recovered": True}
            self._require_open_deal(current, operation.target_id)
            self._require_same_pipeline(current, operation)
            changed = {
                key: {
                    "expected": expected,
                    "actual": current.get(key, MISSING),
                }
                for key, expected in operation.expected_before.items()
                if current.get(key, MISSING) != expected
            }
            if changed:
                raise PreconditionChanged(
                    f"deal {operation.target_id} changed after planning: {changed}"
                )
            record = self.client.upsert("deal", {"id": operation.target_id, **operation.desired})
            return {"record": record, "recovered": False}

        if operation.kind == OperationKind.add_note:
            current = self.client.get("deal", operation.target_id)
            marker = f"[meeting-to-crm:{plan.meeting_id}]"
            if any(marker in str(note.get("body") or "") for note in current.get("notes", [])):
                return {"record": current, "recovered": True}
            self._require_open_deal(current, operation.target_id)
            self._require_same_pipeline(current, operation)
            note = self.client.add_note(
                {"type": "deal", "id": operation.target_id},
                str(operation.desired["body"]),
            )
            return {"note": note, "recovered": False}

        raise RuntimeError(f"unsupported operation: {operation.kind}")

    @staticmethod
    def _require_open_deal(current: dict[str, Any], target_id: str) -> None:
        status = str(current.get("status") or "").casefold()
        if status != "open":
            raise DealNoLongerOpen(
                f"deal {target_id} is no longer open (current status: {status or 'missing'})"
            )

    @staticmethod
    def _require_same_pipeline(current: dict[str, Any], operation: MutationOperation) -> None:
        if "pipeline" not in operation.expected_before:
            raise PreconditionChanged(
                f"operation {operation.operation_id} has no planned pipeline precondition"
            )
        expected = operation.expected_before["pipeline"]
        actual = current.get("pipeline", MISSING)
        if actual != expected:
            raise DealPipelineChanged(
                f"deal {operation.target_id} moved pipelines after planning "
                f"(expected: {expected!r}, current: {actual!r})"
            )

    def _result(
        self,
        plan: MutationPlan,
        meeting_name: str,
        status: ProcessingStatus,
        extra_reasons: list[str] | None = None,
        error: str | None = None,
    ) -> ProcessingResult:
        states = {
            row["operation_id"]: row for row in self.journal.operation_states(plan.meeting_id)
        }
        operations = []
        for operation in plan.operations:
            state = states.get(operation.operation_id, {})
            operations.append(
                OperationResult(
                    operation_id=operation.operation_id,
                    kind=operation.kind,
                    target_id=operation.target_id,
                    status=str(state.get("status") or "pending"),
                    attempts=int(state.get("attempts") or 0),
                    error=state.get("last_error"),
                )
            )
        return ProcessingResult(
            meeting_id=plan.meeting_id,
            meeting_name=meeting_name,
            status=status,
            classification=plan.decision.classification,
            company_id=plan.company_id,
            contact_ids=plan.contact_ids,
            deal_id=plan.deal_id,
            reason_codes=[*plan.reason_codes, *(extra_reasons or [])],
            evidence=plan.decision.all_evidence(),
            operations=operations,
            error=error,
        )
