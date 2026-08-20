from __future__ import annotations

from typing import Iterable

from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown
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
        # Ensure a single visual item per call id. Replace existing entry in-place when updates arrive.
        if call.id not in self._by_id:
            self.calls.append(call)
            self._by_id[call.id] = call
        else:
            # replace the old call object in the list to reflect updates
            old = self._by_id[call.id]
            try:
                idx = self.calls.index(old)
                self.calls[idx] = call
            except ValueError:
                # fallback: append if not found
                self.calls.append(call)
            self._by_id[call.id] = call

    def _symbol(self, call: ToolCall) -> tuple[str, str]:
        if call.state is ToolState.COMPLETED:
            return "✓", "success"
        if call.state is ToolState.ERROR:
            return "✗", "error"
        if call.state is ToolState.CANCELLED:
            return "!", "warning"
        if call.state is ToolState.PENDING:
            return "○", "muted"
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
        # Minimal, panel-less compact display. Use subtle "Working..." header when active.
        lines: list[Text] = []
        any_running = any(c.state in (ToolState.RUNNING, ToolState.PENDING) for c in self.calls)
        if any_running:
            lines.append(Text("Working...", style="muted"))

        for call in self.calls:
            symbol, style = self._symbol(call)
            title = call.title or call.name
            summary = self._summary(call)
            duration = self._duration(call)
            # Build a single line: two-space indent, symbol, title, summary and duration
            line = Text("  ")
            line.append(symbol + " ", style=style)
            line.append(str(title) + " ", style="tool")
            if summary:
                line.append("  ")
                line.append(str(summary), style="muted")
            if duration:
                line.append("  · ")
                line.append(duration, style="muted")
            lines.append(line)

        if not lines:
            return Text("")
        return Group(*lines)

    def render_static(self):
        # If details not requested, use compact view
        if not self.details:
            return self.render_compact()
        rows: list[Group] = []
        for call in self.calls:
            symbol, style = self._symbol(call)
            title = call.title or call.name
            body_lines: list[Text] = []
            body_lines.append(Text(f"  {symbol} {title}", style=style))

            input_summary = call.metadata.get("input_summary")
            if input_summary:
                body_lines.append(Text(""))
                body_lines.append(Text("    input:", style="muted"))
                for line in str(input_summary).splitlines():
                    body_lines.append(Text(f"      {line}"))

            # show other metadata keys (except input_summary)
            other_meta = {k: v for k, v in call.metadata.items() if k != "input_summary"}
            if other_meta:
                body_lines.append(Text(""))
                body_lines.append(Text("    metadata:", style="muted"))
                for k, v in other_meta.items():
                    body_lines.append(Text(f"      {k}: {v}"))

            result = call.display_output or call.error or ""
            if result:
                body_lines.append(Text(""))
                body_lines.append(Text("    result:", style="muted"))
                txt = str(result)
                if len(txt) > 1000:
                    txt = txt[:1000] + "\n      ... (truncated)"
                for line in txt.splitlines():
                    body_lines.append(Text(f"      {line}"))

            if call.duration is not None:
                body_lines.append(Text(""))
                body_lines.append(Text("    duration:", style="muted"))
                body_lines.append(Text(f"      {self._duration(call)}"))

            rows.append(Group(*body_lines))
        return Group(*rows)


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
    # Assistant responses rendered as plain Markdown under a simple heading
    console.print("\n[assistant]Gemini:[/assistant]\n")
    console.print(Markdown(text))


def render_footer(model: str, session_id: str = "", tools_enabled: bool = False) -> None:
    parts = [model]
    if session_id:
        parts.append(f"session {session_id[:8]}")
    parts.append("tools on" if tools_enabled else "tools off")
    console.print(f"[muted]{' · '.join(parts)}[/muted]")
