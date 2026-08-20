"""Shell tool for executing commands in the workspace."""

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


# Configuration constants
DEFAULT_TIMEOUT_MS = 60000   # 60 seconds
MAX_TIMEOUT_MS = 300000      # 5 minutes (300 seconds)
MIN_TIMEOUT_MS = 1000        # 1 second
DEFAULT_MAX_OUTPUT_CHARS = 50000  # ~50KB captured output limit


def get_shell() -> list[str]:
    """Get the appropriate shell command for the current platform."""
    if sys.platform == "win32":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/c"]

    # POSIX: check $SHELL first
    shell_env = os.environ.get("SHELL")
    if shell_env and os.path.exists(shell_env) and os.access(shell_env, os.X_OK):
        return [shell_env, "-c"]

    # Fallback to /bin/sh if it exists and is executable
    if os.path.exists("/bin/sh") and os.access("/bin/sh", os.X_OK):
        return ["/bin/sh", "-c"]

    # Termux / custom POSIX environments where /bin/sh might not exist or sh is in PATH
    sh_path = shutil.which("sh")
    if sh_path:
        return [sh_path, "-c"]

    # Final fallback
    return ["sh", "-c"]


def resolve_workdir(workdir: str | None, workspace: Path | None = None) -> Path:
    """Resolve and validate working directory relative to workspace."""
    if workspace is None:
        workspace = Path.cwd()
    workspace = workspace.resolve()

    if not workdir:
        return workspace

    path = Path(workdir)
    if not path.is_absolute():
        resolved = (workspace / path).resolve()
    else:
        resolved = path.resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"Working directory does not exist: {workdir}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"Working directory is not a directory: {workdir}")

    return resolved


def sanitize_command_for_display(cmd: str) -> str:
    """Mask sensitive tokens and arguments in command string for UI display only."""
    if not cmd:
        return ""
    # Single-line view for status line
    cmd_single = cmd.strip().splitlines()[0] if cmd.strip().splitlines() else ""
    # Mask common sensitive key/value patterns
    patterns = [
        (r'(?i)(api[_-]?key|token|secret|password|passwd|auth|bearer)\s*[:=]\s*([^\s&|;]+)', r'\1=***'),
        (r'(?i)(--?(?:password|token|api[_-]?key|secret)\s+)([^\s&|;]+)', r'\1***'),
        (r'(?i)(Bearer\s+)([A-Za-z0-9_\-\.~+/]+=*)', r'\1***'),
    ]
    sanitized = cmd_single
    for pattern, repl in patterns:
        sanitized = re.sub(pattern, repl, sanitized)

    # Truncate length for compact UI representation
    if len(sanitized) > 60:
        sanitized = sanitized[:57] + "..."
    return sanitized


