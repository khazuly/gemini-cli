import json
import random
import re
import time
import uuid
from pathlib import Path

import httpx

CONFIG_DIR = Path.home() / ".gemini-cli"
COOKIES_FILE = CONFIG_DIR / "cookies.json"
HEADERS_FILE = CONFIG_DIR / "headers.json"
CHATS_FILE = CONFIG_DIR / "chats.json"
DEFAULT_METADATA = ["", "", "", None, None, None, None, None, None, ""]
MODELS = {
    "gemini-3.6-flash": {"name": "Gemini 3.6 Flash", "header": None},
    "gemini-3.1-pro": {"name": "Gemini 3.1 Pro", "header": None},
}


def parse_cookie_string(cookie_str: str) -> dict[str, str]:
    cookie_str = cookie_str.strip()
    if not cookie_str:
        return {}
    try:
        data = json.loads(cookie_str)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return {str(c["name"]): str(c["value"]) for c in data if isinstance(c, dict) and c.get("name") and c.get("value") is not None}
    if isinstance(data, dict) and data.get("cookie"):
        return parse_cookie_string(str(data["cookie"]))
    cookies = {}
    if re.search(r'"?name"?\s*:', cookie_str) and re.search(r'"?value"?\s*:', cookie_str):
        for block in re.findall(r"\{(.*?)\}", cookie_str, flags=re.DOTALL):
            name_match = re.search(r'(?:^|\n|,)\s*"?name"?\s*:\s*"?([^",\n]+)"?', block)
            value_match = re.search(r'(?:^|\n|,)\s*"?value"?\s*:\s*(".*?"|.*?)(?=\n\s*"?\w+"?\s*:|,\s*"?\w+"?\s*:|\s*$)', block, flags=re.DOTALL)
            if name_match and value_match:
                name = name_match.group(1).strip().strip('"\'')
                value = value_match.group(1).strip().rstrip(",").strip().strip('"\'')
                if name and value:
                    cookies[name] = value.replace("\n", "")
        return cookies
    for part in cookie_str.replace("\n", ";").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k and v and not k.startswith("{"):
                cookies[k] = v.strip().strip('"').strip("'")
    return cookies


def load_cookie_sessions() -> dict:
    if COOKIES_FILE.exists():
        with open(COOKIES_FILE) as f:
            data = json.load(f)
    elif HEADERS_FILE.exists():
        raw = re.sub(r"\n\s+", " ", HEADERS_FILE.read_text())
        cookies = parse_cookie_string(json.loads(raw).get("cookie", ""))
        data = {"sessions": [{"name": "default", "cookies": cookies}]} if cookies else {"sessions": []}
    else:
        data = {"sessions": []}
    sessions = data.get("sessions", [])
    current = data.get("current")
    if not any(s.get("name") == current for s in sessions):
        data["current"] = sessions[0].get("name") if sessions else None
    return data


