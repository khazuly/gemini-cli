from pathlib import Path
from .base import Tool


class ReadTool(Tool):
    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "Read file contents from the codebase"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filePath": {
                    "type": "string",
                    "description": "The path to the file to read",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start from (1-indexed)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to read (default 2000)",
                },
            },
            "required": ["filePath"],
        }

    def execute(self, args: dict) -> str:
        file_path = Path(args["filePath"])
        if not file_path.exists():
            return f"Error: File not found: {args['filePath']}"

        if file_path.is_dir():
            items = sorted(
                [f.name + "/" if f.is_dir() else f.name for f in file_path.iterdir()]
            )
            return f"<type>directory</type>\n<path>{file_path}</path>\n<entries>\n" + "\n".join(items) + "\n</entries>"

        offset = args.get("offset", 1)
        limit = args.get("limit", 2000)

        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            return f"Error reading file: {e}"

        total = len(lines)
        start = max(0, offset - 1)
        end = min(total, start + limit)
        selected = lines[start:end]

        if not selected:
            return f"Error: Offset {offset} out of range (file has {total} lines)"

        output = f"<path>{file_path}</path>\n<type>file</type>\n<content>\n"
        for i, line in enumerate(selected, start=start + 1):
            output += f"{i}: {line}\n"

        if end < total:
            output += f"\n(Showing lines {start + 1}-{end} of {total}. Use offset={end + 1} to continue.)"
        else:
            output += f"\n(End of file - total {total} lines)"
        output += "\n</content>"

        return output
