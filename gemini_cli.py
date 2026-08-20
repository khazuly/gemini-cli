#!/usr/bin/env python3
import json
import sys
import httpx
from pathlib import Path
from rich.panel import Panel
from rich.prompt import Prompt

from client import GeminiClient, load_cookies, save_cookies, MODELS
from tools import ToolRegistry
from ui import console, show_banner, show_menu, show_models, show_login_menu

COOKIES_FILE = Path(__file__).parent / "auth" / "cookies.json"
OUTPUT_DIR = Path(__file__).parent / "image_generate_output"


def manual_cookie_login() -> dict[str, str] | None:
    console.print("\n[bold cyan]Manual Cookie Login[/bold cyan]\n")
    console.print("[bold]Step 1:[/bold] Open [link=https://gemini.google.com/app]https://gemini.google.com/app[/link]\n")
    console.print("[bold]Step 2:[/bold] Log in to your Google account\n")
    console.print("[bold]Step 3:[/bold] Open DevTools (F12) → Application → Cookies → gemini.google.com\n")
    console.print("[bold]Step 4:[/bold] Select all cookies → Copy\n")
    console.print("[bold]Step 5:[/bold] Paste below\n")
    cookie_str = Prompt.ask("[bold]Paste cookies[/bold]")
    if not cookie_str:
        return None
    cookies = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k] = v
    if cookies:
        save_cookies(cookies)
        console.print(f"\n[green]Saved {len(cookies)} cookies[/green]")
    return cookies if cookies else None


def download_image(url: str) -> str | None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    try:
        cookies = load_cookies()
        resp = httpx.get(url, cookies=cookies, follow_redirects=True, timeout=60)
        if resp.status_code == 200:
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = "png"
            ct = resp.headers.get("content-type", "")
            if "jpeg" in ct or "jpg" in ct:
                ext = "jpg"
            path = OUTPUT_DIR / f"image_{ts}.{ext}"
            path.write_bytes(resp.content)
            return str(path)
    except Exception:
        pass
    return None


def chat_mode(client: GeminiClient, model: str, registry: ToolRegistry, tools_on: bool) -> bool:
    console.print(f"\n[bold green]Chat Mode[/bold green] [dim]({model})[/dim]")
    console.print("[dim]Type 'new' for new chat, 'back' to menu[/dim]\n")
    system_prompt = registry.build_system_prompt() if tools_on else ""

    while True:
        try:
            msg = Prompt.ask("[bold cyan]You[/bold cyan]")
            if msg.lower() in ("back", "exit", "quit"):
                return tools_on
            if msg.lower() == "new":
                client.new_chat()
                console.print("[dim]New chat started[/dim]")
                continue
            if not msg.strip():
                continue

            full_msg = f"{system_prompt}\n\nUser message: {msg}" if system_prompt else msg

            with console.status("[bold yellow]Thinking...[/bold yellow]"):
                resp = client.send_message(full_msg, MODELS.get(model, {}).get("header"), tools_on)

            tool_calls = registry.parse_tool_calls(resp)
            if tool_calls:
                for call in tool_calls:
                    tool_name = call.get("name", "")
                    tool_args = call.get("args", {})
                    console.print(f"[dim]Calling tool: {tool_name}[/dim]")
                    result = registry.execute(tool_name, tool_args)
                    console.print(Panel(result, title=f"Tool: {tool_name}", border_style="blue", padding=(0, 1)))
                    with console.status("[bold yellow]Processing result...[/bold yellow]"):
                        resp = client.send_message(
                            f"Tool result for {tool_name}:\n{result}\n\nContinue with the original request.",
                            MODELS.get(model, {}).get("header"),
                            tools_on,
                        )

            console.print(f"\n[bold green]Gemini:[/bold green]")
            console.print(Panel(resp, border_style="green", padding=(0, 1)))
            console.print()

        except KeyboardInterrupt:
            return tools_on
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return tools_on


def image_mode(client: GeminiClient) -> None:
    console.print("\n[bold yellow]Image Generation[/bold yellow]")
    console.print("[dim]Type 'back' to menu[/dim]\n")
    while True:
        try:
            prompt = Prompt.ask("[bold cyan]Describe the image[/bold cyan]")
            if prompt.lower() in ("back", "exit", "quit"):
                break
            if not prompt.strip():
                continue
            with console.status("[bold yellow]Generating image...[/bold yellow]"):
                resp = client.send_message(f"Generate an image: {prompt}")
            urls = client.extract_image_urls(resp)
            if urls:
                console.print(f"\n[bold green]Found {len(urls)} image(s)[/bold green]")
                for i, url in enumerate(urls, 1):
                    console.print(f"\n[bold cyan]Image {i}:[/bold cyan]")
                    console.print(Panel(url, border_style="yellow", padding=(0, 1)))
                    path = download_image(url)
                    if path:
                        console.print(f"[green]Saved: {path}[/green]")
            else:
                console.print(Panel(resp, border_style="yellow", padding=(0, 1)))
            console.print()
        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def video_mode(client: GeminiClient) -> None:
    console.print("\n[bold magenta]Video Generation[/bold magenta]")
    console.print("[dim]Type 'back' to menu[/dim]")
    console.print("[yellow]Video generation requires Gemini Advanced subscription[/yellow]\n")
    while True:
        try:
            prompt = Prompt.ask("[bold cyan]Describe the video[/bold cyan]")
            if prompt.lower() in ("back", "exit", "quit"):
                break
            if not prompt.strip():
                continue
            with console.status("[bold yellow]Generating video...[/bold yellow]"):
                resp = client.send_message(f"Generate a video: {prompt}")
            console.print(Panel(resp, border_style="magenta", padding=(0, 1)))
            console.print()
        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def select_model() -> str:
    show_models()
    keys = list(MODELS.keys())
    for i, key in enumerate(keys, 1):
        console.print(f"  [dim]{i}.[/dim] {key}")
    choice = Prompt.ask("\n[bold]Pick model", default="1")
    if choice.isdigit() and 1 <= int(choice) <= len(keys):
        return keys[int(choice) - 1]
    return choice if choice in MODELS else "gemini-3.6-flash"


def main() -> None:
    show_banner()
    registry = ToolRegistry()
    tools_on = False

    cookies = load_cookies()
    if not cookies:
        console.print("[yellow]No saved cookies found[/yellow]\n")
        show_login_menu()
        choice = Prompt.ask("[bold]Select", default="1")
        if choice == "2":
            return
        cookies = manual_cookie_login()
    if not cookies:
        console.print("[red]No cookies available![/red]")
        sys.exit(1)

    console.print(f"[green]Loaded {len(cookies)} cookies[/green]")
    client = GeminiClient(cookies)
    client.init_session()

    current_model = "gemini-3.6-flash"

    while True:
        console.print()
        show_menu(tools_on)
        console.print(f"[dim]Model: {current_model}[/dim]")
        choice = Prompt.ask("\n[bold]Select", default="1")

        if choice == "1":
            tools_on = chat_mode(client, current_model, registry, tools_on)
        elif choice == "2":
            image_mode(client)
        elif choice == "3":
            video_mode(client)
        elif choice == "4":
            current_model = select_model()
        elif choice == "5":
            tools_on = not tools_on
            status = "[green]ON[/green]" if tools_on else "[red]OFF[/red]"
            console.print(f"[bold]Tools: {status}[/bold]")
        elif choice == "6":
            console.print("[bold red]Bye![/bold red]")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]Bye![/bold red]")
