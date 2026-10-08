"""Where a terminal for a project can open (CR-30).

Two kinds of place:

* the project's own repository — any of the four commands;
* a claude-workspace task that names this repository — ``claude`` started in that
  workspace's TOOL directory with ``CW_TASK=<task>``, exactly what ``cw open <task>``
  does, so the SessionStart hook loads that task's RESUME and the rest of the
  workspace's machinery applies.

The tool directory is not the registered source: since claude-workspace adr/0019 the
work data (``tasks/``) lives in a separate work root, and the tool points at it with a
one-line ``.workroot``. So the tool is found by looking, beside each registered source,
for a directory whose ``.workroot`` names that source (``AGORA_CLAUDE_WORKSPACE_TOOL``
overrides). A source that is still the old single-directory layout is its own tool. When
none is found the task is listed without a way to open it, and says why.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agora.projects.import_candidates import recorded_goals


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve(strict=False))) == os.path.normcase(str(b.resolve(strict=False)))
    except OSError:
        return False


def _is_tool(path: Path) -> bool:
    return (path / "scripts" / "cw.sh").is_file() and (path / ".claude").is_dir()


def claude_tool_dir_for(source: Path, sources: list[Path]) -> Path | None:
    override = os.environ.get("AGORA_CLAUDE_WORKSPACE_TOOL", "").strip()
    if override and _is_tool(Path(override)):
        return Path(override)
    if _is_tool(source):
        return source  # the layout before the tool / work split
    seen: set[str] = set()
    for base in {s.parent for s in [source, *sources]}:
        try:
            children = list(base.iterdir())
        except OSError:
            continue
        for child in children:
            key = os.path.normcase(str(child))
            if key in seen:
                continue
            seen.add(key)
            marker = child / ".workroot"
            try:
                if marker.is_file() and _is_tool(child):
                    pointed = Path(marker.read_text(encoding="utf-8").strip())
                    if _same(pointed, source):
                        return child
            except OSError:
                continue
    return None


@dataclass
class TaskTarget:
    source: str
    task: str
    title: str | None
    tool_dir: str | None
    #: Why it cannot be opened, when ``tool_dir`` is None.
    reason: str | None

    def payload(self) -> dict[str, Any]:
        return {"source": self.source, "task": self.task, "title": self.title,
                "tool_dir": self.tool_dir, "reason": self.reason}


def task_targets(sources: list[Path], project_root: Path) -> list[TaskTarget]:
    """The claude-workspace tasks that name this repository, each with the tool directory
    to open it from. kiro-workspace tasks are not offered yet (no ``cw open`` there)."""
    goals, _ = recorded_goals(sources, project_root)
    out: list[TaskTarget] = []
    for g in goals:
        source = Path(g.source)
        if _is_kiro(source):
            continue
        tool = claude_tool_dir_for(source, sources)
        out.append(TaskTarget(
            source=g.source, task=g.task, title=g.title,
            tool_dir=str(tool) if tool else None,
            reason=None if tool else "claude-workspace tool directory not found next to this work root "
                                     "(set AGORA_CLAUDE_WORKSPACE_TOOL)",
        ))
    return out


def _is_kiro(source: Path) -> bool:
    return (source / ".kiro").is_dir() and not (source / ".claude").is_dir()
