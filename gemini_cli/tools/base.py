from abc import ABC, abstractmethod
from typing import Any

from .lifecycle import ToolResult


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict: ...

    @abstractmethod
    def execute(self, args: dict[str, Any]) -> str | ToolResult: ...

    def title(self, args: dict[str, Any]) -> str | None:
        return None

    def summarize_input(self, args: dict[str, Any]) -> str:
        return ", ".join(f"{key}: {value}" for key, value in args.items())

    def summarize_result(self, args: dict[str, Any], output: str) -> ToolResult:
        lines = output.splitlines()
        display = f"{len(lines)} lines" if lines else "empty output"
        return ToolResult(output=output, display_output=display, truncated=len(output) > 2000)
