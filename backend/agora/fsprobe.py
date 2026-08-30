"""One answer to "what is at this path", with *absent* and *unknown* kept apart.

**Why this module exists.** ``Path.exists()`` returns ``False`` for two entirely
different observations: nothing is at the path, and the process could not find out.
``os.path.exists()`` swallows every ``OSError`` — a directory whose ACL denies
traversal, an I/O error, a name too long, a dead network share — and reports the
same ``False`` as a genuinely empty directory. ``is_file()``, ``is_dir()`` and
``is_symlink()`` fail the same way, so a fallback chain of them cannot recover the
distinction either: each link in the chain has already lost it.

That conflation is only a nuisance when the result gates an attempt that will fail
loudly anyway. It is a defect when the ``False`` is **reported as a fact** —
"this project keeps no snapshot", "this workspace is missing", "there is no import
source" — because an unreadable thing is then described to the operator as an
absent one, and the two call for opposite responses: create it, versus fix the
permission. It is also a defect when the ``False`` *unlocks* something, because an
inaccessible directory then reads as a safely empty one.

Agora hit this three times in one night, in three modules, so the rule lives here
once instead of being rediscovered a fourth time:

    Only ``FileNotFoundError`` is absence. Every other ``OSError`` is unknown.

**How.** :func:`probe_path` calls ``lstat`` once — the link itself, so a dangling
symlink is seen as *recorded at the path* rather than as nothing — and classifies
the result. ``PermissionError``, ``NotADirectoryError``, ``OSError`` and anything
else become :attr:`PathProbe.unknown`, which callers must render as unknown and
must never treat as absence or as permission to overwrite.

**One platform note.** A path whose parent component is a regular file
(``.../file.txt/PROGRESS.md``) raises ``NotADirectoryError`` on POSIX and
``FileNotFoundError`` on Windows, so it probes as ``unknown`` there and ``absent``
here. That split is deliberate: erring toward unknown never states a falsehood,
while erring toward absent does. Do not "fix" it by folding ``NotADirectoryError``
into absence — that reintroduces exactly the guess this module exists to refuse.

Pure apart from the single stat, and it never raises: a probe is an observation,
and "the observation failed" is one of its outcomes rather than an exception the
caller has to remember to catch.
"""
from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: ``file`` / ``dir`` / ``other`` are present. ``absent`` is a definite negative.
#: ``unknown`` is "the process could not find out" and is not either of the others.
PathKind = Literal["file", "dir", "other", "absent", "unknown"]


@dataclass(frozen=True)
class PathProbe:
    """What one ``lstat`` established about a path. Absence and unknown are distinct."""

    kind: PathKind
    #: The ``OSError`` that made this ``unknown`` (or that broke a symlink's target),
    #: kept so a caller can name the cause instead of guessing at it. Never the
    #: ``FileNotFoundError`` of a plain absence: that is not a failure to observe.
    error: OSError | None = None
    #: ``st_size`` when ``kind`` is ``file``; ``None`` otherwise, so a size can never
    #: be read off a probe that did not observe a regular file.
    size: int | None = None

    @property
    def absent(self) -> bool:
        """Definitely nothing at the path. Only ``FileNotFoundError`` gets here."""
        return self.kind == "absent"

    @property
    def unknown(self) -> bool:
        """The path could not be observed. Not absence, and not permission to write."""
        return self.kind == "unknown"

    @property
    def present(self) -> bool:
        """Something is at the path — a file, a directory, or another entry kind."""
        return self.kind in ("file", "dir", "other")

    # -- tri-state answers, for payloads a UI has to render honestly --------------
    #
    # ``None`` means "could not tell". The three-valued shape is the point: a
    # ``bool`` field cannot carry the distinction, so any API that has to report
    # existence has to widen to ``bool | None`` rather than pick a lie.

    def exists(self) -> bool | None:
        return None if self.unknown else self.present

    def is_file(self) -> bool | None:
        return None if self.unknown else self.kind == "file"

    def is_dir(self) -> bool | None:
        return None if self.unknown else self.kind == "dir"

    def reason(self) -> str:
        """A short cause for a message, e.g. ``PermissionError``. ``""`` when known."""
        return type(self.error).__name__ if self.error is not None else ""


def probe_path(path: Path, *, follow_symlinks: bool = True) -> PathProbe:
    """Classify ``path`` with one ``lstat``, keeping absence apart from unknown.

    A symlink is followed by default so a link to a regular file probes as ``file``,
    matching what a reader of that path would get. A link whose target cannot be
    stat'd — dangling, looping, or unreachable — probes as ``other``: something *is*
    recorded at the path, which is present-but-unusable and not absence.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return PathProbe("absent")
    except OSError as exc:
        # Permission, I/O, ENOTDIR, ELOOP, name too long, dead share. The read did
        # not happen, so nothing at all was established about the path.
        return PathProbe("unknown", error=exc)

    if stat.S_ISLNK(info.st_mode):
        if not follow_symlinks:
            return PathProbe("other")
        try:
            info = path.stat()
        except OSError as exc:
            return PathProbe("other", error=exc)

    if stat.S_ISREG(info.st_mode):
        return PathProbe("file", size=info.st_size)
    if stat.S_ISDIR(info.st_mode):
        return PathProbe("dir")
    return PathProbe("other")
