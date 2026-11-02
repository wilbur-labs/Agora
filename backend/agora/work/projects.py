"""Which work projects the left column lists (CR-75 §1).

Its own scan, not ``import_candidates._task_documents``: the import page reads "no
task.yaml" as "names no repository" and skips it, which is right there and wrong here —
a project directory the page cannot read is said, not dropped.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agora.fsprobe import probe_path
from agora.terminal.targets import _is_kiro, claude_tool_dir_for, kiro_agent_for


def work_key(source: str | Path, task: str) -> str:
    """Stable, opaque, never a path: names this project's record and report directory."""
    raw = os.path.normcase(str(Path(source))) + "\0" + task
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class WorkProject:
    key: str
    source: str
    task: str
    title: str | None
    #: ``claude`` or ``kiro``.
    workspace: str
    #: Where the terminal starts; None when it cannot be opened (``reason`` says why).
    tool_dir: str | None
    reason: str | None
    #: For a kiro project, the agent ``kiro-cli chat --agent`` is started with.
    agent: str | None = None

    def payload(self) -> dict[str, Any]:
        return {"key": self.key, "source": self.source, "task": self.task, "title": self.title,
                "workspace": self.workspace, "openable": self.tool_dir is not None, "reason": self.reason}


@dataclass
class WorkScan:
    projects: list[WorkProject]
    #: ``{source, task, reason}`` for every project directory that could not be read.
    unreadable: list[dict[str, str]]
    #: Projects whose ``status`` is set to something other than ``active``.
    inactive: int


def _title(document: dict[str, Any]) -> str | None:
    task = document.get("task")
    for holder in (task if isinstance(task, dict) else None, document):
        if isinstance(holder, dict) and isinstance(holder.get("title"), str) and holder["title"].strip():
            return holder["title"].strip()
    return None


def _status(document: dict[str, Any]) -> str | None:
    task = document.get("task")
    for holder in (task if isinstance(task, dict) else None, document):
        if isinstance(holder, dict) and "status" in holder:
            value = holder["status"]
            return value.strip() if isinstance(value, str) else str(value)
    return None


def scan_work_projects(sources: list[Path]) -> WorkScan:
    projects: list[WorkProject] = []
    unreadable: list[dict[str, str]] = []
    inactive = 0
    for source in sources:
        tasks_dir = Path(source) / "tasks"
        try:
            entries = sorted(tasks_dir.iterdir())
        except OSError as exc:
            unreadable.append({"source": str(source), "task": "", "reason": type(exc).__name__})
            continue
        task_dirs = []
        for entry in entries:
            if entry.name.startswith("_"):
                continue
            # One entry that cannot be probed is that entry's problem, not the source's.
            # ``stat`` itself: from Python 3.14 ``is_dir`` answers False for a read that
            # failed, and the entry would vanish from the page unsaid (kiro round 10).
            try:
                mode = entry.stat().st_mode
            except FileNotFoundError:
                if os.path.lexists(entry):
                    unreadable.append({"source": str(source), "task": entry.name, "reason": "link target not found"})
                continue
            except OSError as exc:
                unreadable.append({"source": str(source), "task": entry.name, "reason": type(exc).__name__})
                continue
            if stat.S_ISDIR(mode):
                task_dirs.append(entry)
        kiro = _is_kiro(Path(source))
        tool = None if kiro else claude_tool_dir_for(Path(source), sources)
        for task_dir in task_dirs:
            name = task_dir.name
            manifest = task_dir / "task.yaml"
            probe = probe_path(manifest)
            if probe.absent:
                unreadable.append({"source": str(source), "task": name, "reason": "no task.yaml"})
                continue
            if probe.unknown or not probe.is_file():
                unreadable.append({"source": str(source), "task": name,
                                   "reason": probe.reason() if probe.unknown else "task.yaml is not a file"})
                continue
            try:
                document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                unreadable.append({"source": str(source), "task": name, "reason": type(exc).__name__})
                continue
            if not isinstance(document, dict):
                unreadable.append({"source": str(source), "task": name,
                                   "reason": f"task.yaml is not a mapping ({type(document).__name__})"})
                continue
            status = _status(document)
            if status is not None and status != "active":
                inactive += 1
                continue
            key = work_key(source, name)
            if kiro:
                agent, why = kiro_agent_for(Path(source), name)
                projects.append(WorkProject(key=key, source=str(source), task=name, title=_title(document),
                                            workspace="kiro", tool_dir=str(source) if agent else None,
                                            reason=why, agent=agent))
            else:
                projects.append(WorkProject(
                    key=key, source=str(source), task=name, title=_title(document), workspace="claude",
                    tool_dir=str(tool) if tool else None,
                    reason=None if tool else "claude-workspace tool directory not found next to this work root "
                                             "(set AGORA_CLAUDE_WORKSPACE_TOOL)",
                ))
    return WorkScan(projects=projects, unreadable=unreadable, inactive=inactive)
