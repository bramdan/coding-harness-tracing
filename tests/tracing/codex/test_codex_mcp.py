"""Synthetic MCP rollout cases: no application data or tool execution."""

import json
from unittest.mock import patch

import pytest

from tracing.codex.hooks.handlers import _build_and_send_spans, _extract_turn_from_rollout


def record(kind, payload, second=0):
    return {"timestamp": f"2026-05-20T00:00:{second:02d}.000Z", "type": kind, "payload": payload}


def custom(call_id="exec-1", second=1, output=False):
    payload = {"type": "custom_tool_call_output" if output else "custom_tool_call", "call_id": call_id}
    payload.update({"output": "wrapper result"} if output else {"name": "exec", "input": "opaque source, never parsed"})
    return record("response_item", payload, second)


def mcp(call_id="mcp-1", tool="document.get", second=3, result=None, duration=None):
    return record(
        "event_msg",
        {
            "type": "mcp_tool_call_end",
            "call_id": call_id,
            "invocation": {"server": "example-bridge", "tool": tool, "arguments": {"id": "synthetic-document"}},
            "duration": duration if duration is not None else {"secs": 1, "nanos": 23456789},
            "result": (
                result if result is not None else {"Ok": {"content": [{"type": "text", "text": "fixture result"}]}}
            ),
        },
        second,
    )


def extract(tmp_path, *records):
    path = tmp_path / "synthetic.jsonl"
    rows = [record("event_msg", {"type": "task_started", "turn_id": "test-turn"}), *records]
    rows.append(record("event_msg", {"type": "task_complete", "turn_id": "test-turn"}, 10))
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return _extract_turn_from_rollout(path, "test-turn")


def render(turn):
    with patch("tracing.codex.hooks.handlers.send_span_to_backend", return_value=True) as send:
        with patch("tracing.codex.hooks.handlers.debug_dump"):
            _build_and_send_spans("synthetic-session", "test-turn", turn)
    return send.call_args.args[0]["resourceSpans"][0]["scopeSpans"][0]["spans"]


def attrs(span):
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


@pytest.fixture(autouse=True)
def logging(monkeypatch):
    monkeypatch.setenv("ARIZE_LOG_TOOL_DETAILS", "true")
    monkeypatch.setenv("ARIZE_LOG_TOOL_CONTENT", "true")


def test_parallel_mcp_calls_keep_names_identity_results_and_exact_duration(tmp_path):
    # Overlapping MCP intervals, completed out of start order, in one exec.
    first = mcp("a", "document.get", second=3)
    second = mcp("b", "document.update", second=4, duration={"secs": 2, "nanos": 500000000})
    turn = extract(tmp_path, custom(), first, second, custom(second=5, output=True))
    assert [t["tool"] for t in turn["tool_calls"]] == [
        "exec",
        "example-bridge.document.get",
        "example-bridge.document.update",
    ]
    wrapper, a, b = turn["tool_calls"]
    assert wrapper["span_kind"] == "CHAIN"
    assert a["call_id"] == "a" and b["call_id"] == "b"
    assert json.loads(a["args"]) == first["payload"]["invocation"]["arguments"]
    assert json.loads(a["output"]) == first["payload"]["result"]
    assert b["start_ns"] < a["start_ns"] < a["end_ns"] < b["end_ns"]
    spans = render(turn)
    assert len(spans) == 4
    assert attrs(spans[1])["openinference.span.kind"] == "CHAIN"
    assert "tool.name" not in attrs(spans[1])
    for span, entry in zip(spans[2:], (a, b)):
        assert span["parentSpanId"] == spans[0]["spanId"]  # No guessed exec parent.
        assert attrs(span)["openinference.span.kind"] == "TOOL"
        assert attrs(span)["mcp.server.name"] == "example-bridge"
        assert attrs(span)["mcp.tool.name"] == entry["mcp_tool"]
        assert attrs(span)["codex.tool.call_id"] == entry["call_id"]
        assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == entry["duration_ns"]
        assert span["status"]["code"] == 1


@pytest.mark.parametrize("direct_first", [True, False])
def test_duplicate_completion_and_function_representation_emit_one_mcp(tmp_path, direct_first):
    direct = [
        record(
            "response_item",
            {"type": "function_call", "call_id": "mcp-1", "name": "mcp__example__get", "arguments": "{}"},
            1,
        ),
        record("response_item", {"type": "function_call_output", "call_id": "mcp-1", "output": "duplicate"}, 4),
    ]
    events = [*direct, mcp(), mcp()] if direct_first else [mcp(), mcp(), *direct]
    turn = extract(tmp_path, *events)
    assert len(turn["tool_calls"]) == 1
    spans = render(turn)
    assert len(spans) == 2
    assert spans[1]["name"] == "example-bridge.document.get"


