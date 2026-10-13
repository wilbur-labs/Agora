"""Projects to bring into Agora, from the workspaces' own task records (CR-52).

The person's work is already recorded: every claude-workspace task's ``task.yaml`` names
its repository (``primary_repo`` / ``repos[].path``), and every kiro-workspace task's
names it as ``project_path``. This module reads those records from the registered
workspace sources and turns them into candidates — one per repository on THIS machine —
each saying whether it can be registered as is, and if not, why.

Read-only. Registering is ``register_existing_repo`` below, which adds the project to
the store and touches the repository only to keep Agora's own worktrees out of
``git status`` (``.git/info/exclude``) — no ``git init``, no commit, no file of the
person's is changed.

Paths are written in several ways across the two tools; all are understood:
``E:/x``, ``E:\\x``, Git Bash's ``/e/x``. A path that cannot be on this machine — ``~/x``
or ``/workplace/x`` (the Linux machines), a template placeholder — is reported as such,
not silently dropped.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from agora.fsprobe import probe_path

#: `/e/Work/...` (Git Bash / MSYS) — a single drive letter as the first segment.
_MSYS_DRIVE = re.compile(r"^/([A-Za-z])(/.*)?$")
#: Template text left in a task.yaml (`<edit-me…>`, `/path/to/example-project`).
_PLACEHOLDER = re.compile(r"<[^>]*>|/path/to/")
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
#: Which spellings are local depends on the machine Agora runs on (it runs on Linux too).
_ON_WINDOWS = os.name == "nt"


@dataclass
class CandidateTask:
    source: str   # the workspace root the task lives in
    task: str     # the task's directory name
    title: str | None


@dataclass
class Candidate:
    #: As written in the first task record that named it.
    raw_path: str
    #: The local path it resolves to, or None when it cannot be on this machine.
    root: str | None
    #: "ok" — an existing git repository, ready to register;
    #: "registered" — already an Agora project (``registered_as``);
    #: "not_this_machine" — a Linux / home path (another machine's work);
    #: "placeholder" — template text never filled in (`<edit-me…>`, `/path/to/…`);
    #: "missing" — the path does not exist here;
    #: "not_git" — it exists but is not a git repository (Agora will not git-init it);
    #: "unreadable" — it could not be inspected (never read as "missing");
    #: "agora_tool" — Agora's own checkout or a workspace tool's directory, which the
    #: person does not want as a project (CR-56). Listed, never offered.
    status: str
    registered_as: str | None = None
    suggested_id: str | None = None
    suggested_name: str | None = None
    tasks: list[CandidateTask] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "raw_path": self.raw_path, "root": self.root, "status": self.status,
            "registered_as": self.registered_as, "suggested_id": self.suggested_id,
            "suggested_name": self.suggested_name,
            "tasks": [{"source": t.source, "task": t.task, "title": t.title} for t in self.tasks],
        }


def local_path_of(raw: str) -> Path | None:
    """The path on THIS machine a task record means, or None when it cannot be here.

    On Windows: drive paths (``E:/x``, ``E:\\x``) and Git Bash's ``/e/x``; ``~/x`` and other
    POSIX paths belong to the Linux machines. On Linux / macOS: absolute paths and
    ``~/x``; drive paths belong to a Windows machine. Placeholders are never local."""
    text = raw.strip().strip('"').strip("'").strip()
    if not text or _PLACEHOLDER.search(text):
        return None
    if _ON_WINDOWS:
        msys = _MSYS_DRIVE.match(text)
        if msys:
            text = f"{msys.group(1).upper()}:{msys.group(2) or '/'}"
        if PureWindowsPath(text).is_absolute() and PureWindowsPath(text).drive:
            return Path(text)
        return None
    if PureWindowsPath(text).drive:
        return None
    if text.startswith("~"):
        return Path(text).expanduser()  # this user's home, here
    return Path(text) if PurePosixPath(text).is_absolute() else None


def git_worktree_root_status(root: Path) -> str:
    """``ok`` when git itself says ``root`` is a working tree's top level; ``not_git``
    when git answers that it is not (an empty or stale ``.git`` included); ``unreadable``
    when git could not be asked (missing, timed out). A ``.git`` entry alone proves
    nothing (review)."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return "unreadable"
    if done.returncode != 0 or not done.stdout.strip():
        return "not_git"
    top = Path(done.stdout.strip())
    same = os.path.normcase(str(top.resolve(strict=False))) == os.path.normcase(str(root.resolve(strict=False)))
    return "ok" if same else "not_git"  # inside another repository, not its own root


