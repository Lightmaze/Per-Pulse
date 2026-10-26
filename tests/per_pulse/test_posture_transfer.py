"""Native ordinary-feedback trials; all model decisions here are explicit fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from copy import deepcopy
import json

import httpx
import pytest

from per_pulse.posture_evaluation import _canonical, _digest, _record_digest, main
from per_pulse.posture_transfer import (
    ORDINARY_FEEDBACK, SOURCE_MARKER, STAGES, audit_ordinary_transfer, run_ordinary_transfer,
)
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def fixture_factory(monkeypatch, requests, *, capture=True, leak=False):
    monkeypatch.setenv("ORDINARY_TRANSFER_FIXTURE_KEY", "fixture-only")
    state = {"main": 0}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        arguments = None
        if "WORK_MATERIAL_JSON" in json.dumps(body["messages"]):
            text = "PASS"
        else:
            index = state["main"]
            state["main"] += 1
            if index == 0:
                text = "Search is slow. Saved documents remain available. We are investigating. No recovery time is confirmed. Next update: 09:00 UTC."
            elif not capture:
                text = "Saved documents remain available. Search is slow. The next update is at 09:00 UTC."
            elif index == 1:
                arguments = {"op": "list"}
            elif index == 2:
                latest = next(value for value in reversed(body["messages"]) if value["role"] == "tool")
                sources = json.loads(latest["content"])["output"]["recent_sources"]
                original = next(value for value in sources if value["kind"] == "assistant_result")
                feedback = [value for value in sources if value["source"] == "user"][-1]
                arguments = {
                    "op": "remember", "source_message_id": original["message_id"],
                    "feedback_message_id": feedback["message_id"],
                    "title": "Available service first" + (" " + SOURCE_MARKER if leak else ""),
                    "lesson": "For a requested customer notice, begin with what remains usable, then disruption and next update.",
                    "conditions": {"all": ["A finished customer notice is requested.", {"not": "An outline or plan is requested."}]},
                    "counterexamples": ["When a plan or outline is requested, deliver that structure without an unrequested finished message."],
                    "shift": {"understanding": "Identify whether the user requests a finished notice or a plan.",
                              "candidates": "For a notice, consider leading with the unaffected service.",
                              "delivery": "Deliver the requested format and preserve supplied facts."},
                }
            elif index == 3:
                text = "Saved documents remain available. Search is slow. We are investigating, and the next update is at 09:00 UTC. No recovery time is confirmed."
            elif index in {5, 7}:
                text = "Outline: 1. Current service availability. 2. Disruption and customer impact. 3. Known facts and uncertainties. 4. Next update."
            else:
                text = "Existing files can still be viewed. Uploads are delayed; no data loss has been detected. We are investigating and have no confirmed recovery time. Next update: 14:00 UTC."
        delta = {"content": text} if arguments is None else {"tool_calls": [{"index": 0,
            "id": "fixture-" + str(len(requests)), "type": "function",
            "function": {"name": "posture", "arguments": json.dumps(arguments)}}]}
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop" if arguments is None else "tool_calls"}],
                   "usage": {"prompt_tokens": 11, "completion_tokens": 4}}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "ordinary-transfer-fixture",
                                                   credential_env="ORDINARY_TRANSFER_FIXTURE_KEY"), transport=httpx.MockTransport(handler))
    return factory


def test_ordinary_feedback_keeps_native_capture_and_fresh_transfer_separate(tmp_path, monkeypatch):
    requests = []
    output = tmp_path / "trial"
    result = run_ordinary_transfer(output, model_factory=fixture_factory(monkeypatch, requests), stage_timeout=10)
    assert result["status"] == "completed", result
    assert result["capture_status"] == "native_lesson_admitted"
    assert result["origin"] == "synthetic_fixture"
    assert not result["ordinary_feedback_has_learning_instruction"]
    assert all(term not in ORDINARY_FEEDBACK.lower() for term in ("remember", "posture", "lesson", "tool"))
    assert [stage["stage"] for stage in result["stages"]] == [stage[0] for stage in STAGES]
    source, feedback, *targets = result["stages"]
    assert source["context_id"] == feedback["context_id"]
    assert len({stage["context_id"] for stage in result["stages"]}) == 5
    lesson = result["learned_lessons"][0]
    assert lesson["record_type"] == "constructed_fixture"
    assert lesson["source"]["source_message_id"] == source["result_event_id"]
    assert lesson["source"]["feedback_message_id"] == feedback["input_event_id"]
    assert lesson["scope"] == "context"
    assert result["shared_lessons"] == [{"id": lesson["id"], "revision": lesson["revision"] + 1,
                                          "previous_revision": lesson["revision"], "scope": "subject"}]
    assert result["provider_requests"] == {"training": 4, "bare_transfer": 1, "bare_negative": 1,
                                            "conditional_transfer": 3, "conditional_negative": 3}
    assert sum(result["provider_requests"].values()) == len(requests)
    assert "Posture trial:" not in json.dumps(requests)
    assert all(name not in json.dumps(requests) for name, _, _ in STAGES[2:])
    assert all(value["source_transcript_absent"] and value["expected_memory_exposure"]
               for value in result["exposure_checks"].values())
    assert all(not value["private_episode_fields_present"] for value in result["exposure_checks"].values())
    assert result["exposure_checks"]["bare_transfer"]["offered_lessons"] == []
    assert result["review_status"] == "unreviewed" and not result["eligible_for_behavior_comparison"]
    assert all((output / stage["artifact"]["path"]).is_file() for stage in targets)
    audit = audit_ordinary_transfer(output)
    assert audit["status"] == "verified", audit
    assert not audit["eligible_for_behavior_comparison"]

    # Export consistency must reject substituted response bytes and repeated
    # request snapshots, even after rebinding host-generated record checksums.
    artifact = output / targets[0]["artifact"]["path"]
    original = artifact.read_bytes()
    artifact.write_text("A substituted answer.")
    assert "native_response_artifact_mismatch" in audit_ordinary_transfer(output)["errors"]
    artifact.write_bytes(original)
    snapshots = json.loads((output / "provider-requests.json").read_text())
    snapshots[1] = deepcopy(snapshots[0])
    raw = (_canonical(snapshots) + "\n").encode()
    (output / "provider-requests.json").write_bytes(raw)
    result["request_snapshot_sha256"] = _digest(raw)
    (output / "ordinary-transfer.json").write_text(json.dumps(result))
    manifest_path = output / "ordinary-transfer-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["record_digest"] = _record_digest(result)
    manifest_path.write_text(json.dumps(manifest))
    assert "native_request_audit_mismatch" in audit_ordinary_transfer(output)["errors"]


def test_absent_capture_is_an_observed_outcome_and_never_seeded_by_host(tmp_path, monkeypatch):
    requests = []
    output = tmp_path / "trial"
    result = run_ordinary_transfer(output, model_factory=fixture_factory(monkeypatch, requests, capture=False), stage_timeout=10)
    assert result["status"] == result["capture_status"] == "capture_absent", result
    assert len(result["stages"]) == 2
    assert result["learned_lessons"] == result["shared_lessons"] == result["experiences"] == []
    assert result["provider_requests"]["training"] == len(requests) == 2
    assert sum(result["provider_requests"].values()) == 2
    audit = audit_ordinary_transfer(output)
    assert audit["status"] == "verified", audit
    assert audit["capture_status"] == "capture_absent"


def test_private_marker_in_learned_abstraction_is_detected_at_real_request_boundary(tmp_path, monkeypatch):
    output = tmp_path / "trial"
    result = run_ordinary_transfer(output, model_factory=fixture_factory(monkeypatch, [], leak=True), stage_timeout=10)
    assert result["status"] == "completed", result
    assert not result["exposure_checks"]["conditional_transfer"]["source_transcript_absent"]
    assert result["exposure_checks"]["bare_transfer"]["source_transcript_absent"]
    audit = audit_ordinary_transfer(output)
    assert "transfer_exposure_mismatch" in audit["errors"]


def test_request_budget_failure_does_not_turn_into_capture_absent(tmp_path, monkeypatch):
    requests = []
    result = run_ordinary_transfer(tmp_path / "trial", model_factory=fixture_factory(monkeypatch, requests),
                                   training_calls=1, stage_timeout=10)
    assert result["status"] == "failed", result
    assert len(requests) == sum(result["provider_requests"].values()) == 1
    assert result["capture_status"] == "pending"
    assert result["learned_lessons"] == result["shared_lessons"] == []


def test_unconfigured_ordinary_transfer_makes_no_trial_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("P_PULSE_CHAT_ENDPOINT", raising=False)
    monkeypatch.delenv("P_PULSE_CHAT_MODEL", raising=False)
    output = tmp_path / "trial"
    assert main(["ordinary-transfer", "--output", str(output)]) == 2
    assert "no external request was made" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("kwargs", [{"training_calls": True}, {"trial_calls": 0}, {"stage_timeout": True}])
def test_invalid_bounds_are_rejected_before_provider_discovery(tmp_path, kwargs):
    with pytest.raises(ValueError):
        run_ordinary_transfer(tmp_path / "trial", **kwargs)
    assert not (tmp_path / "trial").exists()
