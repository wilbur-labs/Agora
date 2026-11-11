"""The skills library (workspace CR-75 slice 5): one directory of skills that Agora owns,
``<library>/<name>/SKILL.md``, copied into a project when the person installs one there.

**Why a library of its own.** Before this, a skill lived wherever one agent CLI reads it —
``~/.claude/skills``, ``~/.codex/skills``, a workspace's ``.agents/skills`` holding
hand-made copies of claude commands — and the same skill existed once per agent, kept in
step by hand. The person chose (2026-11-10) one original per skill, here, and copies
made from it. Skills already elsewhere are imported one by one at the person's choice;
nothing is moved and the original is never touched.

**One format.** ``SKILL.md`` with ``name`` and ``description`` in YAML front matter is
read by all three CLIs: claude (``.claude/skills/<name>/SKILL.md``), codex
(``.agents/skills/<name>/SKILL.md``) and kiro's default agent
(``skill://.kiro/skills/*/SKILL.md``, read from kiro-cli 3.1.0's built-in agent).

Reads follow ``fsprobe``'s rule: an unreadable library is reported as unreadable, never
as empty, and one unreadable skill is listed with its reason rather than dropped.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agora.fsprobe import probe_path

SKILL_FILE = "SKILL.md"
#: Written into every installed copy; never part of a skill's content.
MARKER = ".agora-skill.json"
#: Lower-case words joined by hyphens: what claude requires of a skill's ``name``, and
#: safe as a directory name and a git exclude line on every platform.
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
MAX_SKILL_MD_BYTES = 256 * 1024
# The Agent Skills standard (agentskills.io/specification), which claude, codex and kiro
# all read. What it says "must" about the two required fields is refused on write; the
# rest is reported as notes, so a skill written elsewhere can still be read and imported.
MAX_DESCRIPTION_CHARS = 1024
MAX_COMPATIBILITY_CHARS = 500
RECOMMENDED_MAX_LINES = 500
STANDARD_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
#: A skill directory is a handful of files. Past these it is not one, and copying it on
#: every install is not something a click should do.
MAX_FILES = 200
MAX_TREE_BYTES = 8 * 1024 * 1024


class SkillRefused(Exception):
    """A request the library will not carry out; the message says why."""


class SkillChanged(SkillRefused):
    """The skill changed since the person read it: refused rather than overwritten."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_files(root: Path) -> list[tuple[str, Path]]:
    """``(relative posix path, path)`` for every file under ``root``, the marker left out.
    A link or anything that is neither file nor directory is refused: a copy would follow
    it out of the skill, and a hash could not say what it covered."""
    out: list[tuple[str, Path]] = []
    total = 0
    def fail(exc: OSError) -> None:
        # A directory that cannot be listed is not an empty one: a copy or a hash
        # without it would be reported as the whole skill (codex review).
        raise exc

    for dirpath, dirnames, filenames in os.walk(root, onerror=fail, followlinks=False):
        here = Path(dirpath)
        for name in list(dirnames):
            if (here / name).is_symlink():
                raise SkillRefused(f"{(here / name).relative_to(root).as_posix()} is a link")
        dirnames.sort()
        for name in sorted(filenames):
            path = here / name
            rel = path.relative_to(root).as_posix()
            if rel == MARKER:
                continue
            if path.is_symlink() or not path.is_file():
                raise SkillRefused(f"{rel} is not a regular file")
            total += path.stat().st_size
            out.append((rel, path))
            if len(out) > MAX_FILES or total > MAX_TREE_BYTES:
                raise SkillRefused(f"more than {MAX_FILES} files or {MAX_TREE_BYTES // 1024 // 1024} MB")
    return out


def tree_sha(root: Path) -> str:
    """One hash of a skill directory's content: names and bytes, the marker excluded."""
    digest = hashlib.sha256()
    for rel, path in tree_files(root):
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(_sha(path.read_bytes()).encode("ascii") + b"\n")
    return digest.hexdigest()


