from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


class EventLogger:
    def __init__(self, level: str = "INFO", stream: TextIO | None = None) -> None:
        self.level = _LEVELS.get(level.upper(), 20)
        self.stream = stream or sys.stderr

    def emit(self, event: str, *, level: str = "INFO", **fields: Any) -> None:
        if _LEVELS.get(level.upper(), 20) < self.level:
            return
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "event": event,
            **fields,
        }
        self.stream.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        self.stream.flush()
