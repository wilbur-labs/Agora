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
    re.compile(r"\b(?:ghp|github_pat|sk)[_-][A-Za-z0-9_-]{12,}\b"),
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
