from __future__ import annotations

import re

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.text import Text
from rich.theme import Theme

from .tools.lifecycle import ToolCall, ToolEvent, ToolState

THEME = Theme({
    "brand": "bold cyan",
    "user": "bold cyan",
    "assistant": "bold green",
    "tool": "cyan",
    "running": "yellow",
    "success": "green",
    "warning": "yellow",
    "error": "red",
    "muted": "dim",
    "info": "blue",
})

console = Console(theme=THEME)

BANNER = """
 ██████╗ ██████╗ ███████╗███╗   ██╗████████╗    ██████╗ ███████╗██╗████████╗███████╗
██╔════╝██╔═══██╗██╔════╝████╗  ██║╚══██╔══╝    ██╔══██╗██╔════╝██║╚══██╔══╝██╔════╝
██║     ██║   ██║██╔══██╗██╔══██╗██║   ██║      ██║  ██║█████╗  ██║██╔══╝  ██║   ██╔══╝
██║     ██║   ██║██╔══╝  ██║╚██╗██║   ██║       ██║  ██║██╔══╝  ██║██║     ██║   ██║
╚██████╗╚██████╔╝███████╗██║ ╚████║   ██║       ██████╔╝███████╗██║╚██████╗██║   ███████╗
 ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═╝       ╚═════╝ ╚══════╝╚═╝ ╚═════╝╚═╝   ╚══════╝"""


def show_banner() -> None:
    console.print("[bold cyan]" + BANNER + "[/bold cyan]")
    console.print("[dim]Interactive Gemini Chat Client with Tool Calling[/dim]\n")


def show_menu(tools_enabled: bool = False) -> None:
    status = "[green]ON[/green]" if tools_enabled else "[red]OFF[/red]"
    console.print("  [cyan]1[/cyan] [bold green]Chat[/bold green]    Send message to Gemini")
    console.print("  [cyan]2[/cyan] [bold green]Resume[/bold green]   Continue a previous conversation")
    console.print("  [cyan]3[/cyan] [bold yellow]Image[/bold yellow]   Generate image")
    console.print("  [cyan]4[/cyan] [bold magenta]Models[/bold magenta]  Switch AI model")
    console.print("  [cyan]5[/cyan] [bold blue]Tools[/bold blue]   Toggle tools on/off")
    console.print("  [cyan]6[/cyan] [bold red]Exit[/bold red]    Quit")
    console.print(f"\nTools: {status}")


def show_models(models: dict) -> None:
    console.print("\nAvailable models:")
    for key, info in models.items():
        console.print(f"  [cyan]{key}[/cyan]  {info['name']}")
    console.print("")


def ask(prompt: str, default: str = "") -> str:
    return Prompt.ask(prompt, default=default) if default else Prompt.ask(prompt)


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
            return
        old = self._by_id[call.id]
        try:
            self.calls[self.calls.index(old)] = call
        except ValueError:
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

    def _summary(self, call: ToolCall) -> str:
        if call.state is ToolState.PENDING:
            return "pending..."
        if call.state is ToolState.RUNNING:
            return str(call.metadata.get("input_summary") or "running...")
        if call.state is ToolState.COMPLETED:
            return str(call.display_output or "completed")
        if call.state is ToolState.CANCELLED:
            return "cancelled"
        if call.state is ToolState.ERROR:
            return str(call.error or call.display_output or "failed")
        return str(call.display_output or call.error or "failed")

    def render_compact(self):
        lines: list[Text] = []
        if any(c.state in (ToolState.RUNNING, ToolState.PENDING) for c in self.calls):
            lines.append(Text("Working...", style="muted"))
        for call in self.calls:
            symbol, style = self._symbol(call)
            duration = call.duration
            line = Text("  ")
            line.append(symbol + " ", style=style)
            line.append(str(call.title or call.name), style="tool")
            summary = self._summary(call)
            if summary:
                line.append(" · ", style="muted")
                line.append(summary, style="muted")
            if duration is not None:
                line.append(f" · {duration:.2f}s", style="muted")
            lines.append(line)
        return Group(*lines) if lines else Text("")

    def render_static(self):
        if not self.details:
            return self.render_compact()
        rows = []
        for call in self.calls:
            symbol, style = self._symbol(call)
            body = [Text(f"  {symbol} {call.title or call.name}", style=style)]
            input_summary = call.metadata.get("input_summary")
            if input_summary:
                body.append(Text("    input:", style="muted"))
                body.extend(Text(f"      {line}") for line in str(input_summary).splitlines())
            result = str(call.display_output or call.error or "")
            if result:
                body.append(Text("    result:", style="muted"))
                if len(result) > 1000:
                    result = result[:1000] + "\n      ... (truncated)"
                body.extend(Text(f"      {line}") for line in result.splitlines())
            if call.duration is not None:
                body.append(Text(f"    duration: {call.duration:.2f}s", style="muted"))
            rows.append(Group(*body))
        return Group(*rows) if rows else Text("")


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


def clean_response(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"</?tool_call>", "", text)
    return text.strip()


_CODE_LINE = re.compile(r"^\s*(def |class |import |from \S+ import|return\b|print\(|if __name__|@)", re.MULTILINE)


def _wrap_unfenced_code(text: str) -> str:
    if "```" in text or len(_CODE_LINE.findall(text)) < 4:
        return text
    return f"```\n{text.strip()}\n```"


def render_assistant(text: str) -> None:
    console.print("\n[assistant]Gemini:[/assistant]\n")
    console.print(Markdown(_wrap_unfenced_code(clean_response(text))))


def render_footer(model: str, tools_enabled: bool = False) -> None:
    parts = [model, "tools on" if tools_enabled else "tools off"]
    console.print(f"[muted]{' · '.join(parts)}[/muted]")
