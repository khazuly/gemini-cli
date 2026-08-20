from rich.console import Console
from rich.table import Table
from rich.theme import Theme

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
██║     ██║   ██║█████╗  ██╔██╗ ██║   ██║       ██║  ██║█████╗  ██║   ██║   █████╗
██║     ██║   ██║██╔══╝  ██║╚██╗██║   ██║       ██║  ██║██╔══╝  ██║   ██║   ██╔══╝
╚██████╗╚██████╔╝███████╗██║ ╚████║   ██║       ██████╔╝███████╗██║   ██║   ███████╗
 ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝       ╚═════╝ ╚══════╝╚═╝   ╚═╝   ╚══════╝"""


def show_banner():
    console.print(f"[bold cyan]{BANNER}[/bold cyan]")
    console.print("[dim]Interactive Gemini Chat Client with Tool Calling[/dim]\n")


def show_menu(tools_enabled: bool = False):
    # Minimal command-style menu without heavy borders
    console.print("\n  [cyan]1[/cyan]  [bold green]Chat[/bold green]    Send message to Gemini")
    console.print("  [cyan]2[/cyan]  [bold yellow]Image[/bold yellow]   Generate image with Imagen")
    console.print("  [cyan]3[/cyan]  [bold magenta]Video[/bold magenta]   Generate video with Veo")
    console.print("  [cyan]4[/cyan]  [bold blue]Models[/bold blue]   Select AI model")
    console.print("  [cyan]5[/cyan]  [bold cyan]Tools[/bold cyan]   Toggle tools on/off")
    console.print("  [cyan]6[/cyan]  [bold red]Exit[/bold red]    Quit\n")
    status = "[green]ON[/green]" if tools_enabled else "[red]OFF[/red]"
    console.print(f"Tools: {status}    Model: [muted]{''}[/muted]")


def show_models():
    from .client import MODELS
    console.print("\nAvailable models:")
    for key, info in MODELS.items():
        console.print(f"  [cyan]{key}[/cyan]  {info['name']}")
    console.print("")


def show_login_menu():
    console.print("[bold]1.[/bold] Paste cookies manually")
    console.print("[dim]2.[/dim] Exit")
