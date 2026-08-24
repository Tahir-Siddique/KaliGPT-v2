"""Live Cursor run progress (token usage + tool logs)."""

from types import SimpleNamespace

from agents.cursor_cli import TurnLive
from agents.cursor_progress import (
    iter_progress,
    progress_from_stream_event,
    progress_from_update,
    usage_as_dict,
)
from agents.cursor_worker import drive_run


def test_usage_as_dict_from_object_and_mapping():
    usage = SimpleNamespace(
        input_tokens=12,
        output_tokens=4,
        total_tokens=16,
        cache_read_tokens=0,
        reasoning_tokens=None,
    )
    assert usage_as_dict(usage)["input_tokens"] == 12
    assert usage_as_dict({"inputTokens": 3, "outputTokens": 2, "totalTokens": 5})[
        "output_tokens"
    ] == 2


def test_tool_and_token_progress_from_interaction_updates():
    started = SimpleNamespace(
        type="tool-call-started",
        call_id="c1",
        tool_call={"name": "Shell", "args": {"command": "whoami"}},
        model_call_id="m1",
    )
    events = list(progress_from_update(started))
    assert events[0]["kind"] == "tool"
    assert events[0]["name"] == "Shell"
    assert "whoami" in events[0]["detail"]

    tokens = list(progress_from_update(SimpleNamespace(type="token-delta", tokens=9)))
    assert tokens[0] == {"type": "progress", "kind": "token", "delta": 9}

    usage = list(
        progress_from_update(
            SimpleNamespace(
                type="turn-ended",
                usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            )
        )
    )
    assert usage[0]["kind"] == "usage"
    assert usage[0]["total_tokens"] == 120


def test_stream_event_uses_sdk_message_and_update():
    event = SimpleNamespace(
        sdk_message=SimpleNamespace(
            type="thinking",
            text="planning the next step",
        ),
        interaction_update=SimpleNamespace(type="text-delta", text="Hello"),
    )
    kinds = [item["kind"] for item in progress_from_stream_event(event)]
    assert kinds == ["thinking", "assistant"]


def test_iter_progress_reads_events_not_wait():
    class Run:
        def events(self):
            yield SimpleNamespace(
                sdk_message=None,
                interaction_update=SimpleNamespace(
                    type="tool-call-started",
                    call_id="t1",
                    tool_call={"name": "Grep", "args": {"pattern": "hatsoff"}},
                    model_call_id="m",
                ),
            )

        def wait(self):
            raise AssertionError("wait must not be used to collect live logs")

    items = list(iter_progress(Run()))
    assert items[0]["kind"] == "tool"
    assert items[0]["name"] == "Grep"


def test_drive_run_emits_then_waits():
    seen = []

    class Run:
        usage = SimpleNamespace(
            input_tokens=8,
            output_tokens=2,
            total_tokens=10,
            cache_read_tokens=0,
            reasoning_tokens=None,
        )

        def events(self):
            yield SimpleNamespace(
                sdk_message=None,
                interaction_update=SimpleNamespace(type="token-delta", tokens=3),
            )

        def wait(self):
            return SimpleNamespace(status="finished", result="done", id="run-1")

    text, status, usage = drive_run(Run(), emit=seen.append)
    assert text == "done"
    assert status == "finished"
    assert seen[0]["kind"] == "token"
    assert usage["total_tokens"] == 10


def test_turn_live_shows_usage_and_tool_log():
    live = TurnLive()
    live.handle({"type": "progress", "kind": "token", "delta": 11})
    live.handle(
        {
            "type": "progress",
            "kind": "tool",
            "name": "Shell",
            "detail": "id",
            "status": "running",
            "call_id": "1",
        }
    )
    live.handle(
        {
            "type": "progress",
            "kind": "usage",
            "input_tokens": 40,
            "output_tokens": 12,
            "total_tokens": 52,
        }
    )
    live.handle(
        {"type": "progress", "kind": "assistant", "text": "Ready.", "delta": True}
    )
    assert live.input_tokens == 40
    assert live.output_tokens == 12
    assert live.total_tokens == 52
    assert live.live_tokens >= 52
    assert any("Shell" in line for line in live.logs)
    assert live.saw_assistant is True
    assert live.assistant == "Ready."


def test_progress_json_escapes_arrows_for_windows_pipes():
    import json

    event = {
        "type": "progress",
        "kind": "assistant",
        "text": "recon → exploit",
        "delta": True,
    }
    line = json.dumps(event, ensure_ascii=True)
    line.encode("cp1252")
    assert "\\u2192" in line
    assert json.loads(line)["text"] == "recon → exploit"
