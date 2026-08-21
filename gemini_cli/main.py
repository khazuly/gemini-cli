from __future__ import annotations

import codecs
import json
import os
import select
import sys
import termios
import time
import tty
from datetime import datetime
from pathlib import Path

import httpx
from rich.panel import Panel

from .client import GeminiClient, MODELS, delete_chat, load_cookie_sessions, load_cookies, load_chats, parse_cookie_string, save_chat_state, save_cookie_sessions, save_cookies
from .rendering import ToolLive, ToolRenderer, ask, console, render_assistant, render_footer, show_banner, show_menu, show_models
from .tools import ToolRegistry
from .tools.lifecycle import ToolCall, ToolEvent, ToolState

OUTPUT_DIR = Path.home() / ".gemini-cli" / "output"
MAX_ITERATIONS = 40
MAX_TOOL_OUTPUT = 6000


def read_compressed_paste(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffer: list[str] = []
    pasted = 0
    compressed = False
    brackets = braces = 0
    in_string = False
    escape = False

    def track(text: str) -> None:
        nonlocal in_string, escape, brackets, braces
        for item in text:
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
                brackets += 1
            elif item == "]" and brackets:
                brackets -= 1
            elif item == "{":
                braces += 1
            elif item == "}" and braces:
                braces -= 1

    try:
        tty.setcbreak(fd)
        while True:
            chunk = decoder.decode(os.read(fd, 1))
            if not chunk:
                break
            if "\x03" in chunk or "\x04" in chunk:
                raise KeyboardInterrupt
            if chunk in ("\x7f", "\b"):
                if buffer:
                    buffer.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            while select.select([fd], [], [], 0)[0]:
                more = os.read(fd, 65536)
                if not more:
                    break
                chunk += decoder.decode(more)
            buffer.extend(chunk)
            track(chunk)
            if len(chunk) > 1 or compressed:
                pasted += len(chunk)
                compressed = True
                sys.stdout.write(f"\r\033[K{prompt}[paste {pasted} chars]")
            elif chunk not in ("\r", "\n"):
                sys.stdout.write("*")
            sys.stdout.flush()
            text = "".join(buffer).strip()
            if chunk.endswith("\r") or chunk.endswith("\n"):
                if not (text.startswith("[") or text.startswith("{")):
                    break
            if text and (text.startswith("[") or text.startswith("{")) and brackets == 0 and braces == 0:
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\n")
        sys.stdout.flush()
    return "".join(buffer).strip()


def manual_cookie_login(save: bool = True, name: str = "default") -> dict[str, str] | None:
    console.print("\n[bold cyan]Manual Cookie Login[/bold cyan]\n")
    console.print("[bold]1.[/bold] Open [link=https://gemini.google.com/app]https://gemini.google.com/app[/link] and log in")
    console.print("[bold]2.[/bold] DevTools (F12) -> Application -> Cookies -> gemini.google.com")
    console.print("[bold]3.[/bold] Select all cookies, copy, paste below\n")
    cookie_str = read_compressed_paste("Paste cookies: ")
    if not cookie_str:
        return None
    cookies = parse_cookie_string(cookie_str)
    if cookies and save:
        save_cookies(cookies, name)
        console.print(f"\n[green]Saved {len(cookies)} cookies as '{name}'[/green]")
    return cookies or None


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
            choice = ask("[bold]Select", default="1")
            if not choice.isdigit():
                continue
            selected = int(choice)
            if 1 <= selected <= len(sessions):
                session = sessions[selected - 1]
                data["current"] = session.get("name")
                save_cookie_sessions(data)
                return session.get("cookies") or None
            if selected == len(sessions) + 1:
                name = ask("[bold]Session name", default=f"session-{len(sessions) + 1}")
                cookies = manual_cookie_login(name=name)
                if cookies:
                    return cookies
            elif selected == len(sessions) + 2:
                delete_cookie_session(data)
            elif selected == len(sessions) + 3:
                return None
        else:
            console.print("[yellow]No saved cookies found[/yellow]\n")
            console.print("[bold]1.[/bold] Paste cookies manually")
            console.print("[dim]2.[/dim] Exit")
            if ask("[bold]Select", default="1") == "2":
                return None
            name = ask("[bold]Session name", default="default")
            cookies = manual_cookie_login(name=name)
            if cookies:
                return cookies


def delete_cookie_session(data: dict) -> None:
    sessions = data.get("sessions", [])
    if not sessions:
        console.print("[yellow]No sessions to delete[/yellow]")
        return
    for idx, session in enumerate(sessions, 1):
        console.print(f"[bold]{idx}.[/bold] {session.get('name', 'unnamed')}")
    choice = ask("[bold]Delete number")
    if not choice.isdigit() or not 1 <= int(choice) <= len(sessions):
        console.print("[red]Invalid selection[/red]")
        return
    removed = sessions.pop(int(choice) - 1)
    if data.get("current") == removed.get("name"):
        data["current"] = sessions[0].get("name") if sessions else None
    data["sessions"] = sessions
    save_cookie_sessions(data)
    console.print(f"[green]Deleted '{removed.get('name', 'unnamed')}'[/green]")


def _ago(ts: int) -> str:
    delta = max(0, int(time.time()) - ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def run_agent(client: GeminiClient, registry: ToolRegistry, model: str, msg: str, tools_on: bool, details: bool) -> str | None:
    header = MODELS.get(model, {}).get("header")
    system_prompt = registry.build_system_prompt() if tools_on else ""

    def send(text: str) -> str:
        payload = f"{system_prompt}\n\n{text}" if system_prompt else text
        with console.status("[running]◌ thinking...[/running]"):
            return client.send_message(payload, header, tools_on)

    resp = send(f"User message: {msg}")
    signatures: list[str] = []
    error_streak = 0
    for _ in range(MAX_ITERATIONS if tools_on else 0):
        if resp.startswith("[Error]"):
            console.print(f"[error]{resp}[/error]")
            return None
        calls = registry.parse_tool_calls(resp)
        if not calls:
            if not resp.strip():
                console.print("[error]Empty response from model[/error]")
                return None
            return resp
        signatures.extend(json.dumps([s["name"], s["args"]], sort_keys=True) for s in calls)
        if len(signatures) >= 3 and len(set(signatures[-3:])) == 1:
            console.print("[error]Agent stuck repeating the same tool call (doom loop); stopped.[/error]")
            return None
        renderer = ToolRenderer(details=details)
        results = []
        with ToolLive(renderer) as live:
            for spec in calls:
                if not spec["name"]:
                    failed = ToolCall(id=spec["id"] or f"malformed_{len(results) + 1}", name="invalid", input={})
                    failed.state = ToolState.ERROR
                    failed.error = "Malformed <tool_call>: arguments were not valid JSON. Re-emit the exact same tool call with valid JSON."
                    live.event_sink(ToolEvent("tool_failed", failed))
                    results.append(failed)
                    continue
                results.append(registry.execute_call(spec["name"], spec["args"], live.event_sink, call_id=spec["id"]))
        if results and all(c.state is ToolState.ERROR for c in results):
            error_streak += 1
            if error_streak >= 3:
                console.print("[error]Agent hit repeated tool failures; stopped.[/error]")
                return None
        else:
            error_streak = 0
        blocks = [
            json.dumps({"call_id": c.id, "tool": c.name, "status": c.state.value, "result": (c.output or c.error or "")[:MAX_TOOL_OUTPUT]})
            for c in results
        ]
        touched: set[str] = set()
        for c in results:
            for key in ("filePath", "file_path", "path"):
                value = c.input.get(key)
                if isinstance(value, str) and value.strip():
                    touched.add(value.strip())
            if c.name == "list":
                for line in (c.output or "").splitlines():
                    entry = line.strip().rstrip("/")
                    if entry and " " not in entry and "." in entry and not entry.startswith(("Error", "No entries")):
                        touched.add(entry)
        facts = f"Files involved in this task (use ONLY these exact names): {', '.join(sorted(touched))}\n\n" if touched else ""
        feedback = (
            f'Original user request: "{msg}"\n\n'
            "You emitted tool calls and their real results are below.\n"
            "<tool_results>\n" + "\n".join(blocks) + "\n</tool_results>\n\n"
            + facts +
            "Continue the task now using these results.\n"
            "If more tools are needed, emit only the next <tool_call> lines. "
            "Otherwise reply with only a brief final summary of what was done and which files changed. "
            "Do NOT repeat code or file contents you already wrote via tools. Do not describe your reasoning. "
            "NEVER ask the user to paste code, files, or command output - use your tools to obtain them yourself."
        )
        resp = send(feedback)
    if registry.parse_tool_calls(resp):
        console.print("[warning]Tool round limit reached - asking the model to wrap up.[/warning]")
        resp = send(
            "You have reached the maximum number of tool rounds. Do not emit any more tool calls. "
            "Reply NOW with only a brief final summary of what you completed so far and what remains."
        )
        if resp.startswith("[Error]") or registry.parse_tool_calls(resp):
            return None
    return resp


def chat_mode(client: GeminiClient, model: str, registry: ToolRegistry, tools_on: bool, chat_name: str | None = None) -> tuple[bool, str | None]:
    console.print(f"\n[bold green]Chat Mode[/bold green] [dim]({model})[/dim]")
    if chat_name:
        console.print(f"[dim]Resumed chat: {chat_name} · 'new' new chat · 'back' menu · '/details' toggle tool details[/dim]\n")
    else:
        console.print("[dim]'new' new chat · 'back' menu · '/details' toggle tool details[/dim]\n")
    details = False
    while True:
        try:
            render_footer(model, tools_on)
            msg = ask("[user]›[/user]")
            command = msg.lower().strip()
            if command in ("back", "exit", "quit"):
                return tools_on, chat_name
            if command == "new":
                client.new_chat()
                chat_name = None
                console.print("[muted]New chat started[/muted]")
                continue
            if command == "/details":
                details = not details
                console.print(f"[muted]Tool details {'enabled' if details else 'disabled'}[/muted]")
                continue
            if not msg.strip():
                continue
            resp = run_agent(client, registry, model, msg, tools_on, details)
            if resp is not None and not resp.startswith("[Error]"):
                if not client.chat_metadata[0]:
                    render_assistant(resp)
                    console.print()
                    continue
                if not chat_name:
                    base = f"chat-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                    chat_name = base
                    existing = {c.get("name") for c in load_chats().get("chats", [])}
                    n = 1
                    while chat_name in existing:
                        chat_name = f"{base}-{n}"
                        n += 1
                    console.print(f"[muted]Saved as '{chat_name}' — resume it from the menu[/muted]")
                save_chat_state(chat_name, client.chat_metadata, model, msg.strip())
                render_assistant(resp)
                console.print()
        except KeyboardInterrupt:
            console.print("\n[warning]cancelled[/warning]")
            return tools_on, chat_name
        except Exception as e:
            console.print(f"[error]Error: {e}[/error]")
            return tools_on, chat_name


def resume_menu(client: GeminiClient, current_model: str, tools_on: bool) -> tuple[str, bool, str] | None:
    while True:
        chats = sorted(load_chats().get("chats", []), key=lambda c: c.get("updated", 0), reverse=True)
        if not chats:
            console.print("[yellow]No saved conversations yet[/yellow]")
            return None
        console.print("\n[bold cyan]Saved Conversations[/bold cyan]")
        for idx, chat in enumerate(chats, 1):
            preview = chat.get("preview") or "(no preview)"
            state = "" if (chat.get("chat_metadata") or [""])[0] else " · [warning]no state, cannot resume[/warning]"
            console.print(f"[bold]{idx}.[/bold] {chat.get('name', 'unnamed')} [dim]{_ago(chat.get('updated', 0))} · {chat.get('model', '?')} · {preview}{state}[/dim]")
        console.print(f"[bold]{len(chats) + 1}.[/bold] Delete a conversation")
        console.print(f"[dim]{len(chats) + 2}.[/dim] Back")
        choice = ask("[bold]Select", default="1")
        if not choice.isdigit():
            continue
        selected = int(choice)
        if 1 <= selected <= len(chats):
            chat = chats[selected - 1]
            metadata = chat.get("chat_metadata")
            if not metadata or len(metadata) < 2 or not metadata[0]:
                console.print("[error]This chat has no conversation state and cannot be resumed[/error]")
                continue
            client.chat_metadata = list(metadata)
            model = chat.get("model") or current_model
            if model not in MODELS:
                model = current_model
            name = chat.get("name") or ""
            console.print(f"[green]Resumed '{name}'[/green]")
            return model, tools_on, name
        if selected == len(chats) + 1:
            del_choice = ask("[bold]Delete which number (0 cancel)", default="0")
            if del_choice.isdigit() and 1 <= int(del_choice) <= len(chats):
                target = chats[int(del_choice) - 1]
                delete_chat(target.get("name", ""))
                console.print(f"[green]Deleted '{target.get('name')}'[/green]")
        return None


def image_mode(client: GeminiClient) -> None:
    console.print("\n[bold yellow]Image Generation[/bold yellow]\n")
    while True:
        try:
            prompt = ask("[bold cyan]Describe the image[/bold cyan]")
            if prompt.lower() in ("back", "exit", "quit"):
                break
            if not prompt.strip():
                continue
            with console.status("[running]◌ generating...[/running]"):
                resp = client.send_message(f"Generate an image: {prompt}")
            urls = client.extract_image_urls(resp)
            if not urls:
                console.print(Panel(resp, border_style="yellow"))
                continue
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            for i, url in enumerate(urls, 1):
                console.print(f"\n[bold cyan]Image {i}:[/bold cyan] {url}")
                try:
                    image = httpx.get(url, cookies=load_cookies(), follow_redirects=True, timeout=60)
                    ext = "jpg" if "jpeg" in image.headers.get("content-type", "") else "png"
                    path = OUTPUT_DIR / f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}.{ext}"
                    path.write_bytes(image.content)
                    console.print(f"[green]Saved: {path}[/green]")
                except Exception as e:
                    console.print(f"[error]Download failed: {e}[/error]")
            console.print()
        except KeyboardInterrupt:
            break


def select_model(current: str) -> str:
    show_models(MODELS)
    keys = list(MODELS.keys())
    choice = ask("[bold]Pick model", default="1")
    if choice.isdigit() and 1 <= int(choice) <= len(keys):
        return keys[int(choice) - 1]
    return choice if choice in MODELS else current


def main() -> None:
    show_banner()
    registry = ToolRegistry()
    tools_on = True
    cookies = select_cookie_session()
    if not cookies:
        console.print("[red]No cookies available![/red]")
        sys.exit(1)
    console.print(f"[green]Loaded {len(cookies)} cookies[/green]")
    client = GeminiClient(cookies)
    client.init_session()
    current_model = next(iter(MODELS))
    while True:
        console.print()
        show_menu(tools_on)
        console.print(f"[dim]Model: {current_model}[/dim]")
        choice = ask("\n[bold]Select", default="1")
        if choice == "1":
            tools_on, _ = chat_mode(client, current_model, registry, tools_on)
        elif choice == "2":
            resumed = resume_menu(client, current_model, tools_on)
            if resumed:
                current_model, tools_on, chat_name = resumed
                tools_on, _ = chat_mode(client, current_model, registry, tools_on, chat_name=chat_name)
        elif choice == "3":
            image_mode(client)
        elif choice == "4":
            current_model = select_model(current_model)
        elif choice == "5":
            tools_on = not tools_on
        elif choice == "6":
            console.print("[bold red]Bye![/bold red]")
            break
