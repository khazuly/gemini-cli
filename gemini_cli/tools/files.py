from __future__ import annotations

import difflib
import re
from pathlib import Path

from .base import Tool
from .lifecycle import ToolResult


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_indent(text: str) -> str:
    lines = text.split("\n")
    indents = [len(m.group(0)) for line in lines if line.strip() for m in [re.match(r"^[\t ]*", line)]]
    if not indents:
        return text
    cut = min(indents)
    return "\n".join(line[cut:] if line.strip() else line for line in lines)


_ESCAPES = {"\\n": "\n", "\\t": "\t", "\\r": "\r", '\\"': '"', "\\'": "'", "\\\\": "\\"}


def _unescape(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        pair = text[i : i + 2]
        if pair in _ESCAPES:
            out.append(_ESCAPES[pair])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _pop_trailing_blank(lines: list[str]) -> list[str]:
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _r_simple(content: str, find: str):
    yield find


def _r_line_trimmed(content: str, find: str):
    orig = content.split("\n")
    search = _pop_trailing_blank(find.split("\n"))
    n = len(search)
    for i in range(len(orig) - n + 1):
        if all(orig[i + j].strip() == search[j].strip() for j in range(n)):
            yield "\n".join(orig[i : i + n])


def _r_block_anchor(content: str, find: str):
    orig = content.split("\n")
    search = _pop_trailing_blank(find.split("\n"))
    if len(search) < 3:
        return
    first, last = search[0].strip(), search[-1].strip()
    size = len(search)
    delta = max(1, size // 4)
    candidates = []
    for i, line in enumerate(orig):
        if line.strip() != first:
            continue
        for j in range(i + 2, len(orig)):
            if orig[j].strip() == last:
                if abs((j - i + 1) - size) <= delta:
                    candidates.append((i, j))
                break
    best, best_sim = None, 0.0
    for start, end in candidates:
        middles = min(size - 2, end - start - 1)
        if middles <= 0:
            sim = 1.0
        else:
            sim = sum(_similarity(orig[start + k].strip(), search[k].strip()) for k in range(1, min(size - 1, end - start))) / middles
        if sim > best_sim:
            best_sim, best = sim, (start, end)
    if best and best_sim >= 0.65:
        yield "\n".join(orig[best[0] : best[1] + 1])


def _r_whitespace_normalized(content: str, find: str):
    target = _norm_ws(find)
    orig = content.split("\n")
    for line in orig:
        if _norm_ws(line) == target:
            yield line
    search = find.split("\n")
    if len(search) > 1:
        n = len(search)
        for i in range(len(orig) - n + 1):
            if _norm_ws("\n".join(orig[i : i + n])) == target:
                yield "\n".join(orig[i : i + n])


def _r_indentation_flexible(content: str, find: str):
    target = _strip_indent(find)
    orig = content.split("\n")
    n = len(find.split("\n"))
    for i in range(len(orig) - n + 1):
        block = "\n".join(orig[i : i + n])
        if _strip_indent(block) == target:
            yield block


def _r_escape_normalized(content: str, find: str):
    unescaped = _unescape(find)
    if unescaped != find and unescaped in content:
        yield unescaped


def _r_trimmed_boundary(content: str, find: str):
    trimmed = find.strip()
    if trimmed != find and trimmed in content:
        yield trimmed


_REPLACERS = (_r_simple, _r_line_trimmed, _r_block_anchor, _r_whitespace_normalized, _r_indentation_flexible, _r_escape_normalized, _r_trimmed_boundary)


def _is_disproportionate(search: str, old: str) -> bool:
    old_lines = old.split("\n")
    search_lines = search.split("\n")
    if len(search_lines) >= max(len(old_lines) + 3, len(old_lines) * 2):
        return True
    if len(old_lines) == 1:
        return False
    return len(search.strip()) > max(len(old.strip()) + 500, len(old.strip()) * 4)


def apply_edit(content: str, old: str, new: str, replace_all: bool = False) -> tuple[str, int]:
    ending = "\r\n" if "\r\n" in content else "\n"
    old = old.replace("\r\n", "\n").replace("\n", ending)
    new = new.replace("\r\n", "\n").replace("\n", ending)
    not_found = True
    for replacer in _REPLACERS:
        for search in replacer(content, old):
            if not search or search not in content:
                continue
            not_found = False
            if _is_disproportionate(search, old):
                raise ValueError("Matched span is much larger than oldString. Re-read the file and provide the full exact oldString.")
            count = content.count(search)
            use_new = _unescape(new) if replacer is _r_escape_normalized else new
            if replace_all:
                return content.replace(search, use_new), count
            if count > 1:
                continue
            index = content.index(search)
            return content[:index] + use_new + content[index + len(search) :], 1
    if not_found:
        raise ValueError("Could not find oldString in the file. It must match exactly, including whitespace, indentation, and line endings.")
    raise ValueError("Found multiple matches for oldString. Provide more surrounding context to make the match unique.")


class ReadTool(Tool):
    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "Read the contents of a file at a given path. Use this for inspecting source files, configuration, or any text file."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "The path to the file to read"},
                "offset": {"type": ["integer", "null"], "description": "Line number to start reading from (1-indexed)"},
                "limit": {"type": ["integer", "null"], "description": "Maximum number of lines to read"},
            },
            "required": ["filePath"],
        }

    def title(self, args: dict) -> str | None:
        return f"Read {args.get('filePath', '')}"

    def execute(self, args: dict) -> str:
        path = Path(args.get("filePath", ""))
        if not path.exists():
            return f"Error: File not found: {path}"
        if not path.is_file():
            return f"Error: Path is not a file: {path}"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            return f"Error reading file: {e}"
        offset = max(0, int(args.get("offset") or 1) - 1)
        limit = int(args.get("limit") or 2000)
        selected = lines[offset : offset + limit]
        return "\n".join(f"{offset + i + 1}: {line}" for i, line in enumerate(selected)) or "(empty file)"


