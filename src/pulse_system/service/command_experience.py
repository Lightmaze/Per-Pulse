"""Keep an exact command passage already delivered to this local context.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The Kernel's saved read receipt is the observation. Retention never reads the
capture again, runs a command, or interprets its exit code as a quality rating.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from pulse_harness.adapters import command_output
from pulse_harness.core.contracts import ActionStatus
from pulse_harness.core.errors import ScopeConflict
from .posture import _text


def _own_action(runtime, scope, action_id):
    """Resolve only this Work's current and committed predecessor sessions."""
    store = runtime.kernel_service.store
    if not isinstance(action_id, str) or not action_id:
        raise ScopeConflict("command example source has no action identity")
    lineage = runtime.causal_ledger.committed_history_lineage(scope.world_id, scope.engram_id)
    for subject in lineage:
        session = runtime.harness.scope_for(subject, scope.context_id)
        try:
            original = store.action_scope_for_session(session, action_id)
            return store.get_action(original, action_id)
        except ScopeConflict:
            continue
    raise ScopeConflict("command example source does not belong to this context's history")


def _native_turn(runtime, scope):
    turn = runtime.causal_ledger.get_turn_for_world(scope.turn_id, scope.world_id)
    if (turn is None or turn.engram_id != scope.engram_id
            or runtime.native_kernel_context_for_turn(scope.engram_id, turn.id) != scope.context_id):
        raise ScopeConflict("command example source has no matching native turn")
    return turn


def capture_command_example(runtime, scope, receipt_id: str, excerpt: str,
                            *, selecting_action_id: str) -> dict:
    """Keep only selected text from a preceding delivered output-read action."""
    if scope.world_id != runtime.world_id:
        raise ScopeConflict("command example belongs to the current World")
    store = runtime.kernel_service.store
    receipt = _own_action(runtime, scope, receipt_id)
    output = (receipt.result or {}).get("output")
    if (receipt.tool_name != command_output.NAME or receipt.status is not ActionStatus.SUCCEEDED
            or not receipt.result_delivered or not isinstance(output, Mapping)
            or output.get("kind") != command_output.KIND
            or ("receipt_id" in output and output["receipt_id"] != receipt_id)
            or (receipt.result or {}).get("_truncated") is True):
        raise ScopeConflict("command example requires a delivered output-reading receipt")
    # This public read also verifies the saved envelope against its original
    # model call and action result. Do not expand a previously truncated view.
    observed = store.read_transcript_message(
        receipt.scope.session_scope(), "tool-result:" + receipt_id, role="tool")
    if observed is None or observed["truncated"]:
        raise ScopeConflict("command example requires a complete delivered reading receipt")
    rows = store.transcript_rows(scope)
    selectors = []
    for row in rows:
        if row["role"] != "assistant":
            continue
        try:
            calls = json.loads(row["content"]).get("tool_calls", [])
        except (ValueError, AttributeError):
            continue
        if any(call.get("action_request_id") == selecting_action_id for call in calls):
            selectors.append(row)
    if len(selectors) != 1:
        raise ScopeConflict("command example has no unique originating model call")
    source_turn, selecting_turn = _native_turn(runtime, receipt.scope), _native_turn(runtime, scope)
    source_event = runtime.causal_ledger.get_event(source_turn.event_id)
    trigger = runtime.causal_ledger.get_event(selecting_turn.event_id)
    if (source_event is None or trigger is None or source_event.seq is None or trigger.seq is None
            or source_event.seq > trigger.seq
            or (receipt.scope.session_id == scope.session_id
                and observed["transcript_seq"] >= selectors[0]["transcript_seq"])):
        raise ScopeConflict("command passage was not delivered before the originating model call")
    command = _own_action(runtime, scope, output.get("action_id"))
    capture = command_output.capture(command)
    stream = receipt.arguments.get("stream")
    facts = (command.result or {}).get("output", {})
    if (capture is None or not command.result_delivered or stream not in command_output.STREAMS
            or output.get("stream") != stream
            or receipt.arguments.get("action_id") != command.action_request_id
            or output.get("source") != {"session_id": command.scope.session_id, "turn_id": command.scope.turn_id}
            or output.get("sha256") != capture[stream]["sha256"]
            or output.get("saved_bytes") != capture[stream]["saved_bytes"]
            or output.get("observed_bytes") != capture[stream]["observed_bytes"]
            or output.get("capture_complete") is not capture[stream]["complete"]
            or output.get("offset") != receipt.arguments.get("offset", 0)
            or output.get("offset_unit") != "unicode_characters_in_redacted_text"
            or type(output.get("redacted")) is not bool
            or type(facts.get("exit_code")) is not int):
        raise ScopeConflict("command reading receipt does not match its original command")
    command_turn = _native_turn(runtime, command.scope)
    command_event = runtime.causal_ledger.get_event(command_turn.event_id)
    if command_event is None or command_event.seq is None or command_event.seq > source_event.seq:
        raise ScopeConflict("command was not available to the output-reading turn")
    content = output.get("content")
    if (not isinstance(content, str) or not isinstance(excerpt, str)
            or not excerpt.strip() or excerpt not in content):
        raise ScopeConflict("the example must be an exact part of the delivered command passage")
    # Preserve exactly what the model actually saw in its verified receipt.
    # Receipt redaction/completeness remains attached to the observation;
    # literal marker-shaped output is not a new redaction event.
    try:
        _text(excerpt, "example excerpt", 8000)
    except ValueError as exc:
        raise ScopeConflict("select a command passage within 8000 UTF-8 bytes") from exc
    start = content.index(excerpt)
    observation = {"stream": stream, "exit_code": facts["exit_code"],
                   "capture_complete": output["capture_complete"], "redacted": output["redacted"]}
    return {
        "record_type": "observed_episode",
        "encounter": {"situation": f"Selected from delivered command {stream}; exit code {facts['exit_code']}.",
                      "action": excerpt},
        "source": {
            "kind": "native_command_example", "meaning": "retained_command_observation_not_quality_rating",
            "world_id": scope.world_id, "subject_id": scope.engram_id, "context_id": scope.context_id,
            "source_subject_id": receipt.scope.engram_id, "original_turn_id": receipt.scope.turn_id,
            "command_receipt_id": receipt_id, "command_action_id": command.action_request_id,
            "command_subject_id": command.scope.engram_id, "command_turn_id": command.scope.turn_id,
            "observed_at": receipt.terminal_at, "command_observed_at": command.terminal_at,
            "command_observation": observation, "capture_sha256": output["sha256"],
            "saved_bytes": output["saved_bytes"], "observed_bytes": output["observed_bytes"],
            "delivered_range": {"start": output["offset"], "end": output["offset"] + len(content),
                                "offset_unit": output["offset_unit"],
                                "sha256": hashlib.sha256(content.encode()).hexdigest()},
            "feedback_evidence_class": "not_attached",
            "selected_excerpt": {"start": output["offset"] + start,
                                 "end": output["offset"] + start + len(excerpt),
                                 "offset_unit": output["offset_unit"], "occurrence": "first_exact_match_in_receipt",
                                 "sha256": hashlib.sha256(excerpt.encode()).hexdigest()},
        },
    }
