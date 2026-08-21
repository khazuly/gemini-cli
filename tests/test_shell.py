import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from gemini_cli.rendering import ToolRenderer
from gemini_cli.tools import ToolRegistry
from gemini_cli.tools.lifecycle import ToolEvent, ToolState
from gemini_cli.tools.shell import (
    DEFAULT_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
    ShellTool,
    get_shell,
    resolve_workdir,
    sanitize_command_for_display,
)


def test_get_shell_posix():
    with patch("sys.platform", "linux"):
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            shell = get_shell()
            assert shell == ["/bin/sh", "-c"]


def test_get_shell_posix_fallback_when_env_shell_invalid():
    with patch("sys.platform", "linux"):
        with patch.dict(os.environ, {"SHELL": "/nonexistent/custom/shell"}):
            shell = get_shell()
            assert shell[1] == "-c"
            assert shell[0] in ("/bin/sh", "sh") or shell[0].endswith("sh")


def test_get_shell_windows():
    with patch("sys.platform", "win32"):
        with patch.dict(os.environ, {"COMSPEC": "C:\\Windows\\System32\\cmd.exe"}):
            shell = get_shell()
            assert shell == ["C:\\Windows\\System32\\cmd.exe", "/c"]


def test_resolve_workdir(tmp_path):
    # Default workdir
    assert resolve_workdir(None, tmp_path) == tmp_path.resolve()
    assert resolve_workdir("", tmp_path) == tmp_path.resolve()

    # Relative workdir
    sub = tmp_path / "subdir"
    sub.mkdir()
    assert resolve_workdir("subdir", tmp_path) == sub.resolve()

    # Absolute workdir
    assert resolve_workdir(str(sub), tmp_path) == sub.resolve()

    # Nonexistent workdir
    with pytest.raises(FileNotFoundError):
        resolve_workdir("nonexistent_subdir_xyz", tmp_path)

    # File as workdir
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("hello")
    with pytest.raises(NotADirectoryError):
        resolve_workdir("sample.txt", tmp_path)


def test_sanitize_command_for_display():
    assert sanitize_command_for_display("git status") == "git status"
    assert sanitize_command_for_display("curl -H 'Authorization: Bearer mysecrettoken123'") == "curl -H 'Authorization: Bearer ***'"
    assert sanitize_command_for_display("cmd --api-key secret_key_value") == "cmd --api-key ***"
    assert sanitize_command_for_display("cmd --password=secret_password") == "cmd --password=***"

    long_cmd = "python -m " + "a" * 100
    sanitized_long = sanitize_command_for_display(long_cmd)
    assert len(sanitized_long) <= 60
    assert sanitized_long.endswith("...")


def test_shell_basic_commands():
    tool = ShellTool()

    # pwd
    res_pwd = tool.execute({"command": "pwd"})
    data_pwd = json.loads(res_pwd.output)
    assert data_pwd["exit"] == 0
    assert data_pwd["timeout"] is False
    assert data_pwd["truncated"] is False
    assert len(data_pwd["output"].strip()) > 0

    # ls
    res_ls = tool.execute({"command": "ls"})
    data_ls = json.loads(res_ls.output)
    assert data_ls["exit"] == 0
    assert data_ls["timeout"] is False

    # git status
    res_git = tool.execute({"command": "git status"})
    data_git = json.loads(res_git.output)
    assert data_git["exit"] == 0

    # python --version
    res_py = tool.execute({"command": f"{sys.executable} --version"})
    data_py = json.loads(res_py.output)
    assert data_py["exit"] == 0
    assert "Python" in data_py["output"]

    # printf 'hello'
    res_printf = tool.execute({"command": "printf 'hello'"})
    data_printf = json.loads(res_printf.output)
    assert data_printf["exit"] == 0
    assert data_printf["output"] == "hello"


def test_shell_failing_command():
    tool = ShellTool()
    res = tool.execute({"command": "sh -c 'exit 42'"})
    data = json.loads(res.output)
    assert data["exit"] == 42
    assert res.error is not None
    assert "42" in res.error
    assert res.display_output == "exit 42"


def test_shell_stderr_capture():
    tool = ShellTool()
    res = tool.execute({"command": "sh -c 'echo std_err_message >&2; exit 1'"})
    data = json.loads(res.output)
    assert data["exit"] == 1
    assert "std_err_message" in data["output"]


