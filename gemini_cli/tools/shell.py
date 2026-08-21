from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .base import Tool
from .lifecycle import ToolResult

DEFAULT_TIMEOUT_MS = 120000
MAX_TIMEOUT_MS = 600000
MIN_TIMEOUT_MS = 1000
DEFAULT_MAX_OUTPUT_CHARS = 50000


def _output_tail(text) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return " · ".join(lines[-2:])[:140]


def get_shell() -> list[str]:
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c"]
    shell_env = os.environ.get("SHELL")
    if shell_env and os.path.exists(shell_env) and os.access(shell_env, os.X_OK):
        return [shell_env, "-c"]
    if os.path.exists("/bin/sh") and os.access("/bin/sh", os.X_OK):
        return ["/bin/sh", "-c"]
    sh_path = shutil.which("sh")
    return [sh_path, "-c"] if sh_path else ["sh", "-c"]


def resolve_workdir(workdir: str | None, workspace: Path | None = None) -> Path:
    workspace = (workspace or Path.cwd()).resolve()
    if not workdir:
        return workspace
    path = Path(workdir)
    resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Working directory does not exist: {workdir}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"Working directory is not a directory: {workdir}")
    return resolved


def sanitize_command_for_display(cmd: str) -> str:
    if not cmd:
        return ""
    sanitized = cmd.strip().splitlines()[0] if cmd.strip().splitlines() else ""
    patterns = [
        (r"(?i)(api[_-]?key|token|secret|password|passwd|auth|bearer)\s*[:=]\s*([^\s&|;]+)", r"\1=***"),
        (r"(?i)(--?(?:password|token|api[_-]?key|secret)\s+)([^\s&|;]+)", r"\1***"),
        (r"(?i)(Bearer\s+)([A-Za-z0-9_\-\.~+/]+=*)", r"\1***"),
    ]
    for pattern, repl in patterns:
        sanitized = re.sub(pattern, repl, sanitized)
    return sanitized[:57] + "..." if len(sanitized) > 60 else sanitized


def _kill(process: subprocess.Popen) -> None:
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
    else:
        try:
            process.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.02)
    if process.poll() is not None:
        return
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, OSError):
        pass