def test_duplicate_custom_representation_and_distinct_repeated_operations(tmp_path):
    turn = extract(tmp_path, custom("mcp-1"), mcp(), custom("mcp-1", second=4, output=True), mcp("other", second=5))
    assert [t["call_id"] for t in turn["tool_calls"]] == ["mcp-1", "other"]


@pytest.mark.parametrize(
    "result,status",
    [
        ({"Err": "sensitive transport error"}, 2),
        ({"Err": ""}, 2),
        ({"Ok": {"isError": True, "content": [{"text": "sensitive application error"}]}}, 2),
        ({"Ok": {"isError": False}}, 1),
        ({"unexpected": "shape"}, 0),
    ],
)
def test_result_status_and_redaction(tmp_path, monkeypatch, result, status):
    monkeypatch.setenv("ARIZE_LOG_TOOL_DETAILS", "false")
    monkeypatch.setenv("ARIZE_LOG_TOOL_CONTENT", "false")
    span = render(extract(tmp_path, mcp(result=result)))[1]
    assert span["status"]["code"] == status
    assert attrs(span)["input.value"].startswith("<redacted")
    assert attrs(span)["output.value"].startswith("<redacted")
    assert "sensitive" not in json.dumps(span)
    assert "synthetic-document" not in json.dumps(span)


def test_success_output_logging_can_be_disabled_independently(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIZE_LOG_TOOL_CONTENT", "false")
    span = render(extract(tmp_path, mcp()))[1]
    assert json.loads(attrs(span)["input.value"]) == {"id": "synthetic-document"}
    assert "fixture result" not in json.dumps(span)


@pytest.mark.parametrize(
    "duration",
    [
        {},
        {"secs": -1, "nanos": 0},
        {"secs": 0, "nanos": 1000000000},
        {"secs": "1", "nanos": 0},
        {"secs": True, "nanos": 0},
    ],
)
def test_invalid_duration_does_not_invent_a_start_or_group_wrapper(tmp_path, duration):
    turn = extract(tmp_path, custom(), mcp(duration=duration), custom(second=5, output=True))
    wrapper, call = turn["tool_calls"]
    assert "span_kind" not in wrapper
    assert call["start_ts"] == call["end_ts"]
    assert call["duration_ns"] is None


def test_missing_timestamp_preserves_operation_without_inventing_interval(tmp_path):
    event = mcp()
    event.pop("timestamp")
    turn = extract(tmp_path, event)
    assert turn["tool_calls"][0]["start_ns"] is None
    span = render(turn)[1]
    assert span["startTimeUnixNano"] == span["endTimeUnixNano"]


def test_wrapper_only_exec_is_still_a_tool_without_source_inference(tmp_path):
    call = custom()
    call["payload"]["input"] = 'await tools.mcp__example__delete({id: "not executed"})'
    turn = extract(tmp_path, call, custom(second=4, output=True))
    spans = render(turn)
    assert len(spans) == 2
    assert attrs(spans[1])["openinference.span.kind"] == "TOOL"
    assert spans[1]["name"] == "exec"


def test_overlapping_execs_do_not_get_guessed_grouping(tmp_path):
    turn = extract(tmp_path, custom("one"), custom("two"), mcp(), custom("two", 4, True), custom("one", 5, True))
    spans = render(turn)
    assert all(attrs(span)["openinference.span.kind"] == "TOOL" for span in spans[1:])
    assert all(span["parentSpanId"] == spans[0]["spanId"] for span in spans[1:])


def test_incomplete_exec_and_mcp_outside_exec_interval_are_not_grouped(tmp_path):
    turn = extract(tmp_path, custom(second=3), mcp(second=4, duration={"secs": 2, "nanos": 0}))
    assert "span_kind" not in turn["tool_calls"][0]
    turn = extract(
        tmp_path, custom(second=3), mcp(second=4, duration={"secs": 2, "nanos": 0}), custom(second=5, output=True)
    )
    assert "span_kind" not in turn["tool_calls"][0]


@pytest.mark.parametrize("invocation", [None, [], {}, {"server": "s", "tool": 4}])
def test_malformed_invocations_are_skipped_without_losing_other_tools(tmp_path, invocation):
    event = mcp()
    event["payload"]["invocation"] = invocation
    turn = extract(tmp_path, custom(), event, custom(second=4, output=True))
    assert len(turn["tool_calls"]) == 1
    assert turn["tool_calls"][0]["tool"] == "exec"


def test_empty_call_ids_are_not_deduplicated_and_turns_are_isolated(tmp_path):
    turn = extract(
        tmp_path, mcp(""), mcp(""), record("event_msg", {"type": "task_started", "turn_id": "next"}), mcp("next-call")
    )
    assert len(turn["tool_calls"]) == 2