def save_cookie_sessions(data: dict) -> None:
    CONFIG_DIR.mkdir(exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_cookies() -> dict[str, str] | None:
    data = load_cookie_sessions()
    current = data.get("current")
    for session in data.get("sessions", []):
        if session.get("name") == current:
            return session.get("cookies") or None
    return None


def save_cookies(cookies: dict[str, str], name: str = "default") -> None:
    data = load_cookie_sessions()
    sessions = [s for s in data.get("sessions", []) if s.get("name") != name]
    sessions.append({"name": name, "cookies": cookies})
    save_cookie_sessions({"current": name, "sessions": sessions})


def load_chats() -> dict:
    if CHATS_FILE.exists():
        try:
            return json.loads(CHATS_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"chats": []}


def save_chats(data: dict) -> None:
    CONFIG_DIR.mkdir(exist_ok=True)
    CHATS_FILE.write_text(json.dumps(data, indent=2))


def save_chat_state(name: str, chat_metadata: list, model: str, preview: str) -> None:
    data = load_chats()
    now = int(time.time())
    chats = [c for c in data.get("chats", []) if c.get("name") != name]
    chats.insert(0, {"name": name, "chat_metadata": chat_metadata, "model": model, "preview": preview[:80], "created": now, "updated": now})
    save_chats({"chats": chats})


def delete_chat(name: str) -> None:
    data = load_chats()
    data["chats"] = [c for c in data.get("chats", []) if c.get("name") != name]
    save_chats(data)


class GeminiClient:
    def __init__(self, cookies: dict[str, str]):
        self.cookies = cookies
        self.access_token = ""
        self.build_label = ""
        self.session_id = ""
        self.language = "en"
        self.chat_metadata = list(DEFAULT_METADATA)
        self.conversation_id = ""
        self.reply_id = ""
        self._reqid = random.randint(10000, 99999)
        self._session = httpx.Client(follow_redirects=True, timeout=120.0)

    @property
    def user_agent(self) -> str:
        return "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"

    def init_session(self) -> None:
        resp = self._session.get("https://gemini.google.com/app", headers={"User-Agent": self.user_agent}, cookies=self.cookies)
        for key, attr in (("SNlM0e", "access_token"), ("cfb2h", "build_label"), ("FdrFJe", "session_id")):
            match = re.search(rf'\\?"{key}\\?":\\?"(.*?)\\?"', resp.text)
            if match:
                setattr(self, attr, match.group(1))

    def _iter_stream_json(self, raw: str):
        raw = raw.lstrip()
        if raw.startswith(")]}'"):
            raw = raw[4:]
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            json_str = None
            if line[0].isdigit():
                parts = line.split(maxsplit=1)
                json_str = parts[1] if len(parts) > 1 else None
            elif line.startswith(("[", "{")):
                json_str = line
            if json_str:
                try:
                    yield json.loads(json_str)
                except json.JSONDecodeError:
                    pass

    def _extract_texts(self, data) -> list[str]:
        texts = []
        if isinstance(data, str):
            value = data.strip()
            if value.startswith(("[", "{")):
                try:
                    texts.extend(self._extract_texts(json.loads(value)))
                except json.JSONDecodeError:
                    pass
            return texts
        if isinstance(data, dict):
            for value in data.values():
                texts.extend(self._extract_texts(value))
            return texts
        if not isinstance(data, list):
            return texts
        if data and isinstance(data[0], str) and len(data[0].strip()) > 1:
            texts.append(data[0])
        if len(data) > 1 and isinstance(data[1], list):
            for part in data[1]:
                if isinstance(part, list) and part and isinstance(part[0], str) and len(part[0].strip()) > 1:
                    texts.append(part[0])
        for value in data:
            texts.extend(self._extract_texts(value))
        return texts

    def _update_chat_metadata(self, data) -> None:
        conv, reply = None, None
        stack = [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, str):
                if re.fullmatch(r"[A-Za-z0-9_\-]{9,}", node):
                    if node.startswith("c_"):
                        conv = node
                    elif node.startswith("r_"):
                        reply = node
            elif isinstance(node, list):
                if (
                    len(node) >= 2
                    and isinstance(node[0], str)
                    and isinstance(node[1], str)
                    and re.fullmatch(r"[A-Za-z0-9_\-]{8,}", node[0])
                    and re.fullmatch(r"[A-Za-z0-9_\-]{8,}", node[1])
                ):
                    conv, reply = conv or node[0], reply or node[1]
                stack.extend(node)
        if conv and reply:
            self.chat_metadata = [conv, reply]
        elif conv and not self.chat_metadata[0]:
            self.chat_metadata = [conv, ""]

    def _parse_streaming_response(self, raw: str) -> str:
        if not raw:
            return "[Error] Empty response"

        def clean(text: str) -> str | None:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if not text or text.startswith(("wrb.fr", "di", "af.")):
                return None
            if text.lower().startswith(("http://", "https://", "tbn:", "data:", "//")):
                return None
            if re.fullmatch(r"[A-Za-z0-9_-]{24,}", text) or re.fullmatch(r"[a-z]{1,4}_[A-Za-z0-9_-]+", text):
                return None
            return text

        final = ""
        longest = ""
        for data in self._iter_stream_json(raw):
            self._update_chat_metadata(data)
            for text in self._extract_texts(data):
                cleaned = clean(text)
                if not cleaned:
                    continue
                if len(cleaned) > len(longest):
                    longest = cleaned
                if len(cleaned) >= 80:
                    final = cleaned
        body = final if len(final) >= min(len(longest), 80) else longest
        return body or longest or "[Error] Could not parse response"

    def send_message(self, message: str, model: str | None = None, tools_enabled: bool = False) -> str:
        inner = [None] * 81
        inner[0] = [message, 0, None, None, None, None, 0]
        inner[1] = [self.language]
        inner[2] = self.chat_metadata
        inner[6] = [1]
        inner[7] = 1
        inner[10] = 1
        inner[11] = 0
        inner[17] = [[1, 2]] if tools_enabled else [[0]]
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
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": self.user_agent,
            "X-Same-Domain": "1",
            "x-goog-ext-525001261-jspb": self._model_header(model),
            "x-goog-ext-73010989-jspb": "[0]",
            "x-goog-ext-525005358-jspb": f'["{str(uuid.uuid4()).upper()}",1]',
        }
        try:
            if not self.access_token or not self.build_label or not self.session_id:
                self.init_session()
            if not self.access_token:
                return "[Error] Login/session invalid. Add fresh cookies."
            resp = self._session.post(
                "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                params={
                    "hl": self.language,
                    "_reqid": str(self._next_reqid()),
                    "rt": "c",
                    "bl": self.build_label,
                    "f.sid": self.session_id,
                },
                headers=headers,
                data={"at": self.access_token, "f.req": json.dumps([None, json.dumps(inner)])},
                cookies=self.cookies,
            )
            if resp.status_code in (401, 403):
                return "[Error] Login/session expired. Add fresh cookies."
            if resp.status_code >= 400:
                return f"[Error] HTTP {resp.status_code}: {resp.text[:200]}"
            return self._parse_streaming_response(resp.text)
        except Exception as e:
            return f"[Error] {e}"

    def _model_header(self, model: str | None = None) -> str:
        base = "[1,null,null,null,null,null,null,null,[4,5,6,8],null,null,null,null,null,null,null"
        return f'{base},"{model}"]' if model else base + "]"

    def _next_reqid(self) -> int:
        self._reqid += 100000
        return self._reqid

    def extract_image_urls(self, raw: str) -> list[str]:
        urls = re.findall(r'https?://[a-zA-Z0-9.-]+\.googleusercontent\.com/[^\s"\\]+', raw)
        return list(dict.fromkeys(urls))

    def new_chat(self) -> None:
        self.chat_metadata = list(DEFAULT_METADATA)
        self.conversation_id = ""
        self.reply_id = ""
