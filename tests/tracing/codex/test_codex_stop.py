"""Stop transport regressions use synthetic payloads and mocked export only."""

import io
import json
from unittest.mock import patch

import pytest

from tracing.codex.hooks import stop


def test_large_stdin_payload_reaches_handler_without_argv(monkeypatch, capsys):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "synthetic",
        "turn_id": "turn",
        "last_assistant_message": "x" * 200000,
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with (
        patch.object(stop, "load_env_file"),
        patch.object(stop, "check_requirements", return_value=True),
        patch.object(stop, "_handle_stop") as handle,
    ):
        stop.main()
    assert handle.call_args.args[0] == payload
    assert capsys.readouterr().out == "{}\n"


def test_stop_before_task_complete_keeps_tools_and_hook_final_message(tmp_path):
    path = tmp_path / "rollout.jsonl"
    rows = [
        {
            "timestamp": "2026-05-20T00:00:00Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn"},
        },
        {
            "timestamp": "2026-05-20T00:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "mcp_tool_call_end",
                "call_id": "call",
                "invocation": {"server": "synthetic", "tool": "save", "arguments": {}},
                "duration": {"secs": 0, "nanos": 1000000},
                "result": {"Ok": {}},
            },
        },
    ]
    path.write_text("\n".join(map(json.dumps, rows)))
    with (
        patch.object(stop, "_find_rollout_file", return_value=path),
        patch.object(stop, "get_timestamp_ms", return_value=1779235203000),
        patch.object(stop, "_build_and_send_spans") as send,
    ):
        stop._handle_stop(
            {"hook_event_name": "Stop", "session_id": "synthetic", "turn_id": "turn", "last_assistant_message": "Saved"}
        )
    turn = send.call_args.args[2]
    assert turn["assistant_output"] == "Saved"
    assert turn["duration_ms"] == 3000
    assert turn["tool_calls"][0]["tool"] == "synthetic.save"


@pytest.mark.parametrize("payload", [{}, {"hook_event_name": "Other"}, {"hook_event_name": "Stop", "session_id": "s"}])
def test_other_or_incomplete_events_are_ignored(payload):
    with patch.object(stop, "_find_rollout_file") as find:
        stop._handle_stop(payload)
    find.assert_not_called()


def test_disabled_hook_does_not_export(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("private"))
    with (
        patch.object(stop, "load_env_file"),
        patch.object(stop, "check_requirements", return_value=False),
        patch.object(stop, "_handle_stop") as handle,
    ):
        stop.main()
    handle.assert_not_called()
    assert capsys.readouterr().out == "{}\n"


def test_failure_never_blocks_turn_or_leaks_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("private invalid JSON"))
    with (
        patch.object(stop, "load_env_file"),
        patch.object(stop, "check_requirements", return_value=True),
        patch.object(stop, "error") as error,
    ):
        stop.main()
    assert "private" not in str(error.call_args)
    assert capsys.readouterr().out == "{}\n"


def test_missing_rollout_uses_existing_fallback():
    with (
        patch.object(stop, "_find_rollout_file", return_value=None),
        patch.object(stop, "_send_legacy_single_span") as send,
    ):
        stop._handle_stop(
            {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t", "last_assistant_message": "done"}
        )
    send.assert_called_once_with("s", "t", {"last-assistant-message": "done"})
