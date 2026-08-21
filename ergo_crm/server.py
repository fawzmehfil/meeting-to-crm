from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ergo_crm.schema import ENTITY_TYPES, FIELD_SCHEMA
from ergo_crm.store import Store, StoreError


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


class CRMHandler(BaseHTTPRequestHandler):
    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("ERGO_CRM_QUIET"):
            return
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
        qs = parse_qs(parsed.query, keep_blank_values=True)
        try:
            status, payload = self._handle(method, parts, qs)
        except StoreError as exc:
            self._send(exc.status, {"error": exc.message})
            return
        self._send(status, payload)

    def _handle(
        self, method: str, parts: list[str], qs: dict[str, list[str]]
    ) -> tuple[int, Any]:
        if method == "GET" and parts == ["schema"]:
            return 200, FIELD_SCHEMA
        if method == "GET" and len(parts) == 2 and parts[0] == "find":
            field = _one(qs, "field")
            value = _one(qs, "value")
            if field is None or value is None:
                raise StoreError(400, "field and value are required")
            return 200, {"record": self.store.find_by_field(parts[1], field, value)}
        if method == "GET" and len(parts) == 2 and parts[0] == "search":
            query = _one(qs, "q") or ""
            limit = _one(qs, "limit") or "10"
            try:
                cap = int(limit)
            except ValueError as exc:
                raise StoreError(400, "limit must be an integer") from exc
            return 200, {"records": self.store.search(parts[1], query, cap)}
        if method == "GET" and len(parts) == 2 and parts[0] in ENTITY_TYPES:
            return 200, {"record": self.store.get(parts[0], parts[1])}
        if (
            method == "GET"
            and len(parts) == 3
            and parts[0] in ENTITY_TYPES
            and parts[2] == "associated"
        ):
            relation = _one(qs, "relation")
            if not relation:
                raise StoreError(400, "relation is required")
            return 200, {
                "records": self.store.list_associated(parts[0], parts[1], relation)
            }
        if method == "POST" and len(parts) == 1 and parts[0] in ENTITY_TYPES:
            body = self._read_json()
            if not isinstance(body, dict):
                raise StoreError(400, "entity must be an object")
            record = self.store.upsert(parts[0], body, fail_nth=_fail_nth(qs))
            return 200, {"record": record}
        if method == "POST" and parts == ["associate"]:
            body = self._read_json()
            result = self.store.associate(
                _endpoint(body, "from"),
                _endpoint(body, "to"),
                fail_nth=_fail_nth(qs),
            )
            return 200, result
        if method == "POST" and parts == ["notes"]:
            body = self._read_json()
            note = self.store.add_note(
                _endpoint(body, "target"),
                body.get("body"),
                fail_nth=_fail_nth(qs),
            )
            return 200, {"note": note}
        raise StoreError(404, "not found")

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            raise StoreError(400, "json body is required")
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(400, "invalid json") from exc

    def _send(self, status: int, payload: Any) -> None:
        data = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _one(qs: dict[str, list[str]], key: str) -> str | None:
    values = qs.get(key)
    if not values:
        return None
    return values[0]


def _fail_nth(qs: dict[str, list[str]]) -> int | None:
    raw = _one(qs, "fail_nth")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise StoreError(400, "fail_nth must be an integer") from exc


def _endpoint(body: Any, key: str) -> dict[str, str]:
    if not isinstance(body, dict):
        raise StoreError(400, "json object required")
    item = body.get(key)
    if not isinstance(item, dict) or "type" not in item or "id" not in item:
        raise StoreError(400, f"{key} must have type and id")
    return {"type": str(item["type"]), "id": str(item["id"])}


def make_server(
    host: str,
    port: int,
    store: Store | None = None,
    fail_nth_write: int = 0,
) -> ThreadingHTTPServer:
    bound = store or Store.from_seed_file(fail_nth_write=fail_nth_write)
    httpd = ThreadingHTTPServer((host, port), CRMHandler)
    httpd.store = bound  # type: ignore[attr-defined]
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8787, fail_nth_write: int = 0) -> None:
    httpd = make_server(host, port, fail_nth_write=fail_nth_write)
    print(
        f"ergo-crm listening on http://{host}:{httpd.server_port}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
