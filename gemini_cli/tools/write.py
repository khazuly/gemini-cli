from pathlib import Path
from .base import Tool
from .lifecycle import ToolResult


class WriteTool(Tool):
    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "Create new files or overwrite existing ones"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filePath": {
                    "type": "string",
                    "description": "The path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["filePath", "content"],
        }

    def title(self, args: dict) -> str | None:
        return f"Write {args.get('filePath', '')}"

    def summarize_input(self, args: dict) -> str:
        content = args.get("content", "")
        return f"{args.get('filePath', '')}\n{len(str(content).splitlines())} lines"

    def summarize_result(self, args: dict, output: str) -> ToolResult:
        content = str(args.get("content", ""))
        lines = len(content.splitlines())
        metadata = {"path": args.get("filePath"), "lines": lines}
        return ToolResult(output=output, display_output=f"{lines} lines written", metadata=metadata)

    def execute(self, args: dict) -> str:
        file_path = Path(args["filePath"])
        content = args["content"]

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"File written successfully: {file_path}"
        except Exception as e:
            return f"Error writing file: {e}"
