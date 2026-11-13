"""Skills from outside (workspace CR-98): a published repository of skills, listed in
Agora, read before anything is taken, and copied into the library one at a time.

**What a source is.** A git repository whose directories hold ``SKILL.md`` files — the
Agent Skills layout. One is preset, Anthropic's own (the person's choice, 2026-11-13:
only the official one; adding an address by hand is not built). Agora keeps a shallow
clone of it under its data directory and reads the listing from that clone, so the page
never waits on the network and says when its copy was fetched.

**Nothing from a source runs, and nothing arrives unseen.** A skill is instructions the
person's agents will follow, and may carry scripts. So:

- the clone is only ever read as files — no hook, no submodule, no link is followed;
- importing takes the hash of the skill *as it was shown*: the route that returns a
  skill's content returns that hash, and an import without it, or with the hash of some
  other content, is refused. "Look first" is therefore the API's rule, not the page's
  good manners (the person asked for exactly this);
- what is imported is a copy in the library, the same as a skill imported from a CLI's
  own folder: installing it into a project stays a separate, deliberate step.

**What this does not do, stated.** It does not vouch for a skill: the hash proves the
copy is what was displayed, not that it is safe or that every file was read by a human —
the page shows ``SKILL.md`` and names the other files. It does not track updates: a
library copy is not compared against its source again. It does not record where a
library skill came from (a note inside the skill would change its hash and show every
installed copy as outdated).

Reads follow the library's rule: a source that was never fetched is ``not_fetched``, one
whose clone cannot be read is ``unreadable``, and neither is an empty list.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agora.fsprobe import probe_path
from agora.skills.library import (
    SKILL_FILE, SkillChanged, SkillLibrary, SkillRefused, front_matter, standard_notes, tree_files, tree_sha,
)

CACHE_DIR = "skill-sources"
STAMP = "agora-source.json"
#: A clone that takes longer than this is abandoned: the page is waiting on it.
FETCH_TIMEOUT_SECONDS = 180
#: How much of one skill's listing a source may hold before the listing stops looking.
MAX_SKILLS = 2000

#: Held while a source's copy is swapped, and for the whole of every read that hands out
#: or checks an import hash. Without it a fetch from another tab could replace the copy
#: between reading ``SKILL.md`` and hashing the tree: the page would show the old text
#: with the new tree's hash, and the import would take a skill nobody was shown (codex
#: review). The listing takes it too, or it could pair one copy's commit with another's
#: skills. Re-entrant because a content read lists first. One process: two Agora
#: installs do not share a data directory.
_COPY_LOCK = threading.RLock()


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    url: str

    def payload(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "url": self.url}


#: The preset sources. Only the official repository, by the person's decision.
SOURCES: dict[str, Source] = {
    "anthropic": Source("anthropic", "Anthropic — anthropics/skills", "https://github.com/anthropics/skills"),
}


@dataclass
class SourceSkill:
    #: Directory inside the source, posix, relative to its top. The id of this skill.
    path: str
    name: str
    description: str | None
    license: str | None = None
    error: str | None = None
    notes: list[dict[str, Any]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "description": self.description,
                "license": self.license, "error": self.error, "notes": self.notes}


@dataclass
class SourceRead:
    source: Source
    #: ``ok`` | ``not_fetched`` | ``unreadable``
    state: str
    fetched: str | None = None
    commit: str | None = None
    reason: str | None = None
    skills: list[SourceSkill] = field(default_factory=list)
    #: True when the listing stopped at ``MAX_SKILLS``: the list is then a part, said so.
    truncated: bool = False

    def payload(self) -> dict[str, Any]:
        return {**self.source.payload(), "state": self.state, "fetched": self.fetched, "commit": self.commit,
                "reason": self.reason, "truncated": self.truncated, "skills": [s.payload() for s in self.skills]}


def source_of(source_id: str) -> Source:
    source = SOURCES.get(source_id)
    if source is None:
        raise SkillRefused(f"no skills source {source_id!r}")
    return source


def _clone_dir(data_dir: Path, source: Source) -> Path:
    return data_dir / CACHE_DIR / source.id


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """git, unable to ask anything or to run what a repository brings with it."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "GCM_INTERACTIVE": "never"}
    base = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.symlinks=false",
            # Files as published: this machine's autocrlf would rewrite every line end,
            # and a shell script copied into a project with CRLF does not run.
            "-c", "core.autocrlf=false", "-c", "credential.helper="]
    return subprocess.run(base + args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=FETCH_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)


def fetch(data_dir: Path, source: Source) -> SourceRead:
    """Download the source afresh and put it in place of the previous copy. The old copy
    stays until the new one is complete, so a failed fetch leaves the listing as it was."""
    target = _clone_dir(data_dir, source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".tmp-{source.id}-"))
    try:
        try:
            done = _git(["clone", "--depth", "1", "--no-tags", "--single-branch", "--no-recurse-submodules",
                         source.url, str(staging / "repo")])
        except subprocess.TimeoutExpired:
            raise SkillRefused(f"fetching {source.url} took longer than {FETCH_TIMEOUT_SECONDS} seconds") from None
        except OSError as exc:
            raise SkillRefused(f"git could not be run: {type(exc).__name__}") from None
        if done.returncode != 0:
            last = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
            raise SkillRefused(f"fetching {source.url} failed: {last[0][:300]}")
        head = _git(["rev-parse", "HEAD"], cwd=staging / "repo")
        stamp = {"url": source.url, "commit": head.stdout.strip() if head.returncode == 0 else None,
                 "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        # The history is not needed again and is the larger half of the download.
        _remove_git_dir(staging / "repo" / ".git")
        (staging / STAMP).write_text(json.dumps(stamp), encoding="utf-8")
        old = target.with_name(f".old-{source.id}")
        with _COPY_LOCK:
            shutil.rmtree(old, ignore_errors=True)
            if not probe_path(target).absent:
                os.rename(target, old)
            os.rename(staging, target)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return read(data_dir, source)


def _remove_git_dir(path: Path) -> None:
    """git marks its pack files read-only, which on Windows also forbids deleting them."""
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), 0o700)
    shutil.rmtree(path)