class WriteTool(Tool):
    @property
    def name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "Write content to a file, creating it or overwriting it. Use only for creating new files or full rewrites of the same path. NEVER create a new file to hold modified content of an existing file - edit the original in place instead."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "The path to the file to write"},
                "content": {"type": "string", "description": "The content to write to the file"},
            },
            "required": ["filePath", "content"],
        }

    def title(self, args: dict) -> str | None:
        return f"Write {args.get('filePath', '')}"

    def execute(self, args: dict) -> str:
        path = Path(args.get("filePath", ""))
        content = args.get("content")
        if not isinstance(content, str):
            return "Error: 'content' argument is required and must be a string"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            return f"Error writing file: {e}"
        return f"Successfully wrote {len(content)} bytes to {path}"


class EditTool(Tool):
    @property
    def name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return "Edit a file by replacing an exact text snippet with new text. The oldString must match exactly and be unique."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "The path to the file to edit"},
                "oldString": {"type": "string", "description": "The exact text to replace"},
                "newString": {"type": "string", "description": "The replacement text"},
                "replaceAll": {"type": ["boolean", "null"], "description": "Replace all occurrences (default: false)"},
            },
            "required": ["filePath", "oldString", "newString"],
        }

    def title(self, args: dict) -> str | None:
        return f"Edit {args.get('filePath', '')}"

    def execute(self, args: dict) -> str:
        path = Path(args.get("filePath", ""))
        old = args.get("oldString")
        new = args.get("newString")
        if not path.exists() or not path.is_file():
            return f"Error: File not found: {path}"
        if not isinstance(old, str) or not isinstance(new, str):
            return "Error: 'oldString' and 'newString' must be strings"
        if old == "":
            return "Error: oldString cannot be empty. Use write for intentional full-file replacement."
        if old == new:
            return "Error: No changes to apply: oldString and newString are identical."
        try:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
                content = f.read()
        except Exception as e:
            return f"Error reading file: {e}"
        try:
            updated, replaced = apply_edit(content, old, new, bool(args.get("replaceAll")))
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error applying edit: {e}"
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(updated)
        return f"Replaced {replaced} occurrence(s) in {path}"


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
                "pattern": {"type": "string", "description": "The regex pattern to search for in file contents"},
                "path": {"type": "string", "description": "Directory or file to search in (default: current working directory)"},
                "include": {"type": "string", "description": "File pattern to include (e.g. '*.py')"},
            },
            "required": ["pattern"],
        }

    def title(self, args: dict) -> str | None:
        return f"Search {args.get('pattern', '')}"

    def summarize_result(self, args: dict, output: str) -> ToolResult:
        match = re.search(r"Found (\d+) matches", output)
        matches = int(match.group(1)) if match else (0 if output.startswith("No matches") else len([l for l in output.splitlines() if ":" in l]))
        display = f"{matches}+ matches" if matches >= 100 else f"{matches} matches"
        metadata = {"pattern": args.get("pattern"), "path": args.get("path", "."), "matches": matches}
        return ToolResult(output=output, display_output=display, metadata=metadata, truncated=matches >= 100)

    def execute(self, args: dict) -> str:
        search_path = Path(args.get("path", "."))
        if not search_path.exists():
            return f"Error: Path not found: {search_path}"
        try:
            regex = re.compile(args["pattern"], re.IGNORECASE)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"
        files = [search_path] if search_path.is_file() else list(search_path.glob(args.get("include") or "**/*"))
        matches = []
        for f in files:
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if regex.search(line):
                        matches.append(f"{f}:{i}: {line.strip()}")
                        if len(matches) >= 100:
                            break
            except Exception:
                continue
            if len(matches) >= 100:
                break
        if not matches:
            return "No matches found"
        output = f"Found {len(matches)} match(es):\n\n" + "\n".join(matches)
        if len(matches) >= 100:
            output += "\n\n(Results truncated at 100 matches)"
        return output


