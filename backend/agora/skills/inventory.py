"""What the skills page shows besides the library itself (CR-75 slice 5): where the library
is, which projects a skill can be installed into, the commands a workflow provides, and
skills found elsewhere that can be imported.

Nothing here writes. The library location and the project list come from what Agora
already knows — its registered workspace sources and each task's ``task.yaml`` — and a
place that cannot be read is reported as such, never as empty.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agora.fsprobe import probe_path
from agora.skills.library import SKILL_FILE, front_matter, read_entry
from agora.terminal.targets import _is_kiro, claude_tool_dir_for
from agora.work.projects import scan_work_projects

#: Overrides where the library is.
LIBRARY_ENV = "AGORA_SKILLS_LIBRARY"


def library_root(sources: list[Path]) -> tuple[Path | None, str | None]:
    """``<claude-workspace work root>/skills`` — next to ``tasks/`` and ``lessons/``, in the
    work data the person already keeps in git. With no claude-workspace source, or more
    than one, the location is not guessed: ``AGORA_SKILLS_LIBRARY`` names it."""
    override = os.environ.get(LIBRARY_ENV, "").strip()
    if override:
        path = Path(override)
        return (path, None) if path.is_absolute() else (None, f"{LIBRARY_ENV} is not an absolute path")
    claude = [s for s in sources if not _is_kiro(s)]
    if len(claude) == 1:
        return claude[0] / "skills", None
    if not claude:
        return None, f"no claude-workspace work root is registered; set {LIBRARY_ENV}"
    return None, f"{len(claude)} claude-workspace work roots are registered; set {LIBRARY_ENV} to choose"


@dataclass
class SkillProject:
    key: str
    task: str
    title: str | None
    workspace: str
    #: The repository skills are installed into; None when there is none (``reason``).
    repo: str | None
    reason: str | None
    #: Paused or archived: its copies still count (a library original is not deleted from
    #: under them) and can still be removed (codex review).
    shelved: bool = False

    def payload(self) -> dict[str, Any]:
        return {"key": self.key, "task": self.task, "title": self.title, "workspace": self.workspace,
                "repo": self.repo, "reason": self.reason, "shelved": self.shelved}


def _repo_of(source: Path, task: str, kiro: bool, *, archived: bool = False) -> tuple[str | None, str | None]:
    """The task's repository: claude-workspace's ``primary_repo``, kiro-workspace's
    ``project_path``. Absolute, and a directory that is there."""
    key = "project_path" if kiro else "primary_repo"
    folder = source / "tasks" / ("_archive" if archived else "") / task
    try:
        document = yaml.safe_load((folder / "task.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, f"task.yaml cannot be read ({type(exc).__name__})"
    value = document.get(key) if isinstance(document, dict) else None
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, f"task.yaml has no {key}"
    if not isinstance(value, str) or not Path(value).is_absolute():
        return None, f"{key} is not an absolute path"
    probe = probe_path(Path(value))
    if probe.absent:
        return None, f"{key} is not on this machine ({value})"
    if probe.unknown or not probe.is_dir():
        return None, f"{key} cannot be read ({probe.reason() or 'not a directory'})"
    return str(Path(value)), None


def inventory_gaps(sources: list[Path]) -> list[str]:
    """Projects whose copies cannot be counted: a task directory that could not be read,
    or a repository that could not be. While there is one, "installed nowhere" is not
    known, so nothing that depends on it may proceed (codex review)."""
    # A directory with no task.yaml was never a project, so nothing was installed for it:
    # that is a definite absence, not a gap (it would block every delete for good).
    gaps = [f"{u['task'] or u['source']}: {u['reason']}" for u in scan_work_projects(sources).unreadable
            if u["reason"] != "no task.yaml"]
    for p in skill_projects(sources):
        if p.repo is None and p.reason and "cannot be read" in p.reason:
            gaps.append(f"{p.task}: {p.reason}")
    return gaps


def skill_projects(sources: list[Path]) -> list[SkillProject]:
    out: list[SkillProject] = []
    scan = scan_work_projects(sources)
    for p in scan.projects:
        repo, reason = _repo_of(Path(p.source), p.task, p.workspace == "kiro")
        out.append(SkillProject(p.key, p.task, p.title, p.workspace, repo, reason))
    for s in scan.shelved:
        repo, reason = _repo_of(Path(s.source), s.task, s.workspace == "kiro", archived=s.archived_dir)
        out.append(SkillProject(s.key, s.task, s.title, s.workspace, repo, reason, shelved=True))
    return out


@dataclass
class FoundSkill:
    """A skill somewhere other than the library."""
    name: str
    description: str | None
    #: ``workflow`` (a workspace's command: shown, not managed here) or ``importable``.
    kind: str
    where: str
    path: str
    error: str | None = None
    #: Departures from the Agent Skills standard, for an importable skill; a workflow
    #: command is another format on purpose and carries none.
    notes: list[dict[str, Any]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "kind": self.kind,
                "where": self.where, "path": self.path, "error": self.error, "notes": self.notes}


def _first_line(text: str) -> str | None:
    meta = front_matter(text)
    if meta and isinstance(meta.get("description"), str):
        return meta["description"]
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and line != "---":
            return line
    return None


def workflow_commands(sources: list[Path]) -> list[FoundSkill]:
    """The commands the registered workspaces provide: claude-workspace's
    ``.claude/commands/*.md`` (and their codex ports in ``.agents/skills``), kiro-workspace's
    ``.kiro/skills/*.md``. They belong to the workflow (CR-75 slice 4), so they are shown
    and not edited here (the person's choice, 2026-11-10)."""
    out: list[FoundSkill] = []
    seen: set[str] = set()
    for source in sources:
        if _is_kiro(source):
            places = [(source / ".kiro" / "skills", "kiro-workspace", "*.md")]
        else:
            tool = claude_tool_dir_for(source, sources)
            if tool is None:
                continue
            places = [(tool / ".claude" / "commands", "claude-workspace", "*.md"),
                      (tool / ".agents" / "skills", "claude-workspace (codex)", f"*/{SKILL_FILE}")]
        for folder, where, pattern in places:
            key = os.path.normcase(str(folder))
            if key in seen:
                continue
            seen.add(key)
            try:
                files = sorted(folder.glob(pattern))
            except OSError:
                continue
            for f in files:
                if f.name == "README.md":
                    continue
                name = f.parent.name if f.name == SKILL_FILE else f.stem
                try:
                    description = _first_line(f.read_text(encoding="utf-8"))
                    error = None
                except (OSError, UnicodeDecodeError) as exc:
                    description, error = None, type(exc).__name__
                out.append(FoundSkill(name, description, "workflow", where, str(f), error))
    return out


