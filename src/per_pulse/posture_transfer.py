"""Ordinary-feedback transfer through fresh native Work contexts.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This experiment records whether the model elects to learn from a preference
clarification. It never constructs a lesson on the model's behalf. Response
quality remains for review; native consistency and exposure are checked here.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time

from .posture_evaluation import (
    _audit_request, _canonical, _digest, _read_artifact, _readonly_trace, _record_digest,
)


SCHEMA = "per_pulse.ordinary_posture_transfer.v1"
SOURCE_MARKER = "internal-incident-larch-4821"
SOURCE_TASK = (
    "Write a short customer update: search is slow, saved documents remain "
    "available, the cause is being investigated, no recovery time is confirmed, "
    "and the next update is at 09:00 UTC. The internal incident reference is "
    + SOURCE_MARKER + "; keep that reference out of the customer message."
)
ORDINARY_FEEDBACK = (
    "For customer notices, I prefer to lead with what people can still use, "
    "then state the disruption and the next update. Give me the finished "
    "message. That preference does not apply when I ask for an outline or "
    "a writing plan. Please apply it to this update."
)
TRANSFER_TASK = (
    "Write a concise customer update: uploads are delayed; existing files "
    "can still be viewed; no data loss has been detected; the cause is under "
    "investigation; no recovery time is confirmed; the next update is at 14:00 UTC."
)
NEGATIVE_TASK = (
    "Give me a short outline for a customer incident update, with notes on "
    "what belongs in each section. I am planning the structure; do not write "
    "the finished customer message yet."
)
STAGES = (
    ("source", "training", SOURCE_TASK),
    ("feedback", "training", ORDINARY_FEEDBACK),
    ("bare_transfer", "bare_transfer", TRANSFER_TASK),
    ("bare_negative", "bare_negative", NEGATIVE_TASK),
    ("conditional_transfer", "conditional_transfer", TRANSFER_TASK),
    ("conditional_negative", "conditional_negative", NEGATIVE_TASK),
)


TASK_ORDERS = ("notice-first", "outline-first")


def trial_stages(task_order: str = "notice-first") -> tuple[tuple[str, str, str], ...]:
    """Counterbalance notice/outline order *within* both memory phases.

    Source/feedback must precede baseline, and explicit sharing must precede
    conditional trials. This cannot counterbalance baseline-versus-sharing
    order or equalize the extra posture computations.
    """
    if task_order not in TASK_ORDERS:
        raise ValueError("task_order must be notice-first or outline-first")
    if task_order == "notice-first":
        return STAGES
    return (STAGES[0], STAGES[1], STAGES[3], STAGES[2], STAGES[5], STAGES[4])


def _provider_identity(adapter):
    return {
        "adapter_id": adapter.capabilities.adapter_id,
        "endpoint_sha256": _digest(adapter.config.endpoint.encode()),
        "model": adapter.config.model,
        "reasoning_field": adapter.config.reasoning_field,
        "provider_namespace": adapter.config.provider_namespace,
        "output_tokens_per_request": adapter.config.max_output_tokens,
    }


def _message_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _message_texts(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _message_texts(item)


def _exposure_summary(record, requests):
    """Inspect saved exact provider inputs, including side computations."""
    shared = {(item["id"], item["revision"]) for item in record.get("shared_lessons", [])}
    result = {}
    for name, _, _ in STAGES[2:]:
        selected = [item for item in requests if item["stage"] == name]
        offered = set()
        source_hits = set()
        private_episode_fields = False
        for item in selected:
            text = "\n".join(_message_texts(item["request"].get("messages", [])))
            sources = [("source_marker", SOURCE_MARKER), ("source_task", SOURCE_TASK),
                       ("raw_feedback", ORDINARY_FEEDBACK)]
            if len(record.get("source_result_text", "")) >= 32:
                sources.append(("source_result", record["source_result_text"]))
            for label, raw in sources:
                if raw in text or json.dumps(raw, ensure_ascii=False)[1:-1] in text:
                    source_hits.add(label)
            for message in item["request"].get("messages", []):
                content = message.get("content", "")
                if not isinstance(content, str) or "WORK_MATERIAL_JSON\n" not in content:
                    continue
                try:
                    packet = json.loads(content.split("WORK_MATERIAL_JSON\n", 1)[1]).get("posture_experiences")
                    for episode in (packet or {}).get("episodes", []):
                        offered.add((episode["id"], episode["revision"]))
                        if (episode.get("encounter_available") or "encounter" in episode
                                or "evidence" in episode.get("source", {})
                                or any("evidence" in feedback or "source" in feedback
                                       for feedback in episode.get("feedback", []))):
                            private_episode_fields = True
                except (ValueError, TypeError, AttributeError):
                    private_episode_fields = True
        result[name] = {
            "requests": len(selected),
            "offered_lessons": [{"id": key, "revision": revision} for key, revision in sorted(offered)],
            "source_text_hits": sorted(source_hits),
            "private_episode_fields_present": private_episode_fields,
            "expected_memory_exposure": bool(selected) and (offered == shared if name.startswith("conditional") else not offered),
            "source_transcript_absent": bool(selected) and not source_hits and not private_episode_fields,
        }
    return result


def run_ordinary_transfer(
    output: Path, *, model_factory=None, training_calls: int = 12,
    trial_calls: int = 8, stage_timeout: float = 180,
    task_order: str = "notice-first",
) -> dict:
    """Compare fresh contexts before and after explicit sharing of a native lesson.

    The baseline is deliberately run first while the lesson is context-local.
    Sharing is an operator experiment action, not spontaneous generalization.
    No-memory and conditional targets have separate empty native histories.
    """
    from per_pulse.service import PageService
    from pulse_harness.adapters.chat_http import model_factory_from_env
    from pulse_harness.core import HarnessScope
    from pulse_harness.core.errors import ProviderUnavailable, ScopeConflict
    from pulse_system.service.expert_computation import recorded_expert_usage

    if any(type(value) is not int or not 1 <= value <= 64 for value in (training_calls, trial_calls)):
        raise ValueError("call ceilings must be integers between 1 and 64")
    if type(stage_timeout) not in (float, int) or not 1 <= stage_timeout <= 1800:
        raise ValueError("stage_timeout must be between 1 and 1800 seconds")
    stages = trial_stages(task_order)
    factory = model_factory if model_factory is not None else model_factory_from_env()
    if factory is None:
        raise ValueError("No provider configured; no external request was made. Configure the native P_PULSE provider environment explicitly.")
    probe = factory(HarnessScope("ordinary-posture-transfer", "config", "config", 0, "config"))
    try:
        probe.preflight_stream()
        identity, test_only = _provider_identity(probe), probe.capabilities.test_only
    finally:
        probe.close()
    if output.exists():
        raise ValueError("output directory must be new")

    ceilings = {partition: training_calls if partition == "training" else trial_calls for _, partition, _ in stages}
    lock = threading.Lock()
    accounting = {"stage": "source", "partition": "training", "counts": dict.fromkeys(ceilings, 0), "requests": []}

    def budgeted_factory(scope):
        adapter = factory(scope)
        if _provider_identity(adapter) != identity or adapter.capabilities.test_only is not test_only:
            adapter.close()
            raise ValueError("provider configuration or origin changed during the experiment")
        original_stream = adapter.stream

        def stream(request, *, cancel_event=None):
            with lock:
                partition = accounting["partition"]
                if accounting["counts"][partition] >= ceilings[partition]:
                    raise ProviderUnavailable("ordinary-transfer call ceiling reached", code="evaluation_budget_exhausted")
                accounting["counts"][partition] += 1
                # This is the exact credential-free ModelRequest, not HTTP headers.
                accounting["requests"].append({"stage": accounting["stage"], "budget_partition": partition,
                                               "request": request.as_dict(), "audit": _audit_request(request)})
            return original_stream(request, cancel_event=cancel_event)

        adapter.stream = stream
        return adapter

    output.mkdir(parents=True, mode=0o700)
    (output / ".gitignore").write_text("*\n")
    protocol = [{"stage": name, "budget_partition": partition, "prompt": prompt,
                 "prompt_sha256": _digest(prompt.encode())} for name, partition, prompt in stages]
    record = {
        "schema": SCHEMA, "status": "running", "origin": "synthetic_fixture" if test_only else "real_provider",
        "provider_identity": identity, "budget": ceilings, "stage_timeout_seconds": stage_timeout,
        "protocol": protocol, "task_order": task_order, "stages": [], "learned_lessons": [], "shared_lessons": [],
        "capture_status": "pending", "review_status": "unreviewed", "eligible_for_behavior_comparison": False,
        "ordinary_feedback_has_learning_instruction": False,
        "sharing_policy": "Operator explicitly promotes only source-attributed native lessons after both no-memory targets settle.",
        "condition_order": [name for name, _, _ in stages[2:]],
        "limitations": [
            "Baseline runs before sharing; task-order reversal only counterbalances notice versus outline within each phase.",
            "Conditional trials add posture computations; equal request ceilings do not match realized compute.",
            "Explicit operator sharing tests abstraction transfer, not spontaneous cross-page promotion.",
            "Absence checks cover exact source text and private packet fields, not all semantic paraphrases.",
            "Native capture and request exposure do not score the usefulness of the later response.",
        ],
    }
    manifest = {"schema": SCHEMA, "protocol": protocol, "task_order": task_order, "budget": ceilings, "provider_identity": identity,
                "adapter_test_only": test_only}
    manifest_path = output / "ordinary-transfer-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    def save():
        with lock:
            record["provider_requests"] = dict(accounting["counts"])
            requests = list(accounting["requests"])
        request_bytes = (_canonical(requests) + "\n").encode()
        (output / "provider-requests.json").write_bytes(request_bytes)
        record["request_snapshot_sha256"] = _digest(request_bytes)
        record["exposure_checks"] = _exposure_summary(record, requests)
        (output / "ordinary-transfer.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")

    async def conduct():
        pages = PageService(output / "native", posture=True, model_factory=budgeted_factory)
        runtime = pages.runtime
        record.update(world_id=runtime.world_id, subject_id=runtime.continuity_engram_id)
        source_page = None
        try:
            await pages.start()
            for name, partition, prompt in stages:
                if name == stages[4][0]:
                    for lesson in record["learned_lessons"]:
                        promoted = pages.learning.revise(source_page, lesson["id"], lesson["revision"], {"scope": "subject"})["experience"]
                        record["shared_lessons"].append({"id": promoted["id"], "revision": promoted["revision"],
                                                         "previous_revision": lesson["revision"], "scope": promoted["scope"]})
                    save()
                with lock:
                    accounting.update(stage=name, partition=partition)
                if name == "feedback":
                    page_id = source_page
                    input_id = pages.send(page_id, prompt)["messageId"]
                else:
                    # Page metadata can enter native work material. Paired
                    # conditions therefore get the same ordinary task title.
                    title = "Update outline" if name.endswith("negative") else "Customer update"
                    created = pages.create_page(title, prompt)
                    page_id, input_id = created["page"]["id"], created["initialMessageId"]
                    if name == "source":
                        source_page = page_id
                        record["source_page_id"] = source_page
                front = runtime.world.get_task_front(page_id)
                entry = {"stage": name, "budget_partition": partition, "page_id": page_id,
                         "context_id": "work:" + front.center_id, "center_id": front.center_id,
                         "input_event_id": input_id, "prompt_sha256": _digest(prompt.encode()), "status": "pending"}
                record["stages"].append(entry)
                save()
                deadline, turn = time.monotonic() + stage_timeout, None
                while time.monotonic() < deadline:
                    candidates = [value for value in runtime.causal_ledger.list_turns()
                                  if value.event_id == input_id and value.state.value != "running"]
                    if candidates:
                        turn = candidates[-1]
                        break
                    await asyncio.sleep(.05)
                if turn is None:
                    entry["status"] = "timeout"
                    record["status"] = "failed"
                    break
                store, scope = runtime.kernel_service.store, None
                for session in store.sessions_for_world(runtime.world_id, engram_ids=(turn.engram_id,)):
                    if turn.session_id and session.scope.session_id != turn.session_id:
                        continue
                    try:
                        candidate = session.scope.for_turn(turn.id)
                        store.get_turn(candidate)
                        scope = candidate
                        break
                    except ScopeConflict:
                        continue
                entry.update(status=turn.state.value, world_turn_id=turn.id, result_event_id=turn.result_event_id,
                             subject_id=turn.engram_id, error_code=turn.error_code)
                if scope is None:
                    entry["native_trace_error"] = "No matching Kernel turn was admitted."
                    record["status"] = "failed"
                    break
                entry["kernel_scope"] = scope.as_dict()
                entry["actions"] = [{"id": action.action_request_id, "tool": action.tool_name,
                                     "status": action.status.value, "arguments_digest": action.args_digest}
                                    for action in store.actions_for_turn(scope)]
                rounds = list(store.model_rounds_for_turn(scope))
                main_accounted = all(value.provider_usage is not None for value in rounds)
                posture_usage, posture_accounted = recorded_expert_usage(store, scope, trace=runtime.harness.expert.trace)
                entry["usage"] = {
                    "main_provider_rounds": len(rounds),
                    "main_input_tokens": sum(value.provider_usage["input_tokens"] for value in rounds) if main_accounted else None,
                    "main_output_tokens": sum(value.provider_usage["output_tokens"] for value in rounds) if main_accounted else None,
                    "posture": posture_usage, "accounted": main_accounted and posture_accounted,
                }
                if turn.result_event_id:
                    result_event = runtime.causal_ledger.get_event(turn.result_event_id)
                    raw = (result_event.content or "").encode()
                    if name == "source":
                        record["source_result_text"] = result_event.content or ""
                    filename = name.replace("_", "-") + "-response.md"
                    (output / filename).write_bytes(raw)
                    entry["artifact"] = {"path": filename, "sha256": _digest(raw), "source": "native_assistant_result"}
                if turn.state.value != "settled":
                    record["status"] = "failed"
                    break
                if name == "feedback":
                    # Runtime drains native staged captures after World settlement.
                    until = min(deadline, time.monotonic() + 2)
                    source_result = record["stages"][0]["result_event_id"]
                    while True:
                        lessons = runtime.harness.posture_memory.list_experiences(subject_id=runtime.continuity_engram_id)
                        record["learned_lessons"] = [value for value in lessons
                            if value["context_id"] == entry["context_id"] and value["scope"] == "context"
                            and value.get("source", {}).get("source_message_id") == source_result
                            and value.get("source", {}).get("feedback_message_id") == input_id
                            and value.get("source", {}).get("learning_turn_id") == turn.id
                            and value.get("source", {}).get("source_action_id") in {
                                action["id"] for action in entry["actions"]
                                if action["tool"] == "posture" and action["status"] == "succeeded"}]
                        if record["learned_lessons"] or time.monotonic() >= until:
                            break
                        await asyncio.sleep(.05)
                    record["capture_status"] = "native_lesson_admitted" if record["learned_lessons"] else "capture_absent"
                    if not record["learned_lessons"]:
                        record["status"] = "capture_absent"
                        break
                save()
            else:
                record["status"] = "completed"
            record["current_subject_id"] = runtime.continuity_engram_id
            record["experiences"] = runtime.harness.posture_memory.list_experiences(
                subject_id=runtime.continuity_engram_id, include_inactive=True)
        finally:
            await pages.close()
            save()

    try:
        asyncio.run(conduct())
    except KeyboardInterrupt:
        record["status"] = "interrupted"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
    save()
    manifest["record_digest"] = _record_digest(record)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return record


def audit_ordinary_transfer(output: Path) -> dict:
    """Reconcile exact provider exposure, native source/actions and response bytes."""
    from pulse_harness.core import HarnessScope
    from pulse_harness.plugins.expert_computation import fingerprint
    from pulse_system.service.expert_computation import _fork_scope

    errors = []
    record = json.loads((output / "ordinary-transfer.json").read_text())
    manifest = json.loads((output / "ordinary-transfer-manifest.json").read_text())
    if record.get("schema") != SCHEMA or manifest.get("record_digest") != _record_digest(record):
        errors.append("host_record_binding_mismatch")
    if record.get("status") not in {"completed", "capture_absent"}:
        return {"status": "incomplete", "run_status": record.get("status"), "errors": errors,
                "review_status": "unreviewed", "eligible_for_behavior_comparison": False}
    try:
        task_order = record.get("task_order", "notice-first")
        expected_stages = trial_stages(task_order)
        expected_protocol = [{"stage": name, "budget_partition": partition, "prompt": prompt,
                              "prompt_sha256": _digest(prompt.encode())} for name, partition, prompt in expected_stages]
        if (record["protocol"] != expected_protocol or manifest["protocol"] != expected_protocol
                or manifest.get("task_order", "notice-first") != task_order
                or record.get("condition_order") != [name for name, _, _ in expected_stages[2:]]):
            errors.append("predeclared_protocol_mismatch")
        if record["provider_identity"] != manifest["provider_identity"] or record["budget"] != manifest["budget"]:
            errors.append("host_configuration_mismatch")
        raw, problem = _read_artifact(output, "provider-requests.json", record["request_snapshot_sha256"])
        if problem:
            raise ValueError(problem)
        requests = json.loads(raw)
        counts = Counter(item["budget_partition"] for item in requests)
        if any(type(record["provider_requests"].get(key)) is not int
               or record["provider_requests"][key] != counts[key]
               or not 0 <= counts[key] <= ceiling for key, ceiling in record["budget"].items()):
            errors.append("provider_count_or_budget_mismatch")
        if set(counts) - set(record["budget"]):
            errors.append("unknown_budget_partition")
        for request in requests:
            if _digest(_canonical(request["request"]).encode()) != request["audit"]["request_sha256"]:
                errors.append("provider_snapshot_digest_mismatch")
        exposures = _exposure_summary(record, requests)
        if exposures != record["exposure_checks"]:
            errors.append("provider_exposure_projection_mismatch")
        if record["status"] == "completed" and not all(
                value["expected_memory_exposure"] and value["source_transcript_absent"] for value in exposures.values()):
            errors.append("transfer_exposure_mismatch")
        stages = record["stages"]
        expected_names = [name for name, _, _ in expected_stages][:2 if record["status"] == "capture_absent" else 6]
        if [stage["stage"] for stage in stages] != expected_names:
            raise ValueError("completed stage sequence does not match outcome")
        if stages[0]["context_id"] != stages[1]["context_id"] or len({value["context_id"] for value in stages}) != len(stages) - 1:
            errors.append("target_contexts_are_not_fresh_and_distinct")
        native_requests, completed_requests, flags, sequences = [], [], [], []
        request_scopes = {}
        with closing(_readonly_trace(output / "native" / "world.db")) as world, closing(_readonly_trace(output / "native" / "kernel.db")) as kernel:
            previous_subject = record["subject_id"]
            for stage, declared in zip(stages, expected_protocol):
                scope = stage["kernel_scope"]
                successors = {row[0] for row in world.execute(
                    "WITH RECURSIVE lineage(id) AS (SELECT ? UNION SELECT g.successor_id FROM generation_transitions g JOIN lineage l ON g.predecessor_id=l.id WHERE g.state='committed') SELECT id FROM lineage",
                    (previous_subject,)).fetchall()}
                if scope["world_id"] != record["world_id"] or scope["engram_id"] not in successors or scope["context_id"] != stage["context_id"]:
                    errors.append("native_subject_or_context_mismatch")
                previous_subject = scope["engram_id"]
                incoming = world.execute("SELECT * FROM causal_events WHERE id=?", (stage["input_event_id"],)).fetchone()
                outgoing = world.execute("SELECT * FROM causal_events WHERE id=?", (stage["result_event_id"],)).fetchone()
                turn = world.execute("SELECT * FROM harness_turns WHERE id=?", (stage["world_turn_id"],)).fetchone()
                if incoming is None or outgoing is None or turn is None:
                    raise ValueError("native stage event or turn is missing")
                if incoming["source"] != "user" or incoming["kind"] != "stimulus" or incoming["content"] != declared["prompt"]:
                    errors.append("native_task_input_mismatch")
                if any(event["world_id"] != record["world_id"] or event["engram_id"] != scope["engram_id"]
                       or event["center_id"] != stage["center_id"] for event in (incoming, outgoing)):
                    errors.append("native_event_scope_mismatch")
                if (turn["event_id"] != incoming["id"] or turn["result_event_id"] != outgoing["id"]
                        or turn["state"] != "settled" or turn["session_id"] != scope["session_id"]
                        or outgoing["kind"] != "assistant_result" or outgoing["source"] != "self" or outgoing["status"] != "settled"):
                    errors.append("native_delivery_lineage_mismatch")
                sequences.extend((incoming["seq"], outgoing["seq"]))
                artifact = stage["artifact"]
                body, artifact_problem = _read_artifact(output, artifact["path"], artifact["sha256"])
                if artifact_problem or body != (outgoing["content"] or "").encode():
                    errors.append("native_response_artifact_mismatch")
                if stage["stage"] == "source" and record.get("source_result_text") != outgoing["content"]:
                    errors.append("native_source_result_mismatch")
                native_turn = kernel.execute("SELECT * FROM turns WHERE session_id=? AND turn_id=?", (scope["session_id"], scope["turn_id"])).fetchone()
                if native_turn is None or any(native_turn[key] != value for key, value in scope.items()) or native_turn["status"] != "succeeded":
                    errors.append("native_kernel_turn_mismatch")
                actions = kernel.execute("SELECT * FROM actions WHERE session_id=? AND turn_id=? ORDER BY created_at,action_request_id", (scope["session_id"], scope["turn_id"])).fetchall()
                actual = [{"id": action["action_request_id"], "tool": action["tool_name"], "status": action["status"], "arguments_digest": action["args_digest"]} for action in actions]
                if actual != stage["actions"]:
                    errors.append("native_action_receipts_mismatch")
                # Reconstruct the same source-bound advisor/reviewer scopes
                # used by native execution and accounting. Context names alone
                # do not distinguish the source and its later feedback turn.
                main_rounds = kernel.execute("SELECT round_id,round_index FROM model_rounds WHERE session_id=? AND turn_id=?", (scope["session_id"], scope["turn_id"])).fetchall()
                source_scope = HarnessScope(**scope)
                indices = {0, *(row["round_index"] for row in main_rounds)}
                indices.add(max(indices) + 1)
                identities = [source_scope.digest + (f":round:{index}" if index else "") for index in indices]
                identities += [source_scope.digest + ":posture-review:" + fingerprint(row["round_id"]) for row in main_rounds]
                request_scopes[stage["stage"]] = {_canonical(scope), *(_canonical(_fork_scope(source_scope, identity).as_dict()) for identity in identities)}
                if stage["stage"] not in {"source", "feedback"}:
                    user_inputs = world.execute("SELECT id FROM causal_events WHERE world_id=? AND center_id=? AND source='user' AND kind='stimulus'", (record["world_id"], stage["center_id"])).fetchall()
                    if [row[0] for row in user_inputs] != [stage["input_event_id"]]:
                        errors.append("target_has_additional_user_history")
            if sequences != sorted(set(sequences)):
                errors.append("native_stage_order_mismatch")
            rows = kernel.execute("SELECT request_digest,provider_evidence_json,status FROM model_rounds").fetchall()
            native_requests.extend(row["request_digest"] for row in rows)
            completed_requests.extend(row["request_digest"] for row in rows if row["status"] == "completed")
            flags.extend(json.loads(row["provider_evidence_json"]).get("test_only") for row in rows)
            state = world.execute("SELECT state FROM component_state WHERE component=?", ("per-pulse-posture-v1:" + record["world_id"],)).fetchone()
            journal = json.loads(state["state"])["experiences"] if state else {}
            lessons = record["learned_lessons"]
            if bool(lessons) != (record["capture_status"] == "native_lesson_admitted"):
                errors.append("native_capture_status_mismatch")
            for lesson in lessons:
                source = lesson["source"]
                action = kernel.execute("SELECT * FROM actions WHERE action_request_id=?", (source.get("source_action_id"),)).fetchone()
                if (action is None or action["tool_name"] != "posture" or action["status"] != "succeeded"
                        or action["turn_id"] != stages[1]["world_turn_id"]
                        or json.loads(action["arguments_json"]).get("op") != "remember"
                        or source.get("source_message_id") != stages[0]["result_event_id"]
                        or source.get("feedback_message_id") != stages[1]["input_event_id"]
                        or source.get("learning_turn_id") != stages[1]["world_turn_id"]
                        or lesson["context_id"] != stages[0]["context_id"] or lesson["scope"] != "context"):
                    errors.append("native_learning_source_mismatch")
                native = journal.get(lesson["id"])
                if native is None:
                    errors.append("native_lesson_missing")
                elif record["status"] == "completed":
                    expected = next((value for value in record["shared_lessons"] if value["id"] == lesson["id"]), None)
                    if expected is None or native["scope"] != "subject" or native["revision"] != expected["revision"]:
                        errors.append("native_sharing_mismatch")
                    elif (expected["previous_revision"] != lesson["revision"] or expected["revision"] != lesson["revision"] + 1
                          or any(native[key] != lesson[key] for key in ("id", "title", "lesson", "conditions", "counterexamples", "shift", "encounter", "source", "context_id", "record_type"))):
                        errors.append("native_promotion_changed_lesson_content")
            if any(journal.get(value["id"]) != value for value in record.get("experiences", [])):
                errors.append("native_experience_export_mismatch")
        sidecar = output / "native" / "kernel.db.expert.sqlite3.trace"
        if sidecar.exists():
            with closing(_readonly_trace(sidecar)) as trace:
                rows = trace.execute("SELECT request_digest,provider_evidence_json,status FROM model_rounds").fetchall()
                native_requests.extend(row["request_digest"] for row in rows)
                completed_requests.extend(row["request_digest"] for row in rows if row["status"] == "completed")
                flags.extend(json.loads(row["provider_evidence_json"]).get("test_only") for row in rows)
        expected_origin = record["origin"] == "synthetic_fixture"
        if not flags or any(flag is not expected_origin for flag in flags) or manifest["adapter_test_only"] is not expected_origin:
            errors.append("native_provider_origin_mismatch")
        audited = Counter(value["audit"]["request_sha256"] for value in requests)
        if audited - Counter(native_requests) or Counter(completed_requests) - audited:
            errors.append("native_request_audit_mismatch")
        partitions = {name: partition for name, partition, _ in expected_stages}
        for request in requests:
            if (request["stage"] not in request_scopes
                    or request["budget_partition"] != partitions.get(request["stage"])
                    or _canonical(request["request"]["scope"]) not in request_scopes.get(request["stage"], set())):
                errors.append("native_request_stage_mismatch")
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        errors.append("native_verification_error:" + str(exc))
    return {"status": "verified" if not errors else "invalid", "run_status": record.get("status"),
            "origin": record.get("origin"), "capture_status": record.get("capture_status"), "errors": errors,
            "review_status": "unreviewed", "eligible_for_behavior_comparison": False,
            "note": "Checks source, native requests and clean target exposure. Review actual responses for usefulness; host files are not authenticated against their filesystem owner."}
