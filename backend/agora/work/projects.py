"""Which work projects the left column lists (CR-75 §1).

Its own scan, not ``import_candidates._task_documents``: the import page reads "no
task.yaml" as "names no repository" and skips it, which is right there and wrong here —
a project directory the page cannot read is said, not dropped.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agora.fsprobe import probe_path
from agora.terminal.targets import _is_kiro, claude_tool_dir_for, kiro_agent_for


#: Prefix in an archived project's key input, keeping it apart from a same-named active one.
ARCHIVED_PREFIX = "_archive/"


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
class ShelvedProject:
    """A project whose ``status`` is something other than ``active`` (CR-80): not listed
    for work, listed on the overview to be restored."""
    key: str
    source: str
    task: str
    title: str | None
    workspace: str
    status: str
    #: It sits in ``tasks/_archive/`` (claude-workspace's ``cw archive``).
    archived_dir: bool

    def payload(self) -> dict[str, Any]:
        return {"key": self.key, "source": self.source, "task": self.task, "title": self.title,
                "workspace": self.workspace, "status": self.status, "archived_dir": self.archived_dir}


@dataclass
class WorkScan:
    projects: list[WorkProject]
    #: ``{source, task, reason}`` for every project directory that could not be read.
    unreadable: list[dict[str, str]]
    #: Projects whose ``status`` is set to something other than ``active``.
    inactive: int
    shelved: list[ShelvedProject] = field(default_factory=list)


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


def _task_dirs(entries: list[Path], source: Path, prefix: str,
               unreadable: list[dict[str, str]]) -> list[Path]:
    """The task directories among ``entries``; ``prefix`` names them in ``unreadable``."""
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
                unreadable.append({"source": str(source), "task": prefix + entry.name,
                                   "reason": "link target not found"})
            continue
        except OSError as exc:
            unreadable.append({"source": str(source), "task": prefix + entry.name, "reason": type(exc).__name__})
            continue
        if stat.S_ISDIR(mode):
            task_dirs.append(entry)
    return task_dirs


def _manifest(task_dir: Path, source: Path, label: str,
              unreadable: list[dict[str, str]]) -> dict[str, Any] | None:
    manifest = task_dir / "task.yaml"
    probe = probe_path(manifest)
    if probe.absent:
        unreadable.append({"source": str(source), "task": label, "reason": "no task.yaml"})
        return None
    if probe.unknown or not probe.is_file():
        unreadable.append({"source": str(source), "task": label,
                           "reason": probe.reason() if probe.unknown else "task.yaml is not a file"})
        return None
    try:
        document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        unreadable.append({"source": str(source), "task": label, "reason": type(exc).__name__})
        return None
    if not isinstance(document, dict):
        unreadable.append({"source": str(source), "task": label,
                           "reason": f"task.yaml is not a mapping ({type(document).__name__})"})
        return None
    return document


def scan_work_projects(sources: list[Path]) -> WorkScan:
    projects: list[WorkProject] = []
    unreadable: list[dict[str, str]] = []
    shelved: list[ShelvedProject] = []
    for source in sources:
        tasks_dir = Path(source) / "tasks"
        try:
            entries = sorted(tasks_dir.iterdir())
        except OSError as exc:
            unreadable.append({"source": str(source), "task": "", "reason": type(exc).__name__})
            continue
        kiro = _is_kiro(Path(source))
        workspace = "kiro" if kiro else "claude"
        tool = None if kiro else claude_tool_dir_for(Path(source), sources)
        for task_dir in _task_dirs(entries, Path(source), "", unreadable):
            name = task_dir.name
            document = _manifest(task_dir, Path(source), name, unreadable)
            if document is None:
                continue
            key = work_key(source, name)
            status = _status(document)
            if status is not None and status != "active":
                shelved.append(ShelvedProject(key=key, source=str(source), task=name, title=_title(document),
                                              workspace=workspace, status=status, archived_dir=False))
                continue
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
        # ``cw archive`` moves a task into ``tasks/_archive/``: listed as shelved, whatever
        # its status says, so it can be restored from the overview (CR-80).
        archive_dir = tasks_dir / "_archive"
        probe = probe_path(archive_dir)
        if probe.absent:
            continue
        try:
            if probe.unknown or not probe.is_dir():
                raise NotADirectoryError(probe.reason() if probe.unknown else "_archive is not a directory")
            archived = sorted(archive_dir.iterdir())
        except OSError as exc:
            unreadable.append({"source": str(source), "task": "_archive",
                               "reason": str(exc) if isinstance(exc, NotADirectoryError) else type(exc).__name__})
            continue
        for task_dir in _task_dirs(archived, Path(source), "_archive/", unreadable):
            name = task_dir.name
            document = _manifest(task_dir, Path(source), "_archive/" + name, unreadable)
            if document is None:
                continue
            # Its own key: ``tasks/foo`` and ``tasks/_archive/foo`` may both exist, and
            # restoring one must not act on the other (codex review CR-80 round 3).
            shelved.append(ShelvedProject(key=work_key(source, ARCHIVED_PREFIX + name), source=str(source), task=name,
                                          title=_title(document), workspace=workspace,
                                          status=_status(document) or "archived", archived_dir=True))
    return WorkScan(projects=projects, unreadable=unreadable, inactive=len(shelved), shelved=shelved)
