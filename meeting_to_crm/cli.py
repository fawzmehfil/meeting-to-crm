from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from ergo_crm import ErgoCRMClient
from meeting_to_crm.candidates import CandidateResolver
from meeting_to_crm.config import Config
from meeting_to_crm.decision import LunaDecisionEngine
from meeting_to_crm.executor import Executor
from meeting_to_crm.journal import Journal
from meeting_to_crm.models import MeetingWebhook, ProcessingResult, ProcessingStatus
from meeting_to_crm.observability import EventLogger
from meeting_to_crm.policy import PolicyEngine
from meeting_to_crm.workflow import Workflow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meeting-to-crm",
        description="Evidence-gated reconciliation of meeting webhooks into Ergo CRM.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("plan", "Resolve and validate meetings without CRM writes."),
        ("apply", "Resolve, validate, and apply replay-safe CRM writes."),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        subparser.add_argument("inputs", nargs="+", type=Path)
        subparser.add_argument(
            "--json", action="store_true", help="Print machine-readable JSON results."
        )
    return parser


def _load_meeting(path: Path) -> MeetingWebhook:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    try:
        return MeetingWebhook.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid meeting webhook in {path}: {exc}") from exc


def _human(result: ProcessingResult) -> str:
    lines = [f"[{result.status.value}] {result.meeting_name} ({result.meeting_id})"]
    if result.classification:
        lines.append(f"  Classification: {result.classification.value}")
    if result.company_id:
        lines.append(f"  Company: {result.company_id}")
    if result.contact_ids:
        lines.append(f"  Contacts: {', '.join(result.contact_ids)}")
    if result.deal_id:
        lines.append(f"  Deal: {result.deal_id}")
    if result.reason_codes:
        lines.append(f"  Reasons: {', '.join(dict.fromkeys(result.reason_codes))}")
    for operation in result.operations:
        lines.append(
            f"  Operation: {operation.kind.value} -> {operation.target_id} "
            f"[{operation.status}; attempts={operation.attempts}]"
        )
    for evidence in result.evidence:
        lines.append(f"  Evidence ({evidence.supports}): {evidence.quote}")
    if result.error:
        lines.append(f"  Error: {result.error}")
    return "\n".join(lines)


def _exit_code(results: list[ProcessingResult], validation_failed: bool) -> int:
    if any(
        result.status in {ProcessingStatus.error, ProcessingStatus.partial} for result in results
    ):
        return 4
    if validation_failed:
        return 3
    if any(result.status == ProcessingStatus.review for result in results):
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = Config.from_env()
    logger = EventLogger(config.log_level)
    journal = Journal(config.state_path)
    client = ErgoCRMClient(config.crm_url)
    resolver = CandidateResolver(client, config)
    decision_engine = LunaDecisionEngine(config.openai_api_key, config.openai_model)
    policy = PolicyEngine()
    executor = Executor(client, journal, logger)
    workflow = Workflow(
        config,
        client,
        resolver,
        decision_engine,
        policy,
        journal,
        executor,
        logger,
    )

    results: list[ProcessingResult] = []
    validation_failed = False
    try:
        for path in args.inputs:
            try:
                meeting = _load_meeting(path)
            except ValueError as exc:
                validation_failed = True
                logger.emit("input_rejected", level="ERROR", path=str(path), error=str(exc))
                if not args.json:
                    print(f"[error] {path}\n  Error: {exc}", file=sys.stdout)
                continue
            results.append(workflow.process(meeting, apply=args.command == "apply"))
    finally:
        journal.close()

    if args.json:
        print(json.dumps([result.model_dump(mode="json") for result in results], indent=2))
    else:
        for index, result in enumerate(results):
            if index:
                print()
            print(_human(result))
    return _exit_code(results, validation_failed)


if __name__ == "__main__":
    raise SystemExit(main())
