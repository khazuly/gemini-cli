#!/usr/bin/env python3
"""
Gemini CLI - Interactive Gemini Chat Client
Using Rich for beautiful terminal UI
"""

import json
import os
import re
import sys
import uuid
import random
import hashlib
import httpx
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box

console = Console()

HEADERS_FILE = Path(__file__).parent / "headers.json"
OUTPUT_DIR = Path(__file__).parent / "image_generate_output"
VIDEO_OUTPUT_DIR = Path(__file__).parent / "video_generate_output"
AUTH_DIR = Path(__file__).parent / "auth"
COOKIES_FILE = AUTH_DIR / "cookies.json"

MODELS = {
    "gemini-3.6-flash": {"name": "Gemini 3.6 Flash", "desc": "Fastest answers", "header": None},
    "gemini-3.1-pro": {"name": "Gemini 3.1 Pro", "desc": "Advanced reasoning", "header": None},
}

DEFAULT_METADATA = ["", "", "", None, None, None, None, None, None, ""]


def load_cookies():
    if COOKIES_FILE.exists():
        with open(COOKIES_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return {c["name"]: c["value"] for c in data if "name" in c and "value" in c}
        return data

    if HEADERS_FILE.exists():
        with open(HEADERS_FILE) as f:
            raw = f.read()
        raw = re.sub(r"\n\s+", " ", raw)
        data = json.loads(raw)
        cookie_str = data.get("cookie", "")
        cookies = {}
        for part in cookie_str.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                cookies[k] = v
        return cookies

    return None


def manual_cookie_login():
    console.print("\n[bold cyan]Manual Cookie Login[/bold cyan]\n")
    console.print("[bold]Step 1:[/bold] Open [link=https://gemini.google.com/app]https://gemini.google.com/app[/link]\n")
    console.print("[bold]Step 2:[/bold] Log in to your Google account\n")
    console.print("[bold]Step 3:[/bold] Open DevTools (F12) → Application → Cookies → gemini.google.com\n")
    console.print("[bold]Step 4:[/bold] Select all cookies → Copy\n")
    console.print("[bold]Step 5:[/bold] Paste below\n")

    cookie_str = Prompt.ask("[bold]Paste cookies[/bold]")

    if not cookie_str:
        console.print("[red]No cookies provided![/red]")
        return None

    cookies = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k] = v

    if cookies:
        AUTH_DIR.mkdir(exist_ok=True)
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f, indent=2)
        console.print(f"\n[green]Saved {len(cookies)} cookies to auth/cookies.json[/green]")
        return cookies
    else:
        console.print("[red]Invalid cookie format![/red]")
        return None


