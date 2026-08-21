from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ErgoCRMError(Exception):
    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        self.body = body


class ErgoCRMNotFound(ErgoCRMError):
    pass


class ErgoCRMClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, entity_type: str, id: str) -> dict[str, Any]:
        return self._request("GET", f"/{entity_type}/{id}")["record"]

    def find_by_field(
        self, entity_type: str, field: str, value: str
    ) -> dict[str, Any] | None:
        payload = self._request(
            "GET",
            f"/find/{entity_type}",
            query={"field": field, "value": value},
        )
        return payload["record"]

    def list_associated(
        self, entity_type: str, id: str, relation: str
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/{entity_type}/{id}/associated",
            query={"relation": relation},
        )
        return payload["records"]

    def search(
        self, entity_type: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/search/{entity_type}",
            query={"q": query, "limit": str(limit)},
        )
        return payload["records"]

    def upsert(self, entity_type: str, entity: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/{entity_type}", body=entity)["record"]

    def associate(
        self, from_entity: dict[str, str], to_entity: dict[str, str]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/associate",
            body={"from": from_entity, "to": to_entity},
        )

    def add_note(self, target: dict[str, str], body: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/notes",
            body={"target": target, "body": body},
        )["note"]

    def field_schema(self) -> dict[str, Any]:
        return self._request("GET", "/schema")

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: dict[str, str] | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            url = f"{url}?{urlencode(query)}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            parsed = _parse_body(raw)
            message = (
                parsed.get("error", raw) if isinstance(parsed, dict) else raw
            )
            error_cls = ErgoCRMNotFound if exc.code == 404 else ErgoCRMError
            raise error_cls(exc.code, str(message), parsed) from exc
        except URLError as exc:
            raise ErgoCRMError(0, str(exc.reason)) from exc
        parsed = _parse_body(raw)
        if parsed is None:
            raise ErgoCRMError(0, "empty response")
        return parsed


def _parse_body(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