def test_shell_timeout():
    tool = ShellTool()
    # Execute command with 1000ms timeout
    start = time.monotonic()
    res = tool.execute({"command": "sleep 3", "timeout": 1000})
    duration = time.monotonic() - start

    assert duration < 2.5
    data = json.loads(res.output)
    assert data["timeout"] is True
    assert data["exit"] == 124
    assert res.error.startswith("Command timed out after 1000 ms")
    assert "retry with a larger timeout value" in res.error
    assert "timeout" in res.display_output


def test_shell_custom_and_relative_workdir(tmp_path):
    tool = ShellTool()
    sub = tmp_path / "test_sub"
    sub.mkdir()
    marker = sub / "marker.txt"
    marker.write_text("found_marker")

    res = tool.execute({"command": "ls", "workdir": str(sub)})
    data = json.loads(res.output)
    assert data["exit"] == 0
    assert "marker.txt" in data["output"]
    assert data["workdir"] == str(sub.resolve())


def test_shell_invalid_workdir():
    tool = ShellTool()
    res = tool.execute({"command": "ls", "workdir": "/nonexistent_folder_abc_123"})
    data = json.loads(res.output)
    assert data["exit"] == 1
    assert "Working directory does not exist" in data["output"]
    assert res.error is not None


def test_shell_output_truncation():
    tool = ShellTool(max_output_chars=500)
    # Generate 2000 chars of output
    res = tool.execute({"command": f"{sys.executable} -c 'print(\"A\" * 2000)'"})
    data = json.loads(res.output)
    assert data["truncated"] is True
    assert "[output truncated]" in data["output"]
    assert len(data["output"]) < 600
    assert res.truncated is True


def test_shell_lifecycle_and_single_rendering():
    registry = ToolRegistry()
    events: list[ToolEvent] = []

    def sink(event: ToolEvent):
        events.append(event)

    call = registry.execute_call("shell", {"command": "printf 'lifecycle_test'"}, sink=sink, call_id="call_test_01")

    assert call.id == "call_test_01"
    assert call.state == ToolState.COMPLETED
    assert call.error is None
    assert "lifecycle_test" in call.output

    # Verify event types and stable ID
    event_types = [e.type for e in events]
    assert event_types == ["tool_pending", "tool_started", "tool_completed"]
    for e in events:
        assert e.call.id == "call_test_01"

    # Verify renderer handles updates without duplicates
    renderer = ToolRenderer(details=False)
    for e in events:
        renderer.handle(e)

    assert len(renderer.calls) == 1
    assert renderer.calls[0].id == "call_test_01"

    console = Console(record=True, width=120)
    console.print(renderer.render_compact())
    rendered_str = console.export_text()
    assert "Run command" in rendered_str
    assert "lifecycle_test" in rendered_str


def test_multiple_shell_calls_in_one_turn():
    registry = ToolRegistry()
    resp_text = (
        '<tool_call>{"name": "shell", "args": {"command": "echo first"}, "id": "call_1"}</tool_call>\n'
        '<tool_call>{"name": "shell", "args": {"command": "echo second"}, "id": "call_2"}</tool_call>'
    )

    parsed = registry.parse_tool_calls(resp_text)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "call_1"
    assert parsed[1]["id"] == "call_2"

    renderer = ToolRenderer()
    results = []
    for spec in parsed:
        call = registry.execute_call(spec["name"], spec["args"], renderer.handle, call_id=spec["id"])
        results.append(call)

    assert len(results) == 2
    assert results[0].id == "call_1"
    assert results[1].id == "call_2"
    assert "first" in results[0].output
    assert "second" in results[1].output
    assert len(renderer.calls) == 2


def test_shell_call_followed_by_filesystem_tool():
    registry = ToolRegistry()
    resp_text = (
        '<tool_call>{"name": "shell", "args": {"command": "git status"}, "id": "call_shell"}</tool_call>\n'
        '<tool_call>{"name": "list", "args": {"path": "."}, "id": "call_list"}</tool_call>'
    )

    parsed = registry.parse_tool_calls(resp_text)
    assert len(parsed) == 2

    c_shell = registry.execute_call(parsed[0]["name"], parsed[0]["args"], call_id=parsed[0]["id"])
    c_list = registry.execute_call(parsed[1]["name"], parsed[1]["args"], call_id=parsed[1]["id"])

    assert c_shell.state == ToolState.COMPLETED
    assert c_list.state == ToolState.COMPLETED
    assert "pyproject.toml" in (c_list.output or "")


def test_system_prompt_includes_shell():
    registry = ToolRegistry()
    prompt = registry.build_system_prompt()
    assert "- shell: Execute a shell command" in prompt
    assert '"command"' in prompt
    assert "run git status" in prompt
    assert "pytest" in prompt
