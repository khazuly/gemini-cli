import json
import re
import uuid
import random
import httpx
from pathlib import Path

CONFIG_DIR = Path.home() / ".gemini-cli"
COOKIES_FILE = CONFIG_DIR / "cookies.json"
HEADERS_FILE = CONFIG_DIR / "headers.json"
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
        return {
            str(cookie["name"]): str(cookie["value"])
            for cookie in data
            if isinstance(cookie, dict) and cookie.get("name") and cookie.get("value") is not None
        }
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

    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k and v:
                cookies[k] = v
    return cookies


def _parse_headers_cookies() -> dict[str, str] | None:
    if not HEADERS_FILE.exists():
        return None
    with open(HEADERS_FILE) as f:
        raw = f.read()
    raw = re.sub(r"\n\s+", " ", raw)
    data = json.loads(raw)
    cookie_str = data.get("cookie", "")
    cookies = parse_cookie_string(cookie_str)
    return cookies or None


def _normalize_cookie_data(data) -> dict:
    if isinstance(data, dict) and "sessions" in data:
        sessions = data.get("sessions", [])
        current = data.get("current")
        return {"current": current, "sessions": sessions if isinstance(sessions, list) else []}
    if isinstance(data, list):
        cookies = {c["name"]: c["value"] for c in data if "name" in c and "value" in c}
    elif isinstance(data, dict):
        cookies = data
    else:
        cookies = {}
    return {
        "current": "default" if cookies else None,
        "sessions": [{"name": "default", "cookies": cookies}] if cookies else [],
    }


def load_cookie_sessions() -> dict:
    if COOKIES_FILE.exists():
        with open(COOKIES_FILE) as f:
            return _normalize_cookie_data(json.load(f))

    cookies = _parse_headers_cookies()
    return {
        "current": "default" if cookies else None,
        "sessions": [{"name": "default", "cookies": cookies}] if cookies else [],
    }


def save_cookie_sessions(data: dict) -> None:
    auth_dir = COOKIES_FILE.parent
    auth_dir.mkdir(exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_cookies() -> dict[str, str] | None:
    data = load_cookie_sessions()
    current = data.get("current")
    sessions = data.get("sessions", [])
    for session in sessions:
        if session.get("name") == current:
            return session.get("cookies") or None
    if sessions:
        return sessions[0].get("cookies") or None
    return None


def save_cookies(cookies: dict[str, str], name: str = "default") -> None:
    data = load_cookie_sessions()
    sessions = data.get("sessions", [])
    for session in sessions:
        if session.get("name") == name:
            session["cookies"] = cookies
            break
    else:
        sessions.append({"name": name, "cookies": cookies})
    data["sessions"] = sessions
    data["current"] = name
    save_cookie_sessions(data)


class GeminiClient:
    def __init__(self, cookies: dict[str, str]):
        self.cookies = cookies
        self.access_token = ""
        self.build_label = ""
        self.session_id = ""
        self.language = "en"
        self._reqid = random.randint(10000, 99999)
        self.chat_metadata = list(DEFAULT_METADATA)
        self.conversation_id = ""
        self.reply_id = ""
        self._session = httpx.Client(follow_redirects=True, timeout=120.0)

    @property
    def user_agent(self) -> str:
        return "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"

    def _get_next_reqid(self) -> int:
        self._reqid += 100000
        return self._reqid

    def _build_model_header(self, model: str | None = None) -> str:
        if model:
            return f'[1,null,null,null,null,null,null,null,[4,5,6,8],null,null,null,null,null,null,null,"{model}"]'
        return "[1,null,null,null,null,null,null,null,[4,5,6,8],null,null,null,null,null,null,null]"

    def init_session(self) -> None:
        resp = self._session.get(
            "https://gemini.google.com/app",
            headers={"User-Agent": self.user_agent},
            cookies=self.cookies,
        )
        match = re.search(r"SNlM0e\":\"([^\"]+)\"", resp.text)
        if match:
            self.access_token = match.group(1)
        match = re.search(r"cfb2h\":\"([^\"]+)\"", resp.text)
        if match:
            self.build_label = match.group(1)
        match = re.search(r"FdrFJe\":\"([^\"]+)\"", resp.text)
        if match:
            self.session_id = match.group(1)

    def _extract_texts(self, data) -> list[str]:
        texts = []
        if isinstance(data, str):
            value = data.strip()
            if value.startswith(("[", "{")):
                try:
                    texts.extend(self._extract_texts(json.loads(value)))
                except json.JSONDecodeError:
                    texts.extend(re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', value))
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

    def _iter_stream_json(self, raw: str):
        raw = raw.lstrip()
        if raw.startswith(")]}'"):
            raw = raw[4:].lstrip()
        lines = raw.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if not line:
                index += 1
                continue
            json_str = None
            if line[0].isdigit():
                parts = line.split(maxsplit=1)
                if len(parts) > 1:
                    json_str = parts[1]
                elif index + 1 < len(lines):
                    index += 1
                    json_str = lines[index].strip()
            elif line.startswith(("[", "{")):
                json_str = line
            if json_str:
                try:
                    yield json.loads(json_str)
                except json.JSONDecodeError:
                    pass
            index += 1

    def _parse_streaming_response(self, raw: str) -> str:
        if not raw:
            return "Empty response"
        texts = []
        for data in self._iter_stream_json(raw):
            texts.extend(self._extract_texts(data))
        texts = [
            text
            for text in texts
            if text
            and not text.startswith(("wrb.fr", "di", "af."))
            and not re.fullmatch(r"[A-Za-z0-9_-]{24,}", text)
            and not re.fullmatch(r"[a-z]{1,4}_[A-Za-z0-9_-]+", text)
        ]
        if not texts:
            return "Could not parse response"
        return max(texts, key=lambda text: (" " in text or "\n" in text or text.endswith(('.', '!', '?')), len(text)))

    def send_message(self, message: str, model: str | None = None, tools_enabled: bool = False) -> str:
        self._reqid = random.randint(10000, 99999)
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
        model_header = self._build_model_header(model)

        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": self.user_agent,
            "X-Same-Domain": "1",
            "x-goog-ext-525001261-jspb": model_header,
            "x-goog-ext-73010989-jspb": "[0]",
            "x-goog-ext-525005358-jspb": f'["{uuid_val}",1]',
        }

        try:
            if not self.access_token or not self.build_label or not self.session_id:
                self.init_session()
            if not self.access_token:
                return "[Error] Login/session invalid. Delete this cookie session, then add fresh cookies."
            params["bl"] = self.build_label
            params["f.sid"] = self.session_id
            resp = self._session.post(
                "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                params=params,
                headers=headers,
                data={"at": self.access_token, "f.req": f_req},
                cookies=self.cookies,
            )
            if resp.status_code in (401, 403):
                return "[Error] Login/session expired. Delete this cookie session, then add fresh cookies."
            if resp.status_code >= 400:
                return f"[Error] HTTP {resp.status_code}: {resp.text[:200]}"
            return self._parse_streaming_response(resp.text)
        except Exception as e:
            return f"[Error] {e}"

    def extract_image_urls(self, raw: str) -> list[str]:
        urls = re.findall(r'https?://[a-zA-Z0-9.-]+\.googleusercontent\.com/[^\s"\\]+', raw)
        seen = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    def new_chat(self) -> None:
        self.chat_metadata = list(DEFAULT_METADATA)
        self.conversation_id = ""
        self.reply_id = ""
