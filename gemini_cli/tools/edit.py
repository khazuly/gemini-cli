from pathlib import Path
from .base import Tool


class EditTool(Tool):
    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return "Modify files by replacing exact text matches"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filePath": {
                    "type": "string",
                    "description": "The path to the file to modify",
                },
                "oldString": {
                    "type": "string",
                    "description": "The exact text to replace",
                },
                "newString": {
                    "type": "string",
                    "description": "The text to replace it with",
                },
                "replaceAll": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                },
            },
            "required": ["filePath", "oldString", "newString"],
        }

    def execute(self, args: dict) -> str:
        file_path = Path(args["filePath"])
        old = args["oldString"]
        new = args["newString"]
        replace_all = args.get("replaceAll", False)

        if old == new:
            return "Error: oldString and newString are identical"

        if not file_path.exists():
            return f"Error: File not found: {args['filePath']}"

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

        if old not in content:
            return f"Error: oldString not found in {args['filePath']}"

        if replace_all:
            count = content.count(old)
            content = content.replace(old, new)
        else:
            if content.count(old) > 1:
                return f"Error: Found multiple matches for oldString. Provide more context or use replaceAll=true"
            content = content.replace(old, new, 1)

        try:
            file_path.write_text(content, encoding="utf-8")
            action = "Replaced" if not replace_all else "Replaced all"
            return f"{action} successfully in {file_path}"
        except Exception as e:
            return f"Error writing file: {e}"
