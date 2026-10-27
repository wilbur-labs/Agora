"""Opening a terminal as a claude-workspace task on a remote host (CR-30 slice 3).

The remote terminal (slice 2) starts a bare agent in the project root. A task's hooks,
RESUME, CR log and review gate only load when the agent starts in the claude-workspace
TOOL directory with ``CW_TASK`` set — what ``cw open`` does. So on a host where the
environment names its tool directory, this module finds the tasks whose records name
the environment's project root, and opens one by running that host's own
``scripts/cw.sh open`` inside the PTY: ``cw open`` re-checks the task (exists, not
archived, which machine it belongs to) and says why when it refuses.

What is checked here, and why each:

* The tool directory is resolved on the host and must hold ``scripts/cw.sh`` and
  ``.claude/``; its ``.workroot`` gives the work root (absent = the tool itself, the
  same rule as ``scripts/workroot.sh``). The work root Agora scanned is passed to the
  agent as ``CW_WORK``, so the hook reads the records that were listed — not whatever a
  profile's own ``CW_WORK`` points at.
* A task matches when a repository path its task.yaml names resolves, on the host, to
  the same directory as the environment's root. Relative paths and paths with ``..``
  are refused, and a path that cannot be resolved or does not exist does not match.
* The menu is a list of candidates, not an authorisation: opening re-reads the one task
  and repeats every check.
* A scan that could not read everything says so (``partial`` / ``unavailable``); it
  never presents "could not read" as "no tasks".
"""
from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from agora.messages import say
from agora.projects.import_candidates import _paths_in, _title_of
from agora.projects.importer import DuplicateKeyError, StrictLoader
from agora.projects.workspace_tasks import MAX_TASK_YAML_BYTES

#: A task name goes onto the remote shell's command line: nothing else is accepted.
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
#: The whole scan of one environment: connect, resolve, list, read.
SCAN_TIMEOUT = 20.0
#: Task directories read per scan; past it the scan is ``partial`` and says so.
MAX_TASK_DIRS = 200
#: How long each git fact may take.
GIT_TIMEOUT = 8.0

_REMOTE_SHELL = 'exec "${SHELL:-/bin/sh}" -l'


class RemoteTaskRefused(Exception):
    """Why a task cannot be opened (or a scan cannot start). The message is shown as is."""


@dataclass
class RemoteTask:
    task: str
    title: str | None


@dataclass
class RepoFacts:
    """What git says about the work root on one side. ``dirty`` is None when unknown."""

    head: str | None
    dirty: bool | None


@dataclass
class RemoteTaskScan:
    state: str  # "complete" | "partial" | "unavailable"
    reason: str | None = None
    tool_dir: str | None = None
    work_root: str | None = None
    tasks: list[RemoteTask] = field(default_factory=list)
    unreadable: int = 0
    truncated: bool = False
    remote_repo: RepoFacts | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "state": self.state, "reason": self.reason, "tool_dir": self.tool_dir,
            "work_root": self.work_root, "unreadable": self.unreadable, "truncated": self.truncated,
            "tasks": [{"task": t.task, "title": t.title} for t in self.tasks],
            "remote_repo": None if self.remote_repo is None else
            {"head": self.remote_repo.head, "dirty": self.remote_repo.dirty},
        }


def _clean_absolute(value: str) -> PurePosixPath | None:
    """An absolute POSIX path without ``..``, or None. ``~`` and relative paths are not
    resolved: a path this cannot vouch for does not match anything."""
    text = (value or "").strip()
    if not text.startswith("/") or ".." in text.split("/"):
        return None
    return PurePosixPath(text)


async def _existing(environment: Any, path: PurePosixPath, kind: str) -> PurePosixPath | None:
    """``path`` resolved on the host, when it exists and is a ``kind`` ("dir"/"file")."""
    resolved = PurePosixPath(str(await environment.fs.realpath(Path(str(path)))))
    probe = await environment.probe(Path(str(resolved)))
    if probe.kind == "unknown":
        # Could not observe it: not the same as "is not there" (fsprobe's rule).
        raise OSError(f"cannot tell what is at {resolved}")
    return resolved if probe.kind == kind else None


