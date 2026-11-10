"""Installing a library skill into a project's repository (CR-75 slice 5).

One install is three copies, one where each CLI reads project skills:

* claude — ``.claude/skills/<name>/`` (the work page adds the repository with
  ``--add-dir``, which loads that directory's skills);
* codex — ``.agents/skills/<name>/``;
* kiro — ``.kiro/skills/<name>/`` (kiro's default agent reads ``.kiro/skills/*/SKILL.md``;
  a kiro-workspace task agent reads only the skills its own profile lists, which this
  does not edit).

Each copy carries ``.agora-skill.json``: the skill's name and the hash of what was copied.
That is how a copy is known to be Agora's, how a newer library version is noticed, and
how an edit made to the copy in place is noticed — such a copy is never overwritten or
deleted without the person saying so (``force``). A directory without the marker is not
Agora's, and is never touched.

The person chose (2026-11-10) that these copies stay out of the project's git: each
installed directory gets a line in the repository's ``.git/info/exclude``, anchored at the
repository's top even when the project's directory is below it. Uninstalling leaves the
lines: linked worktrees share one exclude file, so another worktree may still hold a
copy, and a line for a directory that is not there costs nothing (codex review). A
directory that is not a git repository gets the copies and no exclude, and the result
says so.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.fsprobe import probe_path
from agora.skills.library import MARKER, SkillLibrary, SkillRefused, check_name, copy_tree, tree_sha

#: Where each CLI reads a project's skills, relative to the repository root.
TARGETS: dict[str, str] = {"claude": ".claude/skills", "codex": ".agents/skills", "kiro": ".kiro/skills"}
EXCLUDE_HEADER = "# Agora skills (installed copies; the originals are in Agora's skills library)"


@dataclass
class CopyState:
    agent: str
    #: ``absent`` / ``current`` / ``outdated`` (library changed since) / ``modified`` (the
    #: copy was edited in place) / ``foreign`` (not installed by Agora) / ``unreadable``.
    state: str
    reason: str | None = None

    def payload(self) -> dict[str, Any]:
        return {"agent": self.agent, "state": self.state, "reason": self.reason}


def _marker(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((path / MARKER).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def copy_state(repo: Path, agent: str, name: str, library_sha: str | None) -> CopyState:
    path = repo / TARGETS[agent] / name
    probe = probe_path(path)
    if probe.absent:
        return CopyState(agent, "absent")
    if probe.unknown:
        return CopyState(agent, "unreadable", probe.reason())
    if not probe.is_dir():
        return CopyState(agent, "foreign", "not a directory")
    marker = _marker(path)
    if marker is None:
        return CopyState(agent, "foreign")
    if marker.get("skill") != name or not isinstance(marker.get("sha"), str):
        return CopyState(agent, "unreadable", f"{MARKER} cannot be read")
    try:
        now = tree_sha(path)
    except (SkillRefused, OSError) as exc:
        return CopyState(agent, "unreadable", str(exc) if isinstance(exc, SkillRefused) else type(exc).__name__)
    if now != marker["sha"]:
        return CopyState(agent, "modified")
    if library_sha is not None and now != library_sha:
        return CopyState(agent, "outdated")
    return CopyState(agent, "current")


def skill_states(repo: Path, name: str, library_sha: str | None) -> list[CopyState]:
    return [copy_state(repo, agent, name, library_sha) for agent in TARGETS]


def summary(states: list[CopyState]) -> str:
    """One word for a project × skill: ``installed`` (all current), ``not_installed``, or
    the worst state among the copies."""
    kinds = {s.state for s in states}
    if kinds == {"current"}:
        return "installed"
    if kinds == {"absent"}:
        return "not_installed"
    for worst in ("unreadable", "foreign", "modified", "outdated"):
        if worst in kinds:
            return worst
    return "partial"


def _exclude_file(repo: Path) -> tuple[Path, str] | None:
    """``info/exclude`` of the repository holding ``repo`` (worktrees included) and
    ``repo``'s path below that repository's top (``""`` at the top, else ``"sub/dir/"``),
    or None when it is not in one."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir", "--show-prefix"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = out.stdout.splitlines()
    if out.returncode != 0 or not lines or not lines[0].strip():
        return None
    common = Path(lines[0].strip())
    if not common.is_absolute():
        common = repo / common
    prefix = lines[1].strip() if len(lines) > 1 else ""
    return common / "info" / "exclude", prefix