class ShellTool(Tool):
    streaming = True
    def __init__(self, max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS):
        self.max_output_chars = max_output_chars

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command in the active workspace. Use this for terminal operations "
            "such as running tests, checking git state, executing scripts, installing dependencies "
            "when appropriate, or inspecting system state. Prefer specialized filesystem tools "
            "for listing, reading, searching, and editing files when those tools are available. "
            "If a long-running command (builds, installs, test suites) times out and is not waiting "
            "for interactive input, retry it with a larger 'timeout' value in milliseconds instead of giving up."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "workdir": {"type": ["string", "null"], "description": "Working directory (default: current workspace)"},
                "timeout": {"type": ["integer", "null"], "description": f"Timeout in milliseconds (default: {DEFAULT_TIMEOUT_MS}, max: {MAX_TIMEOUT_MS}). Increase this when a long-running command times out.", "minimum": MIN_TIMEOUT_MS, "maximum": MAX_TIMEOUT_MS},
            },
            "required": ["command"],
        }

    def title(self, args: dict) -> str | None:
        return "Run command"

    def summarize_input(self, args: dict) -> str:
        cmd = sanitize_command_for_display(str(args.get("command", "")))
        workdir = args.get("workdir")
        return f"$ {cmd}\n  cwd: {workdir}" if workdir else f"$ {cmd}"

    def summarize_result(self, args: dict, output: str) -> ToolResult:
        data = {}
        try:
            loaded = json.loads(output)
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, TypeError):
            pass
        exit_code = data.get("exit", 0)
        truncated = bool(data.get("truncated", False))
        timeout = bool(data.get("timeout", False))
        metadata = {"command": args.get("command", ""), "exit_code": exit_code, "truncated": truncated, "timeout": timeout}
        if args.get("workdir"):
            metadata["workdir"] = args["workdir"]
        if exit_code == 0:
            display, error = sanitize_command_for_display(str(args.get("command", ""))) or "completed", None
        elif timeout:
            display, error = f"timeout · exit {exit_code}", "Command timed out"
        else:
            tail = _output_tail(data.get("output"))
            display = f"exit {exit_code}" + (f" · {tail}" if tail else "")
            error = f"Process exited with code {exit_code}" + (f": {tail}" if tail else "")
        return ToolResult(output=output, display_output=display, metadata=metadata, error=error, truncated=truncated)

    def _result(self, output: str, exit_code: int | None, truncated: bool, timeout: bool, error: str | None, metadata: dict[str, Any]) -> ToolResult:
        structured: dict[str, Any] = {"output": output, "exit": exit_code, "truncated": truncated, "timeout": timeout}
        if metadata.get("workdir"):
            structured["workdir"] = metadata["workdir"]
        if exit_code == 0:
            display = metadata.get("display") or "completed"
        elif timeout:
            display = f"timeout · exit {exit_code}"
        elif exit_code is not None:
            tail = _output_tail(output)
            display = f"exit {exit_code}" + (f" · {tail}" if tail else "")
            error = error + (f": {tail}" if tail and error else "")
        else:
            display = "failed"
        return ToolResult(output=json.dumps(structured, indent=2), display_output=display, metadata=metadata, error=error, truncated=truncated)

    def execute(self, args: dict, progress=None) -> ToolResult:
        command = args.get("command")
        metadata: dict[str, Any] = {"command": str(command)}
        if not isinstance(command, str) or not command.strip():
            metadata["display"] = ""
            return self._result("Error: 'command' argument is required and must be a non-empty string", 1, False, False, "Invalid command argument", metadata)
        metadata["display"] = sanitize_command_for_display(command)
        try:
            workdir = resolve_workdir(args.get("workdir"))
        except Exception as e:
            return self._result(f"Error: {e}", 1, False, False, str(e), metadata)
        metadata["workdir"] = str(workdir)
        timeout_val = args.get("timeout")
        try:
            timeout_ms = int(timeout_val) if timeout_val is not None else DEFAULT_TIMEOUT_MS
        except (ValueError, TypeError):
            timeout_ms = DEFAULT_TIMEOUT_MS
        timeout_ms = max(MIN_TIMEOUT_MS, min(MAX_TIMEOUT_MS, timeout_ms))
        env = os.environ.copy()
        env["PWD"] = str(workdir)
        popen_kwargs: dict[str, Any] = {
            "cwd": workdir,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "errors": "replace",
            "bufsize": 1,
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(get_shell() + [command], **popen_kwargs)
        except Exception as e:
            return self._result(f"Execution error: {e}", 1, False, False, str(e), metadata)
        chunks, timed_out = self._pump(process, timeout_ms / 1000.0, progress)
        combined = "".join(chunks)
        exit_code = process.poll()
        if timed_out:
            msg = (
                f"Command timed out after {timeout_ms} ms. If this command is expected to take longer "
                f'and is not waiting for interactive input, re-emit the same tool call with a larger "timeout" '
                f'(e.g. {{"name": "shell", "args": {{"command": "<same command>", "timeout": 600000}}}}), max 600000.'
            )
            truncated = len(combined) > self.max_output_chars
            if truncated:
                combined = self._truncate_tail(combined)
            combined = f"{combined}\n[{msg}]" if combined else f"[{msg}]"
            metadata.update({"exit_code": 124, "truncated": truncated, "timeout": True})
            return self._result(combined, 124, truncated, True, msg, metadata)
        truncated = False
        if len(combined) > self.max_output_chars:
            combined = self._truncate_tail(combined)
            truncated = True
        metadata.update({"exit_code": exit_code, "truncated": truncated, "timeout": False})
        return self._result(combined, exit_code, truncated, False, None if exit_code == 0 else f"Process exited with code {exit_code}", metadata)

    def _truncate_tail(self, text: str) -> str:
        return "[output truncated - showing last part]\n...\n" + text[-self.max_output_chars :]

    def _pump(self, process: subprocess.Popen, timeout_s: float, progress) -> tuple[list[str], bool]:
        import queue
        import threading

        lines: queue.Queue = queue.Queue()

        def pump(pipe) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    lines.put(line)
            except Exception:
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        readers = [threading.Thread(target=pump, args=(p,), daemon=True) for p in (process.stdout, process.stderr)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout_s
        chunks: list[str] = []
        last_emit = 0.0
        timed_out = False
        while True:
            try:
                chunks.append(lines.get(timeout=0.1))
                now = time.monotonic()
                if progress and now - last_emit >= 0.3:
                    last_emit = now
                    progress(_output_tail("".join(chunks)))
            except queue.Empty:
                now = time.monotonic()
                if process.poll() is not None and lines.empty():
                    break
                if now >= deadline:
                    timed_out = True
                    _kill(process)
                    break
                if progress and now - last_emit >= 0.5:
                    last_emit = now
                    progress(_output_tail("".join(chunks)))
        for reader in readers:
            reader.join(timeout=0.5)
        while True:
            try:
                chunks.append(lines.get_nowait())
            except queue.Empty:
                break
        if progress:
            progress(_output_tail("".join(chunks)))
        return chunks, timed_out

    @staticmethod
    def _combine(stdout: str | None, stderr: str | None) -> str:
        combined = stdout or ""
        if stderr:
            combined = f"{combined}\n{stderr}" if combined else stderr
        return combined
