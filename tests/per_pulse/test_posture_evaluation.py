"""Adversarial evaluation checks; fixtures are never model-quality evidence.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from per_pulse.posture_evaluation import (
    CASE_BY_ID, CONDITIONS, SCHEMA, TRANSFER_TASK, audit_transfer_trial, compare_runs, evaluate_run, main, run_provider_case, run_transfer_trial,
)
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"><rect x="10" y="10" width="100" height="60"/><circle cx="60" cy="40" r="10"/></svg>'
METRICS = {"evaluation_unit": "patient", "population": "held_out", "n_subjects": 2,
           "model_scores": {"A": .8, "B": .85}, "winner": "B"}


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def record_for(tmp_path, case_id="exact_svg", artifacts=None, **overrides):
    case = CASE_BY_ID[case_id]
    artifacts = artifacts or {"diagram.svg": SVG}
    for name, content in artifacts.items():
        (tmp_path / name).write_text(content)
    record = {
        "schema": SCHEMA, "run_id": "fixture-run", "pair_id": "pair-1",
        "case_id": case_id, "condition": "bare", "task_digest": case.task_digest,
        "origin": "synthetic_fixture", "model": "fixture-model", "action_source": "synthetic_fixture",
        "status": "succeeded", "budget": {"provider_calls": 8, "output_tokens_per_request": 4096},
        "available_tools": ["read_file", "mutate_file"],
        "experience_material_digest": sha("same-attributed-experience"),
        "artifacts": {name: sha(content) for name, content in artifacts.items()},
        "actions": [{"id": f"write-{i}", "tool": "mutate_file", "status": "succeeded",
                     "artifact": name, "after_sha256": sha(content)}
                    for i, (name, content) in enumerate(artifacts.items())],
        "observations": [],
    }
    record.update(overrides)
    return record


def observe(record, check_id, *, passed=True, source="browser_probe", artifact=None):
    if artifact is None:
        artifact = next(iter(record["artifacts"]))
    record["observations"].append({
        "check_id": check_id, "passed": passed, "source": source,
        "observer": "fixture-observer", "detail": "A constructed observation for an evaluator test.",
        "artifact_sha256": {artifact: record["artifacts"][artifact]},
    })


def experience():
    return {
        "title": "Complete the useful first encounter",
        "lesson": "A prototype should let someone experience its purpose.",
        "encounter": {"situation": "A practice product opened with profile fields.",
                      "action": "Replaced the fields with a conversation scene.",
                      "outcome": "A synthetic visitor could choose a response and continue."},
        "conditions": {"all": ["The task asks for a practice experience.",
                                 {"not": "The task is a standard registration utility."}]},
        "counterexamples": ["A check-in utility should remain a simple form.",
                            "Exact editable geometry should use the specified vector representation."],
        "shift": {"understanding": "Identify the action the person came to do.",
                  "candidates": "Include a directly usable experience.",
                  "delivery": "Try the actual first action and continuation."},
        "scope": "subject", "source": {"kind": "synthetic_fixture", "id": "museum-case"},
        "record_type": "constructed_fixture",
    }


def test_ux_slogans_and_model_self_report_do_not_pass_a_form(tmp_path):
    record = record_for(tmp_path, "experience_entry", {"prototype.html": "<form><input name='profile'></form>"})
    record["assistant_text"] = "UX-centered. I performed a UX review and a reflection. All tests passed."
    for check in CASE_BY_ID["experience_entry"].checks:
        observe(record, check, source="model_self_report")
    result = evaluate_run(record, tmp_path)
    assert result["verdict"] == "unreviewed"
    assert len(result["rejected_observations"]) == 3
    assert not result["eligible_for_behavior_comparison"]
    observe(record, "first_visit_has_playable_situation", passed=False)
    assert evaluate_run(record, tmp_path)["verdict"] == "fail"


def test_registration_form_is_a_positive_negative_control(tmp_path):
    record = record_for(tmp_path, "registration", {"prototype.html": "<form><input name='name'><input name='email'></form>"})
    for check in CASE_BY_ID["registration"].checks:
        observe(record, check)
    result = evaluate_run(record, tmp_path)
    assert result["verdict"] == "pass"
    assert result["control"] == "negative_control"
    assert not result["eligible_for_behavior_comparison"]


def test_observation_binds_the_actual_artifact_and_failed_receipt_does_not_count(tmp_path):
    record = record_for(tmp_path)
    assert evaluate_run(record, tmp_path)["verdict"] == "pass"
    (tmp_path / "diagram.svg").write_text(SVG.replace('r="10"', 'r="12"'))
    result = evaluate_run(record, tmp_path)
    assert result["artifact_errors"]["diagram.svg"] == "artifact_digest_mismatch"
    (tmp_path / "diagram.svg").write_text(SVG)
    record["actions"][0]["status"] = "failed"
    result = evaluate_run(record, tmp_path)
    assert result["artifact_errors"]["diagram.svg"] == "no_successful_write_receipt_for_artifact"


@pytest.mark.parametrize("change", [
    lambda x: x.replace('r="10"', 'r="11"'),
    lambda x: x.replace('<circle ', '<circle transform="translate(4 0)" '),
    lambda x: x.replace('</svg>', '<image href="remote.png"/></svg>'),
    lambda x: '<!DOCTYPE svg [<!ENTITY x "secret">]>' + x,
    lambda x: x.replace('<rect ', '<rect style="transform:translateX(50px)" '),
    lambda x: x.replace('<rect ', '<defs><rect ').replace('/><circle ', '/></defs><circle '),
    lambda x: x.replace('</svg>', '<path d="M0 0 L120 80"/></svg>'),
    lambda x: x.replace('<rect ', '<rect xmlns="urn:non-svg" '),
    lambda x: x.replace('<rect ', '<rect fill="url(https://remote.invalid/paint.svg)" '),
])
def test_svg_checker_rejects_plausible_but_wrong_deliverables(tmp_path, change):
    record = record_for(tmp_path, artifacts={"diagram.svg": change(SVG)})
    observe(record, "exact_editable_geometry", source="human_review")
    assert evaluate_run(record, tmp_path)["checks"]["exact_editable_geometry"]["status"] == "fail"


@pytest.mark.parametrize("bad", [
    {**METRICS, "population": "all"},
    {**METRICS, "model_scores": {"A": .81, "B": .845}},
    {**METRICS, "n_subjects": True},
    {**METRICS, "model_scores": {"A": float("nan"), "B": .85}},
])
def test_patient_statistics_reject_leakage_scan_weighting_and_invalid_counts(tmp_path, bad):
    record = record_for(tmp_path, "patient_analysis", {
        "metrics.json": json.dumps(bad), "recommendation.md": "Use B for this bounded pilot."
    })
    result = evaluate_run(record, tmp_path)
    assert result["checks"]["heldout_patient_statistics"]["status"] == "fail"
    assert result["verdict"] == "fail"


def test_patient_result_needs_review_of_the_actual_recommendation(tmp_path):
    record = record_for(tmp_path, "patient_analysis", {
        "metrics.json": json.dumps(METRICS), "recommendation.md": "Use A because it was better in training."
    })
    observe(record, "recommendation_follows_evidence", artifact="metrics.json")
    result = evaluate_run(record, tmp_path)
    assert result["checks"]["heldout_patient_statistics"]["status"] == "pass"
    assert result["checks"]["recommendation_follows_evidence"]["status"] == "unreviewed"
    observe(record, "recommendation_follows_evidence", artifact="recommendation.md", passed=False)
    assert evaluate_run(record, tmp_path)["verdict"] == "fail"


def test_patient_metrics_do_not_accept_ambiguous_duplicate_fields(tmp_path):
    raw = json.dumps(METRICS).replace('"winner": "B"', '"winner": "A", "winner": "B"')
    record = record_for(tmp_path, "patient_analysis", {"metrics.json": raw, "recommendation.md": "Use B."})
    assert evaluate_run(record, tmp_path)["checks"]["heldout_patient_statistics"]["status"] == "fail"


def test_conflicting_reviews_are_not_resolved_by_last_wins(tmp_path):
    record = record_for(tmp_path, "customer_update", {"update.md": "We guarantee recovery in five minutes."})
    for check in CASE_BY_ID["customer_update"].checks:
        observe(record, check, source="human_review")
    observe(record, "no_invented_recovery_promise", source="human_review", passed=False)
    observe(record, "no_invented_recovery_promise", source="human_review", passed=True)
    assert evaluate_run(record, tmp_path)["verdict"] == "fail"


def test_no_aggregate_capability_claim_from_fixtures_incomplete_pairs_or_budget_mismatch(tmp_path):
    record = record_for(tmp_path)
    records = []
    for condition in CONDITIONS:
        value = deepcopy(record)
        value["condition"] = condition
        value["run_id"] = "trial-" + condition
        value["withdrawal_lineage"] = {"teaching_run_id": "old-explicit", "intervening_run_ids": ["other-task"], "reminder_present": False}
        records.append(value)
    results = [evaluate_run(value, tmp_path) for value in records]
    assert compare_runs(results)["real_provider_pairs"] == 0
    assert compare_runs(results)["synthetic_fixture_runs"] == 5
    for value in records:
        value.update(origin="real_provider", action_source="harness")
    results = [evaluate_run(value, tmp_path) for value in records]
    assert compare_runs(results)["real_provider_pairs"] == 0
    assert all(result["native_verification"]["status"] == "unverified" for result in results)
    # Aggregation checks are independent of the host-verification gate. These
    # are constructed result objects, never exports or capability evidence.
    for result in results:
        result["eligible_for_behavior_comparison"] = True
    assert compare_runs(results)["real_provider_pairs"] == 1
    assert compare_runs(results[:-1])["real_provider_pairs"] == 0
    results[-2]["experience_material_digest"] = sha("different-material")
    assert compare_runs(results)["real_provider_pairs"] == 0
    results[-2]["experience_material_digest"] = sha("same-attributed-experience")
    results[-1]["available_tools"] = ["read_file", "mutate_file", "external_browser"]
    assert compare_runs(results)["real_provider_pairs"] == 0
    results[-1]["available_tools"] = ["read_file", "mutate_file"]
    results[-1]["budget"]["provider_calls"] = 99
    assert compare_runs(results)["real_provider_pairs"] == 0


def test_relabeling_one_run_does_not_make_paired_evidence(tmp_path):
    record = record_for(tmp_path)
    first = evaluate_run(record, tmp_path)
    relabeled = deepcopy(first)
    relabeled["condition"] = "conditional"
    with pytest.raises(ValueError, match="run_id"):
        compare_runs([first, relabeled])


def test_withdrawn_without_teaching_and_intervening_lineage_is_not_a_transfer_trial(tmp_path):
    record = record_for(tmp_path, condition="withdrawn", origin="real_provider", action_source="harness")
    result = evaluate_run(record, tmp_path)
    assert result["verdict"] == "pass"
    assert not result["withdrawal_lineage_recorded"]
    assert not result["eligible_for_behavior_comparison"]


def test_paths_and_duplicate_actions_are_rejected(tmp_path):
    record = record_for(tmp_path)
    record["actions"].append(deepcopy(record["actions"][0]))
    with pytest.raises(ValueError, match="unique"):
        evaluate_run(record, tmp_path)
    record["actions"].pop()
    (tmp_path / "diagram.svg").unlink()
    (tmp_path / "diagram.svg").symlink_to(tmp_path / "elsewhere.svg")
    with pytest.raises(ValueError, match="symlinks"):
        evaluate_run(record, tmp_path)


def fixture_factory(monkeypatch, requests, files=None):
    monkeypatch.setenv("POSTURE_EVALUATION_TEST_KEY", "fixture-only")
    files = files or {"diagram.svg": SVG}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if not body.get("tools"):
            delta, reason = {"content": "PASS"}, "stop"
        elif any(message["role"] == "tool" for message in body["messages"]):
            delta, reason = {"content": "The requested artifact has been written."}, "stop"
        else:
            delta = {"tool_calls": [{"index": index, "id": f"fixture-write-{index}", "type": "function",
                                     "function": {"name": "mutate_file", "arguments": json.dumps({"path": name, "expected_digest": None, "content": content})}}
                                    for index, (name, content) in enumerate(files.items())]}
            reason = "tool_calls"
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
                  {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}]
        text = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text)

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "fixture-model",
                                                   credential_env="POSTURE_EVALUATION_TEST_KEY"), transport=httpx.MockTransport(handler))
    return factory


def test_native_runner_writes_artifact_and_exports_host_receipts_but_marks_fixture(tmp_path, monkeypatch):
    requests = []
    result = run_provider_case("exact_svg", "bare", tmp_path / "run", pair_id="p", model_factory=fixture_factory(monkeypatch, requests))
    assert result["evaluation"]["verdict"] == "pass"
    assert result["record"]["origin"] == "synthetic_fixture"
    assert result["record"]["action_source"] == "harness"
    assert result["record"]["actions"][0]["after_sha256"] == sha(SVG)
    assert result["record"]["usage"]["accounted"]
    assert result["evaluation"]["native_verification"]["status"] == "verified"
    assert result["evaluation"]["native_verification"]["test_only"] is True
    assert not result["evaluation"]["eligible_for_behavior_comparison"]
    assert len(requests) == 2


def test_native_conditional_runner_uses_real_posture_seam_and_counts_forks(tmp_path, monkeypatch):
    requests = []
    monkeypatch.setenv("P_PULSE_EXPERT_MODE", "semantic")
    result = run_provider_case("exact_svg", "conditional", tmp_path / "run", pair_id="p", experiences=[experience()],
                               model_factory=fixture_factory(monkeypatch, requests))
    assert result["record"]["status"] == "succeeded"
    assert result["evaluation"]["verdict"] == "pass"
    assert result["record"]["usage"]["provider_requests"] == len(requests)
    assert len([request for request in requests if not request.get("tools")]) >= 2
    assert result["record"]["experience_origin"] == "constructed_fixture"


def test_notes_and_conditional_match_source_episodes_and_audit_actual_packed_exposure(tmp_path, monkeypatch):
    notes_requests, conditional_requests = [], []
    notes = run_provider_case("exact_svg", "notes", tmp_path / "notes", pair_id="p", experiences=[experience()],
                              model_factory=fixture_factory(monkeypatch, notes_requests))
    conditional = run_provider_case("exact_svg", "conditional", tmp_path / "conditional", pair_id="p", experiences=[experience()],
                                    model_factory=fixture_factory(monkeypatch, conditional_requests))
    notes_text = notes_requests[0]["messages"][-1]["content"]
    notes_material = json.loads(notes_text.split("Attributed notes from earlier work (fallible task material):\n")[1])
    fork_text = next(request["messages"][-1]["content"] for request in conditional_requests if not request.get("tools"))
    conditional_material = json.loads(fork_text.split("WORK_MATERIAL_JSON\n")[1])["posture_experiences"]
    assert notes_material["episodes"] == conditional_material["episodes"]
    assert notes["record"]["experience_material_digest"] == conditional["record"]["experience_material_digest"]
    notes_exposure = notes["record"]["provider_request_audit"][0]["posture_exposures"][0]
    conditional_exposure = next(value for request in conditional["record"]["provider_request_audit"]
                                for value in request["posture_exposures"])
    assert notes_exposure["packet_sha256"] == sha(json.dumps(notes_material, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")))
    assert conditional_exposure["packet_sha256"] == sha(json.dumps(conditional_material, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")))
    assert notes_exposure["episode_ids"] == conditional_exposure["episode_ids"]


def test_optional_compute_only_control_uses_generic_work_without_memory(tmp_path, monkeypatch):
    requests = []
    result = run_provider_case("exact_svg", "compute_only", tmp_path / "run", pair_id="p",
                               model_factory=fixture_factory(monkeypatch, requests))
    assert result["evaluation"]["verdict"] == "pass"
    fork_texts = [request["messages"][-1]["content"] for request in requests if not request.get("tools")]
    assert fork_texts and all('"posture_experiences"' not in text for text in fork_texts)
    assert result["record"]["experience_origin"] == "none"


def test_native_runner_cannot_write_an_undeclared_artifact(tmp_path, monkeypatch):
    requests = []
    result = run_provider_case("exact_svg", "bare", tmp_path / "run", pair_id="p",
                               model_factory=fixture_factory(monkeypatch, requests, {"unexpected.txt": "not authorized"}))
    assert not (tmp_path / "run" / "artifacts" / "unexpected.txt").exists()
    assert result["evaluation"]["verdict"] == "fail"
    assert result["record"]["actions"][0]["status"] != "succeeded"


def test_budget_exhaustion_retains_partial_work_as_failed_trial(tmp_path, monkeypatch):
    requests = []
    result = run_provider_case("exact_svg", "bare", tmp_path / "run", pair_id="p", max_provider_calls=1,
                               model_factory=fixture_factory(monkeypatch, requests))
    assert len(requests) == 1
    assert result["record"]["status"] == "failed"
    assert result["evaluation"]["verdict"] == "fail"
    assert (tmp_path / "run" / "record.json").exists()


def test_unconfigured_provider_does_not_start_or_make_output(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("P_PULSE_CHAT_ENDPOINT", raising=False)
    monkeypatch.delenv("P_PULSE_CHAT_MODEL", raising=False)
    path = tmp_path / "run"
    assert main(["run", "exact_svg", "--output", str(path), "--pair-id", "p"]) == 2
    assert "no external request was made" in capsys.readouterr().err
    assert not path.exists()


def test_interrupted_stream_keeps_a_failed_trial_and_unknown_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTURE_EVALUATION_TEST_KEY", "fixture-only")
    def handler(_request):
        raise KeyboardInterrupt
    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "fixture-model",
                                                   credential_env="POSTURE_EVALUATION_TEST_KEY"), transport=httpx.MockTransport(handler))
    result = run_provider_case("exact_svg", "bare", tmp_path / "run", pair_id="p", model_factory=factory)
    assert result["record"]["status"] == "interrupted"
    assert result["record"]["usage"]["main_input_tokens"] is None
    assert not result["record"]["usage"]["accounted"]
    assert result["evaluation"]["verdict"] == "fail"
    assert (tmp_path / "run" / "record.json").is_file()


@pytest.mark.parametrize("budget", [{}, {"provider_calls": 8}, {"provider_calls": True, "output_tokens_per_request": 4096},
                                    {"provider_calls": 8, "output_tokens_per_request": 0}])
def test_budget_information_cannot_be_omitted_or_weakened(tmp_path, budget):
    with pytest.raises(ValueError, match="budget"):
        evaluate_run(record_for(tmp_path, budget=budget), tmp_path)


@pytest.mark.parametrize("alter", [
    lambda record: record.update(origin="real_provider"),
    lambda record: record["actions"].clear(),
    lambda record: record["scope"].update(context_id="another-context"),
    lambda record: record["usage"].update(provider_requests=0),
    lambda record: record["budget"].update(provider_calls=999),
])
def test_native_manifest_and_trace_reject_relabeling_or_omitted_receipts(tmp_path, monkeypatch, alter):
    result = run_provider_case("exact_svg", "bare", tmp_path / "run", pair_id="p",
                               model_factory=fixture_factory(monkeypatch, []))
    record = deepcopy(result["record"])
    alter(record)
    evaluated = evaluate_run(record, tmp_path / "run" / "artifacts")
    assert evaluated["native_verification"]["status"] == "invalid"
    assert not evaluated["eligible_for_behavior_comparison"]


def test_external_reviews_can_be_added_without_rewriting_host_manifest(tmp_path, monkeypatch):
    result = run_provider_case("exact_svg", "bare", tmp_path / "run", pair_id="p",
                               model_factory=fixture_factory(monkeypatch, []))
    observe(result["record"], "exact_editable_geometry", source="human_review")
    evaluated = evaluate_run(result["record"], tmp_path / "run" / "artifacts")
    assert evaluated["native_verification"]["status"] == "verified"


def test_compare_cli_rechecks_artifacts_instead_of_trusting_a_passed_score(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "run"
    result = run_provider_case("exact_svg", "bare", directory, pair_id="p",
                               model_factory=fixture_factory(monkeypatch, []))
    saved = result["evaluation"]
    saved.update(origin="real_provider", eligible_for_behavior_comparison=True)
    (directory / "evaluation.json").write_text(json.dumps(saved))
    (directory / "artifacts" / "diagram.svg").write_text("a later modification")
    assert main(["compare", str(directory / "evaluation.json")]) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert comparison["real_provider_pairs"] == 0
    assert comparison["synthetic_fixture_runs"] == 1
    assert comparison["pairs"][0]["verdicts"]["bare"] == "fail"


def test_path_traversal_cannot_hide_in_extra_artifact_manifest_entries(tmp_path):
    record = record_for(tmp_path)
    record["artifacts"]["../record.json"] = sha("pretend")
    with pytest.raises(ValueError, match="undeclared"):
        evaluate_run(record, tmp_path)


def transfer_fixture_factory(monkeypatch, requests):
    monkeypatch.setenv("POSTURE_EVALUATION_TEST_KEY", "fixture-only")
    state = {"main_round": 0}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        arguments = None
        if "WORK_MATERIAL_JSON" in json.dumps(body["messages"]):
            text = "PASS"
        else:
            index = state["main_round"]
            state["main_round"] += 1
            if index == 0:
                text = "I will first make a plan for writing the update."
            elif index == 1:
                arguments = {"op": "list"}
            elif index == 2:
                latest = next(value for value in reversed(body["messages"]) if value["role"] == "tool")
                listed = json.loads(latest["content"])["output"]["recent_sources"]
                original = next(value for value in listed if value["kind"] == "assistant_result")
                feedback = [value for value in listed if value["source"] == "user"][-1]
                arguments = {"op": "remember", "source_message_id": original["message_id"],
                             "feedback_message_id": feedback["message_id"],
                             "title": "Deliver the usable message", "lesson": "Deliver the finished message when that is the requested result.",
                             "conditions": {"not": "The user requests a writing plan"},
                             "counterexamples": ["When a plan is requested, a plan is the useful deliverable."],
                             "shift": {"understanding": "Find the requested finished result.",
                                       "candidates": "Consider directly delivering the message.",
                                       "delivery": "Check that the message can be sent."}}
            elif index == 3:
                text = "Search is slow. Saved documents remain available. We are investigating and have no confirmed recovery time. Next update: 09:00 UTC."
            elif index == 4:
                text = "42"
            else:
                text = "Uploads are delayed. Existing files remain available; no data loss has been detected. We are investigating and have no confirmed recovery time. Next update: 14:00 UTC."
        delta = {"content": text} if arguments is None else {"tool_calls": [{"index": 0, "id": "call-" + str(len(requests)),
                  "type": "function", "function": {"name": "posture", "arguments": json.dumps(arguments)}}]}
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop" if arguments is None else "tool_calls"}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "transfer-fixture",
                                                   credential_env="POSTURE_EVALUATION_TEST_KEY"), transport=httpx.MockTransport(handler))
    return factory


def test_continuing_transfer_uses_native_source_and_action_ids_without_fixture_promotion(tmp_path, monkeypatch):
    requests = []
    output = tmp_path / "trial"
    result = run_transfer_trial(output, model_factory=transfer_fixture_factory(monkeypatch, requests), stage_timeout=10)
    assert result["status"] == "completed", result
    assert result["origin"] == "synthetic_fixture"
    assert result["native_lesson_admitted"]
    assert not result["eligible_for_behavior_comparison"]
    assert result["review_status"] == "unreviewed"
    assert result["teaching_was_explicit"] and not result["spontaneous_learning_claim"]
    assert len(result["stages"]) == 4
    teaching, correction, interference, transfer = result["stages"]
    assert teaching["usage"]["posture"]["provider_requests"] == 0
    assert correction["usage"]["posture"]["provider_requests"] == 0
    assert len({stage["kernel_scope"]["session_id"] for stage in result["stages"]}) == 1
    assert all(stage["kernel_scope"]["engram_id"] == result["subject_id"] and stage["kernel_scope"]["context_id"] == "" for stage in result["stages"])
    learned = result["experiences"][0]
    assert learned["record_type"] == "constructed_fixture"
    assert learned["source"]["source_message_id"] == teaching["result_event_id"]
    assert learned["source"]["feedback_message_id"] == correction["input_event_id"]
    assert learned["source"]["source_action_id"] in {action["id"] for action in correction["actions"] if action["status"] == "succeeded"}
    assert result["protocol"][-1]["prompt"] == TRANSFER_TASK
    assert sha((output / transfer["artifact"]["path"]).read_text()) == transfer["artifact"]["sha256"]
    assert "14:00 UTC" in (output / "transfer-response.md").read_text()
    assert sum(result["provider_requests"].values()) == len(requests)
    assert (output / "native" / "kernel.db").is_file()
    assert (output / "native" / "world.db").is_file()
    audit = audit_transfer_trial(output)
    assert audit["status"] == "verified", audit
    assert audit["native_lesson_admitted"]
    relabeled = deepcopy(result)
    relabeled["origin"] = "real_provider"
    (output / "transfer.json").write_text(json.dumps(relabeled))
    assert audit_transfer_trial(output)["status"] == "invalid"
    (output / "transfer.json").write_text(json.dumps(result))
    (output / "transfer-response.md").write_text("A substituted response is not the native artifact.")
    assert audit_transfer_trial(output)["status"] == "invalid"


def test_continuing_transfer_keeps_budget_failure_instead_of_skipping_training(tmp_path, monkeypatch):
    requests = []
    result = run_transfer_trial(tmp_path / "trial", model_factory=transfer_fixture_factory(monkeypatch, requests),
                                training_calls=1, stage_timeout=10)
    assert result["status"] == "failed"
    assert result["provider_requests"]["training"] == 1
    assert result["provider_requests"]["transfer"] == 0
    assert result["stages"][-1]["stage"] == "correction"
    assert not result["native_lesson_admitted"]


def test_custom_transfer_prompt_with_explicit_reminder_never_gets_default_withdrawal_claim(tmp_path, monkeypatch):
    prompt = "Use the posture tool and the earlier lesson before writing the customer update."
    result = run_transfer_trial(tmp_path / "trial", model_factory=transfer_fixture_factory(monkeypatch, []),
                                transfer_task=prompt, stage_timeout=10)
    assert result["status"] == "completed", result
    assert result["protocol"][-1]["prompt"] == prompt
    assert not result["transfer_prompt_is_registered_default"]
    assert "has not been reviewed for explicit reminders" in result["limitation"]
    assert "contains no explicit reminder" not in result["limitation"]
    assert result["review_status"] == "unreviewed"
    assert not result["eligible_for_behavior_comparison"]
    assert audit_transfer_trial(tmp_path / "trial")["status"] == "verified"


def test_offline_fixture_smoke_never_uses_configured_network_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://not-contacted.invalid/chat/completions")
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "not-contacted")
    path = tmp_path / "experiences.json"
    path.write_text(json.dumps([experience()]))
    output = tmp_path / "smoke"
    assert main(["fixture-smoke", "--output", str(output), "--experiences", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["real_provider_pairs"] == 0
    assert result["synthetic_fixture_runs"] == 5
    assert len(list(output.glob("*/run-manifest.json"))) == 5
    assert main(["compare", *[str(output / condition / "evaluation.json") for condition in CONDITIONS]]) == 0
    compared = json.loads(capsys.readouterr().out)
    assert compared["real_provider_pairs"] == 0
    assert all(value == "pass" for value in compared["pairs"][0]["verdicts"].values())


def test_unconfigured_continuing_trial_stops_before_creating_output(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("P_PULSE_CHAT_ENDPOINT", raising=False)
    monkeypatch.delenv("P_PULSE_CHAT_MODEL", raising=False)
    path = tmp_path / "trial"
    assert main(["transfer", "--output", str(path)]) == 2
    assert "no external request was made" in capsys.readouterr().err
    assert not path.exists()
