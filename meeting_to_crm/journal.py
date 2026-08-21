from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from meeting_to_crm.models import MutationPlan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meetings (
                    meeting_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id),
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    expected_json TEXT NOT NULL,
                    desired_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    response_json TEXT,
                    last_error TEXT,
                    UNIQUE(meeting_id, sequence)
                );
                """
            )

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM meetings WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        return dict(row) if row else None

    def start_meeting(self, meeting_id: str, payload_hash: str) -> None:
        now = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO meetings(
                    meeting_id, payload_hash, status, plan_json,
                    created_at, updated_at, last_error
                ) VALUES (?, ?, 'planning', NULL, ?, ?, NULL)
                ON CONFLICT(meeting_id) DO NOTHING
                """,
                (meeting_id, payload_hash, now, now),
            )

    def save_plan(self, plan: MutationPlan, status: str) -> None:
        now = _now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO meetings(
                    meeting_id, payload_hash, status, plan_json,
                    created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    payload_hash = excluded.payload_hash,
                    status = excluded.status,
                    plan_json = excluded.plan_json,
                    updated_at = excluded.updated_at,
                    last_error = NULL
                """,
                (
                    plan.meeting_id,
                    plan.payload_hash,
                    status,
                    plan.model_dump_json(),
                    now,
                    now,
                ),
            )
            for operation in plan.operations:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO operations(
                        operation_id, meeting_id, sequence, kind, target_id,
                        expected_json, desired_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        operation.operation_id,
                        plan.meeting_id,
                        operation.sequence,
                        operation.kind.value,
                        operation.target_id,
                        json.dumps(operation.expected_before, sort_keys=True),
                        json.dumps(operation.desired, sort_keys=True),
                    ),
                )

    def load_plan(self, meeting_id: str) -> MutationPlan | None:
        row = self.get_meeting(meeting_id)
        if not row or not row.get("plan_json"):
            return None
        return MutationPlan.model_validate_json(row["plan_json"])

    def mark_meeting(self, meeting_id: str, status: str, error: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE meetings
                SET status = ?, updated_at = ?, last_error = ?
                WHERE meeting_id = ?
                """,
                (status, _now(), error, meeting_id),
            )

    def operation_states(self, meeting_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM operations WHERE meeting_id = ? ORDER BY sequence", (meeting_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_operation_attempt(self, operation_id: str) -> int:
        with self.connection:
            self.connection.execute(
                """
                UPDATE operations
                SET attempts = attempts + 1, status = 'pending', last_error = NULL
                WHERE operation_id = ?
                """,
                (operation_id,),
            )
        row = self.connection.execute(
            "SELECT attempts FROM operations WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        return int(row["attempts"])

    def mark_operation_error(self, operation_id: str, error: str, *, final: bool = False) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE operations
                SET status = ?, last_error = ?
                WHERE operation_id = ?
                """,
                ("failed" if final else "pending", error, operation_id),
            )

    def mark_operation_succeeded(self, operation_id: str, response: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE operations
                SET status = 'succeeded', response_json = ?, last_error = NULL
                WHERE operation_id = ?
                """,
                (json.dumps(response, sort_keys=True), operation_id),
            )
