"""Native selected-source advisory contracts. All model requests are fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from threading import Event

import pytest

from per_pulse.selected_source_advice import (
    MAX_SELECTED_JSON_CHARS, SOURCE_POLICY, SelectedSourceTurnAdvisor, source_context,
)
from pulse_harness.core import HarnessScope
from pulse_harness.core.provider import ModelRequest
from pulse_harness.core.turn_advice import TurnAdvice


def status(kind="revised"):
    return {
        "id": "experience-1", "revision": 2, "currentRevision": 3,
        "status": kind,
        **({"selected": {
            "id": "experience-1", "revision": 3, "title": "New choice",
            "lesson": "Use a real preview", "source_kind": "native_correction_lesson",
            "counterexamples": ["Registration is itself the product."],
        }} if kind == "revised" else {}),
    }


def test_native_source_state_is_attributed_and_revisable_without_copying_private_history():
    assert source_context(None) is None
    current = source_context(status("current"))
    assert "current" in current
    assert "currentSelection" not in current
    revised = source_context(status())
    assert "SELECTED_SOURCE_STATUS_JSON" in revised
    assert '"currentRevision": 3' in revised
    assert '"selectedRevision": 2' in revised
    assert "Use a real preview" in revised
    assert "not a new user request" in revised
    assert "This advisory does not authorize" in revised
    for withdrawn in ("withdrawn", "unavailable"):
        context = source_context(status(withdrawn))
        assert withdrawn in context
        assert "currentSelection" not in context
        assert "Use a real preview" not in context


def test_oversized_revised_lesson_remains_usable_before_the_next_work_decision():
    import json

    value = status()
    value["selected"]["lesson"] = (
        "NEW_REVISION_START: use the playable preview. "
        + "Background from this source. " * MAX_SELECTED_JSON_CHARS
        + "NEW_REVISION_END: never require intake first."
    )
    value["selected"]["conditions"] = {"any": ["The visitor can try it", "Use a small demo"]}
    context = source_context(value)
    current_json = context.split("SELECTED_SOURCE_STATUS_JSON\n", 1)[1].split("\n", 1)[0]
    payload = json.loads(current_json)
    selected = payload["currentSelection"]
    assert len(json.dumps(selected, ensure_ascii=False)) <= MAX_SELECTED_JSON_CHARS
    assert selected["detailsOmitted"] is True
    assert selected["readCurrentWith"] == {
        "tool": "posture", "op": "get", "experience_id": "experience-1",
    }
    assert selected["lessonPreview"]["first"].startswith("NEW_REVISION_START")
    assert selected["lessonPreview"]["last"].endswith("NEW_REVISION_END: never require intake first.")
    assert selected["lessonPreview"]["truncated"] is True
    assert "Background from this source. " * MAX_SELECTED_JSON_CHARS not in context
    assert "before relying on omitted details" in context
    assert selected["conditions"] == value["selected"]["conditions"]
    assert value["selected"]["lesson"] == (
        "NEW_REVISION_START: use the playable preview. "
        + "Background from this source. " * MAX_SELECTED_JSON_CHARS
        + "NEW_REVISION_END: never require intake first."
    )


@pytest.mark.parametrize("invalid", [
    {"id": "experience-1", "revision": 2, "currentRevision": 3, "status": "revised"},
    {**status(), "selected": {"id": "different", "revision": 3}},
    {**status(), "selected": {"id": "experience-1", "revision": 4}},
    {**status(), "status": "withdrawn"},
])
def test_invalid_current_source_cannot_enter_model_advice(invalid):
    with pytest.raises(ValueError):
        source_context(invalid)


def test_continuing_source_advice_does_not_repeat_one_shot_previous_computation():
    class Store:
        index = 0

        def next_model_round_index(self, scope):
            return self.index

    seen = []
    class Previous:
        continues = False

        def advise(self, request, *, cancel_event):
            seen.append(request)
            return TurnAdvice(context="Other one-time advice.", receipt={"other": "retained"})

    state = status("withdrawn")
    store = Store()
    advisor = SelectedSourceTurnAdvisor(store, lambda scope: dict(state), Previous())
    request = ModelRequest(HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn"), (), (), 0)
    first = advisor.advise(request, cancel_event=Event())
    assert advisor.continues
    assert first.receipt["other"] == "retained"
    assert first.receipt["per_pulse_selected_source"] == {
        "policy": SOURCE_POLICY, "id": "experience-1", "selectedRevision": 2,
        "currentRevision": 3, "status": "withdrawn",
    }
    assert "Other one-time advice." in first.context
    store.index = 1
    state = status()
    second = advisor.advise(request, cancel_event=Event())
    assert "Use a real preview" in second.context
    assert "Other one-time advice." not in second.context
    assert second.receipt["per_pulse_selected_source"]["status"] == "revised"
    assert len(seen) == 1
