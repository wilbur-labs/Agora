"""Redaction helpers for persisted execution data."""
from __future__ import annotations

import os
import re
from typing import Any


_SECRET_KEYS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PAT", "CONNECTION", "AUTH",
)
_TOKEN_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)(\s*[:=]\s*)([^\s,;]+)"),
    # Ends where the token alphabet ends, not at a general word boundary: `\b`
    # between "…p" and "日" is not a boundary, so `sk-…日本語のログ` — an ordinary
    # shape in this project's own output — matched nothing at all and the
    # credential was written in clear by every redaction pass.
    re.compile(
        r"(?<![A-Za-z0-9_-])(?:ghp|github_pat|sk)[_-][A-Za-z0-9_-]{12,}(?![A-Za-z0-9_-])"
    ),
)


def redact_text(value: str) -> str:
    result = value
    for pattern in _TOKEN_PATTERNS:
        if pattern.groups >= 3:
            result = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    for secret in _environment_secrets():
        result = result.replace(secret, "[REDACTED]")
    return result


def _redaction_spans(text: str) -> list[tuple[int, int]]:
    """Every region of ``text`` that redaction would replace.

    Both patterns **and** each occurrence of each environment secret, because a
    cut through any of them writes half a credential that nothing can match
    afterwards.
    """
    spans: list[tuple[int, int]] = []
    for pattern in _TOKEN_PATTERNS:
        spans.extend((match.start(), match.end()) for match in pattern.finditer(text))
    for secret in _environment_secrets():
        start = text.find(secret)
        while start >= 0:
            spans.append((start, start + len(secret)))
            start = text.find(secret, start + 1)
    return spans


def _trailing_secret_prefix(text: str) -> int | None:
    """Where a **partial** environment secret at the end of ``text`` starts.

    An environment value can be multi-line — a PEM in ``PRIVATE_KEY`` is the
    ordinary case — and a CLI prints it line by line. The first line matched
    nothing on its own, was written the moment its newline arrived, and the log
    then held a piece of the key for ever. What has arrived so far is a proper
    prefix of the secret, so the text is held until the rest of it turns up.

    Only the *longest* prefix matters, and only at the very end: anything earlier
    is either a whole match (handled above) or ordinary text.
    """
    start: int | None = None
    for secret in _environment_secrets():
        # The longest suffix of `text` that is a proper prefix of `secret`.
        window = min(len(text), len(secret) - 1)
        while window > 0:
            if text.endswith(secret[:window]):
                position = len(text) - window
                start = position if start is None else min(start, position)
                break
            window -= 1
    return start


def safe_cut(text: str, limit: int) -> int:
    """The largest index at or below ``limit`` that no redaction straddles.

    A streaming writer emits ``text[:cut]`` and keeps the rest, so a cut inside a
    secret writes its first half in clear — and the retained half then matches
    nothing on any later pass. This is the one question that has to be answered
    correctly for that to be safe, and answering it in pieces produced four
    wrong versions in a row:

    * emit at a fixed point and hope — the tail of a long credential leaked;
    * drop what followed a cut — ordinary output disappeared;
    * defer what followed — its prefix was already a marker, so it leaked again;
    * keep a match that reaches the end whole, but ignore environment secrets —
      **measured**: a secret straddling the cut leaked at 53 of 161 offsets
      tested, because "the retained tail is longer than any secret" never
      implied "the cut cannot fall inside one".

    Two rules, and nothing else: never cut inside a match, and never cut inside a
    match that reaches the end of ``text`` (it may still be growing, so it is
    kept whole for the next pass).
    """
    cut = max(0, min(limit, len(text)))
    spans = _redaction_spans(text)
    # A match touching the end may continue into what has not arrived yet, so it
    # is treated as reaching past the end.
    spans = [(start, len(text) + 1 if end == len(text) else end) for start, end in spans]
    # And a secret that has only half arrived matches nothing yet, so it has to be
    # recognised by its prefix or its first line goes out in clear.
    partial = _trailing_secret_prefix(text)
    if partial is not None:
        spans.append((partial, len(text) + 1))
    moved = True
    while moved:
        moved = False
        for start, end in spans:
            if start < cut < end:
                cut = start
                moved = True
    return max(0, cut)


def longest_environment_secret() -> int:
    """Length of the longest environment secret redaction will look for.

    Read rather than guessed. A streaming writer has to retain enough of what it
    already emitted for the *next* pass to see a secret whole; the retained
    amount used to be a fixed 512 characters with a comment claiming it was
    "longer than any credential pattern this project matches", which is not
    something a constant can know. A connection string or a PEM in the
    environment is routinely longer than that, and its two halves then never sat
    in the buffer together.
    """
    return max((len(value) for value in _environment_secrets()), default=0)


def sanitize_data(value: Any, *, key: str = "") -> Any:
    if key and any(marker in key.upper() for marker in _SECRET_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): sanitize_data(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _environment_secrets() -> set[str]:
    return {
        value for key, value in os.environ.items()
        if value and len(value) >= 16 and any(marker in key.upper() for marker in _SECRET_KEYS)
    }
