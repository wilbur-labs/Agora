"""Artifacts API — track, list, read, and download agent-generated files."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter()

# In-memory artifact tracking (per-session, reset on chat reset)
_artifacts: list[str] = []


def track_artifact(path: str) -> None:
    """Record a file path as an artifact."""
    if path not in _artifacts:
        _artifacts.append(path)


def get_artifacts() -> list[str]:
    return list(_artifacts)


def clear_artifacts() -> None:
    _artifacts.clear()


@router.get("/artifacts")
async def list_artifacts():
    """List all tracked artifact files with metadata."""
    items = []
    for filepath in _artifacts:
        p = Path(filepath).expanduser()
        if p.exists():
            items.append({
                "path": filepath,
                "name": p.name,
                "size": p.stat().st_size,
                "ext": p.suffix.lstrip("."),
            })
    return {"artifacts": items}


@router.get("/artifacts/{path:path}")
async def get_artifact(path: str, download: bool = False):
    """Read or download an artifact file."""
    # Try path as-is, then with home expansion
    p = Path(path).expanduser()
    if not p.exists():
        p = Path("/" + path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {path}")

    if download:
        return FileResponse(str(p), filename=p.name)

    # Return text content for previewable files
    mime = mimetypes.guess_type(p.name)[0] or ""
    if p.stat().st_size > 1_000_000:
        raise HTTPException(413, "File too large for preview")

    try:
        content = p.read_text(errors="replace")
        return PlainTextResponse(content)
    except Exception:
        return FileResponse(str(p), filename=p.name)
