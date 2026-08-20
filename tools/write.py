from pathlib import Path
from .base import Tool


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

    def execute(self, args: dict) -> str:
        file_path = Path(args["filePath"])
        content = args["content"]

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"File written successfully: {file_path}"
        except Exception as e:
            return f"Error writing file: {e}"