class GeminiClient:
    def __init__(self, cookies: dict):
        self.cookies = cookies
        self.access_token = ""
        self.build_label = ""
        self.session_id = ""
        self.language = "en"
        self.push_id = ""
        self._reqid = random.randint(10000, 99999)
        self.chat_metadata = list(DEFAULT_METADATA)
        self.conversation_id = ""
        self.reply_id = ""
        self.reply_candidate_id = ""
        self._session = httpx.Client(follow_redirects=True, timeout=120.0)

    @property
    def user_agent(self):
        return "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"

    def _get_next_reqid(self):
        self._reqid += 100000
        return self._reqid

    def init_session(self):
        console.print("[dim]Initializing session...[/dim]")
        resp = self._session.get(
            "https://gemini.google.com/app",
            headers={"User-Agent": self.user_agent, "Accept": "text/html"},
            cookies=self.cookies,
        )

        text = resp.text

        snl = re.search(r'"SNlM0e":\s*"(.*?)"', text)
        bl = re.search(r'"cfb2h":\s*"(.*?)"', text)
        sid = re.search(r'"FdrFJe":\s*"(.*?)"', text)
        lang = re.search(r'"TuX5cc":\s*"(.*?)"', text)
        pid = re.search(r'"qKIAYe":\s*"(.*?)"', text)

        if not snl:
            console.print("[red]Failed to get access token. Cookies may be expired.[/red]")
            sys.exit(1)

        self.access_token = snl.group(1)
        self.build_label = bl.group(1) if bl else ""
        self.session_id = sid.group(1) if sid else ""
        self.language = lang.group(1) if lang else "en"
        self.push_id = pid.group(1) if pid else ""

        # Merge session cookies
        self.cookies.update(dict(self._session.cookies))

        console.print(f"[green]Session initialized![/green]")
        console.print(f"  [dim]Build: {self.build_label[:40]}...[/dim]")
        console.print(f"  [dim]Session: {self.session_id[:30]}...[/dim]")

    def _build_model_header(self, model_id=None):
        client_id = str(uuid.uuid4()).upper()
        header = [
            1, None, None, None,
            model_id,
            None, None, 0,
            [4, 5, 6, 8],
            None, None, None,
            None, None, None,
            None, None,
            client_id,
        ]
        return json.dumps(header)

    def send_message(self, message: str, model: str = None):
        self._reqid = random.randint(10000, 99999)

        inner = [None] * 81
        inner[0] = [message, 0, None, None, None, None, 0]
        inner[1] = [self.language]
        inner[2] = self.chat_metadata
        inner[6] = [1]
        inner[7] = 1
        inner[10] = 1
        inner[11] = 0
        inner[17] = [[0]]
        inner[18] = 0
        inner[27] = 1
        inner[30] = [4]
        inner[41] = [1]
        inner[53] = 0
        inner[59] = str(uuid.uuid4()).upper()
        inner[61] = []
        inner[68] = 1
        inner[79] = 1
        inner[80] = 1

        f_req = json.dumps([None, json.dumps(inner)])
        reqid = self._get_next_reqid()

        params = {
            "hl": self.language,
            "_reqid": str(reqid),
            "rt": "c",
            "bl": self.build_label,
            "f.sid": self.session_id,
        }

        model_header = self._build_model_header(model)

        uuid_val = str(uuid.uuid4()).upper()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": self.user_agent,
            "X-Same-Domain": "1",
            "x-goog-ext-525001261-jspb": "[1,null,null,null,null,null,null,null,[4,5,6,8],null,null,null,null,null,null,null]",
            "x-goog-ext-73010989-jspb": "[0]",
            "x-goog-ext-525005358-jspb": f'["{uuid_val}",1]',
        }

        try:
            resp = self._session.post(
                "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                params=params,
                headers=headers,
                data={"at": self.access_token, "f.req": f_req},
                cookies=self.cookies,
            )
            return self._parse_streaming_response(resp.text)
        except Exception as e:
            return f"[Error] {str(e)}"

    def _extract_image_urls(self, raw_text: str) -> list:
        urls = re.findall(r'https?://[a-zA-Z0-9.-]+\.googleusercontent\.com/[^\s"\\]+', raw_text)
        seen = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    def send_message_with_images(self, message: str, model: str = None):
        self._reqid = random.randint(10000, 99999)

        inner = [None] * 81
        inner[0] = [message, 0, None, None, None, None, 0]
        inner[1] = [self.language]
        inner[2] = self.chat_metadata
        inner[6] = [1]
        inner[7] = 1
        inner[10] = 1
        inner[11] = 0
        inner[17] = [[0]]
        inner[18] = 0
        inner[27] = 1
        inner[30] = [4]
        inner[41] = [1]
        inner[53] = 0
        inner[59] = str(uuid.uuid4()).upper()
        inner[61] = []
        inner[68] = 1
        inner[79] = 1
        inner[80] = 1

        f_req = json.dumps([None, json.dumps(inner)])
        reqid = self._get_next_reqid()

        params = {
            "hl": self.language,
            "_reqid": str(reqid),
            "rt": "c",
            "bl": self.build_label,
            "f.sid": self.session_id,
        }

        uuid_val = str(uuid.uuid4()).upper()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": self.user_agent,
            "X-Same-Domain": "1",
            "x-goog-ext-525001261-jspb": "[1,null,null,null,null,null,null,null,[4,5,6,8],null,null,null,null,null,null,null]",
            "x-goog-ext-73010989-jspb": "[0]",
            "x-goog-ext-525005358-jspb": f'["{uuid_val}",1]',
        }

        try:
            resp = self._session.post(
                "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                params=params,
                headers=headers,
                data={"at": self.access_token, "f.req": f_req},
                cookies=self.cookies,
            )
            text = resp.text
            parsed = self._parse_streaming_response(text)
            image_urls = self._extract_image_urls(text)
            return parsed, image_urls
        except Exception as e:
            return f"[Error] {str(e)}", []

    def download_image(self, url: str, filename: str) -> str:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": self.user_agent, "Referer": "https://gemini.google.com/"},
                cookies=self.cookies,
                follow_redirects=True,
                timeout=30.0
            )
            if resp.status_code == 200:
                ext = ".png"
                ct = resp.headers.get("content-type", "")
                if "jpeg" in ct or "jpg" in ct:
                    ext = ".jpg"
                elif "webp" in ct:
                    ext = ".webp"
                filepath = OUTPUT_DIR / f"{filename}{ext}"
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                return str(filepath)
        except Exception as e:
            console.print(f"[red]Download error: {e}[/red]")
        return ""

    def _extract_video_urls(self, raw_text: str) -> list:
        urls = re.findall(r'https?://[a-zA-Z0-9.-]+\.googleusercontent\.com/[^\s"\\]+', raw_text)
        seen = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    def send_video_request(self, message: str, aspect: int = 16):
        self._reqid = random.randint(10000, 99999)

        video_tool = [None, None, None, None, None, None, [[None, None, None, 1]]]
        mc = [message, 0, None, None, None, None, 0, None, None, video_tool]

        inner = [None] * 69
        inner[0] = mc
        inner[1] = [self.language]
        inner[2] = self.chat_metadata
        inner[6] = [1]
        inner[7] = 1
        inner[10] = 1
        inner[11] = 0
        inner[17] = [[1]]
        inner[18] = 0
        inner[27] = 1
        inner[30] = [4]
        inner[41] = [1]
        inner[53] = 0
        inner[54] = []
        inner[55] = [[aspect]]
        inner[59] = str(uuid.uuid4()).upper()
        inner[61] = []
        inner[68] = 2

        f_req = json.dumps([None, json.dumps(inner)])
        reqid = self._get_next_reqid()

        params = {
            "hl": self.language,
            "_reqid": str(reqid),
            "rt": "c",
            "bl": self.build_label,
            "f.sid": self.session_id,
        }

        uuid_val = str(uuid.uuid4()).upper()

        video_model_header = (
            '[1,null,null,null,null,null,null,null,[4,5,6,8],'
            f'null,null,null,null,null,null,null,"{uuid_val}"]'
        )

        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": self.user_agent,
            "X-Same-Domain": "1",
            "x-goog-ext-525001261-jspb": video_model_header,
            "x-goog-ext-525005358-jspb": f'["{uuid_val}",1]',
        }

        try:
            resp = self._session.post(
                "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                params=params,
                headers=headers,
                data={"at": self.access_token, "f.req": f_req},
                cookies=self.cookies,
                timeout=180.0,
            )
            return resp.text
        except Exception as e:
            return f"[Error] {str(e)}"

    def download_video(self, url: str, filename: str) -> str:
        VIDEO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": self.user_agent, "Referer": "https://gemini.google.com/"},
                cookies=self.cookies,
                follow_redirects=True,
                timeout=120.0
            )
            if resp.status_code == 200:
                ext = ".mp4"
                ct = resp.headers.get("content-type", "")
                if "webm" in ct:
                    ext = ".webm"
                elif "quicktime" in ct:
                    ext = ".mov"
                filepath = VIDEO_OUTPUT_DIR / f"{filename}{ext}"
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                return str(filepath)
        except Exception as e:
            console.print(f"[red]Download error: {e}[/red]")
        return ""

    def _parse_streaming_response(self, text: str) -> str:
        try:
            if text.startswith(")]}'"):
                text = text[4:]

            all_texts = []
            seen = set()

            for match in re.finditer(r'(\d+)\n(.*?)(?=\d+\n|$)', text, re.DOTALL):
                try:
                    data = json.loads(match.group(2))
                except json.JSONDecodeError:
                    continue

                if not isinstance(data, list):
                    continue

                for part in data:
                    if not isinstance(part, list) or len(part) < 3:
                        continue

                    inner_str = part[2]
                    if not isinstance(inner_str, str):
                        continue

                    try:
                        inner_data = json.loads(inner_str)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    if not isinstance(inner_data, list):
                        continue

                    # Extract metadata (cid, rid)
                    if len(inner_data) > 1 and isinstance(inner_data[1], list):
                        meta = inner_data[1]
                        if len(meta) > 0 and meta[0]:
                            self.conversation_id = meta[0]
                            self.chat_metadata[0] = meta[0]
                        if len(meta) > 1 and meta[1]:
                            self.reply_id = meta[1]
                            self.chat_metadata[1] = meta[1]

                    # Extract text from candidates: inner_data[4][*][1][0]
                    if len(inner_data) > 4 and isinstance(inner_data[4], list):
                        for candidate in inner_data[4]:
                            if isinstance(candidate, list) and len(candidate) > 1:
                                text_parts = candidate[1]
                                if isinstance(text_parts, list) and len(text_parts) > 0:
                                    actual_text = text_parts[0]
                                    if isinstance(actual_text, str) and len(actual_text) > 1:
                                        # Streaming sends partial then full text - keep only the longest
                                        replaced = False
                                        for i, existing in enumerate(all_texts):
                                            if actual_text.startswith(existing) or existing.startswith(actual_text):
                                                if len(actual_text) > len(existing):
                                                    all_texts[i] = actual_text
                                                replaced = True
                                                break
                                        if not replaced:
                                            all_texts.append(actual_text)

                    # Extract context string for conversation continuity
                    if len(inner_data) > 25 and isinstance(inner_data[25], str):
                        ctx = inner_data[25]
                        if ctx:
                            self.chat_metadata = ["", "", "", None, None, None, None, None, None, ctx]

            return "\n".join(all_texts) if all_texts else "[No response text found]"
        except Exception as e:
            return f"[Parse Error] {str(e)}\n\nRaw: {text[:1000]}"

    def new_chat(self):
        self.chat_metadata = list(DEFAULT_METADATA)
        self.conversation_id = ""
        self.reply_id = ""
        self.reply_candidate_id = ""
        console.print("[dim]New conversation started[/dim]")


