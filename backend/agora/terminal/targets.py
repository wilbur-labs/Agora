"""Where a terminal for a project can open (CR-30).

Two kinds of place:

* the project's own repository — any of the four commands;
* a claude-workspace task that names this repository — ``claude`` started in that
  workspace's TOOL directory with ``CW_TASK=<task>``, exactly what ``cw open <task>``
  does, so the SessionStart hook loads that task's RESUME and the rest of the
  workspace's machinery applies;
* a kiro-workspace task that names it — ``kiro-cli chat --agent <agent>`` started in the
  kiro-workspace directory, which is how that workspace opens a task: each task has an
  agent profile ``.kiro/agents/<task>.json`` whose resources are that task's RESUME,
  task.yaml and WORKFLOW. A task without one is listed, with why it cannot open.

The tool directory is not the registered source: since claude-workspace adr/0019 the
work data (``tasks/``) lives in a separate work root, and the tool points at it with a
one-line ``.workroot``. So the tool is found by looking, beside each registered source,
for a directory whose ``.workroot`` names that source (``AGORA_CLAUDE_WORKSPACE_TOOL``
overrides). A source that is still the old single-directory layout is its own tool. When
none is found the task is listed without a way to open it, and says why.
"""
from __future__ import annotations

import json
import os
import re
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
            except (OSError, UnicodeDecodeError, ValueError):
                continue
    return None


#: An agent name that is safe to put on a command line: it ends up inside the shell
#: command that starts kiro-cli, so nothing outside this set is ever passed.
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def kiro_agent_for(source: Path, task: str) -> tuple[str | None, str | None]:
    """The kiro agent that opens ``task`` in the kiro-workspace at ``source``, or why there
    is none. The profile's own ``name`` is what ``--agent`` matches; the file name is used
    only when the profile does not say."""
    profile = source / ".kiro" / "agents" / f"{task}.json"
    try:
        if not profile.is_file():
            return None, f"no kiro agent for this task ({profile.relative_to(source).as_posix()} not found)"
        data = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"kiro agent profile could not be read: {type(exc).__name__}"
    name = data.get("name") if isinstance(data, dict) else None
    name = name if isinstance(name, str) and name else task
    if not AGENT_NAME_RE.fullmatch(name):
        return None, f"kiro agent name is not usable on a command line: {name!r}"
    return name, None


@dataclass
class TaskTarget:
    source: str
    task: str
    title: str | None
    tool_dir: str | None
    #: Why it cannot be opened, when ``tool_dir`` is None.
    reason: str | None
    #: Which workspace the task belongs to: ``claude`` or ``kiro``.
    workspace: str = "claude"
    #: For a kiro task, the agent ``kiro-cli chat --agent`` is started with.
    agent: str | None = None

    def payload(self) -> dict[str, Any]:
        return {"source": self.source, "task": self.task, "title": self.title,
                "tool_dir": self.tool_dir, "reason": self.reason,
                "workspace": self.workspace, "agent": self.agent}


def task_targets(sources: list[Path], project_root: Path) -> list[TaskTarget]:
    """The workspace tasks that name this repository, each with where and how to open it."""
    goals, _ = recorded_goals(sources, project_root)
    out: list[TaskTarget] = []
    for g in goals:
        source = Path(g.source)
        if _is_kiro(source):
            agent, why = kiro_agent_for(source, g.task)
            out.append(TaskTarget(
                source=g.source, task=g.task, title=g.title,
                tool_dir=str(source) if agent else None, reason=why,
                workspace="kiro", agent=agent,
            ))
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