def _skill_dirs(repo: Path) -> tuple[list[Path], bool]:
    found: list[Path] = []

    def fail(exc: OSError) -> None:
        raise exc

    for dirpath, dirnames, filenames in os.walk(repo, onerror=fail, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not (here / d).is_symlink())
        if SKILL_FILE in filenames:
            found.append(here)
            # A skill's own sub-directories are its files, not further skills.
            dirnames[:] = []
            if len(found) >= MAX_SKILLS:
                return found, True
    return found, False


def _entry(repo: Path, path: Path) -> SourceSkill:
    rel = path.relative_to(repo).as_posix()
    name = path.name
    try:
        tree_files(path)
        text = (path / SKILL_FILE).read_text(encoding="utf-8")
    except SkillRefused as exc:
        return SourceSkill(rel, name, None, error=str(exc))
    except (OSError, UnicodeDecodeError) as exc:
        return SourceSkill(rel, name, None, error=type(exc).__name__)
    meta = front_matter(text) or {}
    description = meta.get("description") if isinstance(meta.get("description"), str) else None
    license_ = meta.get("license") if isinstance(meta.get("license"), str) else None
    return SourceSkill(rel, name, description, license_, notes=standard_notes(name, text))


def read(data_dir: Path, source: Source) -> SourceRead:
    """The listing from the copy on disk. No network."""
    with _COPY_LOCK:
        return _read(data_dir, source)


def _read(data_dir: Path, source: Source) -> SourceRead:
    target = _clone_dir(data_dir, source)
    probe = probe_path(target)
    if probe.absent:
        return SourceRead(source, "not_fetched")
    if probe.unknown or not probe.is_dir():
        return SourceRead(source, "unreadable", reason=probe.reason() if probe.unknown else "not a directory")
    try:
        stamp = json.loads((target / STAMP).read_text(encoding="utf-8"))
        if not isinstance(stamp, dict) or stamp.get("url") != source.url:
            # A copy of some other address is not this source's listing.
            return SourceRead(source, "not_fetched")
        dirs, truncated = _skill_dirs(target / "repo")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return SourceRead(source, "unreadable", reason=type(exc).__name__)
    repo = target / "repo"
    skills = sorted((_entry(repo, d) for d in dirs if d != repo), key=lambda s: (s.name, s.path))
    return SourceRead(source, "ok", fetched=stamp.get("fetched"), commit=stamp.get("commit"),
                      skills=skills, truncated=truncated)


def _skill_path(data_dir: Path, source: Source, rel: str) -> tuple[Path, Path]:
    """The directory of one listed skill. Only a path the listing itself gives is taken:
    this is not a way to read other files of the clone, or files outside it."""
    listing = read(data_dir, source)
    if listing.state != "ok":
        raise SkillRefused("this source has not been fetched" if listing.state == "not_fetched"
                           else f"this source's copy cannot be read: {listing.reason}")
    if rel not in {s.path for s in listing.skills}:
        raise SkillRefused("that is not one of the skills this source lists")
    repo = _clone_dir(data_dir, source) / "repo"
    return repo, repo / rel


def content(data_dir: Path, source: Source, rel: str) -> dict[str, Any]:
    """One skill, for reading before it is taken: ``SKILL.md``, every file's name and
    size, and the hash an import must present."""
    with _COPY_LOCK:
        repo, path = _skill_path(data_dir, source, rel)
        files = tree_files(path)
        text = (path / SKILL_FILE).read_bytes().decode("utf-8")
        entry = _entry(repo, path)
        return {**entry.payload(), "source": source.id, "content": text, "sha": tree_sha(path),
                "files": [{"path": r, "bytes": p.stat().st_size} for r, p in files]}


def import_skill(data_dir: Path, source: Source, rel: str, sha: str, library: SkillLibrary,
                 *, reserved: set[str]) -> dict[str, Any]:
    """Copy one listed skill into the library, as it was shown. ``sha`` is the hash the
    content route returned; a skill never shown has none to give, and one fetched again
    since has another."""
    with _COPY_LOCK:
        _, path = _skill_path(data_dir, source, rel)
        if not sha or tree_sha(path) != sha:
            raise SkillChanged("this skill is not as it was shown (the source was fetched again, or it was never "
                               "opened); open it and read it before importing")
        entry = library.import_from(path, path.name, reserved=reserved)
        if entry.sha != sha:
            # The copy is not what was read: remove it rather than leave an unseen skill.
            shutil.rmtree(library.path_of(path.name), ignore_errors=True)
            raise SkillChanged("the skill changed while it was being copied; nothing was imported")
        return entry.payload()
