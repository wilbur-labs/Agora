"""JSONL audit logging for research dispatch."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class AuditLogger:
    def __init__(self, path: str | Path | None):
        self.path = Path(path).expanduser() if path else None

    def write(self, event: str, **data: Any) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": now_iso(), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
