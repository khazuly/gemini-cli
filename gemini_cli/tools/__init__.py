from __future__ import annotations

import json
import re
import time
import traceback
from typing import Any

from .base import Tool
from .files import EditTool, GrepTool, ListTool, ReadTool, RememberTool, WriteTool
from .lifecycle import EventSink, ToolCall, ToolEvent, ToolResult, ToolState, next_call_id
from .shell import ShellTool

ALL_TOOLS: list[Tool] = [ReadTool(), WriteTool(), EditTool(), GrepTool(), ListTool(), ShellTool(), RememberTool()]

_PARAM_ALIASES = {
    "filePath": ("path", "file_path", "file", "filename"),
    "pattern": ("regex", "query", "search"),
    "command": ("cmd",),
    "replaceAll": ("replace_all", "all"),
    "note": ("text", "content", "fact", "memory"),
}


def _normalize_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    props = (tool.parameters or {}).get("properties") or {}
    normalized = dict(args)
    for canonical, aliases in _PARAM_ALIASES.items():
        if canonical in props and normalized.get(canonical) in (None, ""):
            for alias in aliases:
                if normalized.get(alias) not in (None, ""):
                    normalized[canonical] = normalized.pop(alias)
                    break
    return normalized


_TOOL_ALIASES = {
    "bash": "shell",
    "run_command": "shell",
    "terminal": "shell",
    "execute": "shell",
    "cat": "read",
    "view": "read",
    "open": "read",
    "ls": "list",
    "dir": "list",
    "search": "grep",
    "find": "grep",
    "glob": "grep",
    "create": "write",
    "new_file": "write",
    "patch": "edit",
    "replace": "edit",
    "str_replace": "edit",
}


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools = {t.name: t for t in tools or ALL_TOOLS}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def resolve_name(self, name: str) -> str | None:
        lowered = name.lower()
        if lowered in self._tools:
            return lowered
        return _TOOL_ALIASES.get(lowered)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def build_system_prompt(self) -> str:
        tools_desc = []
        for t in self._tools.values():
            tools_desc.append(f"- {t.name}: {t.description}")
            tools_desc.append(f"  Parameters: {json.dumps(t.parameters)}")
        return (
            "You are Gem, a precise and experienced software engineer and autonomous coding agent. Keep going until the user's query is completely resolved before ending your turn.\n\n"
            "Available tools:\n\n" + "\n".join(tools_desc) + "\n\n"
            "Tool call format:\n"
            "When you need to use a tool, output one or more tool calls, each on its own line, in exactly this format:\n"
            '<tool_call>{"name": "tool_name", "args": {"param1": "value1"}}</tool_call>\n'
            'Inside JSON string arguments, escape newlines as \\n and double quotes as \\" so the JSON stays valid.\n\n'
            "Response rules:\n"
            "- Respond with EITHER tool calls OR your final answer to the user. Never mix narration with tool calls.\n"
            "- NEVER narrate, summarize, or describe your internal analysis or thought process. Never output text like \"Reviewing the input\", \"Analyzing the request\", \"I have finished analyzing\", or any similar meta-commentary.\n"
            "- NEVER explain what you are about to do. Just emit the tool calls immediately.\n"
            "- When you say you will do something, ACTUALLY emit the tool call instead of describing it.\n"
            "- After receiving tool results, continue immediately with the next tool calls or give the final answer. Do not comment on the results themselves.\n"
            "- Treat tool results as authoritative. Never invent files, code, command output, edits, or test results.\n"
            "- A non-zero exit code does not mean nothing happened - commands can have partial effects. Verify the actual state with list/read/shell before claiming success or failure, and report exactly what changed and what remains.\n"
            "- NEVER ask the user to paste code, files, or command output. Use your tools to read them yourself and keep working autonomously until the task is fully resolved.\n"
            "- Work ONLY on the exact file paths confirmed by your own list/read results. NEVER invent file names or switch to different ones (such as 'main.py' or 'index.js') that did not come from tool output or the user.\n"
            "- When reporting on files, name only paths you actually accessed with tools in this task.\n"
            "- Modify existing files in place with edit. Use write ONLY to create a genuinely new file the user asked for - never to park modified content of an existing file under a different name.\n"
            "- Iterate with tools until the task is fully done, then reply with only the final answer: what was found, changed, and verified.\n"
            "- When work was done via tools, keep the final answer to a brief summary: what was done, which file paths changed, and how it was verified. NEVER reprint code or file contents you already wrote or edited via tools. Do not display code to the user unless they explicitly asked for it in their request.\n"
            "- Always wrap any code you do show in triple-backtick fenced code blocks with a language tag.\n"
            "- Keep final answers concise and direct. Use markdown when helpful.\n"
            "- Ask for clarification only when the request is genuinely ambiguous.\n\n"
            "Tool selection:\n"
            "- Use `list` for directory listings and checking what exists in a folder.\n"
            "- Use `grep` for searching file contents across the workspace.\n"
            "- Use `read` for reading known files.\n"
            "- Use `edit` for modifying existing files by replacing exact text.\n"
            "- Use `write` only for creating new files or full rewrites.\n"
            "- Use `shell` for terminal operations such as running tests, checking git state, or executing scripts. Prefer specialized tools over shell equivalents like ls, cat, grep, sed.\n"
            "- Long-running commands (dependency installs, full test suites, builds) often exceed the default 120000 ms shell timeout - pass a larger 'timeout' value in milliseconds (up to 600000) in the same tool call for such commands instead of letting them time out.\n"
            "- Use `remember` to persist important project facts (conventions, commands, gotchas) to GEMINI.md for future sessions.\n\n"
            "Examples:\n"
            'User: list the files in this folder -> <tool_call>{"name": "list", "args": {"path": "."}}</tool_call>\n'
            'User: run git status -> <tool_call>{"name": "shell", "args": {"command": "git status"}}</tool_call>\n'
            'User: run the tests -> <tool_call>{"name": "shell", "args": {"command": "pytest"}}</tool_call>\n'
            'User: find code references to requests -> <tool_call>{"name": "grep", "args": {"pattern": "requests"}}</tool_call>\n'
            "User: read README.md -> read README.md with the read tool, then answer directly without commentary."
        )

    def execute(self, name: str, args: dict[str, Any]) -> str:
        return self.execute_call(name, args).output or ""

    def execute_call(self, name: str, args: dict[str, Any], sink: EventSink | None = None, call_id: str | None = None) -> ToolCall:
        call = ToolCall(id=call_id or next_call_id(), name=name, input=args)
        tool = self.get(name)
        if not tool:
            resolved = self.resolve_name(name)
            if resolved:
                call.name = resolved
                name = resolved
                tool = self.get(resolved)
        if not tool:
            call.state = ToolState.ERROR
            call.error = (
                f"Unknown tool '{name}'. Available tools: {', '.join(self._tools)}. "
                "Re-emit the <tool_call> using one of these exact tool names with valid JSON arguments."
            )
            call.output = f"Error: {call.error}"
            call.finished_at = time.monotonic()
            if sink:
                sink(ToolEvent("tool_failed", call))
            return call
        args = _normalize_args(tool, args)
        call.input = args
        required = (tool.parameters or {}).get("required") or []
        missing = [r for r in required if args.get(r) in (None, "")]
        if missing:
            props = list(((tool.parameters or {}).get("properties") or {}).keys())
            call.state = ToolState.ERROR
            call.error = f"Missing required argument(s) {missing} for tool '{name}'. Valid params: {props}. Re-emit the exact same tool call with the correct arguments."
            call.output = f"Error: {call.error}"
            call.finished_at = time.monotonic()
            if sink:
                sink(ToolEvent("tool_failed", call))
            return call
        call.title = tool.title(args)
        call.metadata = {"input_summary": tool.summarize_input(args)}
        if sink:
            sink(ToolEvent("tool_pending", call))
        call.state = ToolState.RUNNING
        call.started_at = time.monotonic()
        if sink:
            sink(ToolEvent("tool_started", call))
        progress = None
        if getattr(tool, "streaming", False) and sink:
            def progress(tail: str) -> None:
                call.progress = tail or None
                sink(ToolEvent("tool_progress", call))

        try:
            raw = tool.execute(args, progress=progress) if progress else tool.execute(args)
            result = raw if isinstance(raw, ToolResult) else tool.summarize_result(args, str(raw))
            call.state = ToolState.ERROR if result.error or (result.output or "").startswith("Error") else ToolState.COMPLETED
            call.output = result.output
            call.display_output = result.display_output if call.state is ToolState.COMPLETED else None
            call.error = result.error or (result.output if call.state is ToolState.ERROR else None)
            call.title = result.title or call.title
            call.metadata = {**call.metadata, **result.metadata}
            call.truncated = result.truncated
            call.finished_at = time.monotonic()
            if sink:
                sink(ToolEvent("tool_failed" if call.state is ToolState.ERROR else "tool_completed", call))
        except KeyboardInterrupt:
            call.state = ToolState.CANCELLED
            call.error = "cancelled"
            call.finished_at = time.monotonic()
            if sink:
                sink(ToolEvent("tool_cancelled", call))
            raise
        except Exception as e:
            call.state = ToolState.ERROR
            call.error = str(e)
            call.output = f"Error executing {name}: {e}"
            call.metadata = {**call.metadata, "traceback": traceback.format_exc()}
            call.finished_at = time.monotonic()
            if sink:
                sink(ToolEvent("tool_failed", call))
        return call

    def parse_tool_calls(self, text: str) -> list[dict]:
        calls: list[dict] = []
        for match in re.finditer(r"<tool_call>(.*?)(?:</tool_call>|(?=<tool_call>)|$)", text, re.DOTALL):
            raw = match.group(0)
            payload = match.group(1).strip()
            data = self._loads_lenient(payload)
            if isinstance(data, dict) and data.get("name"):
                calls.append({"name": data.get("name"), "args": self._coerce_args(data), "raw": raw, "id": data.get("id") or data.get("call_id")})
                continue
            salvaged = self._salvage_truncated_json(payload)
            if isinstance(salvaged, dict) and salvaged.get("name"):
                calls.append({"name": salvaged.get("name"), "args": self._coerce_args(salvaged), "raw": raw, "id": None})
                continue
            extracted = self._extract_write_args(payload)
            if extracted:
                calls.append({"name": "write", "args": extracted, "raw": raw, "id": None})
                continue
            calls.append({"name": None, "args": {}, "raw": raw, "id": None})
        if not calls:
            bare = re.search(r'\{\s*"name"\s*:', text)
            if bare:
                salvaged = self._salvage_truncated_json(text[bare.start():])
                if isinstance(salvaged, dict) and salvaged.get("name"):
                    calls.append({"name": salvaged.get("name"), "args": self._coerce_args(salvaged), "raw": bare.group(0), "id": None})
        return calls

    @staticmethod
    def _coerce_args(data: dict) -> dict:
        args = data.get("args")
        if not isinstance(args, dict):
            args = {k: v for k, v in data.items() if k not in ("name", "id", "call_id")}
        return args

    @staticmethod
    def _loads_lenient(payload: str) -> dict | None:
        candidates = [payload]
        start, end = payload.find("{"), payload.rfind("}")
        if start != -1 and end > start:
            candidates.append(payload[start : end + 1])
        for candidate in candidates:
            variants = (
                candidate,
                candidate.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'"),
                re.sub(r",\s*([}\]])", r"\1", candidate),
            )
            for variant in variants:
                try:
                    return json.loads(variant, strict=False)
                except json.JSONDecodeError:
                    continue
        repaired = ToolRegistry._repair_quotes(candidates[-1] if candidates else payload)
        if repaired:
            try:
                return json.loads(repaired, strict=False)
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _repair_quotes(payload: str) -> str | None:
        start, end = payload.find("{"), payload.rfind("}")
        if start == -1 or end <= start:
            return None
        body = payload[start : end + 1]
        out: list[str] = []
        in_string = False
        i = 0
        while i < len(body):
            ch = body[i]
            if in_string and ch == "\\" and i + 1 < len(body):
                out.append(body[i : i + 2])
                i += 2
                continue
            if ch == '"':
                if not in_string:
                    in_string = True
                    out.append(ch)
                else:
                    j = i + 1
                    while j < len(body) and body[j] in " \t\r\n":
                        j += 1
                    nxt = body[j] if j < len(body) else ""
                    if nxt in ",}]:":
                        in_string = False
                        out.append(ch)
                    else:
                        out.append('\\"')
            else:
                out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _salvage_truncated_json(payload: str) -> dict | None:
        start = payload.find("{")
        if start == -1:
            return None
        body = payload[start:]
        stack: list[str] = []
        in_string = False
        escape = False
        best: tuple[int, list[str]] | None = None
        closed_at = 0
        for i, ch in enumerate(body):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]" and stack:
                stack.pop()
                if not stack and not closed_at:
                    closed_at = i + 1
            elif ch == "," and stack:
                best = (i, list(stack))
        if not stack and not in_string:
            for candidate in (body, body[:closed_at] if closed_at else ""):
                if not candidate:
                    continue
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            return None
        if best is None:
            return None
        cut = body[: best[0] + 1].rstrip().rstrip(",")
        closers = "".join("}" if s == "{" else "]" for s in reversed(best[1]))
        try:
            return json.loads(cut + closers)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_write_args(payload: str) -> dict | None:
        path_match = re.search(r'"(?:filePath|file_path|path)"\s*:\s*"([^"]+)"', payload)
        content_match = re.search(r'"content"\s*:\s*"', payload)
        if not path_match or not content_match:
            return None
        start = content_match.end()
        end = payload.rfind('"')
        if end <= start:
            return None
        content = payload[start:end]
        content = content.replace("\\\\", "\x00").replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\x00", "\\")
        return {"filePath": path_match.group(1), "content": content}
