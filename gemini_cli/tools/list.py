from pathlib import Path
from .base import Tool
from .lifecycle import ToolResult


class ListTool(Tool):
    @property
    def name(self) -> str:
        return "list"

    @property
    def description(self) -> str:
        return "List files and directories in a given path. Use this for directory listings, checking what exists in a folder, or viewing the contents of the current directory. Non-recursive by default. Do NOT use grep with .* pattern as a substitute for directory listing."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (default: current working directory)"},
                "show_hidden": {"type": "boolean", "description": "Include hidden files", "default": False},
                "recursive": {"type": "boolean", "description": "Recursively list files (use grep for content search instead)", "default": False},
            },
            "required": []
        }

    def title(self, args: dict) -> str | None:
        return f"List files"

    def summarize_input(self, args: dict) -> str:
        p = args.get("path") or "."
        return f"{p}"

    def summarize_result(self, args: dict, output: str) -> ToolResult:
        lines = [l for l in output.splitlines() if l.strip()]
        display = f"{len(lines)} entries"
        # include path in metadata
        metadata = {"path": args.get("path") or ".", "entries": len(lines)}
        return ToolResult(output=output, display_output=display, metadata=metadata)

    def execute(self, args: dict) -> str:
        path = Path(args.get("path") or ".")
        show_hidden = bool(args.get("show_hidden", False))
        recursive = bool(args.get("recursive", False))

        if not path.exists():
            return f"Error: Path not found: {path}"
        if path.is_file():
            return f"Error: Path is a file, not a directory: {path}"

        try:
            entries = []
            if recursive:
                for p in path.rglob("*"):
                    name = str(p.relative_to(path))
                    if not show_hidden and any(part.startswith(".") for part in name.split("/")):
                        continue
                    if p.is_dir():
                        entries.append(f"{name}/")
                    else:
                        entries.append(name)
            else:
                for p in sorted(path.iterdir()):
                    if not show_hidden and p.name.startswith("."):
                        continue
                    entries.append(p.name + ("/" if p.is_dir() else ""))

            if not entries:
                return "No entries found"

            output = "\n".join(entries)
            return output
        except Exception as e:
            return f"Error listing directory: {e}"
