from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()

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
    table = Table(box=box.ROUNDED, border_style="cyan", show_header=True, header_style="bold magenta")
    table.add_column("#", justify="center", style="cyan", width=3)
    table.add_column("Option", style="white")
    table.add_column("Description", style="dim")
    table.add_row("1", "[bold green]Chat[/bold green]", "Send message to Gemini")
    table.add_row("2", "[bold yellow]Image[/bold yellow]", "Generate image with Imagen")
    table.add_row("3", "[bold magenta]Video[/bold magenta]", "Generate video with Veo")
    table.add_row("4", "[bold blue]Models[/bold blue]", "Select AI model")
    table.add_row("5", "[bold cyan]Tools[/bold cyan]", "Toggle tools on/off")
    table.add_row("6", "[bold red]Exit[/bold red]", "Quit")
    console.print(table)
    status = "[green]ON[/green]" if tools_enabled else "[red]OFF[/red]"
    console.print(f"[dim]Tools: {status}[/dim]")


def show_models():
    from .client import MODELS
    table = Table(title="[bold]Models[/bold]", box=box.ROUNDED, border_style="blue", show_header=True, header_style="bold white")
    table.add_column("Key", style="cyan", width=25)
    table.add_column("Name", style="white")
    for key, info in MODELS.items():
        table.add_row(key, info["name"])
    console.print(table)


def show_login_menu():
    console.print("[bold]1.[/bold] Paste cookies manually")
    console.print("[dim]2.[/dim] Exit")
