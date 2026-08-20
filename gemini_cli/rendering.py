from __future__ import annotations

from typing import Iterable

from rich import box
from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .ui import console
from .tools.lifecycle import ToolCall, ToolEvent, ToolState


class ToolRenderer:
    def __init__(self, details: bool = False):
        self.details = details
        self.calls: list[ToolCall] = []
        self._by_id: dict[str, ToolCall] = {}

    def reset(self) -> None:
        self.calls.clear()
        self._by_id.clear()

    def handle(self, event: ToolEvent) -> None:
        call = event.call
        if call.id not in self._by_id:
            self.calls.append(call)
        self._by_id[call.id] = call

    def _symbol(self, call: ToolCall) -> tuple[str, str]:
        if call.state is ToolState.COMPLETED:
            return "✓", "success"
        if call.state is ToolState.ERROR:
            return "✗", "error"
        if call.state is ToolState.CANCELLED:
            return "!", "warning"
        return "●", "running"

    def _duration(self, call: ToolCall) -> str:
        duration = call.duration
        return f"{duration:.2f}s" if duration is not None else ""

    def _summary(self, call: ToolCall) -> str:
        if call.state is ToolState.PENDING:
            return "pending..."
        if call.state is ToolState.RUNNING:
            base = call.metadata.get("input_summary") or "running..."
            duration = self._duration(call)
            return f"{base} · {duration}" if duration else str(base)
        if call.state is ToolState.COMPLETED:
            parts = [call.display_output or "completed", self._duration(call)]
            return " · ".join(part for part in parts if part)
        if call.state is ToolState.CANCELLED:
            return "cancelled"
        error = call.error or "failed"
        duration = self._duration(call)
        return f"{error} · {duration}" if duration else error

    def render_compact(self):
        table = Table.grid(padding=(0, 1))
        table.add_column(width=2)
        table.add_column(ratio=1)
        table.add_column(justify="right", no_wrap=True)
        for call in self.calls:
            symbol, style = self._symbol(call)
            title = call.title or call.name
            summary = self._summary(call)
            # Combine title and summary into one cell for a compact one-line display
            title_cell = Text(str(title), style="tool")
            if summary:
                title_cell.append("  ")
                title_cell.append(str(summary), style="muted")
            table.add_row(Text(symbol, style=style), title_cell, Text(self._duration(call), style="muted"))
        return Panel(table, title="Working", border_style="tool", box=box.ROUNDED, padding=(0, 1))

    def render_static(self):
        if not self.details:
            return self.render_compact()
        rows = []
        for call in self.calls:
            symbol, style = self._symbol(call)
            title = call.title or call.name
            body = [Text(f"{symbol} {title}", style=style)]
            input_summary = call.metadata.get("input_summary")
            if input_summary:
                body.extend([Text("input:", style="muted"), Text(str(input_summary))])
            result = call.display_output or call.error or ""
            if result:
                body.extend([Text("result:", style="muted"), Text(str(result))])
            if call.duration is not None:
                body.extend([Text("duration:", style="muted"), Text(self._duration(call))])
            rows.append(Group(*body))
        return Panel(Group(*rows), title="Tool details", border_style="tool", box=box.ROUNDED, padding=(0, 1))


class ToolLive:
    def __init__(self, renderer: ToolRenderer):
        self.renderer = renderer
        self.live: Live | None = None

    def __enter__(self):
        self.live = Live(self.renderer.render_compact(), console=console, refresh_per_second=6, transient=True)
        self.live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.live:
            self.live.__exit__(exc_type, exc, tb)
            self.live = None
        if self.renderer.calls:
            console.print(self.renderer.render_static())

    def event_sink(self, event: ToolEvent) -> None:
        self.renderer.handle(event)
        if self.live:
            self.live.update(self.renderer.render_compact())


def render_assistant(text: str) -> None:
    console.print("\n[assistant]Gemini:[/assistant]")
    console.print(Panel(Markdown(text), border_style="assistant", padding=(0, 1)))


def render_footer(model: str, session_id: str = "", tools_enabled: bool = False) -> None:
    parts = [model]
    if session_id:
        parts.append(f"session {session_id[:8]}")
    parts.append("tools on" if tools_enabled else "tools off")
    console.rule(" · ".join(parts), style="muted")
