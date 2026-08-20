from gemini_cli.rendering import ToolRenderer
from gemini_cli.tools.lifecycle import ToolCall, ToolState


def make_call(id, name, title=None, state=ToolState.PENDING, duration=None, input_summary=None, output=None):
    call = ToolCall(id=id, name=name, input={})
    call.title = title
    call.state = state
    if duration is not None:
        import time
        call.started_at = time.monotonic()
        call.finished_at = call.started_at + duration
    if input_summary:
        call.metadata["input_summary"] = input_summary
    call.display_output = output
    return call


def test_renderer_compact_and_static():
    r = ToolRenderer(details=True)
    c1 = make_call("c1", "read", title="Read file", state=ToolState.COMPLETED, duration=0.02, input_summary="file.py", output="20 lines")
    c2 = make_call("c2", "grep", title="Search logs", state=ToolState.RUNNING, duration=0.01, input_summary='pattern: "error"')
    r.calls = [c1, c2]
    # Ensure render methods don't raise
    compact = r.render_compact()
    static = r.render_static()
    assert compact is not None
    assert static is not None
