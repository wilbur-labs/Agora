"""Tool registry — collect all tools, look up by name."""
from __future__ import annotations

from .base import Tool
from .file_ops import ReadFile, WriteFile, PatchFile, ListDir
from .shell import Shell
from .web import WebSearch, WebFetch


class ToolRegistry:
    def __init__(self, sandbox=None):
        self._tools: dict[str, Tool] = {}
        for t in [ReadFile(), WriteFile(), PatchFile(), ListDir(), Shell(sandbox=sandbox), WebSearch(), WebFetch()]:
            self._tools[t.name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def function_schemas(self) -> list[dict]:
        return [t.to_function_schema() for t in self._tools.values()]
