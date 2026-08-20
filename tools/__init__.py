import json
from typing import Any
from .base import Tool
from .read import ReadTool
from .write import WriteTool
from .edit import EditTool
from .grep import GrepTool

ALL_TOOLS: list[Tool] = [ReadTool(), WriteTool(), EditTool(), GrepTool()]


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools = {t.name: t for t in tools or ALL_TOOLS}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def build_system_prompt(self) -> str:
        tools_desc = []
        for t in self._tools.values():
            tools_desc.append(f"- {t.name}: {t.description}")
            tools_desc.append(f"  Parameters: {json.dumps(t.parameters, indent=2)}")

        return f"""You have access to the following tools:

{chr(10).join(tools_desc)}

When you need to use a tool, output EXACTLY this format (on its own line):
<tool_call>{{"name": "tool_name", "args": {{"param1": "value1"}}}}</tool_call>

Example:
<tool_call>{{"name": "read", "args": {{"filePath": "src/main.py"}}}}</tool_call>

Rules:
- Output tool_call tags on their own line
- Use valid JSON for args
- Wait for tool results before continuing
- You can make multiple tool calls in sequence"""

    def execute(self, name: str, args: dict[str, Any]) -> str:
        tool = self.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"
        try:
            return tool.execute(args)
        except Exception as e:
            return f"Error executing {name}: {e}"

    def parse_tool_calls(self, text: str) -> list[dict]:
        import re
        pattern = r"<tool_call>(\{.*?\})</tool_call>"
        calls = []
        for match in re.finditer(pattern, text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                calls.append(data)
            except json.JSONDecodeError:
                continue
        return calls