def _literal(path: str) -> str:
    """``path`` as a gitignore pattern that matches only itself: ``apps/[web]`` would
    otherwise be a character class (codex review)."""
    return re.sub(r"([\\*?\[\]!# ])", r"\\\1", path)


def _exclude_lines(name: str, prefix: str = "") -> list[str]:
    return [f"/{_literal(prefix)}{TARGETS[a]}/{name}/" for a in TARGETS]


def _edit_exclude(repo: Path, name: str, *, add: bool) -> bool | None:
    """Add this skill's lines. None when ``repo`` is not in a git repository."""
    found = _exclude_file(repo)
    if found is None:
        return None
    path, prefix = found
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    lines = text.splitlines()
    missing = [w for w in _exclude_lines(name, prefix) if w not in lines]
    if not add or not missing:
        return True
    if EXCLUDE_HEADER not in lines:
        missing.insert(0, EXCLUDE_HEADER)
    head = text if not text or text.endswith("\n") else text + "\n"
    new = head + "\n".join(missing) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return True


def install(library: SkillLibrary, repo: Path, name: str, *, force: bool = False) -> dict[str, Any]:
    """Copy the library's ``name`` into all three places in ``repo``. Checked for all three
    before anything is written: a foreign directory refuses the whole install; a copy
    edited in place refuses unless ``force``."""
    check_name(name)
    source = library.path_of(name)
    entry = library.entry(name)
    if entry.error or entry.sha is None:
        raise SkillRefused(f"the library's {name!r} cannot be read: {entry.error}")
    states = skill_states(repo, name, entry.sha)
    for s in states:
        if s.state in ("foreign", "unreadable"):
            raise SkillRefused(f"{TARGETS[s.agent]}/{name} is {s.state} ({s.reason or 'not installed by Agora'}); "
                               "it is left as it is")
        if s.state == "modified" and not force:
            raise SkillRefused(f"{TARGETS[s.agent]}/{name} was edited in place since it was installed")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for s in states:
        if s.state == "current":
            continue
        target = repo / TARGETS[s.agent] / name
        if s.state != "absent":
            shutil.rmtree(target)
        copy_tree(source, target)
        (target / MARKER).write_text(json.dumps({"skill": name, "sha": entry.sha, "installed": stamp}, indent=2) + "\n",
                                     encoding="utf-8")
    excluded = _edit_exclude(repo, name, add=True)
    return {"name": name, "states": [s.payload() for s in skill_states(repo, name, entry.sha)],
            "excluded": excluded}


def uninstall(repo: Path, name: str, *, force: bool = False) -> dict[str, Any]:
    """Remove Agora's copies of ``name`` from ``repo``. A foreign directory is left; a copy
    edited in place refuses unless ``force``."""
    check_name(name)
    states = skill_states(repo, name, None)
    for s in states:
        if s.state == "unreadable":
            raise SkillRefused(f"{TARGETS[s.agent]}/{name} cannot be read ({s.reason})")
        if s.state == "modified" and not force:
            raise SkillRefused(f"{TARGETS[s.agent]}/{name} was edited in place since it was installed")
    for s in states:
        if s.state in ("current", "outdated", "modified"):
            shutil.rmtree(repo / TARGETS[s.agent] / name)
    left = [s.agent for s in states if s.state == "foreign"]
    return {"name": name, "left_foreign": left}
