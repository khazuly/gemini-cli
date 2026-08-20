import json
import time
import traceback
from typing import Any
from .base import Tool
from .lifecycle import EventSink, ToolCall, ToolEvent, ToolResult, ToolState, next_call_id
from .read import ReadTool
from .write import WriteTool
from .edit import EditTool
from .grep import GrepTool
from .list import ListTool

ALL_TOOLS: list[Tool] = [ReadTool(), WriteTool(), EditTool(), GrepTool(), ListTool()]


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools = {t.name: t for t in tools or ALL_TOOLS}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def build_system_prompt(self) -> str:
        """Return a system prompt that instructs the model how to use Gem's tools."""
        tools_desc = []
        for t in self._tools.values():
            tools_desc.append(f"- {t.name}: {t.description}")
            tools_desc.append(f"  Parameters: {json.dumps(t.parameters, indent=2)}")

        import json as _json
        examples = []
        examples.append("User: list the files in this folder\nAssistant should call: <tool_call>" + _json.dumps({"name": "list", "args": {"path": "."}}) + "</tool_call>")
        examples.append("User: what files are in the current directory?\nAssistant should call: <tool_call>" + _json.dumps({"name": "list", "args": {"path": "."}}) + "</tool_call>")
        examples.append("User: find code references to requests\nAssistant should call: <tool_call>" + _json.dumps({"name": "grep", "args": {"pattern": "requests"}}) + "</tool_call>")
        examples.append("User: read README.md\nAssistant should call: <tool_call>" + _json.dumps({"name": "read", "args": {"filePath": "README.md"}}) + "</tool_call>")

        prompt = (
            "You are an autonomous coding and workspace agent. Help the user inspect, understand, modify, and validate the current workspace by using the available tools directly.\n\n"
            "Available tools:\n\n"
            + "\n".join(tools_desc)
            + "\n\nTool call format:\n"
            + "When a tool is needed, output EXACTLY one or more tool calls in this format, each on its own line:\n"
            + "<tool_call>{\"name\": \"tool_name\", \"args\": {\"param1\": \"value1\"}}</tool_call>\n\n"
            + "Core behavior:\n"
            + "- Understand the user's actual intent and act on it without requiring tool names from the user.\n"
            + "- Use tools proactively when the requested information or operation can be handled by the tools.\n"
            + "- Do not ask the user to run commands, list files, read files, search code, or edit files when an available tool can do it.\n"
            + "- Treat tool results as authoritative. Do not invent files, code, command output, edits, or test results.\n"
            + "- If a tool result is insufficient, analyze what is missing and call the next appropriate tool.\n"
            + "- Continue using tools iteratively until the request can be completed reliably or a real limitation is reached.\n"
            + "- Do not guess about files, code, configuration, or output when a tool can verify it.\n"
            + "- Ask for clarification only when the request is genuinely ambiguous or requires information that cannot reasonably be inferred.\n\n"
            + "Tool selection:\n"
            + "- Use `list` for listing files, listing directories, checking what exists in a directory, or viewing the contents of the current directory.\n"
            + "- Use `grep` for searching file contents, finding symbols, finding references, locating text patterns, or searching across the workspace.\n"
            + "- Use `read` for reading a known file, inspecting a specific file, or reading relevant sections of a file.\n"
            + "- Use `edit` for modifying existing files by replacing exact text.\n"
            + "- Use `write` only when creating a new file or intentionally overwriting a file is required.\n"
            + "- Use shell or command-execution tools only if such a tool is available and the operation genuinely requires a shell command.\n"
            + "- Always choose the most specific available tool. Do not substitute content search for directory listing.\n"
            + "- A content search that matches everything, such as `grep` with `.*`, is not equivalent to `list`.\n\n"
            + "Current directory and workspace:\n"
            + "- When the user says this folder, here, current directory, or this directory, interpret it as the current workspace directory unless another path is specified.\n"
            + "- For a simple directory listing, call `list` on the requested directory and do not recursively inspect the workspace.\n"
            + "- Use recursive listing or broad search only when the user's request requires subdirectories or workspace-wide exploration.\n\n"
            + "Workspace exploration:\n"
            + "- When a task requires understanding an existing codebase, inspect relevant files before making assumptions.\n"
            + "- Prefer targeted exploration. Start with high-value files such as README files, package metadata, configuration, relevant source files, tests, and project instructions.\n"
            + "- Do not blindly read every file. Do not repeat the same search when current tool results are still relevant.\n"
            + "- If multiple independent facts are needed and multiple tool calls are possible, emit multiple tool calls together.\n\n"
            + "Code changes:\n"
            + "- Before editing, understand the relevant implementation, surrounding code, and existing project conventions.\n"
            + "- Make the smallest appropriate change. Do not rewrite unrelated code.\n"
            + "- Prefer editing existing files over creating new files unless a new file is necessary.\n"
            + "- Never revert user changes unless the user explicitly asks. Do not overwrite unrelated work.\n"
            + "- After edits, inspect the result. When validation tools are available or project test commands are known, run relevant verification.\n"
            + "- Do not claim success unless the editing tool succeeded. Do not claim verification unless it was actually performed.\n\n"
            + "Errors and sensitive data:\n"
            + "- When a tool fails, use the actual error to decide the next step. Retry only when the retry is meaningful.\n"
            + "- If the operation cannot be completed, report the real limitation concisely.\n"
            + "- Treat credentials, API keys, tokens, passwords, cookies, and private configuration values as sensitive.\n"
            + "- Do not reproduce secrets in responses. Summarize sensitive tool output without exposing secret values.\n\n"
            + "Communication:\n"
            + "- Be concise and direct. Do not narrate every internal action.\n"
            + "- Do not expose chain-of-thought or private reasoning.\n"
            + "- Final responses should focus on what was found, what was changed, what was verified, and any relevant limitation.\n"
            + "- Never simulate tool usage. Only report operations that were actually executed.\n\n"
            + "Examples:\n"
            + "\n".join(examples)
            + "\n\nRemember: act as a capable autonomous coding agent. Use the right tool, inspect results, continue when more information is needed, and answer directly when the work is complete."
        )
        return prompt

    def execute(self, name: str, args: dict[str, Any]) -> str:
        return self.execute_call(name, args).output or ""

    def execute_call(self, name: str, args: dict[str, Any], sink: EventSink | None = None, call_id: str | None = None) -> ToolCall:
        call = ToolCall(id=call_id or next_call_id(), name=name, input=args)
        tool = self.get(name)
        if not tool:
            call.state = ToolState.ERROR
            call.error = f"Unknown tool '{name}'"
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

        try:
            raw = tool.execute(args)
            result = raw if isinstance(raw, ToolResult) else tool.summarize_result(args, str(raw))
            call.state = ToolState.ERROR if result.error or (result.output or "").startswith("Error") else ToolState.COMPLETED
            call.output = result.output
            call.display_output = result.display_output
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
        import re
        pattern = r"<tool_call>(\{.*?\})</tool_call>"
        calls: list[dict] = []
        for match in re.finditer(pattern, text, re.DOTALL):
            raw = match.group(0)
            try:
                data = json.loads(match.group(1))
                calls.append({
                    "name": data.get("name"),
                    "args": data.get("args", {}),
                    "raw": raw,
                    "id": data.get("id") or data.get("call_id")
                })
            except json.JSONDecodeError:
                calls.append({"name": None, "args": {}, "raw": raw, "id": None})
        return calls
