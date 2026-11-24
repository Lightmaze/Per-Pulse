"""Keep an exact public webpage passage already delivered to this local context.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The saved observation is attributed extracted text, not an independently
verified fact, an instruction, a fresh network read or a success rating.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib

from pulse_harness.adapters import web_read
from pulse_harness.core.contracts import ActionStatus
from pulse_harness.core.errors import ScopeConflict

from .posture import _text


def capture_web_example(runtime, scope, receipt_id: str, excerpt: str) -> dict:
    """Select only from a delivered read_web result in this Work's lineage."""
    store = runtime.kernel_service.store
    record = None
    # The page's operator request has a session scope without a turn ID.
    # Team history requires an executing turn and can include other Works;
    # an operator may select only from this Work's own committed history.
    # A running model retains its existing delegated-history visibility.
    history_scopes = (runtime.harness._history_scopes(scope) if scope.turn_id
                      else runtime.harness._own_history_scopes(scope))
    for session in history_scopes:
        try:
            previous_scope = store.action_scope_for_session(session.session_scope(), receipt_id)
        except ScopeConflict:
            continue
        record = store.get_action(previous_scope, receipt_id)
        break

    output = (record.result or {}).get("output") if record else None
    if (record is None or record.tool_name != web_read.NAME
            or record.status is not ActionStatus.SUCCEEDED
            or not record.result_delivered or not isinstance(output, Mapping)
            or (record.result or {}).get("_truncated") is True
            or web_read.source_from_action(record) is None):
        raise ScopeConflict("web example requires a delivered webpage observation in this context")

    content = output.get("content")
    if (not isinstance(content, str) or not isinstance(excerpt, str)
            or not excerpt.strip() or excerpt not in content):
        raise ScopeConflict("web example must quote an exact observed webpage passage")
    try:
        _text(excerpt, "web example excerpt", 8000)
    except ValueError as exc:
        raise ScopeConflict("select a webpage passage within 8000 UTF-8 bytes") from exc

    # Source fields are produced by read_web, whose URL normalization and
    # delivered-text digest have just been checked; no webpage is refetched.
    source = output["source"]
    start = content.index(excerpt)
    selected_source = {key: deepcopy(source[key]) for key in (
        "requested_url", "url", "title", "fetched_at", "media_type",
        "text_sha256", "observation_sha256", "truncated",
        "network_truncated", "text_start", "text_end",
    )}
    return {
        "record_type": "observed_episode",
        "encounter": {
            "situation": "Selected from a previously exposed public HTTPS webpage passage.",
            "action": excerpt,
        },
        "source": {
            "kind": "native_web_example",
            "meaning": "retained_web_text_observation_not_verified_claim_or_outcome",
            "world_id": scope.world_id, "subject_id": scope.engram_id,
            "context_id": scope.context_id, "source_subject_id": record.scope.engram_id,
            "original_turn_id": record.scope.turn_id, "web_receipt_id": receipt_id,
            "web_source": selected_source, "current_webpage_checked": False,
            "feedback_evidence_class": "not_attached",
            "selected_excerpt": {
                "start": start, "end": start + len(excerpt),
                "offset_unit": "unicode_code_points_in_exposed_receipt_content",
                "occurrence": "first_exact_match",
                "sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            },
        },
    }
