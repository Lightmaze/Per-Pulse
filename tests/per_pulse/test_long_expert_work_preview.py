"""Long Work instructions remain visible to native pre-action expert work.

All requests here are local input projections; no scripted provider output is
presented as observed model behavior. Copyright 2026 Lightmaze Contributors.
Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json

from pulse_harness.core import HarnessScope
from pulse_harness.core.provider import ModelRequest
from pulse_harness.plugins.expert_work import (
    make_work_prompt, preview_work_message, working_view,
)


def _request(*contents):
    messages = tuple(
        {"role": "user", "content": content, "item_id": f"user-{i}",
         "transcript_seq": i}
        for i, content in enumerate(contents, 1)
    )
    scope = HarnessScope(
        "world", "subject", "session", 0, "owner",
        context_id="work:next", turn_id="continuing-turn",
    )
    return ModelRequest(scope, messages, (), 0)


def test_long_selected_work_source_keeps_followup_action_in_preaction_prompt():
    source = (
        "SOURCE_OPENING: earlier Work delivered a real prototype.\n"
        + ("OBSERVATION_IN_MIDDLE 🔬 needs a separate source read.\n" * 300)
        + "\nNEXT_TASK_AT_END: change the tool choice and finish the actual file artifact."
    )
    latest = "Continue from the exact chosen source and inspect the resulting file."
    view, evidence = working_view(_request(source, latest), byte_budget=18000)

    assert len(view["messages"]) == 2
    preview = view["messages"][0]["content"]
    assert preview.startswith("SOURCE_OPENING:")
    assert preview.endswith("NEXT_TASK_AT_END: change the tool choice and finish the actual file artifact.")
    assert "WORK_MESSAGE_MIDDLE_OMITTED" in preview
    assert f"sha256={hashlib.sha256(source.encode()).hexdigest()}" in preview
    assert "original_bytes=" + str(len(source.encode())) in preview
    assert len(preview.encode()) <= 5000
    assert preview.count("OBSERVATION_IN_MIDDLE") < 300
    assert evidence["visible_items"] == ["user-1", "user-2"]
    assert view["omitted_messages"] == 0

    prompt = make_work_prompt(view, {"previous_computations": [], "episodes": []}, model_round=0)
    packet = json.loads(prompt.split("WORK_MATERIAL_JSON\n", 1)[1])
    assert packet["current_work"]["messages"][0]["content"] == preview
    assert packet["current_work"]["messages"][1]["content"] == latest
    assert "NEXT_TASK_AT_END" in prompt


def test_multibyte_preview_is_bounded_and_never_presents_two_spans_as_contiguous():
    source = "原始来源 🔬" + ("🧬" * 3500) + "最后的明确任务：写出可用作品 🧪"
    preview = preview_work_message(source)
    assert preview.startswith("原始来源 🔬")
    assert preview.endswith("最后的明确任务：写出可用作品 🧪")
    assert "[WORK_MESSAGE_MIDDLE_OMITTED" in preview
    assert "\ufffd" not in preview
    assert len(preview.encode("utf-8")) <= 5000
    assert preview_work_message("Short user task 🧪") == "Short user task 🧪"
    assert preview_work_message("a" * 5000) == "a" * 5000
    assert len(preview_work_message("a" * 5001).encode()) <= 5000


def test_recent_long_task_keeps_its_latest_instruction_under_existing_view_budget():
    opening = "A prior Work outcome with verified source. " + "source detail " * 600
    followup = "Current task begins here. " + "middle progress " * 900 + (
        "FINAL_CURRENT_INSTRUCTION: use the native read tool to verify the saved file."
    )
    view, _ = working_view(_request(opening, followup), byte_budget=18000)
    assert len(view["messages"]) == 2
    assert view["messages"][0]["content"].startswith("A prior Work outcome")
    assert view["messages"][-1]["content"].endswith(
        "FINAL_CURRENT_INSTRUCTION: use the native read tool to verify the saved file."
    )
    assert view["content_bound_bytes_per_message"] == 5000
    assert all(len(message["content"].encode()) <= 5000 for message in view["messages"])


def test_current_user_task_survives_large_tool_batch_before_the_next_action():
    """Keep the continuing purpose and recent evidence, not just old history."""
    task = ("Compare the four prepared release notes. " + "Comparison constraints. " * 300
            + " Use the verified result rather than a provisional candidate.")
    original = _request("Prepare comparison notes for a later task.", task)
    messages = original.messages + tuple(
        {"role": "tool", "content": f"Evidence {i}: " + "Detailed observations. " * 350,
         "item_id": f"result-{i}", "transcript_seq": i + 3}
        for i in range(4)
    )
    request = ModelRequest(original.scope, messages, (), 0)
    for budget in (18000, 14000):
        view, evidence = working_view(request, byte_budget=budget)
        assert "user-2" in evidence["visible_items"]
        current = next(row["content"] for row in view["messages"] if row["item_id"] == "user-2")
        assert current.startswith("Compare the four prepared release notes.")
        assert current.endswith("Use the verified result rather than a provisional candidate.")
        assert "result-3" in evidence["visible_items"]
        assert view["omitted_messages"] > 0
        assert evidence["visible_items"] == [
            message["item_id"] for message in messages
            if message["item_id"] in evidence["visible_items"]
        ]


def test_experience_cues_reserve_space_for_the_revised_purpose():
    from pulse_system.service.expert_computation import _task_terms

    task = ("Review the comparison catalogue. " + "comparison catalogue " * 360
            + " Instead select an interoperability hypothesis for planning.")
    terms = _task_terms(_request(task))
    assert "interoperability" in terms
    assert "hypothesis" in terms
    assert "comparison" in terms
    assert len(terms) <= 6
    assert len(set(terms)) == len(terms)


def test_terse_revision_borrows_only_previous_user_task_objects():
    from pulse_system.service.expert_computation import _task_terms

    original = _request("Prepare the microscopy calibration report.", "Use the alternative.")
    messages = original.messages[:1] + (
        {"role": "tool", "content": "unauthorized fabricated observation",
         "item_id": "tool-1", "transcript_seq": 2},
        {**original.messages[1], "transcript_seq": 3},
    )
    terms = _task_terms(ModelRequest(original.scope, messages, (), 0))
    assert terms[0] == "alternative"
    assert "microscopy" in terms
    assert "calibration" in terms
    assert "unauthorized" not in terms
    assert "fabricated" not in terms
    assert len(terms) <= 6


def test_long_chinese_revision_keeps_late_task_objects():
    from pulse_system.service.expert_computation import _task_terms

    terms = _task_terms(_request("背景材料。" * 1800 + "请改为互操作性验证和原型测试。"))
    assert "互操作" in "".join(terms)
    assert len(terms) <= 6


def test_fitted_history_and_auxiliary_context_keep_request_order_without_inventing_sequences():
    from pulse_harness.core.context_window import fit_context

    original = _request("Opening task.", "Earlier correction. " * 500, "Continue the current task.")
    saved = tuple({**row, "message_id": row["item_id"]} for row in original.messages)
    fitted = fit_context(saved, 500)
    assert fitted[0]["item_id"] == "context-window:omitted"
    assert "transcript_seq" not in fitted[0]
    auxiliary = {"role": "system", "content": "Native auxiliary context.",
                 "item_id": "auxiliary-context:example"}
    request = ModelRequest(original.scope, (*fitted, auxiliary), (), 3)
    view, source = working_view(request)
    assert [row["item_id"] for row in view["messages"]] == [
        row["item_id"] for row in request.messages
    ]
    assert source["transcript_cursor"] == 3
    assert source["visible_items"] == [row["item_id"] for row in request.messages]
    assert "transcript_seq" not in auxiliary
