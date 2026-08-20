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


def load_cookies() -> dict[str, str] | None:
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


def save_cookies(cookies: dict[str, str]) -> None:
    auth_dir = COOKIES_FILE.parent
    auth_dir.mkdir(exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)


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

    def _parse_streaming_response(self, raw: str) -> str:
        if not raw:
            return "Empty response"
        raw = raw.lstrip()
        if raw.startswith(")]}'"):
            raw = raw[4:].lstrip()
        texts = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or not line[0].isdigit():
                continue
            try:
                length = int(line.split()[0])
                json_str = line[len(str(length)):].strip()
                if not json_str:
                    continue
                data = json.loads(json_str)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    inner = data[1]
                    for item in inner:
                        if isinstance(item, list):
                            for sub in item:
                                if isinstance(sub, list):
                                    for candidate in sub:
                                        if isinstance(candidate, list) and len(candidate) > 1:
                                            parts = candidate[1]
                                            if isinstance(parts, list):
                                                for part in parts:
                                                    if isinstance(part, list) and len(part) > 0 and isinstance(part[0], str):
                                                        texts.append(part[0])
            except (json.JSONDecodeError, ValueError, IndexError, TypeError):
                continue
        if not texts:
            return "Could not parse response"
        longest = ""
        for t in texts:
            if len(t) > len(longest) or not longest.startswith(t):
                longest = t
        return longest if longest else texts[-1]

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
            resp = self._session.post(
                "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                params=params,
                headers=headers,
                data={"at": self.access_token, "f.req": f_req},
                cookies=self.cookies,
            )
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
