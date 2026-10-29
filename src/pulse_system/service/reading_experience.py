"""Retain an exact observed reading passage in the existing experience journal.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
A stored observation survives changes to its file. It is not a fresh read,
verified external authorship, a favorable outcome, or an instruction authority.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from collections.abc import Mapping

from pulse_harness.core.contracts import ActionStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.replay import bound_text

from .workspace_reading import KIND, NAME


def capture_reading_example(runtime, scope, receipt_id: str, excerpt: str) -> dict:
    """Resolve only a delivered passage in this logical context's native history."""
    store = runtime.kernel_service.store
    # The host's committed lineage includes prior generations of THIS Work.
    # A guessed receipt from another Work cannot expand the readable history.
    record = None
    for session in runtime.harness._history_scopes(scope):
        try:
            previous_scope = store.action_scope_for_session(session.session_scope(), receipt_id)
        except ScopeConflict:
            continue
        record = store.get_action(previous_scope, receipt_id)
        break
    output = (record.result or {}).get("output") if record else None
    if (record is None or record.tool_name != NAME or record.status != ActionStatus.SUCCEEDED
            or not record.result_delivered or not isinstance(output, Mapping)
            or output.get("kind") != KIND or output.get("receipt_id") != receipt_id):
        raise ScopeConflict("example source is not an exposed reading passage in this context")
    content = output.get("content")
    if (not isinstance(content, str) or not isinstance(excerpt, str)
            or not excerpt.strip() or excerpt not in content):
        raise ScopeConflict("the example must be an exact part of the exposed reading passage")
    clean, redacted, truncated = bound_text(excerpt, 8000)
    if redacted or truncated or clean != excerpt:
        raise ScopeConflict("select a shorter example passage without credentials")
    # Retain only the selected text. Full source files and the rest of a large
    # passage do not become hidden context merely because one example was saved.
    offset = content.index(excerpt)
    return {
        "record_type": "observed_episode",
        "encounter": {
            "situation": "Selected from a previously exposed workspace reading passage.",
            "action": excerpt,
        },
        "source": {
            "kind": "native_reading_example",
            "meaning": "retained_reading_observation_not_verified_source_or_outcome",
            "world_id": scope.world_id, "subject_id": scope.engram_id,
            "context_id": scope.context_id, "source_subject_id": record.scope.engram_id,
            "original_turn_id": record.scope.turn_id, "reading_receipt_id": receipt_id,
            "reading_source": deepcopy(output["source"]),
            "reading_position": deepcopy(output["position"]),
            "observed_at": output["observed_at"],
            "reading_redacted": output["redacted"],
            "current_file_checked": False,
            "feedback_evidence_class": "not_attached",
            "selected_excerpt": {
                "start": offset, "end": offset + len(excerpt),
                "offset_unit": "unicode_code_points_in_exposed_receipt_content",
                "occurrence": "first_exact_match",
                "sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            },
        },
    }
