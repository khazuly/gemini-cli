import re
from pathlib import Path
from .base import Tool
from .lifecycle import ToolResult


class GrepTool(Tool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "Search file contents using regular expressions. Use this for finding code references, symbols, text patterns, or searching across the workspace. Do NOT use this for directory listings - use the 'list' tool instead."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regex pattern to search for in file contents",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current working directory)",
                },
                "include": {
                    "type": "string",
                    "description": "File pattern to include (e.g. '*.py')",
                },
            },
            "required": ["pattern"],
        }

    def title(self, args: dict) -> str | None:
        return f"Search {args.get('pattern', '')}"

    def summarize_input(self, args: dict) -> str:
        lines = [f"pattern: {args.get('pattern', '')}", f"path: {args.get('path', '.')}"]
        if args.get("include"):
            lines.append(f"include: {args['include']}")
        return "\n".join(lines)

    def summarize_result(self, args: dict, output: str) -> ToolResult:
        metadata = {"pattern": args.get("pattern"), "path": args.get("path", ".")}
        match = re.search(r"Found (\d+) matches", output)
        if match:
            matches = int(match.group(1))
        elif output.startswith("No matches"):
            matches = 0
        else:
            matches = len([line for line in output.splitlines() if ":" in line])
        metadata["matches"] = matches
        display = f"{matches}+ matches" if matches >= 100 else f"{matches} matches"
        return ToolResult(output=output, display_output=display, metadata=metadata, truncated=matches >= 100)

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