def front_matter(text: str) -> dict[str, Any] | None:
    """The YAML block between the first two ``---`` lines, or None when there is none or
    it is not a mapping."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                return None
            return data if isinstance(data, dict) else None
    return None


def check_skill_md(name: str, text: str) -> str:
    """The description, or SkillRefused: a SKILL.md every CLI can load needs front matter
    whose ``name`` is the directory's and a non-empty ``description`` (what the agent
    decides by)."""
    if len(text.encode("utf-8")) > MAX_SKILL_MD_BYTES:
        raise SkillRefused(f"SKILL.md is larger than {MAX_SKILL_MD_BYTES // 1024} KB")
    meta = front_matter(text)
    if meta is None:
        raise SkillRefused("SKILL.md needs a front matter block (--- name / description ---) at the top")
    if meta.get("name") != name:
        raise SkillRefused(f"the front matter's name must be {name!r}")
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillRefused("the front matter needs a description")
    if len(description.strip()) > MAX_DESCRIPTION_CHARS:
        raise SkillRefused(f"the description is {len(description.strip())} characters; the Agent Skills "
                           f"standard allows at most {MAX_DESCRIPTION_CHARS}")
    return description.strip()


def standard_notes(name: str, text: str) -> list[dict[str, Any]]:
    """Where a SKILL.md departs from the Agent Skills standard, as ``{code, ...}`` for the
    page to word. Empty means nothing was found by these checks — not a certificate: the
    standard's own validator (``skills-ref validate``) is the authority."""
    meta = front_matter(text)
    if meta is None:
        return [{"code": "no_front_matter"}]
    notes: list[dict[str, Any]] = []
    if not NAME_RE.fullmatch(name) or "--" in name:
        notes.append({"code": "bad_name"})
    if meta.get("name") != name:
        notes.append({"code": "name_mismatch", "found": str(meta.get("name"))[:80]})
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        notes.append({"code": "no_description"})
    elif len(description.strip()) > MAX_DESCRIPTION_CHARS:
        notes.append({"code": "description_too_long", "length": len(description.strip()), "max": MAX_DESCRIPTION_CHARS})
    compatibility = meta.get("compatibility")
    if compatibility is not None and (not isinstance(compatibility, str)
                                      or not 1 <= len(compatibility) <= MAX_COMPATIBILITY_CHARS):
        notes.append({"code": "bad_compatibility", "max": MAX_COMPATIBILITY_CHARS})
    extra = sorted(str(k) for k in meta if k not in STANDARD_FIELDS)
    if extra:
        notes.append({"code": "extra_fields", "fields": ", ".join(extra)[:200]})
    lines = len(text.splitlines())
    if lines > RECOMMENDED_MAX_LINES:
        notes.append({"code": "long_body", "lines": lines, "max": RECOMMENDED_MAX_LINES})
    return notes


def check_name(name: str) -> None:
    """A name that is safe to look up: what every version of the library has accepted.
    Reading, deleting and uninstalling use this, so a skill named before the standard's
    stricter rule (``a--b``) can still be managed (codex review)."""
    if not NAME_RE.fullmatch(name):
        raise SkillRefused("a name is lower-case letters, digits and hyphens (at most 64)")


def check_new_name(name: str) -> None:
    """A name a new skill may take: the Agent Skills standard also forbids ``--``."""
    check_name(name)
    if "--" in name:
        raise SkillRefused("a name is lower-case letters, digits and single hyphens (at most 64)")


@dataclass
class SkillEntry:
    name: str
    description: str | None
    #: Hash of the whole directory; None when it could not be read.
    sha: str | None
    #: Newest file time, ISO-8601 UTC.
    updated: str | None
    #: Why it could not be read, when it could not.
    error: str | None = None
    #: Departures from the Agent Skills standard (``standard_notes``); empty when unread.
    notes: list[dict[str, Any]] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "sha": self.sha,
                "updated": self.updated, "error": self.error, "notes": self.notes}


@dataclass
class LibraryRead:
    #: ``ok``; ``absent`` = no library directory yet (nothing created, an honest empty);
    #: ``unreadable``; ``unknown`` = Agora does not know where the library is.
    state: str
    root: str | None
    skills: list[SkillEntry] = field(default_factory=list)
    reason: str | None = None

    def payload(self) -> dict[str, Any]:
        return {"state": self.state, "root": self.root, "reason": self.reason,
                "skills": [s.payload() for s in self.skills]}


def read_entry(path: Path) -> SkillEntry:
    name = path.name
    try:
        files = tree_files(path)
        if SKILL_FILE not in {rel for rel, _ in files}:
            return SkillEntry(name, None, None, None, f"no {SKILL_FILE}")
        text = (path / SKILL_FILE).read_text(encoding="utf-8")
        meta = front_matter(text) or {}
        description = meta.get("description") if isinstance(meta.get("description"), str) else None
        newest = max(p.stat().st_mtime for _, p in files)
        return SkillEntry(name, description, tree_sha(path),
                          datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds"),
                          notes=standard_notes(name, text))
    except SkillRefused as exc:
        return SkillEntry(name, None, None, None, str(exc))
    except (OSError, UnicodeDecodeError) as exc:
        return SkillEntry(name, None, None, None, type(exc).__name__)


