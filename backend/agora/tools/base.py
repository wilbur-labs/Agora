"""Tool system — base class and result type."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str = ""


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for LLM function calling

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult: ...

    def to_function_schema(self) -> dict:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
