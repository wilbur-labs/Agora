"""Canonical path validation shared by protocol objects."""
from __future__ import annotations

import ntpath


def canonical_repository_path(value: str) -> str:
    """Return a canonical repository-relative path or fail closed."""
    normalized = value.replace("\\", "/")
    drive, _ = ntpath.splitdrive(normalized)
    if drive or ntpath.isabs(normalized) or normalized.startswith("/"):
        raise ValueError("path must be repository-relative")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must not contain empty, current, or parent segments")
    return "/".join(parts)
