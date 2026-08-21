from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from typing import Any, Callable


class ToolState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class ToolResult:
    output: str
    display_output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    error: str | None = None
    truncated: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    state: ToolState = ToolState.PENDING
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    output: str | None = None
    display_output: str | None = None
    progress: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    truncated: bool = False

    @property
    def duration(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)


@dataclass
class ToolEvent:
    type: str
    call: ToolCall


EventSink = Callable[[ToolEvent], None]

_call_counter = count(1)


def next_call_id() -> str:
    return f"tool_{next(_call_counter):02d}"