class ShellTool(Tool):
    """Execute shell commands in the workspace."""

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
            "for listing, reading, searching, and editing files when those tools are available."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "workdir": {
                    "type": ["string", "null"],
                    "description": "Working directory (default: current workspace). Relative paths resolve from workspace root.",
                },
                "timeout": {
                    "type": ["integer", "null"],
                    "description": "Timeout in milliseconds (default: 60000, max: 300000)",
                    "minimum": 1000,
                    "maximum": MAX_TIMEOUT_MS,
                },
            },
            "required": ["command"],
        }

    def title(self, args: dict) -> str | None:
        return "Run command"

    def summarize_input(self, args: dict) -> str:
        cmd = sanitize_command_for_display(str(args.get("command", "")))
        workdir = args.get("workdir")
        if workdir:
            return f"$ {cmd}\n  cwd: {workdir}"
        return f"$ {cmd}"

    def summarize_result(self, args: dict, output: str) -> ToolResult:
        cmd = args.get("command", "")
        cmd_display = sanitize_command_for_display(cmd)
        workdir = args.get("workdir")

        exit_code = 0
        truncated = False
        timeout = False
        raw_output = output

        try:
            data = json.loads(output)
            if isinstance(data, dict):
                exit_code = data.get("exit", 0)
                truncated = bool(data.get("truncated", False))
                timeout = bool(data.get("timeout", False))
                raw_output = data.get("output", output)
        except (json.JSONDecodeError, TypeError):
            pass

        metadata = {
            "command": cmd,
            "exit_code": exit_code,
            "truncated": truncated,
            "timeout": timeout,
        }
        if workdir:
            metadata["workdir"] = workdir

        if exit_code == 0:
            display = cmd_display or "completed"
            error = None
        else:
            if timeout:
                display = f"timeout · exit {exit_code}" if exit_code is not None else "timeout"
                error = f"Command timed out (exit {exit_code})" if exit_code is not None else "Command timed out"
            else:
                display = f"exit {exit_code}" if exit_code is not None else "failed"
                error = f"Process exited with code {exit_code}" if exit_code is not None else "Process failed"

        return ToolResult(
            output=output,
            display_output=display,
            metadata=metadata,
            error=error,
            truncated=truncated,
        )

    def _terminate_process(self, process: subprocess.Popen) -> None:
        """Safely terminate a process and its process group to avoid orphans."""
        pid = process.pid
        if sys.platform != "win32":
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    return
                time.sleep(0.02)

            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        else:
            try:
                process.terminate()
            except OSError:
                pass
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    return
                time.sleep(0.02)
            try:
                process.kill()
            except OSError:
                pass

    def _build_result(
        self,
        output: str,
        exit_code: int | None,
        truncated: bool,
        timeout: bool,
        error: str | None,
        cmd_display: str,
        metadata: dict[str, Any],
    ) -> ToolResult:
        structured_data: dict[str, Any] = {
            "output": output,
            "exit": exit_code,
            "truncated": truncated,
            "timeout": timeout,
        }
        if "workdir" in metadata and metadata["workdir"]:
            structured_data["workdir"] = metadata["workdir"]

        json_output = json.dumps(structured_data, indent=2)

        if exit_code == 0:
            display_output = cmd_display or "completed"
        elif timeout:
            display_output = f"timeout · exit {exit_code}" if exit_code is not None else "timeout"
        elif exit_code is not None:
            display_output = f"exit {exit_code}"
        else:
            display_output = "failed"

        return ToolResult(
            output=json_output,
            display_output=display_output,
            metadata=metadata,
            error=error,
            truncated=truncated,
        )

    def execute(self, args: dict) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return self._build_result(
                output="Error: 'command' argument is required and must be a non-empty string",
                exit_code=1,
                truncated=False,
                timeout=False,
                error="Invalid command argument",
                cmd_display="",
                metadata={"command": str(command)},
            )

        workdir = args.get("workdir")
        timeout_val = args.get("timeout")

        # Validate timeout
        if timeout_val is None:
            timeout_ms = DEFAULT_TIMEOUT_MS
        else:
            try:
                timeout_ms = int(timeout_val)
            except (ValueError, TypeError):
                timeout_ms = DEFAULT_TIMEOUT_MS

        if timeout_ms > MAX_TIMEOUT_MS:
            timeout_ms = MAX_TIMEOUT_MS
        if timeout_ms < MIN_TIMEOUT_MS:
            timeout_ms = MIN_TIMEOUT_MS

        timeout_sec = timeout_ms / 1000.0

        # Resolve working directory
        try:
            resolved_workdir = resolve_workdir(workdir)
        except Exception as e:
            return self._build_result(
                output=f"Error: {e}",
                exit_code=1,
                truncated=False,
                timeout=False,
                error=str(e),
                cmd_display=sanitize_command_for_display(command),
                metadata={"command": command, "workdir": str(workdir) if workdir else None},
            )

        # Get platform shell
        try:
            shell_cmd = get_shell()
        except Exception as e:
            return self._build_result(
                output=f"Error determining shell: {e}",
                exit_code=127,
                truncated=False,
                timeout=False,
                error=f"Shell unavailable: {e}",
                cmd_display=sanitize_command_for_display(command),
                metadata={"command": command},
            )

        # Inherit existing environment
        env = os.environ.copy()
        env["PWD"] = str(resolved_workdir)

        cmd_display = sanitize_command_for_display(command)
        metadata: dict[str, Any] = {
            "command": command,
            "workdir": str(resolved_workdir),
        }

        process = None
        try:
            popen_kwargs: dict[str, Any] = {
                "cwd": resolved_workdir,
                "env": env,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "errors": "replace",
            }
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True

            process = subprocess.Popen(
                shell_cmd + [command],
                **popen_kwargs,
            )

            stdout, stderr = process.communicate(timeout=timeout_sec)
            exit_code = process.returncode

            # Combine stdout and stderr
            combined = stdout or ""
            if stderr:
                if combined:
                    if not combined.endswith("\n"):
                        combined += "\n"
                    combined += stderr
                else:
                    combined = stderr

            truncated = False
            if len(combined) > self.max_output_chars:
                combined = combined[: self.max_output_chars] + "\n[output truncated]"
                truncated = True

            metadata.update({
                "exit_code": exit_code,
                "truncated": truncated,
                "timeout": False,
            })

            error = None
            if exit_code != 0:
                error = f"Process exited with code {exit_code}"

            return self._build_result(
                output=combined,
                exit_code=exit_code,
                truncated=truncated,
                timeout=False,
                error=error,
                cmd_display=cmd_display,
                metadata=metadata,
            )

        except subprocess.TimeoutExpired:
            if process is not None:
                self._terminate_process(process)

            captured_out = ""
            captured_err = ""
            if process is not None:
                try:
                    captured_out, captured_err = process.communicate(timeout=0.5)
                except Exception:
                    pass

            combined = captured_out or ""
            if captured_err:
                if combined:
                    if not combined.endswith("\n"):
                        combined += "\n"
                    combined += captured_err
                else:
                    combined = captured_err

            timeout_msg = f"Command timed out after {timeout_ms}ms"
            if combined:
                combined = f"{combined}\n[{timeout_msg}]"
            else:
                combined = f"[{timeout_msg}]"

            truncated = False
            if len(combined) > self.max_output_chars:
                combined = combined[: self.max_output_chars] + "\n[output truncated]"
                truncated = True

            metadata.update({
                "exit_code": 124,
                "truncated": truncated,
                "timeout": True,
            })

            return self._build_result(
                output=combined,
                exit_code=124,
                truncated=truncated,
                timeout=True,
                error="Command timed out",
                cmd_display=cmd_display,
                metadata=metadata,
            )

        except FileNotFoundError as e:
            return self._build_result(
                output=f"Shell executable not found: {e}",
                exit_code=127,
                truncated=False,
                timeout=False,
                error=f"Shell executable not found: {e}",
                cmd_display=cmd_display,
                metadata=metadata,
            )
        except PermissionError as e:
            return self._build_result(
                output=f"Permission denied: {e}",
                exit_code=126,
                truncated=False,
                timeout=False,
                error=f"Permission denied: {e}",
                cmd_display=cmd_display,
                metadata=metadata,
            )
        except Exception as e:
            return self._build_result(
                output=f"Execution error: {e}",
                exit_code=1,
                truncated=False,
                timeout=False,
                error=str(e),
                cmd_display=cmd_display,
                metadata=metadata,
            )