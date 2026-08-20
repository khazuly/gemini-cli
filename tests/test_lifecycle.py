import time
from gemini_cli.tools.lifecycle import ToolCall, ToolState


def test_tool_duration_and_state():
    call = ToolCall(id="t1", name="t", input={})
    assert call.duration is None
    call.started_at = time.monotonic()
    time.sleep(0.01)
    call.finished_at = time.monotonic()
    assert call.duration >= 0.01
    call.state = ToolState.RUNNING
    assert call.state == ToolState.RUNNING
    call.state = ToolState.COMPLETED
    assert call.state == ToolState.COMPLETED