def show_banner():
    console.print("""[bold cyan]  ██████╗ ███████╗███╗   ███╗██╗███╗   ██╗ █████╗ ██╗     
██╔════╝ ██╔════╝████╗ ████║██║████╗  ██║██╔══██╗██║     
██║  ███╗█████╗  ██╔████╔██║██║██╔██╗ ██║███████║██║     
██║   ██║██╔══╝  ██║╚██╔╝██║██║██║╚██╗██║██╔══██║██║     
╚██████╔╝███████╗██║ ╚═╝ ██║██║██║ ╚████║██║  ██║███████╗
 ╚═════╝ ╚══════╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝[/bold cyan]
[dim]  Interactive Gemini Chat Client[/dim]
""")


def show_menu():
    table = Table(box=box.ROUNDED, border_style="cyan", show_header=True, header_style="bold magenta")
    table.add_column("#", justify="center", style="cyan", width=3)
    table.add_column("Option", style="white")
    table.add_column("Description", style="dim")
    table.add_row("1", "[bold green]Chat[/bold green]", "Send message to Gemini")
    table.add_row("2", "[bold yellow]Image[/bold yellow]", "Generate image with Imagen")
    table.add_row("3", "[bold magenta]Video[/bold magenta]", "Generate video with Veo")
    table.add_row("4", "[bold blue]Models[/bold blue]", "Select AI model")
    table.add_row("5", "[bold red]Exit[/bold red]", "Quit")
    console.print(table)


