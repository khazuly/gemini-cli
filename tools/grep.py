import re
from pathlib import Path
from .base import Tool


class GrepTool(Tool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "Search file contents using regular expressions"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current)",
                },
                "include": {
                    "type": "string",
                    "description": "File pattern to include (e.g. '*.py')",
                },
            },
            "required": ["pattern"],
        }

    def execute(self, args: dict) -> str:
        pattern = args["pattern"]
        search_path = Path(args.get("path", "."))
        include = args.get("include")

        if not search_path.exists():
            return f"Error: Path not found: {search_path}"

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        matches = []
        files_searched = 0

        if search_path.is_file():
            files = [search_path]
        else:
            glob_pattern = include if include else "**/*"
            files = list(search_path.glob(glob_pattern))

        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        matches.append(f"{f}:{i}: {line.strip()}")
                        if len(matches) >= 100:
                            break
            except Exception:
                continue
            files_searched += 1
            if len(matches) >= 100:
                break

        if not matches:
            return "No matches found"

        output = f"Found {len(matches)} match(es):\n\n"
        output += "\n".join(matches)
        if len(matches) >= 100:
            output += "\n\n(Results truncated at 100 matches)"
        return output
