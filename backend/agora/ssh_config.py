"""Read the operator's ``~/.ssh/config`` to offer its hosts when registering one (CR-25).

Only to PRE-FILL a form: a connection never reads this file (``environments_remote``
passes ``config=None``), so a personal ssh config still cannot silently change an Agora
connection. Read here as UTF-8 by Agora itself — asyncssh would decode it in the platform
encoding, which on a Japanese Windows (cp932) fails on any non-ASCII comment.

What is understood, as OpenSSH does: ``Host`` blocks with patterns (``*``, ``?``,
negation ``!``), values taken from the FIRST block that sets them in file order (so a
trailing ``Host *`` supplies defaults), ``Include`` of files relative to ``~/.ssh`` (glob
allowed), and the four fields a registration needs: HostName, User, Port, IdentityFile.
Not understood: ``Match`` blocks (skipped — their conditions need a live connection),
tokens such as ``%h`` (left as written). Wildcard-only ``Host`` lines are defaults, not
hosts, and are not offered.
"""
from __future__ import annotations

import fnmatch
import glob
import re
from dataclasses import dataclass, field
from pathlib import Path

_WANTED = frozenset({"hostname", "user", "port", "identityfile"})
_MAX_INCLUDE_DEPTH = 8


@dataclass
class SshHost:
    alias: str
    hostname: str | None = None
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None


@dataclass
class SshConfigRead:
    """``status``: ``absent`` (no file — a real "none"), ``unreadable`` (it exists but
    could not be read or decoded — NOT "none"), or ``ok``."""

    status: str
    hosts: list[SshHost] = field(default_factory=list)
    error: str | None = None


@dataclass
class _Block:
    patterns: list[str]
    values: dict[str, str]
    #: The conditions this block sits under: the ``Host`` patterns of every block an
    #: ``Include`` brought it in from. All must match too. An empty pattern list (from a
    #: ``Match``) never matches — its condition needs a live connection.
    scope: list[list[str]] = field(default_factory=list)


#: `Key value`, `Key=value`, `Key = value`: the key, then whitespace and/or one `=`.
_LINE = re.compile(r"^([^\s=]+)(?:\s*=\s*|\s+)(.*)$")


def _split(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _LINE.match(stripped)
    if match is None:
        return stripped.lower(), ""
    return match.group(1).lower(), match.group(2).strip()


def _words(value: str) -> list[str]:
    r"""Words of a value, split as OpenSSH does: whitespace separates, ``"`` / ``'`` group,
    a backslash escapes only a quote, a backslash or a space (so ``C:\Users\me`` keeps its
    separators — POSIX shlex dropped them, review), and an unquoted ``#`` starting a
    word ends the line as a comment (``Host box # production`` names one host)."""
    words: list[str] = []
    current: list[str] = []
    started = False
    quote: str | None = None
    i = 0
    while i < len(value):
        ch = value[i]
        if quote is not None:
            if ch == "\\" and i + 1 < len(value) and value[i + 1] in "\\\"'":
                current.append(value[i + 1])  # escapes work inside quotes too (review)
                i += 1
            elif ch == quote:
                quote = None
            else:
                current.append(ch)
        elif ch in "\"'":
            quote, started = ch, True
        elif ch == "\\" and i + 1 < len(value) and value[i + 1] in "\\\"' ":
            current.append(value[i + 1])
            started = True
            i += 1
        elif ch.isspace():
            if started:
                words.append("".join(current))
                current, started = [], False
        elif ch == "#" and not started:
            break
        else:
            current.append(ch)
            started = True
        i += 1
    if started:
        words.append("".join(current))
    return words


def _read_blocks(
    path: Path, ssh_dir: Path, depth: int, blocks: list[_Block], scope: list[list[str]],
) -> None:
    """Append ``path``'s blocks. ``scope`` is the condition the file was included under:
    an ``Include`` inside a ``Host`` block applies only where that block matches, as in
    OpenSSH — flattening it would hand one host's settings to another (review)."""
    text = path.read_text(encoding="utf-8")  # raises: the caller reports "unreadable"
    current = _Block(patterns=["*"], values={}, scope=scope)  # lines before any Host
    blocks.append(current)
    for line in text.splitlines():
        parsed = _split(line)
        if parsed is None:
            continue
        key, value = parsed
        if key == "host":
            current = _Block(patterns=_words(value), values={}, scope=scope)
            blocks.append(current)
        elif key == "match":
            current = _Block(patterns=[], values={}, scope=scope)  # needs a connection: never matches
            blocks.append(current)
        elif key == "include":
            if depth >= _MAX_INCLUDE_DEPTH:
                continue
            inner = scope + [current.patterns]
            for word in _words(value):
                target = Path(word).expanduser()
                pattern = str(target if target.is_absolute() else ssh_dir / target)
                for included in sorted(glob.glob(pattern)):
                    _read_blocks(Path(included), ssh_dir, depth + 1, blocks, inner)
            # The lines after an Include still belong to the block it sat in — but they
            # come AFTER the included ones, so they continue in a new block appended
            # behind them: "first value wins" then follows file order, as in ssh (review).
            current = _Block(patterns=current.patterns, values={}, scope=scope)
            blocks.append(current)
        elif key in _WANTED:
            words = _words(value)
            if words:
                current.values.setdefault(key, words[0])


def _matches(alias: str, patterns: list[str]) -> bool:
    matched = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if fnmatch.fnmatchcase(alias, pattern[1:]):
                return False
        elif fnmatch.fnmatchcase(alias, pattern):
            matched = True
    return matched


def read_ssh_config(path: Path | None = None) -> SshConfigRead:
    """The hosts in ``path`` (default ``~/.ssh/config``), each with its resolved values."""
    config = path if path is not None else Path.home() / ".ssh" / "config"
    try:
        if not config.is_file():
            return SshConfigRead(status="absent")
        blocks: list[_Block] = []
        _read_blocks(config, config.parent, 0, blocks, [])
    except (OSError, UnicodeDecodeError, RuntimeError) as exc:
        return SshConfigRead(status="unreadable", error=f"{type(exc).__name__}: {exc}")

    aliases: list[str] = []
    for block in blocks:
        for pattern in block.patterns:
            if pattern.startswith("!") or any(ch in pattern for ch in "*?"):
                continue
            if pattern not in aliases:
                aliases.append(pattern)

    hosts: list[SshHost] = []
    for alias in aliases:
        values: dict[str, str] = {}
        for block in blocks:
            if _matches(alias, block.patterns) and all(_matches(alias, s) for s in block.scope):
                for key, value in block.values.items():
                    values.setdefault(key, value)  # first obtained value wins, as in ssh
        port = values.get("port")
        hosts.append(SshHost(
            alias=alias,
            hostname=values.get("hostname"),
            user=values.get("user"),
            port=int(port) if port and port.isdigit() else None,
            identity_file=values.get("identityfile"),
        ))
    return SshConfigRead(status="ok", hosts=hosts)
