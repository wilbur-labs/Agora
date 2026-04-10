"""Hermes-style bounded memory — MEMORY.md + USER.md."""
from __future__ import annotations

from pathlib import Path

from agora.config.settings import get_config

SEP = "\n§\n"


class MemoryStore:
    def __init__(self, data_dir: str | Path | None = None):
        cfg = get_config().get("memory", {})
        self.data_dir = Path(data_dir or cfg.get("data_dir", "./data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.memory_limit = cfg.get("memory_char_limit", 2200)
        self.user_limit = cfg.get("user_char_limit", 1375)

    def _path(self, target: str) -> Path:
        return self.data_dir / ("MEMORY.md" if target == "memory" else "USER.md")

    def _read(self, target: str) -> list[str]:
        p = self._path(target)
        if not p.exists():
            return []
        text = p.read_text().strip()
        return [e.strip() for e in text.split("§") if e.strip()] if text else []

    def _write(self, target: str, entries: list[str]):
        self._path(target).write_text(SEP.join(entries) + "\n" if entries else "")

    def _limit(self, target: str) -> int:
        return self.memory_limit if target == "memory" else self.user_limit

    def _chars(self, entries: list[str]) -> int:
        return sum(len(e) for e in entries) + len(SEP) * max(0, len(entries) - 1)

    def add(self, target: str, content: str) -> tuple[bool, str]:
        entries = self._read(target)
        content = content.strip()
        if any(content == e for e in entries):
            return True, "Already exists"
        if self._chars(entries + [content]) > self._limit(target):
            return False, f"Exceeds limit. Remove or replace entries first."
        entries.append(content)
        self._write(target, entries)
        return True, "Added"

    def replace(self, target: str, old_text: str, new_content: str) -> tuple[bool, str]:
        entries = self._read(target)
        matches = [i for i, e in enumerate(entries) if old_text in e]
        if len(matches) != 1:
            return False, "No unique match" if not matches else "Multiple matches"
        entries[matches[0]] = new_content.strip()
        self._write(target, entries)
        return True, "Replaced"

    def remove(self, target: str, old_text: str) -> tuple[bool, str]:
        entries = self._read(target)
        matches = [i for i, e in enumerate(entries) if old_text in e]
        if len(matches) != 1:
            return False, "No unique match" if not matches else "Multiple matches"
        entries.pop(matches[0])
        self._write(target, entries)
        return True, "Removed"

    def get_injection_text(self) -> str:
        parts = []
        for target, label in [("memory", "MEMORY"), ("user", "USER PROFILE")]:
            entries = self._read(target)
            if entries:
                used = self._chars(entries)
                pct = int(used / self._limit(target) * 100)
                parts.append(f"{label} [{pct}% — {used}/{self._limit(target)} chars]\n" + SEP.join(entries))
        return "\n\n".join(parts)
