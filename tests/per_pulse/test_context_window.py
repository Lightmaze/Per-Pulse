"""A long conversation is projected to fit the model's context.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import pytest

from pulse_harness.core.context_window import OMITTED_NOTE, estimate_tokens, fit_context


def _user(text):
    return {"role": "user", "content": text}


def _answer(text):
    return {"role": "assistant", "content": text}


def _call(call_id):
    return {"role": "assistant", "tool_call_count": 1, "content": json.dumps({"text": "", "tool_calls": [
        {"tool_call_id": call_id, "action_request_id": call_id, "name": "read_file", "arguments": {"path": "a"}}]})}


def _result(call_id, size=10):
    return {"role": "tool", "content": json.dumps({"tool_call_id": call_id, "status": "succeeded",
                                                   "output": {"text": "x" * size}})}


def _turns(count, size=300):
    messages = []
    for index in range(count):
        messages += [_user(f"Question {index} " + "q" * size), _call(f"c{index}"),
                     _result(f"c{index}", size), _answer(f"Answer {index} " + "a" * size)]
    return messages


def test_a_conversation_that_fits_is_unchanged():
    messages = _turns(3)
    assert fit_context(messages, 10_000) == tuple(messages)


def test_a_long_conversation_keeps_the_opening_and_the_latest_whole_turns():
    messages = _turns(40)
    budget = 2_000
    fitted = fit_context(messages, budget)
    assert sum(estimate_tokens(m) for m in fitted) <= budget
    assert fitted[0] == {"role": "system", "content": OMITTED_NOTE, "item_id": "context-window:omitted"}
    assert fitted[1] == messages[0]
    window = fitted[2:]
    assert window[0]["role"] == "user"
    assert window == tuple(messages[len(messages) - len(window):])
    assert window[-1] == messages[-1]
    assert len(window) < len(messages) - 1


def test_a_window_never_starts_with_a_tool_result():
    messages = [_user("Start")]
    for index in range(30):
        messages += [_call(f"c{index}"), _result(f"c{index}", 400)]
    messages.append(_answer("Done"))
    fitted = fit_context(messages, 500)
    # Inside one long tool exchange there is no safe cut: nothing is dropped.
    assert fitted == tuple(messages)


def test_plain_json_tool_call_examples_remain_separate_replies():
    messages = [_user("Start")]
    for index in range(30):
        # These have the internal envelope's exact spelling, but no terminal
        # tool-call authority. They must not bind every later reply together.
        example = {**_call(f"example-{index}"), "tool_call_count": 0}
        messages += [example, {"role": "system", "content": "Context " + "x" * 400}]
    fitted = fit_context(messages, 500)
    assert len(fitted) < len(messages)
    assert fitted[-2:] == tuple(messages[-2:])


def test_the_latest_turn_is_kept_whole_even_when_it_is_larger_than_the_budget():
    messages = _turns(5) + [_user("Big " + "b" * 9_000)]
    fitted = fit_context(messages, 1_000)
    assert fitted[-1] == messages[-1]
    assert messages[0] not in fitted


def test_an_oversized_historical_opening_does_not_crowd_out_a_short_followup():
    messages = [_user("ORIGINAL_OPENING " + "x" * 12_000), _answer("Saved the original."),
                _user("Continue with the next step.")]
    original = tuple(dict(message) for message in messages)
    fitted = fit_context(messages, 2_048)
    assert sum(estimate_tokens(message) for message in fitted) <= 2_048
    assert fitted[-1] == messages[-1]
    assert messages[0] not in fitted
    assert fitted[0]["content"] == OMITTED_NOTE
    assert tuple(messages) == original


def test_opening_is_optional_only_when_it_cannot_fit_beside_the_current_exchange():
    opening = _user("Original goal " + "x" * 1_800)
    current = [_user("Continue " + "y" * 1_800), _call("current"), _result("current")]
    messages = [opening, _answer("An earlier result."), *current]
    assert estimate_tokens(opening) < 1_000
    assert sum(estimate_tokens(message) for message in current) < 1_000
    fitted = fit_context(messages, 1_000)
    assert opening not in fitted
    assert tuple(fitted[-len(current):]) == tuple(current)
    assert sum(estimate_tokens(message) for message in fitted) <= 1_000


def test_a_first_input_is_never_removed_as_an_old_opening():
    messages = [_user("CURRENT_INPUT " + "x" * 12_000)]
    assert fit_context(messages, 2_048) == tuple(messages)


def test_a_current_exchange_stays_whole_after_an_oversized_opening_is_omitted():
    current = [_user("Read the next part."), _call("current"), _result("current", 9_000)]
    messages = [_user("ORIGINAL_OPENING " + "x" * 12_000), _answer("Done."), *current]
    fitted = fit_context(messages, 2_048)
    assert messages[0] not in fitted
    assert tuple(fitted[-len(current):]) == tuple(current)



def test_large_current_tool_result_is_addressable_without_replaying_the_action():
    result = {**_result("current", 18_000), "message_id": "saved-tool-message-7",
              "item_id": "tool-result:current", "transcript_seq": 7}
    messages = [_user("Opening goal"), _answer("Previous answer"),
                _user("Read and continue."), _call("current"), result]
    original = tuple(dict(message) for message in messages)
    fitted = fit_context(messages, 1_000)
    assert sum(estimate_tokens(message) for message in fitted) <= 1_000
    assert fitted[-3]["role"] == "user"
    assert fitted[-2] == messages[-2]  # tool-call authority stays intact
    note = json.loads(fitted[-1]["content"])
    assert note["tool_call_id"] == "current"
    assert note["status"] == "succeeded"
    reference = note["output"]["context_window"]
    assert reference["message_id"] == "saved-tool-message-7"
    assert reference["original_chars"] == len(result["content"])
    assert "read_history" in reference["next_step"]
    assert "x" * 18_000 not in fitted[-1]["content"]
    assert tuple(messages) == original  # saved original is unchanged



def test_oversized_first_tool_exchange_still_keeps_the_first_user_goal():
    result = {**_result("first", 20_000), "message_id": "saved-first-tool-5"}
    messages = [_user("Investigate this file."), _call("first"), result]
    fitted = fit_context(messages, 1_000)
    assert sum(estimate_tokens(item) for item in fitted) <= 1_000
    assert fitted[1] == messages[0]
    assert fitted[2] == messages[1]
    assert json.loads(fitted[3]["content"])["output"]["context_window"]["message_id"] == "saved-first-tool-5"
    assert fitted[0]["content"] == OMITTED_NOTE
    assert result == messages[-1]


def test_unaddressable_current_tool_result_is_not_silently_discarded():
    messages = [_user("Read"), _call("current"), _result("current", 18_000)]
    fitted = fit_context(messages, 1_000)
    assert fitted[-1] == messages[-1]  # no trusted message_id, no false pointer
    assert sum(estimate_tokens(message) for message in fitted) > 1_000


def test_budget_must_be_positive():
    with pytest.raises(ValueError):
        fit_context([], 0)
