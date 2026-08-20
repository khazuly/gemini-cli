import select
import sys
import termios
import tty
from pathlib import Path

import httpx
from rich.panel import Panel
from rich.prompt import Prompt

from .client import GeminiClient, load_cookies, load_cookie_sessions, parse_cookie_string, save_cookie_sessions, save_cookies, MODELS
from .rendering import ToolLive, ToolRenderer, render_assistant, render_footer
from .tools import ToolRegistry
from .ui import console, show_banner, show_menu, show_models, show_login_menu

OUTPUT_DIR = Path.home() / ".gemini-cli" / "output"


def read_compressed_paste(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    buffer: list[str] = []
    paste_count = 0
    compressed = False
    bracket_depth = 0
    brace_depth = 0
    in_string = False
    escape = False

    try:
        tty.setcbreak(fd)
        while True:
            char = sys.stdin.read(1)
            if char in ("\x03", "\x04"):
                raise KeyboardInterrupt
            if char in ("\x7f", "\b"):
                if buffer:
                    buffer.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue

            chunk = char
            while select.select([sys.stdin], [], [], 0)[0]:
                chunk += sys.stdin.read(1)
            buffer.extend(chunk)

            for item in chunk:
                if escape:
                    escape = False
                    continue
                if item == "\\" and in_string:
                    escape = True
                    continue
                if item == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if item == "[":
                    bracket_depth += 1
                elif item == "]" and bracket_depth:
                    bracket_depth -= 1
                elif item == "{":
                    brace_depth += 1
                elif item == "}" and brace_depth:
                    brace_depth -= 1

            if len(chunk) > 1 or compressed:
                paste_count += len(chunk)
                compressed = True
                sys.stdout.write(f"\r\033[K{prompt}[paste {paste_count} chars]")
                sys.stdout.flush()
            elif char not in ("\r", "\n"):
                sys.stdout.write("*")
                sys.stdout.flush()

            text = "".join(buffer).strip()
            if char in ("\r", "\n") and not (text.startswith("[") or text.startswith("{")):
                break
            if text and (text.startswith("[") or text.startswith("{")) and bracket_depth == 0 and brace_depth == 0:
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\n")
        sys.stdout.flush()

    return "".join(buffer).strip()


def manual_cookie_login(save: bool = True, name: str = "default") -> dict[str, str] | None:
    console.print("\n[bold cyan]Manual Cookie Login[/bold cyan]\n")
    console.print("[bold]Step 1:[/bold] Open [link=https://gemini.google.com/app]https://gemini.google.com/app[/link]\n")
    console.print("[bold]Step 2:[/bold] Log in to your Google account\n")
    console.print("[bold]Step 3:[/bold] Open DevTools (F12) → Application → Cookies → gemini.google.com\n")
    console.print("[bold]Step 4:[/bold] Select all cookies → Copy\n")
    console.print("[bold]Step 5:[/bold] Paste below\n")
    cookie_str = read_compressed_paste("Paste cookies: ")
    if not cookie_str:
        return None
    cookies = parse_cookie_string(cookie_str)
    if cookies and save:
        save_cookies(cookies, name)
        console.print(f"\n[green]Saved {len(cookies)} cookies as '{name}'[/green]")
    return cookies if cookies else None


def select_cookie_session() -> dict[str, str] | None:
    while True:
        data = load_cookie_sessions()
        sessions = data.get("sessions", [])
        current = data.get("current")

        if sessions:
            console.print("\n[bold cyan]Cookie Sessions[/bold cyan]")
            for idx, session in enumerate(sessions, 1):
                marker = " [green](current)[/green]" if session.get("name") == current else ""
                count = len(session.get("cookies") or {})
                console.print(f"[bold]{idx}.[/bold] Use {session.get('name', 'unnamed')}{marker} [dim]({count} cookies)[/dim]")
            console.print(f"[bold]{len(sessions) + 1}.[/bold] Add new cookies")
            console.print(f"[bold]{len(sessions) + 2}.[/bold] Delete cookies")
            console.print(f"[dim]{len(sessions) + 3}.[/dim] Exit")
        else:
            console.print("[yellow]No saved cookies found[/yellow]\n")
            show_login_menu()

        choice = Prompt.ask("[bold]Select", default="1")
        if sessions and choice.isdigit():
            selected = int(choice)
            if 1 <= selected <= len(sessions):
                session = sessions[selected - 1]
                data["current"] = session.get("name")
                save_cookie_sessions(data)
                return session.get("cookies") or None
            if selected == len(sessions) + 1:
                name = Prompt.ask("[bold]Session name", default=f"session-{len(sessions) + 1}")
                cookies = manual_cookie_login(save=True, name=name)
                if cookies:
                    return cookies
            elif selected == len(sessions) + 2:
                delete_cookie_session(data)
            elif selected == len(sessions) + 3:
                return None
        elif not sessions:
            if choice == "2":
                return None
            name = Prompt.ask("[bold]Session name", default="default")
            cookies = manual_cookie_login(save=True, name=name)
            if cookies:
                return cookies


def delete_cookie_session(data: dict) -> None:
    sessions = data.get("sessions", [])
    if not sessions:
        console.print("[yellow]No sessions to delete[/yellow]")
        return
    console.print("\n[bold red]Delete Cookie Session[/bold red]")
    for idx, session in enumerate(sessions, 1):
        console.print(f"[bold]{idx}.[/bold] {session.get('name', 'unnamed')}")
    choice = Prompt.ask("[bold]Delete number")
    if not choice.isdigit() or not 1 <= int(choice) <= len(sessions):
        console.print("[red]Invalid selection[/red]")
        return
    removed = sessions.pop(int(choice) - 1)
    if data.get("current") == removed.get("name"):
        data["current"] = sessions[0].get("name") if sessions else None
    data["sessions"] = sessions
    save_cookie_sessions(data)
    console.print(f"[green]Deleted '{removed.get('name', 'unnamed')}'[/green]")


def download_image(url: str) -> str | None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
    console.print("[dim]Type 'new' for new chat, 'back' to menu, '/details' to toggle tool details[/dim]\n")
    details = False

    while True:
        try:
            render_footer(model, client.session_id, tools_on)
            msg = Prompt.ask("[user]›[/user]")
            command = msg.lower().strip()
            if command in ("back", "exit", "quit"):
                return tools_on
            if command == "new":
                client.new_chat()
                console.print("[muted]New chat started[/muted]")
                continue
            if command == "/details":
                details = not details
                console.print(f"[muted]Tool details {'enabled' if details else 'disabled'}[/muted]")
                continue
            if not msg.strip():
                continue

            system_prompt = registry.build_system_prompt() if tools_on else ""
            full_msg = f"{system_prompt}\n\nUser message: {msg}" if system_prompt else msg

            with console.status("[running]◌ analyzing...[/running]"):
                resp = client.send_message(full_msg, MODELS.get(model, {}).get("header"), tools_on)

            # Iteratively handle tool calls: execute, inject results, and resend until LLM returns no tool_calls
            import json
            renderer = ToolRenderer(details=details)
            iteration = 0
            max_iterations = 6
            while True:
                iteration += 1
                if iteration > max_iterations:
                    # safety to prevent infinite loops
                    break
                tool_calls = registry.parse_tool_calls(resp)
                if not tool_calls:
                    break

                tool_results = []
                with ToolLive(renderer) as live:
                    for call_spec in tool_calls:
                        tool_name = call_spec.get("name")
                        tool_args = call_spec.get("args", {}) or {}
                        provided_id = call_spec.get("id")
                        executed = registry.execute_call(tool_name, tool_args, live.event_sink, call_id=provided_id)
                        # preserve original raw tag for conversation injection
                        executed._orig_raw = call_spec.get("raw")
                        tool_results.append(executed)

                # Build assistant-like blocks: include original tool_call tags and structured tool_result tags
                assistant_tool_tags = "\n".join(getattr(c, "_orig_raw", json.dumps({"name": c.name, "args": c.input})) for c in tool_results)
                result_blocks = []
                for c in tool_results:
                    # Keep full output for LLM, but JSON-encode it to avoid breaking conversation format
                    result_blocks.append(
                        f"<tool_result>{json.dumps({'call_id': c.id, 'name': c.name, 'args': c.input, 'output': c.output or c.error or ''})}</tool_result>"
                    )
                payload = assistant_tool_tags + "\n\n" + "\n".join(result_blocks) + "\n\nContinue with the original request."

                with console.status("[running]◌ reviewing tool results...[/running]"):
                    resp = client.send_message(payload, MODELS.get(model, {}).get("header"), tools_on)

            render_assistant(resp)
            console.print()

        except KeyboardInterrupt:
            console.print("[warning]cancelled[/warning]")
            return tools_on
        except Exception as e:
            console.print(f"[error]Error: {e}[/error]")
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

    cookies = select_cookie_session()
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
