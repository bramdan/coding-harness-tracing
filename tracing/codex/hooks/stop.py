"""Stdin-based Codex Stop hook, for payloads too large for legacy notify argv."""

from __future__ import annotations

import json
import sys

from core.common import error, get_timestamp_ms
from tracing.codex.constants import get_codex_home
from tracing.codex.hooks.adapter import check_requirements, load_env_file
from tracing.codex.hooks.handlers import (
    _build_and_send_spans,
    _extract_turn_from_rollout,
    _find_rollout_file,
    _send_legacy_single_span,
)


def _handle_stop(payload: dict) -> None:
    """Observe the finished response without blocking or continuing the agent."""
    if payload.get("hook_event_name") != "Stop":
        return
    session_id, turn_id = payload.get("session_id"), payload.get("turn_id")
    if not isinstance(session_id, str) or not session_id or not isinstance(turn_id, str) or not turn_id:
        return
    # Resolve only beneath configured CODEX_HOME, not an arbitrary input path.
    path = _find_rollout_file(session_id)
    turn = _extract_turn_from_rollout(path, turn_id) if path else None
    message = payload.get("last_assistant_message")
    if turn is None:
        _send_legacy_single_span(session_id, turn_id, {"last-assistant-message": message})
        return
    # Stop precedes task_complete in Codex. The hook supplies the final response;
    # use receipt time for the end instead of the last tool's completion time.
    if isinstance(message, str):
        turn["assistant_output"] = message
    turn["turn_end_ms"] = max(get_timestamp_ms(), turn["turn_start_ms"], turn["turn_end_ms"])
    turn["duration_ms"] = turn["turn_end_ms"] - turn["turn_start_ms"]
    _build_and_send_spans(session_id, turn_id, turn)


def main() -> None:
    """Read hook JSON on stdin; always return a neutral valid Stop response."""
    try:
        load_env_file(get_codex_home() / "arize-env.sh")
        if check_requirements():
            payload = json.load(sys.stdin)
            if isinstance(payload, dict):
                _handle_stop(payload)
    except Exception:
        # Do not expose payload content through exception messages or stdout.
        error("Codex Stop tracing failed; the agent response is unaffected")
    finally:
        print("{}")


if __name__ == "__main__":
    main()