#: What marks a directory as one of the tools rather than a piece of the person's work
#: (CR-56: "Agora 不应该再出现在项目里了" — and neither should the two workspaces).
#: Markers, not names: the private repository, the stable worktree and the public
#: repository are three checkouts under three names, and a folder called
#: "claude-workspace" that is someone's ordinary repository is not the tool.
_TOOL_MARKERS: tuple[tuple[tuple[str, ...], str], ...] = (
    # Any Agora checkout carries its backend package.
    (("backend", "agora", "__init__.py"), "agora_checkout"),
    # claude-workspace's tool directory: its work-root resolver, and its CLI — the split
    # layout (tool directory + .workroot) is found by either. Not `tasks/_template`: an
    # ordinary repository may keep a task template of its own (review).
    (("scripts", "workroot.sh"), "workspace_tool"),
    (("scripts", "cw.sh"), "workspace_tool"),
    # kiro-workspace's tool directory: its work-data helper.
    (("scripts", "kwd.sh"), "workspace_tool"),
)


@dataclass(frozen=True)
class ToolCheck:
    """Whether a directory is one of the tools (Agora itself, a workspace tool).

    ``kind`` is ``"agora_checkout"``, ``"workspace_source"``, ``"workspace_tool"``, or
    None when every marker was looked for and none is there. ``unknown`` lists the
    markers that could not be looked for: with no positive marker found and something
    unknown, the answer is not "not a tool" — it is unknown, and callers refuse it the
    way they refuse any directory that could not be inspected.
    """

    kind: str | None
    unknown: tuple[str, ...] = ()

    @property
    def is_tool(self) -> bool:
        return self.kind is not None

    @property
    def undecided(self) -> bool:
        return self.kind is None and bool(self.unknown)

    def reason(self) -> str:
        return ", ".join(self.unknown)


def tool_directory_check(root: Path, workspace_sources: list[Path]) -> ToolCheck:
    """Is ``root`` Agora's own checkout or a workspace tool's directory? (CR-56)

    A registered workspace source is a tool by definition — it is where the task records
    live, not a project those records describe. That comparison needs no file access.
    The rest is markers (``_TOOL_MARKERS``); a marker found is enough, and a marker
    that could not be probed is reported rather than read as absent.
    """
    try:
        key = _key(root)
        if any(_key(Path(source)) == key for source in workspace_sources):
            return ToolCheck("workspace_source")
    except (OSError, RuntimeError):
        pass  # the markers below still decide, or say they could not
    unknown: list[str] = []
    for parts, kind in _TOOL_MARKERS:
        probe = probe_path(root.joinpath(*parts))
        if probe.unknown:
            unknown.append(f"{'/'.join(parts)}: {probe.reason()}")
        elif not probe.absent:
            return ToolCheck(kind)
    return ToolCheck(None, tuple(unknown))