async def resolve_tool(environment: Any, tool_dir: str) -> tuple[PurePosixPath, PurePosixPath]:
    """The tool directory and its work root, both resolved on the host. Refuses with the
    reason when either is not what it must be."""
    clean = _clean_absolute(tool_dir)
    if clean is None:
        raise RemoteTaskRefused(say("remote.tool_dir_not_absolute", tool_dir=tool_dir))
    tool = await _existing(environment, clean, "dir")
    if tool is None:
        raise RemoteTaskRefused(say("remote.no_such_dir", tool_dir=tool_dir))
    for part, kind in (("scripts/cw.sh", "file"), (".claude", "dir")):
        if await _existing(environment, tool / part, kind) is None:
            raise RemoteTaskRefused(say("remote.not_a_tool_dir", tool=tool, part=part))
    try:
        marker = (await environment.fs.read_file(Path(str(tool / ".workroot")), max_bytes=4096)).decode("utf-8").strip()
    except FileNotFoundError:
        marker = ""  # the layout before the tool / work split: the tool is its own work root
    if marker:
        root = _clean_absolute(marker)
        if root is None:
            raise RemoteTaskRefused(say("remote.workroot_not_absolute", tool=tool, marker=marker))
        work = await _existing(environment, root, "dir")
        if work is None:
            raise RemoteTaskRefused(say("remote.workroot_missing", marker=marker))
    else:
        work = tool
    if await _existing(environment, work / "tasks", "dir") is None:
        raise RemoteTaskRefused(say("remote.no_tasks_dir", work=work))
    return tool, work


