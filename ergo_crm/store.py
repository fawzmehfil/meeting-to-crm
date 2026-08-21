from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from ergo_crm.schema import (
    ENTITY_TYPES,
    FIELD_SCHEMA,
    FINDABLE_FIELDS,
    RELATION_FOR_TYPES,
    RELATIONS,
    SEARCH_LIMIT_CAP,
)

SEED_PATH = Path(__file__).with_name("seed.json")
_ID_SUFFIX = re.compile(r"^(\d+)$")
_PLURAL = {"company": "companies", "contact": "contacts", "deal": "deals"}


class StoreError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Store:
    def __init__(self, seed: dict[str, Any], fail_nth_write: int = 0) -> None:
        self.fail_nth_write = fail_nth_write
        self.write_count = 0
        self._lock = threading.Lock()
        self.entities: dict[str, dict[str, dict[str, Any]]] = {
            t: {} for t in ENTITY_TYPES
        }
        self.seq = {t: 0 for t in ENTITY_TYPES}
        self.edges: set[tuple[str, str, str, str, str]] = set()
        self.notes: list[dict[str, Any]] = []
        self.note_seq = 0
        self._load(seed)

    @classmethod
    def from_seed_file(
        cls,
        path: Path | None = None,
        fail_nth_write: int = 0,
    ) -> Store:
        data = json.loads((path or SEED_PATH).read_text(encoding="utf-8"))
        return cls(data, fail_nth_write=fail_nth_write)

    def _load(self, seed: dict[str, Any]) -> None:
        for typ in ENTITY_TYPES:
            for raw in seed.get(_PLURAL[typ], []):
                record = deepcopy(raw)
                eid = record.get("id")
                if not eid:
                    raise StoreError(500, f"seed {typ} missing id")
                self.entities[typ][eid] = record
                self._bump_seq(typ, eid)
        for raw in seed.get("associations", []):
            self._add_edge(raw["from"], raw["to"], count_write=False)

    def _bump_seq(self, typ: str, eid: str) -> None:
        prefix = f"{typ}_"
        if eid.startswith(prefix):
            suffix = eid[len(prefix) :]
            if _ID_SUFFIX.match(suffix):
                self.seq[typ] = max(self.seq[typ], int(suffix))

    def _require_type(self, typ: str) -> None:
        if typ not in self.entities:
            raise StoreError(400, f"unknown entity type: {typ}")

    def _require_entity(self, typ: str, eid: str) -> dict[str, Any]:
        self._require_type(typ)
        record = self.entities[typ].get(eid)
        if record is None:
            raise StoreError(404, "not found")
        return record

    def _public(self, typ: str, record: dict[str, Any]) -> dict[str, Any]:
        out = deepcopy(record)
        out["notes"] = [
            {"id": note["id"], "body": note["body"]}
            for note in self.notes
            if note["target_type"] == typ and note["target_id"] == record["id"]
        ]
        return out

    def _begin_write(self, fail_nth: int | None) -> None:
        n = self.fail_nth_write if fail_nth is None else fail_nth
        self.write_count += 1
        if n and self.write_count == n:
            raise StoreError(500, "internal error")

    def _apply_write_rules(
        self, typ: str, existing: dict[str, Any], incoming: dict[str, Any]
    ) -> dict[str, Any]:
        schema = FIELD_SCHEMA.get(typ, {})
        merged = dict(existing)
        for key, value in incoming.items():
            if key in {"id", "notes"}:
                continue
            spec = schema.get(key)
            if (
                spec
                and spec.get("write") == "write-once"
                and key in existing
                and existing[key] is not None
            ):
                continue
            merged[key] = value
        return merged

    def _edge_key(
        self, relation: str, left: dict[str, str], right: dict[str, str]
    ) -> tuple[str, str, str, str, str]:
        a, b = sorted(
            ((left["type"], left["id"]), (right["type"], right["id"]))
        )
        return (relation, a[0], a[1], b[0], b[1])

    def _relation_for(self, left: dict[str, str], right: dict[str, str]) -> str:
        types = frozenset({left["type"], right["type"]})
        relation = RELATION_FOR_TYPES.get(types)
        if relation is None:
            raise StoreError(400, "cannot associate those entity types")
        return relation

    def _add_edge(
        self,
        left: dict[str, str],
        right: dict[str, str],
        count_write: bool,
        fail_nth: int | None = None,
    ) -> dict[str, Any]:
        relation = self._relation_for(left, right)
        self._require_entity(left["type"], left["id"])
        self._require_entity(right["type"], right["id"])
        if count_write:
            self._begin_write(fail_nth)
        self.edges.add(self._edge_key(relation, left, right))
        return {"ok": True, "relation": relation}

    def get(self, typ: str, eid: str) -> dict[str, Any]:
        with self._lock:
            return self._public(typ, self._require_entity(typ, eid))

    def find_by_field(self, typ: str, field: str, value: str) -> dict[str, Any] | None:
        with self._lock:
            self._require_type(typ)
            if (typ, field) not in FINDABLE_FIELDS:
                raise StoreError(400, "unsupported find field")
            for record in self.entities[typ].values():
                if record.get(field) == value:
                    return self._public(typ, record)
            return None

    def search(self, typ: str, query: str, limit: int = SEARCH_LIMIT_CAP) -> list[dict[str, Any]]:
        with self._lock:
            self._require_type(typ)
            needle = query.strip().casefold()
            if not needle:
                return []
            try:
                cap = int(limit)
            except (TypeError, ValueError):
                cap = SEARCH_LIMIT_CAP
            cap = max(0, min(cap, SEARCH_LIMIT_CAP))
            hits: list[dict[str, Any]] = []
            for record in self.entities[typ].values():
                if _name_blob(typ, record).find(needle) != -1:
                    hits.append(self._public(typ, record))
                    if len(hits) >= cap:
                        break
            return hits

    def list_associated(
        self, typ: str, eid: str, relation: str
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._require_entity(typ, eid)
            ends = RELATIONS.get(relation)
            if ends is None:
                raise StoreError(400, f"unknown relation: {relation}")
            if typ not in ends:
                raise StoreError(400, f"{typ} is not part of {relation}")
            other_type = next(iter(ends - {typ}))
            found: list[dict[str, Any]] = []
            for edge in self.edges:
                if edge[0] != relation:
                    continue
                pair = ((edge[1], edge[2]), (edge[3], edge[4]))
                for i, (et, ee) in enumerate(pair):
                    if et == typ and ee == eid:
                        ot, oe = pair[1 - i]
                        if ot == other_type and oe in self.entities[ot]:
                            found.append(self._public(ot, self.entities[ot][oe]))
                        break
            return found

    def upsert(
        self, typ: str, entity: dict[str, Any], fail_nth: int | None = None
    ) -> dict[str, Any]:
        with self._lock:
            self._require_type(typ)
            if not isinstance(entity, dict):
                raise StoreError(400, "entity must be an object")
            incoming = deepcopy(entity)
            eid = incoming.get("id")
            existing = self.entities[typ].get(eid) if eid else None
            self._begin_write(fail_nth)
            if existing is None:
                if not eid:
                    self.seq[typ] += 1
                    eid = f"{typ}_{self.seq[typ]}"
                incoming["id"] = eid
                record = self._apply_write_rules(typ, {}, incoming)
                record["id"] = eid
                self.entities[typ][eid] = record
            else:
                record = self._apply_write_rules(typ, existing, incoming)
                record["id"] = existing["id"]
                self.entities[typ][existing["id"]] = record
            return self._public(typ, record)

    def associate(
        self,
        from_entity: dict[str, str],
        to_entity: dict[str, str],
        fail_nth: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._add_edge(from_entity, to_entity, count_write=True, fail_nth=fail_nth)

    def add_note(
        self, target: dict[str, str], body: str, fail_nth: int | None = None
    ) -> dict[str, Any]:
        with self._lock:
            if not isinstance(body, str) or not body:
                raise StoreError(400, "note body is required")
            self._require_entity(target["type"], target["id"])
            self._begin_write(fail_nth)
            self.note_seq += 1
            note = {
                "id": f"note_{self.note_seq}",
                "target_type": target["type"],
                "target_id": target["id"],
                "body": body,
            }
            self.notes.append(note)
            return {"id": note["id"], "body": note["body"]}


def _name_blob(typ: str, record: dict[str, Any]) -> str:
    if typ == "contact":
        parts = [record.get("first_name") or "", record.get("last_name") or ""]
        return " ".join(parts).casefold()
    return str(record.get("name") or "").casefold()