class ListTool(Tool):
    @property
    def name(self) -> str:
        return "list"

    @property
    def description(self) -> str:
        return "List files and directories in a given path. Use this for directory listings, checking what exists in a folder, or viewing the contents of the current directory. Non-recursive by default."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (default: current working directory)"},
                "show_hidden": {"type": "boolean", "description": "Include hidden files", "default": False},
                "recursive": {"type": "boolean", "description": "Recursively list files", "default": False},
            },
            "required": [],
        }

    def title(self, args: dict) -> str | None:
        return "List files"

    def summarize_input(self, args: dict) -> str:
        return args.get("path") or "."

    def summarize_result(self, args: dict, output: str) -> ToolResult:
        entries = len([l for l in output.splitlines() if l.strip()])
        metadata = {"path": args.get("path") or ".", "entries": entries}
        return ToolResult(output=output, display_output=f"{entries} entries", metadata=metadata)

    def execute(self, args: dict) -> str:
        path = Path(args.get("path") or ".")
        show_hidden = bool(args.get("show_hidden", False))
        recursive = bool(args.get("recursive", False))
        if not path.exists():
            return f"Error: Path not found: {path}"
        if path.is_file():
            return f"Error: Path is a file, not a directory: {path}"
        limit = 500
        try:
            if recursive:
                entries = []
                for p in path.rglob("*"):
                    name = p.relative_to(path).as_posix()
                    if not show_hidden and any(part.startswith(".") for part in name.split("/")):
                        continue
                    entries.append(name + ("/" if p.is_dir() else ""))
                    if len(entries) > limit:
                        break
            else:
                entries = [p.name + ("/" if p.is_dir() else "") for p in sorted(path.iterdir()) if show_hidden or not p.name.startswith(".")][: limit + 1]
            note = ""
            if len(entries) > limit:
                entries = entries[:limit]
                note = f"\n\n(truncated at {limit} entries - use grep or a more specific path)"
            return ("\n".join(entries) if entries else "No entries found") + note
        except Exception as e:
            return f"Error listing directory: {e}"


class RememberTool(Tool):
    streaming = False

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Save a durable note about this project (conventions, commands, gotchas, decisions) "
            "to GEMINI.md in the workspace root. Notes are loaded automatically in future sessions. "
            "Use for facts worth remembering across tasks; never store secrets."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "The fact to remember, one concise sentence"}},
            "required": ["note"],
        }

    def title(self, args: dict) -> str:
        return "Remember"

    def execute(self, args: dict) -> ToolResult:
        note = str(args.get("note") or "").strip()
        if not note:
            return ToolResult(output="Error: 'note' is required", error="'note' is required")
        path = Path("GEMINI.md")
        try:
            existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            marker = "## Agent notes"
            entry = f"- {note}"
            if entry in existing.splitlines():
                return ToolResult(output=f"Note already saved: {note}", display_output="already saved", metadata={"path": str(path)})
            if marker in existing:
                content = existing.replace(marker, f"{marker}\n{entry}", 1)
            else:
                content = (existing + ("\n\n" if existing.strip() else "") + f"{marker}\n{entry}\n")
            lines = content.splitlines()
            while len("\n".join(lines)) > 8000:
                idx = next((i for i, line in enumerate(lines) if line.startswith("- ")), None)
                if idx is None:
                    break
                lines.pop(idx)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return ToolResult(output=f"Saved to {path}: {note}", display_output="saved", metadata={"path": str(path)})
        except Exception as e:
            return ToolResult(output=f"Error saving note: {e}", error=str(e))