class SkillLibrary:
    def __init__(self, root: Path | None, reason: str | None = None):
        self.root = root
        self.reason = reason

    def _need_root(self) -> Path:
        if self.root is None:
            raise SkillRefused(self.reason or "the skills library's location is not known")
        return self.root

    def read(self) -> LibraryRead:
        if self.root is None:
            return LibraryRead("unknown", None, reason=self.reason)
        probe = probe_path(self.root)
        if probe.absent:
            return LibraryRead("absent", str(self.root))
        if probe.unknown or not probe.is_dir():
            return LibraryRead("unreadable", str(self.root),
                               reason=probe.reason() if probe.unknown else "not a directory")
        try:
            children = sorted(self.root.iterdir())
        except OSError as exc:
            return LibraryRead("unreadable", str(self.root), reason=type(exc).__name__)
        skills = [read_entry(c) for c in children
                  if not c.name.startswith(".") and c.is_dir() and not c.is_symlink()]
        return LibraryRead("ok", str(self.root), skills)

    def path_of(self, name: str) -> Path:
        check_name(name)
        return self._need_root() / name

    def entry(self, name: str) -> SkillEntry:
        path = self.path_of(name)
        if not probe_path(path).is_dir():
            raise SkillRefused(f"no skill {name!r} in the library")
        return read_entry(path)

    def content(self, name: str) -> dict[str, Any]:
        path = self.path_of(name)
        entry = self.entry(name)
        # The token is the hash of the bytes ``update`` compares against: decoding first
        # would turn CRLF into LF and no save could ever match (codex review).
        raw = (path / SKILL_FILE).read_bytes()
        files = [rel for rel, _ in tree_files(path)]
        return {**entry.payload(), "content": raw.decode("utf-8"), "content_sha": _sha(raw), "files": files}

    def create(self, name: str, text: str, *, reserved: set[str]) -> SkillEntry:
        check_new_name(name)
        if name in reserved:
            raise SkillRefused(f"{name!r} is a workflow command; a skill of that name would replace /{name}")
        check_skill_md(name, text)
        root = self._need_root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        try:
            path.mkdir()
        except FileExistsError:
            raise SkillRefused(f"{name!r} already exists in the library") from None
        _write(path / SKILL_FILE, text)
        return read_entry(path)

    def update(self, name: str, text: str, base: str) -> SkillEntry:
        path = self.path_of(name)
        current = (path / SKILL_FILE).read_bytes()
        if _sha(current) != base:
            raise SkillChanged("SKILL.md changed since it was opened; reopen it before saving")
        check_skill_md(name, text)
        _write(path / SKILL_FILE, text)
        return read_entry(path)

    def delete(self, name: str, installed_in: list[str]) -> None:
        path = self.path_of(name)
        if installed_in:
            raise SkillRefused(f"still installed in {', '.join(installed_in)}; uninstall it there first")
        if not probe_path(path).is_dir():
            raise SkillRefused(f"no skill {name!r} in the library")
        tree_files(path)  # refuses a link before rmtree could follow one
        shutil.rmtree(path)

    def import_from(self, source: Path, name: str, *, reserved: set[str]) -> SkillEntry:
        """Copy the skill directory at ``source`` into the library as ``name``. The source
        is only read."""
        check_new_name(name)
        if name in reserved:
            raise SkillRefused(f"{name!r} is a workflow command; a skill of that name would replace /{name}")
        root = self._need_root()
        target = root / name
        if not probe_path(target).absent:
            raise SkillRefused(f"{name!r} already exists in the library")
        text = (source / SKILL_FILE).read_text(encoding="utf-8")
        meta = front_matter(text) or {}
        if meta.get("name") != name:
            raise SkillRefused(f"its front matter names it {meta.get('name')!r}; import it under that name")
        check_skill_md(name, text)
        root.mkdir(parents=True, exist_ok=True)
        copy_tree(source, target)
        return read_entry(target)


def _write(path: Path, text: str) -> None:
    """Replace ``path`` whole: a temporary file beside it, then a rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def copy_tree(source: Path, target: Path) -> None:
    """Copy a skill's files (never the marker, never a link) to ``target``, which must not
    exist: built in a temporary sibling, then renamed into place."""
    files = tree_files(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".tmp-{target.name}-"))
    try:
        for rel, path in files:
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # ``copy``: bytes and mode, so a helper script stays executable (codex review).
            shutil.copy(path, dest)
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