async def _task_document(environment: Any, work: PurePosixPath, task: str) -> dict[str, Any]:
    raw = await environment.fs.read_file(Path(str(work / "tasks" / task / "task.yaml")), max_bytes=MAX_TASK_YAML_BYTES)
    try:
        document = yaml.load(raw.decode("utf-8"), Loader=StrictLoader)
    except (UnicodeDecodeError, DuplicateKeyError, yaml.YAMLError) as exc:
        raise ValueError(f"task.yaml unreadable: {type(exc).__name__}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("task"), dict):
        raise ValueError("task.yaml has no task: section")
    return document


async def _names_root(environment: Any, document: dict[str, Any], root: PurePosixPath) -> bool | None:
    """True when a path the record names resolves to ``root``; False when none does;
    None when one could not be resolved and none matched — "could not tell", never "no"."""
    unknown = False
    for value, _ in _paths_in(document):
        clean = _clean_absolute(value)
        if clean is None:
            continue
        try:
            resolved = await _existing(environment, clean, "dir")
        except OSError:
            unknown = True
            continue
        if resolved == root:
            return True
    return None if unknown else False


def _archived(document: dict[str, Any]) -> bool:
    status = document["task"].get("status")
    return isinstance(status, str) and status.strip().lower() == "archived"


async def _repo_facts(environment: Any, work: PurePosixPath) -> RepoFacts:
    head = await environment.run(["git", "-C", str(work), "rev-parse", "--short", "HEAD"], timeout=GIT_TIMEOUT, read_only=True)
    status = await environment.run(["git", "-C", str(work), "status", "--porcelain"], timeout=GIT_TIMEOUT, read_only=True)
    return RepoFacts(
        head=head.stdout.strip() or None if head.outcome == "completed" and head.returncode == 0 else None,
        dirty=bool(status.stdout.strip()) if status.outcome == "completed" and status.returncode == 0 else None,
    )


async def _scan(environment: Any, tool_dir: str, project_root: str) -> RemoteTaskScan:
    tool, work = await resolve_tool(environment, tool_dir)
    clean_root = _clean_absolute(project_root)
    root = None if clean_root is None else await _existing(environment, clean_root, "dir")
    if root is None:
        raise RemoteTaskRefused(say("remote.project_root_missing", project_root=project_root))
    names = sorted(n for n in await environment.fs.listdir(Path(str(work / "tasks")))
                   if not n.startswith(("_", ".")))
    scan = RemoteTaskScan(state="complete", tool_dir=str(tool), work_root=str(work))
    if len(names) > MAX_TASK_DIRS:
        names, scan.truncated = names[:MAX_TASK_DIRS], True
    for name in names:
        if not TASK_NAME_RE.match(name):
            # A name that cannot go onto a command line cannot be opened here; it is
            # counted, not dropped, so the list says it may be incomplete.
            scan.unreadable += 1
            continue
        try:
            document = await _task_document(environment, work, name)
        except FileNotFoundError:
            continue  # a directory without a task.yaml is not a task
        except (OSError, ValueError):
            scan.unreadable += 1
            continue
        if _archived(document):
            continue
        names_root = await _names_root(environment, document, root)
        if names_root is None:
            scan.unreadable += 1
        elif names_root:
            scan.tasks.append(RemoteTask(task=name, title=_title_of(document)))
    if scan.unreadable or scan.truncated:
        scan.state = "partial"
    scan.remote_repo = await _repo_facts(environment, work)
    return scan


async def scan_remote_tasks(environment: Any, tool_dir: str | None, project_root: str) -> RemoteTaskScan:
    """The tasks on this environment's host that name its project root. Never raises:
    every failure is a scan that says it could not read."""
    if not tool_dir:
        return RemoteTaskScan(state="unavailable", reason=say("remote.tool_dir_unregistered"))
    try:
        return await asyncio.wait_for(_scan(environment, tool_dir, project_root), timeout=SCAN_TIMEOUT)
    except RemoteTaskRefused as exc:
        return RemoteTaskScan(state="unavailable", reason=str(exc))
    except (asyncio.TimeoutError, TimeoutError):
        return RemoteTaskScan(state="unavailable", reason=say("remote.scan_timeout", seconds=int(SCAN_TIMEOUT)))
    except Exception as exc:  # noqa: BLE001 - connection, SFTP, host key: said as "could not read"
        return RemoteTaskScan(state="unavailable", reason=say("remote.scan_unreadable", error_type=type(exc).__name__))


async def verify_task(environment: Any, tool_dir: str | None, project_root: str, task: str) -> tuple[PurePosixPath, PurePosixPath]:
    """Re-check, at the moment of opening, that ``task`` is one this environment's root
    is named by. Returns the resolved tool directory and work root; raises
    ``RemoteTaskRefused`` with the reason otherwise."""
    if not TASK_NAME_RE.match(task or ""):
        raise RemoteTaskRefused(say("remote.bad_task_name", task=task))
    if not tool_dir:
        raise RemoteTaskRefused(say("remote.tool_dir_unregistered"))
    tool, work = await resolve_tool(environment, tool_dir)
    clean_root = _clean_absolute(project_root)
    root = None if clean_root is None else await _existing(environment, clean_root, "dir")
    if root is None:
        raise RemoteTaskRefused(say("remote.project_root_missing", project_root=project_root))
    try:
        document = await _task_document(environment, work, task)
    except FileNotFoundError:
        raise RemoteTaskRefused(say("remote.no_such_task", task=task)) from None
    except (OSError, ValueError) as exc:
        raise RemoteTaskRefused(say("remote.task_yaml_unreadable", task=task, error=exc)) from None
    if _archived(document):
        raise RemoteTaskRefused(say("remote.task_archived", task=task))
    names_root = await _names_root(environment, document, root)
    if names_root is None:
        raise RemoteTaskRefused(say("remote.task_root_unreadable", task=task))
    if not names_root:
        raise RemoteTaskRefused(say("remote.task_not_this_project", task=task, project_root=project_root))
    return tool, work


def open_line(work: PurePosixPath, task: str) -> str:
    """The command a task terminal runs, in the tool directory: the host's own
    ``cw open``, with the work root Agora checked pinned as ``CW_WORK``."""
    if not TASK_NAME_RE.match(task):
        raise ValueError(f"unusable task name {task!r}")
    return (f"CW_WORK={shlex.quote(str(work))} CW_AGENT=claude bash ./scripts/cw.sh open {task}; "
            f"{_REMOTE_SHELL}")