def _paths_in(document: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Every repository path a task.yaml names, with the ``repos[].name`` it was given if
    any: claude-workspace's ``primary_repo`` and ``repos[].path``, kiro-workspace's
    ``project_path``. First the primary one."""
    found: list[tuple[str, str | None]] = []
    for value in (document.get("primary_repo"), document.get("project_path")):
        if isinstance(value, str) and value.strip():
            found.append((value, None))
    repos = document.get("repos")
    if isinstance(repos, list):
        for repo in repos:
            if isinstance(repo, dict) and isinstance(repo.get("path"), str) and repo["path"].strip():
                name = repo.get("name")
                found.append((repo["path"], name.strip() if isinstance(name, str) and name.strip() else None))
    return found


def _title_of(document: dict[str, Any]) -> str | None:
    task = document.get("task")
    if isinstance(task, dict) and isinstance(task.get("title"), str) and task["title"].strip():
        return task["title"].strip()
    for key in ("title", "name"):  # kiro-workspace
        if isinstance(document.get(key), str) and document[key].strip():
            return document[key].strip()
    return None


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _identity(raw: str) -> str:
    """Which repository a path names, so ``E:/x``, ``E:\\x`` and ``/e/x`` count as one;
    a path that cannot be resolved here stands for itself."""
    local = local_path_of(raw)
    if local is not None:
        try:
            return _key(local)
        except (OSError, RuntimeError):
            pass
    return raw.strip()


def _suggest_id(root: Path, taken: set[str]) -> str:
    base = _ID_UNSAFE.sub("-", root.name).strip("-_").lower()[:64] or "project"
    if not base[0].isalnum():
        base = f"p-{base}"[:64]
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate, n = f"{base[: 64 - len(suffix)]}{suffix}", n + 1
    return candidate


def _task_documents(source_roots: list[Path], unreadable: list[dict[str, str]]):
    """Every ``<source>/tasks/*/task.yaml`` that parses to a mapping, as
    ``(source, task_dir, document)``. What cannot be read is appended to
    ``unreadable`` — said, not skipped."""
    for source in source_roots:
        tasks_dir = Path(source) / "tasks"
        try:
            task_dirs = sorted(p for p in tasks_dir.iterdir() if p.is_dir() and not p.name.startswith("_"))
        except OSError as exc:
            unreadable.append({"source": str(source), "task": "", "reason": f"{type(exc).__name__}"})
            continue
        for task_dir in task_dirs:
            manifest = task_dir / "task.yaml"
            probe = probe_path(manifest)
            if probe.absent:
                continue  # a task directory with no task.yaml simply names no repository
            if probe.unknown or not probe.is_file():
                unreadable.append({"source": str(source), "task": task_dir.name,
                                   "reason": probe.reason() if probe.unknown else "not a file"})
                continue
            try:
                document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                unreadable.append({"source": str(source), "task": task_dir.name, "reason": type(exc).__name__})
                continue
            if isinstance(document, dict):
                yield source, task_dir, document


def find_candidates(
    source_roots: list[Path], registered: dict[str, str],
) -> tuple[list[Candidate], list[dict[str, str]]]:
    """Candidates from every ``<source>/tasks/*/task.yaml``, one per repository, plus
    the task files that could not be read (said, not skipped). ``registered`` maps a
    normcased project root to its project id.

    ``source_roots`` are also what makes a candidate a workspace source and therefore
    ``agora_tool`` (CR-56): the workspaces being read are the registry's own list."""
    by_key: dict[str, Candidate] = {}
    elsewhere: list[Candidate] = []
    unreadable: list[dict[str, str]] = []
    taken = set(registered.values())
    for source, task_dir, document in _task_documents(source_roots, unreadable):
        ref = CandidateTask(source=str(source), task=task_dir.name, title=_title_of(document))
        seen_here: set[str] = set()
        named = _paths_in(document)
        # A task naming several repositories: its title describes the task, not any one
        # of them, so each is named after itself (CR-29).
        identities = {raw: _identity(raw) for raw, _ in named}
        several = len(set(identities.values())) > 1
        for raw, repo_name in named:
            if raw.strip() in seen_here:  # primary_repo and repos[].path often repeat
                continue
            seen_here.add(raw.strip())
            local = local_path_of(raw)
            if local is None:
                status = "placeholder" if _PLACEHOLDER.search(raw) else "not_this_machine"
                elsewhere.append(Candidate(raw_path=raw, root=None, status=status, tasks=[ref]))
                continue
            try:
                key = _key(local)
            except (OSError, RuntimeError):
                # One path that cannot even be resolved (an unreachable share) is
                # that candidate's problem, not the whole scan's (review, CR-28).
                elsewhere.append(Candidate(raw_path=raw, root=str(local), status="unreadable", tasks=[ref]))
                continue
            if key in by_key:
                if all(t.task != ref.task or t.source != ref.source for t in by_key[key].tasks):
                    by_key[key].tasks.append(ref)
                continue
            repo_name = repo_name or next(
                (n for r, n in named if n and identities[r] == identities[raw]), None)
            name = (repo_name or local.name) if several else (ref.title or local.name)
            by_key[key] = _classify(raw, local, key, registered, taken, ref, name, source_roots)
    return list(by_key.values()) + elsewhere, unreadable


def _classify(raw, local: Path, key: str, registered, taken: set[str], ref, name: str,
              source_roots: list[Path]) -> Candidate:
    root = str(local.resolve(strict=False))
    if key in registered:
        # Before the tool check on purpose: an Agora checkout that is already a project
        # is reported as the project it is, so it can be found and removed (CR-56).
        return Candidate(raw_path=raw, root=root, status="registered",
                         registered_as=registered[key], tasks=[ref])
    probe = probe_path(local)
    if probe.unknown:
        return Candidate(raw_path=raw, root=root, status="unreadable", tasks=[ref])
    if probe.absent:
        return Candidate(raw_path=raw, root=root, status="missing", tasks=[ref])
    if not probe.is_dir():
        return Candidate(raw_path=raw, root=root, status="not_git", tasks=[ref])
    tool = tool_directory_check(local, source_roots)
    if tool.is_tool:
        return Candidate(raw_path=raw, root=root, status="agora_tool", tasks=[ref])
    if tool.undecided:
        # Could not tell whether it is a tool: not offered, and said as uninspectable
        # rather than "ready" — the same as a directory that could not be probed.
        return Candidate(raw_path=raw, root=root, status="unreadable", tasks=[ref])
    git_status = git_worktree_root_status(local)
    if git_status != "ok":
        return Candidate(raw_path=raw, root=root, status=git_status, tasks=[ref])
    suggested = _suggest_id(local, taken)
    taken.add(suggested)
    # Bounded to what registration accepts (200), so an "ok" is always importable (CR-28).
    return Candidate(raw_path=raw, root=root, status="ok", suggested_id=suggested,
                     suggested_name=name[:200], tasks=[ref])


#: kiro-workspace keeps a task's goal in RESUME.md, as a line "目标：…" (CR-35).
_RESUME_GOAL = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?目标(?:\*\*)?\s*[：:]\s*(?P<first>.*)$")
#: An unfilled template goal: the whole value is one `<…>` marker, or an `<edit-me…>`.
#: Not any angle bracket — "a <canvas> based editor" is a real goal (review).
_GOAL_PLACEHOLDER = re.compile(r"^\s*<[^>]+>\s*$|<edit-me")
_CJK_END = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uff00-\uffef]$")


