"""File operation tools — read, write, patch, list."""
from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult

_MAX_READ = 100_000  # chars


def _resolve(path: str, workspace: str) -> Path:
    """Resolve path: if workspace is set and path is relative, resolve under workspace."""
    p = Path(path).expanduser()
    if workspace and not p.is_absolute():
        p = Path(workspace) / p
    return p


class ReadFile(Tool):
    name = "read_file"
    description = "Read the contents of a file. Returns the text content."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path"},
        },
        "required": ["path"],
    }

    def __init__(self, workspace: str = ""):
        self._workspace = workspace

    async def execute(self, *, path: str, **_) -> ToolResult:
        p = _resolve(path, self._workspace)
        if not p.exists():
            return ToolResult(False, "", f"File not found: {path}")
        if not p.is_file():
            return ToolResult(False, "", f"Not a file: {path}")
        text = p.read_text(errors="replace")
        if len(text) > _MAX_READ:
            text = text[:_MAX_READ] + f"\n... [truncated, {len(text)} chars total]"
        return ToolResult(True, text)


class WriteFile(Tool):
    name = "write_file"
    description = "Create or overwrite a file with the given content."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path"},
            "content": {"type": "string", "description": "File content to write"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace: str = ""):
        self._workspace = workspace

    async def execute(self, *, path: str, content: str, **_) -> ToolResult:
        p = _resolve(path, self._workspace)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        from agora.api.artifacts import track_artifact
        track_artifact(str(p))
        return ToolResult(True, f"Wrote {len(content)} chars to {p}")


class PatchFile(Tool):
    name = "patch_file"
    description = "Replace a specific string in a file. The old_str must match exactly once."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path"},
            "old_str": {"type": "string", "description": "Exact string to find (must be unique in file)"},
            "new_str": {"type": "string", "description": "Replacement string"},
        },
        "required": ["path", "old_str", "new_str"],
    }

    def __init__(self, workspace: str = ""):
        self._workspace = workspace

    async def execute(self, *, path: str, old_str: str, new_str: str, **_) -> ToolResult:
        p = _resolve(path, self._workspace)
        if not p.is_file():
            return ToolResult(False, "", f"File not found: {path}")
        text = p.read_text()
        count = text.count(old_str)
        if count == 0:
            return ToolResult(False, "", "old_str not found in file")
        if count > 1:
            return ToolResult(False, "", f"old_str matches {count} times, must be unique")
        p.write_text(text.replace(old_str, new_str, 1))
        from agora.api.artifacts import track_artifact
        track_artifact(str(p))
        return ToolResult(True, f"Patched {p}")


class ListDir(Tool):
    name = "list_dir"
    description = "List files and directories at the given path."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path", "default": "."},
        },
    }

    def __init__(self, workspace: str = ""):
        self._workspace = workspace

    async def execute(self, *, path: str = ".", **_) -> ToolResult:
        p = _resolve(path, self._workspace)
        if not p.is_dir():
            return ToolResult(False, "", f"Not a directory: {path}")
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
        lines = []
        for e in entries[:200]:
            prefix = "📁 " if e.is_dir() else "📄 "
            lines.append(f"{prefix}{e.name}")
        if len(entries) > 200:
            lines.append(f"... and {len(entries) - 200} more")
        return ToolResult(True, "\n".join(lines))