def show_models():
    table = Table(title="[bold]Models[/bold]", box=box.ROUNDED, border_style="blue", show_header=True, header_style="bold white")
    table.add_column("Key", style="cyan", width=25)
    table.add_column("Name", style="white")
    table.add_column("Description", style="dim")
    for key, info in MODELS.items():
        table.add_row(key, info["name"], info["desc"])
    console.print(table)


def select_model():
    show_models()
    keys = list(MODELS.keys())
    for i, key in enumerate(keys, 1):
        console.print(f"  [dim]{i}.[/dim] {key}")
    choice = Prompt.ask("\n[bold]Pick model", default="1")
    if choice.isdigit() and 1 <= int(choice) <= len(keys):
        return keys[int(choice) - 1]
    if choice in MODELS:
        return choice
    return "gemini-3.6-flash"


def chat_mode(client, model):
    console.print(f"\n[bold green]Chat Mode[/bold green] [dim]({model})[/dim]")
    console.print("[dim]Type 'new' for new chat, 'back' to menu[/dim]\n")

    while True:
        try:
            msg = Prompt.ask("[bold cyan]You[/bold cyan]")
            if msg.lower() in ("back", "exit", "quit"):
                break
            if msg.lower() == "new":
                client.new_chat()
                continue
            if not msg.strip():
                continue

            with console.status("[bold yellow]Thinking...[/bold yellow]"):
                resp = client.send_message(msg, MODELS.get(model, {}).get("header"))

            console.print(f"\n[bold green]Gemini:[/bold green]")
            console.print(Panel(resp, border_style="green", padding=(0, 1)))
            console.print()

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def image_mode(client):
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
                resp, image_urls = client.send_message_with_images(f"Generate an image: {prompt}")

            if image_urls:
                console.print(f"\n[bold green]Found {len(image_urls)} image(s)[/bold green]")
                for i, url in enumerate(image_urls, 1):
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"gemini_{ts}_{i}"
                    with console.status(f"[bold yellow]Downloading image {i}/{len(image_urls)}...[/bold yellow]"):
                        saved = client.download_image(url, filename)
                    if saved:
                        console.print(f"  [green]Saved:[/green] {saved}")
                    else:
                        console.print(f"  [red]Failed to download image {i}[/red]")
            else:
                console.print(Panel(resp, border_style="yellow", padding=(0, 1)))
            console.print()

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def video_mode(client):
    console.print("\n[bold magenta]Video Generation[/bold magenta]")
    console.print("[dim]Type 'back' to menu[/dim]\n")

    while True:
        try:
            console.print("[bold]Choose video generation method:[/bold]")
            console.print("  [dim]1.[/dim] Open Gemini web (recommended)")
            console.print("  [dim]2.[/dim] Generate via API (may fail due to restrictions)")
            console.print("  [dim]3.[/dim] Back to menu\n")

            choice = Prompt.ask("[bold]Select", default="1")
            if choice == "3" or choice.lower() in ("back", "exit", "quit"):
                break

            if choice == "1":
                prompt = Prompt.ask("[bold cyan]Describe the video[/bold cyan]")
                if prompt.lower() in ("back", "exit", "quit"):
                    break
                if not prompt.strip():
                    continue

                gemini_url = f"https://gemini.google.com/app?q={prompt.replace(' ', '+')}"
                console.print(f"\n[bold green]Open this URL in your browser to generate the video:[/bold green]")
                console.print(Panel(f"[link={gemini_url}]{gemini_url}[/link]", border_style="green", padding=(0, 1)))
                console.print("[dim]Copy the URL above and paste it in your browser[/dim]\n")

            elif choice == "2":
                prompt = Prompt.ask("[bold cyan]Describe the video[/bold cyan]")
                if prompt.lower() in ("back", "exit", "quit"):
                    break
                if not prompt.strip():
                    continue

                console.print("\n[bold magenta]Video Settings[/bold magenta]")
                console.print("[dim]Duration: 4s, 6s, or 8s (1080p/4K only 8s)[/dim]")
                console.print("[dim]Resolution: 720p, 1080p, 4k[/dim]")
                console.print("[dim]Aspect Ratio: 16:9 (landscape), 9:16 (portrait)[/dim]\n")

                duration = Prompt.ask("[bold]Duration", default="8")
                if duration.lower() in ("back", "exit", "quit"):
                    break
                if duration not in ("4", "6", "8"):
                    console.print("[yellow]Invalid duration, using 8s[/yellow]")
                    duration = "8"

                resolution = Prompt.ask("[bold]Resolution", default="720p")
                if resolution.lower() in ("back", "exit", "quit"):
                    break
                if resolution not in ("720p", "1080p", "4k"):
                    console.print("[yellow]Invalid resolution, using 720p[/yellow]")
                    resolution = "720p"

                if resolution in ("1080p", "4k") and duration != "8":
                    console.print(f"[yellow]Warning: {resolution} requires 8s duration, adjusting...[/yellow]")
                    duration = "8"

                aspect_str = Prompt.ask("[bold]Aspect Ratio", default="16:9")
                if aspect_str.lower() in ("back", "exit", "quit"):
                    break
                if aspect_str not in ("16:9", "9:16"):
                    console.print("[yellow]Invalid aspect ratio, using 16:9[/yellow]")
                    aspect_str = "16:9"

                aspect = 16 if aspect_str == "16:9" else 9

                full_prompt = f"Generate a {duration} second {resolution} video in {aspect_str} aspect ratio: {prompt}"

                with console.status("[bold magenta]Starting video generation...[/bold magenta]"):
                    console.print("[dim]Priming conversation...[/dim]")
                    client.send_message("I want to create a video. Reply with just: READY")

                with console.status("[bold magenta]Requesting video generation...[/bold magenta]"):
                    resp = client.send_video_request(full_prompt, aspect)

                video_urls = client._extract_video_urls(resp)
                download_urls = re.findall(r'https?://[^"\\s]*usercontent\.google\.com/download\?[^"\\s]*', resp)

                if download_urls:
                    console.print(f"\n[bold green]Video is being generated![/bold green]")
                    for i, url in enumerate(download_urls, 1):
                        clean_url = url.replace("\\u003d", "=").replace("\\/", "/")
                        console.print(f"\n[bold cyan]Video {i} download URL:[/bold cyan]")
                        console.print(Panel(f"[link={clean_url}]{clean_url}[/link]", border_style="green", padding=(0, 1)))
                        console.print("[dim]Open this URL in your browser to download the video[/dim]")
                elif video_urls:
                    console.print(f"\n[bold green]Found {len(video_urls)} video(s)[/bold green]")
                    for i, url in enumerate(video_urls, 1):
                        console.print(f"  [dim]URL:[/dim] {url}")
                        console.print("[dim]Open this URL in your browser to download[/dim]")
                else:
                    if "upgrade" in resp.lower() or "subscription" in resp.lower():
                        console.print(Panel("[bold yellow]Video generation requires Gemini Advanced subscription[/bold yellow]\n" + resp, border_style="red", padding=(0, 1)))
                    elif "generating" in resp.lower() or "creating" in resp.lower():
                        console.print(Panel("[bold yellow]Video is being generated. It may take a few minutes.[/bold yellow]\n" + resp, border_style="magenta", padding=(0, 1)))
                    elif "[13" in resp or "1155" in resp or "1053" in resp or "1097" in resp:
                        console.print(Panel("[bold red]Video generation failed - server error or quota exceeded[/bold red]\n" + resp, border_style="red", padding=(0, 1)))
                    else:
                        console.print(Panel(resp, border_style="magenta", padding=(0, 1)))
                console.print()

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def main():
    show_banner()

    cookies = load_cookies()

    if not cookies:
        console.print("[yellow]No saved cookies found[/yellow]\n")
        console.print("[bold]1.[/dim] Paste cookies manually")
        console.print("[dim]2.[/dim] Exit\n")
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
        show_menu()
        console.print(f"[dim]Model: {current_model}[/dim]")
        choice = Prompt.ask("\n[bold]Select", default="1")

        if choice == "1":
            chat_mode(client, current_model)
        elif choice == "2":
            image_mode(client)
        elif choice == "3":
            video_mode(client)
        elif choice == "4":
            current_model = select_model()
        elif choice == "5":
            console.print("[bold red]Bye![/bold red]")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]Bye![/bold red]")