@dataclass
class RecordedGoal:
    source: str
    task: str
    title: str | None
    #: The goal as the person wrote it, or None when the record states none.
    goal: str | None
    #: Where it was read: "task.yaml" or "RESUME.md"; None with no goal.
    found_in: str | None

    def payload(self) -> dict[str, Any]:
        return {"source": self.source, "task": self.task, "title": self.title,
                "goal": self.goal, "found_in": self.found_in}


def _goal_in_manifest(document: dict[str, Any]) -> str | None:
    task = document.get("task")
    for value in ((task or {}).get("goal") if isinstance(task, dict) else None, document.get("goal")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _goal_in_resume(text: str) -> str | None:
    """The first "目标：" line of a RESUME.md and its continuation lines (a wrapped
    paragraph), up to a blank line, a heading or a list item."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = _RESUME_GOAL.match(line)
        if not match:
            continue
        goal = match.group("first").strip()
        for following in lines[i + 1:]:
            stripped = following.strip()
            if not stripped or stripped[0] in "#-*>|":
                break
            # Chinese wraps mid-sentence with no space; English needs one.
            goal += ("" if _CJK_END.search(goal) else " ") + stripped
        return goal.strip() or None
    return None


def recorded_goals(
    source_roots: list[Path], project_root: Path,
) -> tuple[list[RecordedGoal], list[dict[str, str]]]:
    """The goals the person already wrote down for this repository: one per task record
    (in any registered workspace) that names it. A record that names it but states no
    goal is listed with ``goal=None`` — the page says so instead of hiding the task."""
    unreadable: list[dict[str, str]] = []
    want = _identity(str(project_root))
    found: list[RecordedGoal] = []
    for source, task_dir, document in _task_documents(source_roots, unreadable):
        if all(_identity(raw) != want for raw, _ in _paths_in(document)):
            continue
        goal, found_in = _goal_in_manifest(document), "task.yaml"
        if goal is not None and _GOAL_PLACEHOLDER.search(goal):
            goal = None  # an unfilled template; the RESUME may still say it (review)
        if goal is None:
            found_in = "RESUME.md"
            resume = task_dir / "RESUME.md"
            probe = probe_path(resume)
            if probe.unknown:
                unreadable.append({"source": str(source), "task": task_dir.name, "reason": probe.reason()})
            elif probe.is_file():
                try:
                    goal = _goal_in_resume(resume.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as exc:
                    unreadable.append({"source": str(source), "task": task_dir.name, "reason": type(exc).__name__})
        if goal is not None and _GOAL_PLACEHOLDER.search(goal):
            goal = None  # a template's "<edit-me>" is not a goal
        found.append(RecordedGoal(source=str(source), task=task_dir.name, title=_title_of(document),
                                  goal=goal, found_in=found_in if goal else None))
    return found, unreadable


def exclude_agora_worktrees(root: Path) -> bool:
    """Add ``.agora/workspaces/`` to the repository's ``.git/info/exclude`` (local, never
    committed), so Agora's worktrees do not show in the person's ``git status``. True
    when the entry is there afterwards. Never raises: registering does not depend on it."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
        ).stdout.strip()
        path = Path(out)
        if not path.is_absolute():
            path = root / path
        line = ".agora/workspaces/"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if line in existing.splitlines():
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(f"# Agora's per-agent worktrees (added when the project was registered)\n{line}\n")
        return True
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return False