def reserved_names(commands: list[FoundSkill]) -> set[str]:
    """Names a library skill may not take: in claude a skill replaces the command of the
    same name (``/adr`` would stop being the workflow's)."""
    names = set()
    for c in commands:
        if c.where == "claude-workspace":
            names.add(c.name)
    return names


def global_dirs() -> list[tuple[Path, str]]:
    home = Path.home()
    return [(home / ".claude" / "skills", "~/.claude/skills"),
            (home / ".codex" / "skills", "~/.codex/skills"),
            (home / ".agents" / "skills", "~/.agents/skills"),
            (home / ".kiro" / "skills", "~/.kiro/skills")]


def importable(extra: list[tuple[Path, str]] | None = None) -> list[FoundSkill]:
    """Skill directories (``<dir>/<name>/SKILL.md``) in the CLIs' own places, plus
    ``extra``. Copies Agora installed (they carry the marker) are not offered."""
    from agora.skills.library import MARKER

    out: list[FoundSkill] = []
    for folder, where in [*global_dirs(), *(extra or [])]:
        try:
            children = sorted(folder.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith(".") or not (child / SKILL_FILE).is_file() or (child / MARKER).exists():
                continue
            entry = read_entry(child)
            out.append(FoundSkill(child.name, entry.description, "importable", where, str(child), entry.error,
                                  entry.notes))
    return out
