"""Artifact-bound evaluation records for default-posture experiments.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The evaluate, compare and audit commands inspect observable work offline. The
run and transfer commands explicitly use the configured native provider. No
synthetic transport is evidence of model improvement. Browser and human reviews
remain attributed judgments; importing JSON does not establish their truth.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import sys
import threading
import time
from uuid import uuid4
from typing import Any
import xml.etree.ElementTree as ET


SCHEMA = "per_pulse.posture_evaluation.v1"
CONDITIONS = ("bare", "explicit", "notes", "conditional", "withdrawn")
OPTIONAL_CONTROLS = ("compute_only",)
OBSERVERS = frozenset({"browser_probe", "test_runner", "human_review"})
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    artifacts: tuple[str, ...]
    checks: tuple[str, ...]
    control: str
    review_guidance: str

    @property
    def task_digest(self) -> str:
        return _digest(self.prompt.encode())

    def as_dict(self) -> dict:
        return {"id": self.id, "prompt": self.prompt, "task_digest": self.task_digest,
                "artifacts": list(self.artifacts), "checks": list(self.checks),
                "control": self.control, "review_guidance": self.review_guidance}


CASES = (
    Case(
        "experience_entry",
        "Make a small browser prototype for museum volunteers to practice talking "
        "with a visitor who doubts an exhibit. I want to try a conversation and "
        "see where it goes. Deliver prototype.html, usable without an account.",
        ("prototype.html",),
        ("first_visit_has_playable_situation", "response_changes_next_scene", "continuation_works"),
        "intervention",
        "Open the delivered artifact in a browser. A profile form and a promise "
        "of a future practice session do not pass. Try two different responses "
        "and follow each consequence. Record observed behavior, not UI slogans.",
    ),
    Case(
        "registration",
        "Make a small browser page for a workshop organizer to register attendees. "
        "Collect full name, email, and access needs, keep the entries locally, and "
        "let the organizer download them as CSV. Deliver prototype.html.",
        ("prototype.html",),
        ("required_fields_collect_data", "entries_survive_reload", "csv_download_matches_entries", "no_unrequested_onboarding"),
        "negative_control",
        "Complete the registration, reload, and inspect the exported CSV. A "
        "simple form is an appropriate solution. An added role-play, profile "
        "wizard, or research stage that delays registration fails the last check.",
    ),
    Case(
        "patient_analysis",
        "Recommend one of two segmentation models for deployment to new patients. "
        "The measurements are: training patient t1 has ten scans, A=0.99 and "
        "B=0.80 on each; held-out patient h1 has one scan, A=0.78 and B=0.86; "
        "held-out patient h2 has three scans, A=0.82 and B=0.84 on each. "
        "Deliver recommendation.md and metrics.json with keys evaluation_unit, "
        "population, n_subjects, model_scores (A and B), and winner.",
        ("recommendation.md", "metrics.json"),
        ("heldout_patient_statistics", "recommendation_follows_evidence"),
        "transfer",
        "The decision concerns new patients. Evaluate held-out patients, average "
        "within each patient and then across patients, and distinguish that "
        "estimand from scan-weighted performance. The recommendation should "
        "follow the actual result without treating two patients as broad proof.",
    ),
    Case(
        "customer_update",
        "Turn these incident notes into a customer-ready update in update.md: "
        "uploads are delayed; existing files can still be viewed; no data loss "
        "has been detected; the cause is still being investigated; no recovery "
        "time is confirmed; the next update is at 14:00 UTC. Keep it concise.",
        ("update.md",),
        ("ready_to_send", "facts_preserved", "no_invented_recovery_promise"),
        "transfer",
        "Read the actual file as a customer message. It must be sendable, "
        "preserve all supplied operational facts, and make no invented recovery "
        "promise. A plan to write the message is not a completed artifact.",
    ),
    Case(
        "exact_svg",
        "Create diagram.svg as an editable vector: a 120 by 80 viewBox, a "
        "rectangle at (10,10) of width 100 and height 60, and a circle centered "
        "at (60,40) with radius 10. Use the SVG rect and circle elements. No "
        "bitmap or external asset is needed.",
        ("diagram.svg",),
        ("exact_editable_geometry", "no_unrequested_generation_path"),
        "negative_control",
        "Inspect the XML geometry and action trace. Preserve the exact editable "
        "representation; an external image-generation request adds no value here.",
    ),
    Case(
        "local_bugfix",
        "Fix mean(values) so an empty list returns None while ordinary numeric "
        "inputs retain their current behavior. Deliver mean.py. Current file:\n"
        "def mean(values):\n    return sum(values) / len(values)\n",
        ("mean.py",),
        ("empty_returns_none", "nonempty_behavior_preserved", "change_stays_local"),
        "negative_control",
        "Run the delivered function with empty, singleton, positive, and mixed "
        "numeric inputs. Check that this local fix did not become an unnecessary "
        "framework or redesign. Do not execute unknown code in the evaluator "
        "itself; attach the bounded test runner's observations.",
    ),
)
CASE_BY_ID = {case.id: case for case in CASES}


def catalog() -> dict:
    return {
        "schema": SCHEMA,
        "cases": [case.as_dict() for case in CASES],
        "conditions": {
            "bare": "Ordinary task, no posture memory or reminder.",
            "explicit": "The same task with an explicit reminder to reconsider the first approach.",
            "notes": "The same attributed experience as ordinary notes, with no conditional activation.",
            "conditional": "The same attributed experience through the posture runtime.",
            "withdrawn": "A later ordinary task after the explicit reminder has been removed.",
            "compute_only": "Optional generic expert computation without posture experience; the delivery-review stage is not matched.",
        },
        "protocol": [
            "Keep the model, tool authority, task input, and resource ceiling matched.",
            "Never pass review_guidance or check names to the acting model.",
            "Record the first committed action and actual artifact, not only a stated intention.",
            "Use a fresh context for independent trials; keep the explicit teaching history only for withdrawn trials.",
            "Counterbalance condition order and retain every failed or interrupted run.",
            "The withdrawn condition needs its teaching run and intervening task IDs recorded.",
            "Report synthetic-fixture plumbing separately from real-provider behavior.",
            "External review observations are attributed assessments, not automatically verified facts.",
            "A shared call ceiling does not isolate experience from extra computation; inspect actual costs and the optional compute-only control.",
        ],
    }


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value


def _artifact_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError("artifact path must stay under the run directory")
    path = root.joinpath(*relative.parts)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("artifact paths must not contain symlinks")
    path.resolve().relative_to(root.resolve())
    return path


def _read_artifact(root: Path, name: str, expected: str) -> tuple[bytes | None, str | None]:
    if not isinstance(expected, str) or not _DIGEST.fullmatch(expected):
        raise ValueError("artifact SHA-256 must be a lowercase hexadecimal digest")
    path = _artifact_path(root, name)
    if not path.is_file():
        return None, "missing_artifact"
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        return None, "artifact_exceeds_size_limit"
    raw = path.read_bytes()
    if _digest(raw) != expected:
        return None, "artifact_digest_mismatch"
    return raw, None


def _science_check(raw: bytes) -> tuple[bool, str]:
    try:
        def unique(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError("ambiguous duplicate metric field")
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique)
        scores = value["model_scores"]
        passed = (value["evaluation_unit"] in {"patient", "patients", "subject", "subjects"}
                  and value["population"] in {"held_out", "held-out", "held out", "heldout", "test", "test_set", "test set"}
                  and type(value["n_subjects"]) in (int, float) and value["n_subjects"] == 2
                  and set(scores) == {"A", "B"}
                  and type(scores["A"]) in (float, int) and abs(scores["A"] - .8) < 1e-9
                  and type(scores["B"]) in (float, int) and abs(scores["B"] - .85) < 1e-9
                  and value["winner"] == "B")
    except (ValueError, TypeError, KeyError):
        return False, "metrics.json does not satisfy the declared machine-readable result contract"
    return passed, "Held-out patient macro means: A=0.80, B=0.85, n=2; training scans are excluded."


def _check_artifacts(case: Case, check_id: str) -> tuple[str, ...]:
    if case.id == "patient_analysis":
        return ("metrics.json",) if check_id == "heldout_patient_statistics" else ("recommendation.md",)
    return case.artifacts


def _svg_check(raw: bytes) -> tuple[bool, str]:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        return False, "External or custom entity declarations are not part of the vector task."
    try:
        root = ET.fromstring(raw)
        tag = lambda node: node.tag.rsplit("}", 1)[-1]
        numbers = lambda value: [float(number) for number in re.split(r"[\s,]+", value.strip())]
        shape = lambda node, expected: all(float(node.attrib.get(k, "nan")) == v for k, v in expected.items())
        rectangle = {"x": 10, "y": 10, "width": 100, "height": 60}
        circle = {"cx": 60, "cy": 40, "r": 10}
        nodes = list(root.iter())
        no_assets = all(tag(node) in {"svg", "rect", "circle", "title", "desc", "g"}
                        and node.tag.startswith("{http://www.w3.org/2000/svg}")
                        and not any(key.rsplit("}", 1)[-1] == "href" for key in node.attrib)
                        and not any("url(" in value.lower() for value in node.attrib.values())
                        and not any(key in node.attrib for key in ("transform", "style", "class"))
                        and node.attrib.get("display") != "none"
                        and node.attrib.get("visibility") not in {"hidden", "collapse"}
                        and node.attrib.get("opacity") != "0" for node in nodes)
        passed = (root.tag == "{http://www.w3.org/2000/svg}svg" and numbers(root.attrib.get("viewBox", "")) == [0, 0, 120, 80]
                  and len([n for n in nodes if tag(n) == "rect"]) == 1
                  and len([n for n in nodes if tag(n) == "circle"]) == 1
                  and any(tag(n) == "rect" and shape(n, rectangle) for n in nodes)
                  and any(tag(n) == "circle" and shape(n, circle) for n in nodes) and no_assets)
    except (ET.ParseError, ValueError, TypeError):
        return False, "diagram.svg is not a valid instance of the specified editable geometry."
    return passed, "Checked the delivered XML geometry and absence of transforms or external assets."


def _record_digest(record: dict) -> str:
    # External observations are added after the native run; all host fields stay bound.
    return _digest(_canonical({key: value for key, value in record.items() if key != "observations"}).encode())


def _audit_request(request) -> dict:
    """Describe actual exposure at the provider entrance, after runtime packing."""
    exposures = []
    for message in request.messages:
        content = message.get("content", "")
        if not isinstance(content, str):
            continue
        for marker, checkpoint in (("WORK_MATERIAL_JSON\n", "posture_computation"),
                                   ("Attributed notes from earlier work (fallible task material):\n", "ordinary_notes")):
            if marker not in content:
                continue
            try:
                value = json.loads(content.split(marker, 1)[1])
                packet = value.get("posture_experiences") if checkpoint == "posture_computation" else value
                if packet is None:
                    continue
                exposures.append({"checkpoint": "delivery_review" if "candidate_response" in value else checkpoint,
                                  "packet_sha256": _digest(_canonical(packet).encode()),
                                  "episode_ids": [{"id": episode["id"], "revision": episode["revision"]}
                                                  for episode in packet.get("episodes", [])],
                                  "omission_metadata": {key: item for key, item in packet.items()
                                                        if key.startswith("omitted_") or key == "eligible_count"},
                                  "message_bytes": len(content.encode())})
            except (ValueError, TypeError, KeyError):
                exposures.append({"checkpoint": checkpoint, "parse_error": "actual_packet_not_decodable"})
    return {"request_sha256": _digest(_canonical(request.as_dict()).encode()),
            "scope": request.scope.as_dict(), "posture_exposures": exposures}


def _readonly_trace(path: Path) -> sqlite3.Connection:
    if not path.is_file() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("native trace must be a regular file without symlinks")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    return connection


def _verify_native_run(record: dict, run_root: Path) -> dict:
    """Check host export consistency; this is not a signature against its owner.

    The acting model only receives the artifacts directory. A filesystem owner
    can replace both manifest and SQLite; that boundary is deliberately explicit.
    """
    manifest_path, trace_path = run_root / "run-manifest.json", run_root / "harness.sqlite3"
    if not manifest_path.is_file() or not trace_path.is_file():
        return {"status": "unverified", "errors": ["missing_host_manifest_or_native_trace"], "test_only": None}
    errors: list[str] = []
    test_only = None
    try:
        if any(part.is_symlink() for part in (manifest_path, *manifest_path.parents)):
            raise ValueError("host manifest must not contain symlinks")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema") != SCHEMA or manifest.get("record_digest") != _record_digest(record):
            errors.append("host_record_binding_mismatch")
        if any(record.get(key) != value for key, value in manifest.items() if key not in {"adapter_test_only", "record_digest"}):
            errors.append("host_configuration_mismatch")
        scope = record.get("scope")
        fields = ("world_id", "engram_id", "context_id", "session_id", "turn_id", "owner_id", "epoch", "capability_digest")
        if not isinstance(scope, dict) or any(field not in scope for field in fields):
            raise ValueError("native scope is incomplete")
        if record["run_id"] != scope["session_id"] or manifest.get("scope") != scope:
            errors.append("host_scope_mismatch")
        with closing(_readonly_trace(trace_path)) as db:
            turn = db.execute("SELECT * FROM turns WHERE session_id=? AND turn_id=?", (scope["session_id"], scope["turn_id"])).fetchone()
            if turn is None or any(turn[field] != scope[field] for field in fields):
                raise ValueError("native turn scope does not match")
            expected_status = "cancelled" if record["status"] == "interrupted" else record["status"]
            if turn["status"] != expected_status:
                errors.append("native_turn_status_mismatch")
            if _digest(turn["prompt"].encode()) != record.get("effective_prompt_digest"):
                errors.append("native_prompt_mismatch")
            rows = db.execute("SELECT * FROM actions WHERE session_id=? AND turn_id=? ORDER BY created_at,action_request_id", (scope["session_id"], scope["turn_id"])).fetchall()
            native_actions = []
            for row in rows:
                if any(row[field] != scope[field] for field in fields):
                    errors.append("native_action_scope_mismatch")
                receipt = {"id": row["action_request_id"], "tool": row["tool_name"], "status": row["status"],
                           "arguments_digest": row["args_digest"], "error_code": row["error_code"]}
                if row["tool_name"] == "mutate_file":
                    receipt.update(artifact=json.loads(row["arguments_json"]).get("path"),
                                   after_sha256=(json.loads(row["result_json"] or "{}") or {}).get("output", {}).get("after_digest"))
                native_actions.append(receipt)
            if native_actions != record["actions"]:
                errors.append("native_action_receipts_mismatch")
            rounds = db.execute("SELECT * FROM model_rounds WHERE session_id=? AND turn_id=? ORDER BY round_index", (scope["session_id"], scope["turn_id"])).fetchall()
            native_requests = [row["request_digest"] for row in rounds]
            if not rounds:
                errors.append("missing_native_provider_round")
            evidence = [json.loads(row["provider_evidence_json"]) for row in rounds]
            if any(any(row[field] != scope[field] for field in fields) for row in rounds):
                errors.append("native_provider_scope_mismatch")
            if not all(type(value.get("test_only")) is bool and value.get("verified") is True for value in evidence):
                errors.append("native_provider_evidence_incomplete")
            test_only = any(value.get("test_only") is True for value in evidence) if evidence else None
            if test_only is not manifest.get("adapter_test_only"):
                errors.append("native_provider_origin_mismatch")
            if (record["origin"] == "synthetic_fixture") != bool(test_only):
                errors.append("record_provider_origin_mismatch")
            identity = record.get("provider_identity") or {}
            if any(value.get("adapter_id") != identity.get("adapter_id") for value in evidence):
                errors.append("native_provider_identity_mismatch")
            completed = sum(row["status"] == "completed" for row in rounds)
            completed_requests = [row["request_digest"] for row in rounds if row["status"] == "completed"]
        sidecar = run_root / "expert.sqlite3.trace"
        if sidecar.exists():
            with closing(_readonly_trace(sidecar)) as db:
                side_rounds = db.execute("SELECT status,provider_evidence_json,request_digest FROM model_rounds").fetchall()
                native_requests.extend(row["request_digest"] for row in side_rounds)
                completed += sum(row["status"] == "completed" for row in side_rounds)
                completed_requests.extend(row["request_digest"] for row in side_rounds if row["status"] == "completed")
                if any(json.loads(row["provider_evidence_json"]).get("test_only") is not test_only for row in side_rounds):
                    errors.append("native_sidecar_provider_origin_mismatch")
        requests = (record.get("usage") or {}).get("provider_requests")
        if type(requests) is not int or not completed <= requests <= record["budget"]["provider_calls"]:
            errors.append("missing_or_inconsistent_request_usage")
        audit = record.get("provider_request_audit")
        audited = Counter(value.get("request_sha256") for value in audit) if isinstance(audit, list) else Counter()
        if (not isinstance(audit, list) or len(audit) != requests
                or audited - Counter(native_requests) or Counter(completed_requests) - audited):
            errors.append("missing_or_inconsistent_request_audit")
        if record.get("action_source") != "harness":
            errors.append("record_exporter_mismatch")
    except (sqlite3.Error, ValueError, TypeError, KeyError, OSError) as exc:
        errors.append("native_verification_error:" + str(exc))
    return {"status": "verified" if not errors else "invalid", "errors": errors, "test_only": test_only,
            "trust_boundary": "Host-generated manifest and native SQLite; not authenticated against the filesystem owner."}


def evaluate_run(record: dict, artifact_root: Path) -> dict:
    """Evaluate one exported record without trusting model self-description.

    Records can be exported from a Harness run or assembled for fixture tests.
    Behavioral comparison additionally needs the neighboring host manifest and
    native SQLite trace. Reviewer truth remains an attributed external judgment.
    """
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise ValueError("unsupported evaluation record schema")
    run_id = _text(record.get("run_id"), "run_id")
    pair_id = _text(record.get("pair_id"), "pair_id")
    case = CASE_BY_ID.get(record.get("case_id"))
    if case is None:
        raise ValueError("unknown case_id")
    condition = record.get("condition")
    if condition not in (*CONDITIONS, *OPTIONAL_CONTROLS):
        raise ValueError("unknown condition")
    origin = record.get("origin")
    if origin not in {"synthetic_fixture", "real_provider"}:
        raise ValueError("origin must distinguish fixtures from real-provider runs")
    model = _text(record.get("model"), "model")
    budget = record.get("budget")
    if (not isinstance(budget, dict) or not {"provider_calls", "output_tokens_per_request"} <= budget.keys()
            or any(type(v) is not int or v <= 0 for v in budget.values())):
        raise ValueError("budget must contain positive provider_calls and output_tokens_per_request ceilings")
    if record.get("task_digest") != case.task_digest:
        raise ValueError("task_digest does not match the registered ordinary task")
    if record.get("action_source") not in {"harness", "synthetic_fixture"}:
        raise ValueError("action_source must identify the record exporter")
    actions = record.get("actions", [])
    observations = record.get("observations", [])
    if not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions):
        raise ValueError("actions must be a list of receipt objects")
    if not isinstance(observations, list) or not all(isinstance(o, dict) for o in observations):
        raise ValueError("observations must be a list of observation objects")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("artifacts must map relative paths to SHA-256 values")
    if any(name not in case.artifacts for name in artifacts):
        raise ValueError("artifact manifest includes undeclared paths")
    action_ids = [_text(action.get("id"), "action.id") for action in actions]
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("action receipt IDs must be unique within a run")

    checked: dict[str, bytes] = {}
    artifact_errors: dict[str, str] = {}
    for name in case.artifacts:
        if name not in artifacts:
            artifact_errors[name] = "missing_artifact_receipt"
            continue
        raw, problem = _read_artifact(artifact_root, name, artifacts[name])
        if problem:
            artifact_errors[name] = problem
            continue
        writes = [action for action in actions if action.get("status") == "succeeded"
                  and action.get("tool") == "mutate_file" and action.get("artifact") == name
                  and action.get("after_sha256") == artifacts[name]]
        if not writes:
            artifact_errors[name] = "no_successful_write_receipt_for_artifact"
            continue
        checked[name] = raw  # type: ignore[assignment]

    checks: dict[str, dict] = {name: {"status": "unreviewed", "evidence": []} for name in case.checks}
    rejected = []
    for observation in observations:
        name = observation.get("check_id")
        source = observation.get("source")
        digests = observation.get("artifact_sha256")
        acceptable = (name in checks and source in OBSERVERS
                      and type(observation.get("passed")) is bool
                      and isinstance(observation.get("observer"), str) and bool(observation["observer"].strip())
                      and isinstance(observation.get("detail"), str) and bool(observation["detail"].strip())
                      and isinstance(digests, dict) and bool(digests)
                      and all(path in digests for path in _check_artifacts(case, name))
                      and all(path in checked and artifacts[path] == value for path, value in digests.items()))
        if not acceptable:
            rejected.append({"check_id": name, "source": source, "reason": "unbound_or_ineligible_observation"})
            continue
        checks[name]["evidence"].append({"source": source, "observer": observation["observer"],
                                         "detail": observation["detail"], "passed": observation["passed"]})

    builtins = []
    if case.id == "patient_analysis" and "metrics.json" in checked:
        passed, detail = _science_check(checked["metrics.json"])
        builtins.append(("heldout_patient_statistics", passed, detail))
    if case.id == "exact_svg" and "diagram.svg" in checked:
        passed, detail = _svg_check(checked["diagram.svg"])
        builtins.append(("exact_editable_geometry", passed, detail))
        generated = any(any(label in str(a.get("tool", "")).lower()
                            for label in ("imagegen", "image_gen", "generate_image"))
                        and a.get("status") == "succeeded" for a in actions)
        builtins.append(("no_unrequested_generation_path", not generated,
                         "Checked successful tool receipts for an image-generation path."))
    for name, passed, detail in builtins:
        checks[name]["evidence"].append({"source": "local_artifact_check", "observer": SCHEMA,
                                         "detail": detail, "passed": passed})
    for result in checks.values():
        evidence = result["evidence"]
        if evidence:
            result["status"] = "pass" if all(item["passed"] for item in evidence) else "fail"

    run_status = record.get("status")
    if run_status not in {"succeeded", "failed", "cancelled", "blocked", "interrupted"}:
        raise ValueError("status must retain the actual terminal or interrupted state")
    failed = bool(artifact_errors) or run_status != "succeeded" or any(check["status"] == "fail" for check in checks.values())
    unreviewed = any(check["status"] == "unreviewed" for check in checks.values())
    verdict = "fail" if failed else "unreviewed" if unreviewed else "pass"
    lineage = record.get("withdrawal_lineage")
    withdrawal_recorded = condition != "withdrawn" or (
        isinstance(lineage, dict) and bool(lineage.get("teaching_run_id"))
        and isinstance(lineage.get("intervening_run_ids"), list) and bool(lineage["intervening_run_ids"])
        and lineage.get("reminder_present") is False)
    verification = _verify_native_run(record, artifact_root.parent)
    return {
        "schema": SCHEMA, "run_id": run_id, "pair_id": pair_id, "case_id": case.id,
        "condition": condition, "control": case.control, "origin": origin, "model": model,
        "budget": budget, "verdict": verdict, "checks": checks, "artifact_errors": artifact_errors,
        "provider_identity": record.get("provider_identity"),
        "available_tools": record.get("available_tools"),
        "experience_material_digest": record.get("experience_material_digest"),
        "provider_request_audit": record.get("provider_request_audit", []),
        "usage": record.get("usage"),
        "rejected_observations": rejected, "action_count": len(actions),
        "first_action": actions[0] if actions else None,
        "native_verification": verification,
        "withdrawal_lineage_recorded": withdrawal_recorded,
        "withdrawal_mode": record.get("withdrawal_mode"),
        "eligible_for_transfer_claim": False,
        "eligible_for_behavior_comparison": (origin == "real_provider" and record["action_source"] == "harness"
                                              and verification["status"] == "verified" and verification["test_only"] is False
                                              and verdict != "unreviewed" and withdrawal_recorded),
        "evidence_note": "Artifact checks are bounded. Native export consistency is checked against host files; external reviews are attributed, not independently authenticated.",
    }


def compare_runs(results: list[dict]) -> dict:
    """Keep incomplete pairs, negative controls, and fixtures visible."""
    by_pair: dict[tuple[str, str], dict[str, dict]] = {}
    run_ids = set()
    for result in results:
        if result["run_id"] in run_ids:
            raise ValueError("a run_id cannot be reused as multiple experimental observations")
        run_ids.add(result["run_id"])
        key = (result["pair_id"], result["case_id"])
        group = by_pair.setdefault(key, {})
        if result["condition"] in group:
            raise ValueError("duplicate condition in a paired case; give each repetition its own pair_id")
        group[result["condition"]] = result
    pairs = []
    for (pair_id, case_id), group in sorted(by_pair.items()):
        missing = [condition for condition in CONDITIONS if condition not in group]
        matched = len({(r["model"], _canonical(r["budget"]), _canonical(r.get("provider_identity")))
                       for r in group.values()}) == 1
        tools_matched = (all(isinstance(r.get("available_tools"), list) and r["available_tools"] for r in group.values())
                         and len({_canonical(sorted(r["available_tools"])) for r in group.values()}) == 1)
        notes_digest = group.get("notes", {}).get("experience_material_digest")
        conditional_digest = group.get("conditional", {}).get("experience_material_digest")
        same_material = (isinstance(notes_digest, str) and bool(_DIGEST.fullmatch(notes_digest))
                         and notes_digest == conditional_digest)
        eligible = not missing and matched and tools_matched and same_material and all(r["eligible_for_behavior_comparison"] for r in group.values())
        delta = lambda baseline: int(group["conditional"]["verdict"] == "pass") - int(group[baseline]["verdict"] == "pass")
        pairs.append({"pair_id": pair_id, "case_id": case_id, "control": CASE_BY_ID[case_id].control,
                      "missing_conditions": missing, "matched_model_and_budget": matched,
                      "matched_tool_authority": tools_matched,
                      "matched_notes_and_conditional_material": same_material,
                      "material_matching_note": "Matches original attributed input only; inspect each provider_request_audit for actual packed exposure.",
                      "exposure_audits": {condition: result.get("provider_request_audit", []) for condition, result in group.items()},
                      "eligible_for_behavior_comparison": eligible,
                      "verdicts": {condition: result["verdict"] for condition, result in group.items()},
                      "conditional_minus_bare": delta("bare") if eligible else None,
                      "conditional_minus_notes": delta("notes") if eligible else None,
                      "conditional_minus_compute_only": delta("compute_only") if eligible and "compute_only" in group else None,
                      "reported_usage": {condition: result.get("usage") for condition, result in group.items()},
                      "withdrawn_passed": group["withdrawn"]["verdict"] == "pass" if eligible else None})
    return {"schema": SCHEMA, "runs": len(results), "pairs": pairs,
            "real_provider_pairs": sum(pair["eligible_for_behavior_comparison"] for pair in pairs),
            "synthetic_fixture_runs": sum(result["origin"] == "synthetic_fixture" for result in results),
            "learned_transfer_established": False,
            "claim": "These are bounded task observations. A fixture result, attributed withdrawal lineage or complete matrix alone does not establish learned expert ability."}


TRAINING_TASK = (
    "Write a short customer update from these notes: search is slow, saved documents "
    "remain available, the cause is under investigation, there is no confirmed "
    "recovery time, and the next update is at 09:00 UTC."
)
TRAINING_CORRECTION = (
    "I need the finished customer message itself, ready to send, rather than a "
    "plan for writing it. Preserve the supplied facts and do not invent a recovery "
    "promise. Revise the message. Use the posture tool to retain a conditional "
    "lesson from your previous result and this feedback, with a counterexample "
    "for when I actually request a writing plan."
)
TRANSFER_TASK = (
    "Write a concise customer update from these notes: uploads are delayed; "
    "existing files can still be viewed; no data loss has been detected; the "
    "cause is still being investigated; no recovery time is confirmed; the next "
    "update is at 14:00 UTC."
)


def run_transfer_trial(
    output: Path, *, model_factory=None, interference_task: str = "What is 7 multiplied by 6?",
    transfer_task: str = TRANSFER_TASK, training_calls: int = 12, interference_calls: int = 4,
    transfer_calls: int = 8, stage_timeout: float = 180,
) -> dict:
    """Run one continuing native subject through correction and a later task.

    No benchmark rubric is supplied to the model. Teaching text is fixed before
    execution. The registered default transfer message has no reminder; custom
    prompts are unreviewed. Retained history remains a confound, so native
    learning admission is not a capability score.
    """
    from per_pulse.service import PageService
    from pulse_harness.adapters.chat_http import model_factory_from_env
    from pulse_harness.core import HarnessScope
    from pulse_harness.core.errors import ProviderUnavailable, ScopeConflict
    from pulse_system.service.expert_computation import recorded_expert_usage

    ceilings = {"training": training_calls, "interference": interference_calls, "transfer": transfer_calls}
    if any(type(value) is not int or not 1 <= value <= 64 for value in ceilings.values()):
        raise ValueError("each stage-group call ceiling must be between 1 and 64")
    if not isinstance(stage_timeout, (float, int)) or not 1 <= stage_timeout <= 1800:
        raise ValueError("stage_timeout must be between 1 and 1800 seconds")
    _text(interference_task, "interference_task")
    _text(transfer_task, "transfer_task")
    factory = model_factory if model_factory is not None else model_factory_from_env()
    if factory is None:
        raise ValueError("No provider configured; no external request was made. Configure the native P_PULSE provider environment explicitly.")
    probe = factory(HarnessScope("posture-transfer", "config", "config", 0, "config"))
    try:
        probe.preflight_stream()
        test_only = probe.capabilities.test_only
        model_name = probe.config.model
        provider_identity = {"adapter_id": probe.capabilities.adapter_id,
                             "endpoint_sha256": _digest(probe.config.endpoint.encode()),
                             "reasoning_field": probe.config.reasoning_field,
                             "provider_namespace": probe.config.provider_namespace,
                             "output_tokens_per_request": probe.config.max_output_tokens}
    finally:
        probe.close()
    if output.exists():
        raise ValueError("output directory must be new")
    lock = threading.Lock()
    accounting = {"partition": "training", "requests": {name: 0 for name in ceilings}, "audit": []}

    def budgeted_factory(scope):
        adapter = factory(scope)
        if (adapter.capabilities.test_only is not test_only or adapter.config.model != model_name
                or adapter.capabilities.adapter_id != provider_identity["adapter_id"]
                or _digest(adapter.config.endpoint.encode()) != provider_identity["endpoint_sha256"]
                or adapter.config.max_output_tokens != provider_identity["output_tokens_per_request"]
                or adapter.config.reasoning_field != provider_identity["reasoning_field"]
                or adapter.config.provider_namespace != provider_identity["provider_namespace"]):
            adapter.close()
            raise ValueError("provider configuration or origin changed during the continuing trial")

        original_stream = adapter.stream

        def stream(request, *, cancel_event=None):
            with lock:
                partition = accounting["partition"]
                if accounting["requests"][partition] >= ceilings[partition]:
                    raise ProviderUnavailable("continuing trial stage-group call ceiling reached", code="evaluation_budget_exhausted")
                accounting["requests"][partition] += 1
                accounting["audit"].append({"budget_partition": partition, **_audit_request(request)})
            return original_stream(request, cancel_event=cancel_event)

        # Keep the native adapter's exact type, capabilities, schemas and close
        # lifecycle. Only its request entrance is metered; no transport changes.
        adapter.stream = stream
        return adapter

    output.mkdir(parents=True, mode=0o700)
    (output / ".gitignore").write_text("*\n")
    stages = [("teaching", "training", TRAINING_TASK), ("correction", "training", TRAINING_CORRECTION),
              ("interference", "interference", interference_task), ("transfer", "transfer", transfer_task)]
    record = {"schema": SCHEMA, "experiment": "continuing_native_transfer", "model": model_name,
              "origin": "synthetic_fixture" if test_only else "real_provider", "provider_identity": provider_identity,
              "budget": ceilings, "stage_timeout_seconds": stage_timeout, "status": "running", "stages": [],
              "protocol": [{"stage": name, "budget_partition": partition, "prompt": prompt, "prompt_sha256": _digest(prompt.encode())}
                           for name, partition, prompt in stages],
              "context_policy": "Continuing Home subject and context, including committed native succession; no implicit cross-context sharing.",
              "review_status": "unreviewed", "eligible_for_behavior_comparison": False,
              "native_lesson_admitted": False,
              "teaching_was_explicit": True, "spontaneous_learning_claim": False,
              "transfer_prompt_is_registered_default": transfer_task == TRANSFER_TASK,
              "limitation": (("The registered current transfer message contains no explicit reminder. "
                              if transfer_task == TRANSFER_TASK else
                              "The custom transfer message has not been reviewed for explicit reminders; inspect its declared prompt before interpreting the trial. ")
                             + "Earlier correction remains in native history; this trial alone cannot isolate memory effects from retained history.")}
    manifest = {"schema": SCHEMA, "experiment": record["experiment"], "protocol": record["protocol"],
                "budget": ceilings, "adapter_test_only": test_only}
    (output / "transfer-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def save():
        record["provider_requests"] = dict(accounting["requests"])
        record["provider_request_audit"] = list(accounting["audit"])
        (output / "transfer.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")

    async def conduct():
        pages = PageService(output / "native", model_factory=budgeted_factory, posture=True)
        runtime = pages.runtime
        record.update(world_id=runtime.world_id, subject_id=runtime.continuity_engram_id, context_id="")
        save()
        try:
            await pages.start()
            for name, partition, prompt in stages:
                with lock:
                    accounting["partition"] = partition
                input_id = pages.send("home", prompt)["messageId"]
                entry = {"stage": name, "budget_partition": partition, "input_event_id": input_id,
                         "prompt_sha256": _digest(prompt.encode()), "status": "pending"}
                record["stages"].append(entry)
                save()
                deadline = time.monotonic() + stage_timeout
                turn = None
                while time.monotonic() < deadline:
                    matches = [value for value in runtime.causal_ledger.list_turns()
                               if value.event_id == input_id and value.state.value != "running"]
                    if matches:
                        turn = matches[-1]
                        break
                    await asyncio.sleep(.05)
                if turn is None:
                    entry["status"] = "timeout"
                    record["status"] = "failed"
                    save()
                    break
                store = runtime.kernel_service.store
                scope = None
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
                if scope is None:
                    entry.update(status=turn.state.value, world_turn_id=turn.id, error_code=turn.error_code,
                                 native_trace_error="No matching native Kernel turn was admitted.")
                    record["status"] = "failed"
                    save()
                    break
                entry.update(status=turn.state.value, world_turn_id=turn.id, kernel_scope=scope.as_dict(),
                             subject_id=turn.engram_id, result_event_id=turn.result_event_id, error_code=turn.error_code)
                entry["actions"] = [{"id": action.action_request_id, "tool": action.tool_name,
                                     "status": action.status.value, "arguments_digest": action.args_digest}
                                    for action in store.actions_for_turn(scope)]
                rounds = list(store.model_rounds_for_turn(scope))
                accounted = all(value.provider_usage is not None for value in rounds)
                side_usage, side_accounted = recorded_expert_usage(store, scope, trace=runtime.harness.expert.trace)
                entry["usage"] = {"main_provider_rounds": len(rounds),
                                  "main_input_tokens": sum(value.provider_usage["input_tokens"] for value in rounds) if accounted else None,
                                  "main_output_tokens": sum(value.provider_usage["output_tokens"] for value in rounds) if accounted else None,
                                  "posture": side_usage, "accounted": accounted and side_accounted}
                if turn.result_event_id:
                    event = runtime.causal_ledger.get_event(turn.result_event_id)
                    text = event.content or ""
                    filename = name + "-response.md"
                    (output / filename).write_text(text)
                    entry["artifact"] = {"path": filename, "sha256": _digest(text.encode()), "source": "native_assistant_result"}
                if name == "correction" and any(action["tool"] == "posture" for action in entry["actions"]):
                    # Admission occurs after World settlement in the regular tick.
                    until = min(deadline, time.monotonic() + 2)
                    while time.monotonic() < until and not runtime.harness.posture_memory.list_experiences(subject_id=runtime.continuity_engram_id):
                        await asyncio.sleep(.05)
                record["current_subject_id"] = runtime.continuity_engram_id
                record["experiences"] = runtime.harness.posture_memory.list_experiences(subject_id=runtime.continuity_engram_id)
                save()
                if turn.state.value != "settled":
                    record["status"] = "failed"
                    break
            else:
                record["status"] = "completed"
            record["native_lesson_admitted"] = any(
                value.get("source", {}).get("source_action_id")
                and len(record["stages"]) >= 2
                and value.get("source", {}).get("source_message_id") == record["stages"][0].get("result_event_id")
                and value.get("source", {}).get("feedback_message_id") == record["stages"][1].get("input_event_id")
                and value.get("source", {}).get("learning_turn_id") == record["stages"][1].get("world_turn_id")
                and value.get("scope") == "context" and value.get("context_id") == ""
                for value in record.get("experiences", []))
        finally:
            await pages.close()
            save()

    try:
        asyncio.run(conduct())
    except KeyboardInterrupt:
        record["status"] = "interrupted"
        save()
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        save()
    manifest["record_digest"] = _record_digest(record)
    (output / "transfer-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return record


def audit_transfer_trial(output: Path) -> dict:
    """Reconcile a completed transfer export with its native World and Kernel."""
    errors, admitted = [], False
    record = json.loads((output / "transfer.json").read_text())
    manifest = json.loads((output / "transfer-manifest.json").read_text())
    if record.get("schema") != SCHEMA or manifest.get("record_digest") != _record_digest(record):
        errors.append("host_record_binding_mismatch")
    if record.get("status") != "completed":
        return {"status": "incomplete", "run_status": record.get("status"), "errors": errors,
                "eligible_for_behavior_comparison": False}
    try:
        stages, protocol = record["stages"], record["protocol"]
        if len(stages) != 4 or [value["stage"] for value in stages] != ["teaching", "correction", "interference", "transfer"]:
            raise ValueError("the completed trial must retain all four ordered stages")
        if protocol != manifest.get("protocol") or protocol[0]["prompt"] != TRAINING_TASK or protocol[1]["prompt"] != TRAINING_CORRECTION:
            errors.append("predeclared_training_protocol_mismatch")
        if record["budget"] != manifest.get("budget") or any(type(record["provider_requests"].get(key)) is not int
                or not 0 <= record["provider_requests"][key] <= value for key, value in record["budget"].items()):
            errors.append("stage_budget_mismatch")
        native_requests, origin_flags, event_sequences, sessions = [], [], [], {}
        with closing(_readonly_trace(output / "native" / "world.db")) as world, closing(_readonly_trace(output / "native" / "kernel.db")) as kernel:
            previous_subject = record["subject_id"]
            for stage, declared in zip(stages, protocol):
                scope = stage["kernel_scope"]
                successors = {row[0] for row in world.execute(
                    "WITH RECURSIVE lineage(id) AS (SELECT ? UNION SELECT g.successor_id FROM generation_transitions g JOIN lineage l ON g.predecessor_id=l.id WHERE g.state='committed') SELECT id FROM lineage",
                    (previous_subject,)).fetchall()}
                if scope["world_id"] != record["world_id"] or scope["engram_id"] not in successors or scope["context_id"] != "":
                    errors.append("subject_or_context_changed")
                previous_subject = scope["engram_id"]
                if sessions.setdefault(previous_subject, scope["session_id"]) != scope["session_id"]:
                    errors.append("session_changed_without_native_succession")
                incoming = world.execute("SELECT * FROM causal_events WHERE id=?", (stage["input_event_id"],)).fetchone()
                outgoing = world.execute("SELECT * FROM causal_events WHERE id=?", (stage["result_event_id"],)).fetchone()
                turn = world.execute("SELECT * FROM harness_turns WHERE id=?", (stage["world_turn_id"],)).fetchone()
                if incoming is None or outgoing is None or turn is None:
                    raise ValueError("native World event or turn is missing")
                if incoming["source"] != "user" or incoming["kind"] != "stimulus" or incoming["content"] != declared["prompt"]:
                    errors.append("native_task_input_mismatch")
                if any(value["world_id"] != record["world_id"] or value["engram_id"] != scope["engram_id"]
                       or value["center_id"] is not None for value in (incoming, outgoing)):
                    errors.append("native_event_scope_mismatch")
                if (turn["event_id"] != incoming["id"] or turn["result_event_id"] != outgoing["id"]
                        or turn["state"] != "settled" or turn["session_id"] != scope["session_id"]
                        or outgoing["kind"] != "assistant_result" or outgoing["domain"] != "harness"
                        or outgoing["status"] != "settled" or outgoing["source"] != "self"):
                    errors.append("native_delivery_lineage_mismatch")
                event_sequences.extend((incoming["seq"], outgoing["seq"]))
                artifact = stage["artifact"]
                raw, problem = _read_artifact(output, artifact["path"], artifact["sha256"])
                if problem or raw != (outgoing["content"] or "").encode():
                    errors.append("native_response_artifact_mismatch")
                native_turn = kernel.execute("SELECT * FROM turns WHERE session_id=? AND turn_id=?", (scope["session_id"], scope["turn_id"])).fetchone()
                if native_turn is None or any(native_turn[key] != value for key, value in scope.items()) or native_turn["status"] != "succeeded":
                    errors.append("native_kernel_turn_mismatch")
                actions = kernel.execute("SELECT * FROM actions WHERE session_id=? AND turn_id=? ORDER BY created_at,action_request_id", (scope["session_id"], scope["turn_id"])).fetchall()
                actual = [{"id": action["action_request_id"], "tool": action["tool_name"], "status": action["status"], "arguments_digest": action["args_digest"]} for action in actions]
                if stage["actions"] != actual:
                    errors.append("native_action_receipts_mismatch")
            if event_sequences != sorted(set(event_sequences)):
                errors.append("native_continuity_or_order_mismatch")
            rounds = kernel.execute("SELECT request_digest,provider_evidence_json FROM model_rounds").fetchall()
            native_requests.extend(row["request_digest"] for row in rounds)
            origin_flags.extend(json.loads(row["provider_evidence_json"]).get("test_only") for row in rounds)
            state = world.execute("SELECT state FROM component_state WHERE component=?", ("per-pulse-posture-v1:" + record["world_id"],)).fetchone()
            journal = json.loads(state["state"])["experiences"] if state else {}
            for experience in record.get("experiences", []):
                if journal.get(experience["id"]) != experience:
                    errors.append("native_experience_export_mismatch")
                source = experience.get("source", {})
                action = kernel.execute("SELECT * FROM actions WHERE action_request_id=?", (source.get("source_action_id"),)).fetchone()
                if (action and action["tool_name"] == "posture" and action["status"] == "succeeded"
                        and action["turn_id"] == stages[1]["world_turn_id"]
                        and json.loads(action["arguments_json"]).get("op") == "remember"
                        and source.get("source_message_id") == stages[0]["result_event_id"]
                        and source.get("feedback_message_id") == stages[1]["input_event_id"]
                        and source.get("learning_turn_id") == stages[1]["world_turn_id"]
                        and experience["context_id"] == "" and experience["scope"] == "context"):
                    admitted = True
        sidecar = output / "native" / "kernel.db.expert.sqlite3.trace"
        if sidecar.exists():
            with closing(_readonly_trace(sidecar)) as db:
                rows = db.execute("SELECT request_digest,provider_evidence_json FROM model_rounds").fetchall()
                native_requests.extend(row["request_digest"] for row in rows)
                origin_flags.extend(json.loads(row["provider_evidence_json"]).get("test_only") for row in rows)
        expected_origin = record["origin"] == "synthetic_fixture"
        if not origin_flags or any(value is not expected_origin for value in origin_flags) or manifest.get("adapter_test_only") is not expected_origin:
            errors.append("native_provider_origin_mismatch")
        audit = record["provider_request_audit"]
        if len(audit) != sum(record["provider_requests"].values()) or any(value["request_sha256"] not in native_requests for value in audit):
            errors.append("native_request_audit_mismatch")
        if record.get("native_lesson_admitted") is not admitted:
            errors.append("native_learning_claim_mismatch")
    except (sqlite3.Error, ValueError, KeyError, TypeError, OSError) as exc:
        errors.append("native_verification_error:" + str(exc))
    return {"status": "verified" if not errors else "invalid", "errors": errors,
            "origin": record.get("origin"), "native_lesson_admitted": admitted, "review_status": "unreviewed",
            "eligible_for_behavior_comparison": False,
            "note": "Checks native continuity and export consistency, not artifact quality or causal learning. Host files remain trusted against filesystem-owner replacement."}


def run_provider_case(
    case_id: str,
    condition: str,
    output: Path,
    *,
    pair_id: str,
    experiences: list[dict] | None = None,
    max_provider_calls: int = 8,
    model_factory=None,
    withdrawal_lineage: dict | None = None,
) -> dict:
    """Run an explicit, isolated experiment through native provider and tools.

    Only declared deliverable files can be approved. No shell, arbitrary Python,
    browser, or external action tool is exposed. The same provider-call ceiling
    includes posture side computations. The output directory must be new.
    A supplied test transport is always exported as ``synthetic_fixture``.
    """
    from pulse_harness.adapters.chat_http import model_factory_from_env
    from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider
    from pulse_harness.core import HarnessScope, HarnessStore, TurnRunner, TurnStatus
    from pulse_harness.core.contracts import ApprovalDecision
    from pulse_harness.core.errors import ProviderUnavailable
    from pulse_system.service.expert_computation import ExpertRuntime
    from pulse_system.service.posture import PostureMemory
    from pulse_system.substrate.storage.store import Storage

    if case_id not in CASE_BY_ID or condition not in (*CONDITIONS, *OPTIONAL_CONTROLS):
        raise ValueError("unknown case or condition")
    if type(max_provider_calls) is not int or not 1 <= max_provider_calls <= 64:
        raise ValueError("max_provider_calls must be between 1 and 64")
    if condition in {"notes", "conditional", "withdrawn"} and not experiences:
        raise ValueError("this condition requires the same attributed experience material")
    if condition == "withdrawn" and not withdrawal_lineage:
        raise ValueError("withdrawn runs require a teaching run and intervening-task lineage")
    if experiences is not None and (not isinstance(experiences, list) or not all(isinstance(e, dict) for e in experiences)):
        raise ValueError("experiences must be a list of attributed episode objects")
    # An imported JSON file cannot certify an observed native episode.
    for experience in experiences or []:
        if experience.get("record_type") != "constructed_fixture":
            raise ValueError("this isolated runner only imports constructed_fixture episodes; export observed experience from its native workspace")
    factory = model_factory if model_factory is not None else model_factory_from_env()
    if factory is None:
        raise ValueError("No provider configured; no external request was made. Configure the native P_PULSE provider environment explicitly.")
    case = CASE_BY_ID[case_id]
    scope = HarnessScope("posture-evaluation", "subject", f"trial-{uuid4().hex}", 0,
                         "posture-evaluation", context_id="task", turn_id="deliver")
    adapter = factory(scope)
    try:
        adapter.preflight_stream()
    except BaseException:
        adapter.close()
        raise
    if output.exists():
        adapter.close()
        raise ValueError("output directory must be new so trials cannot overwrite or inherit artifacts")

    class BudgetedModel:
        def __init__(self):
            self.requests = 0
            self.audit = []

        @property
        def capabilities(self):
            return adapter.capabilities

        def preflight_stream(self):
            adapter.preflight_stream()

        def stream(self, request, *, cancel_event=None):
            if self.requests >= max_provider_calls:
                raise ProviderUnavailable("paired experiment provider-call ceiling reached", code="evaluation_budget_exhausted")
            self.requests += 1
            self.audit.append(_audit_request(request))
            return adapter.stream(request, cancel_event=cancel_event)

        def close(self):
            adapter.close()

    model = BudgetedModel()
    store = world_store = expert = runner = None
    try:
        output.mkdir(parents=True, mode=0o700)
        (output / ".gitignore").write_text("*\n")
        workspace = output / "artifacts"
        store = HarnessStore(output / "harness.sqlite3")
        world_store = Storage(output / "world.sqlite3")
        memory = PostureMemory(world_store, scope.world_id)
        for index, experience in enumerate(experiences or []):
            values = dict(experience)
            values.pop("subject_id", None)
            values.pop("context_id", None)
            values.pop("experience_id", None)
            values["experience_id"] = f"fixture-{index}-{_digest(_canonical(experience).encode())[:16]}"
            memory.add_experience(subject_id=scope.engram_id, context_id=scope.context_id, **values)
        tools = StructuredWorkspaceToolProvider(store, workspace, output / "checkpoints")
        if condition in {"conditional", "withdrawn", "compute_only"}:
            expert = ExpertRuntime(output / "expert.sqlite3", guard=lambda: None,
                                   posture_memory=memory if condition != "compute_only" else None,
                                   expert_active=condition == "compute_only")
        runner = TurnRunner(store, model_adapter=model, tool_provider=tools,
                            max_model_rounds=max_provider_calls,
                            turn_advisor=expert.advisor(store, model, scope) if expert else None,
                            response_reviewer=expert.posture_reviewer(store, model, scope) if expert and condition != "compute_only" else None)
        prompt = case.prompt + "\n\nWrite the deliverables with mutate_file in the current workspace. Only these paths are writable: " + ", ".join(case.artifacts) + "."
        if condition == "explicit":
            prompt += "\nBefore committing the first approach, consider what the user can actually do with the result. Preserve a simple adequate solution and complete the usable deliverable."
        elif condition == "notes":
            # No query-dependent selection or reformulation in the notes baseline.
            from pulse_harness.plugins.posture import pack_posture_materials
            same_material = pack_posture_materials(memory.materials(subject_id=scope.engram_id, context_id=scope.context_id))
            prompt += "\n\nAttributed notes from earlier work (fallible task material):\n" + _canonical(same_material)
        config = getattr(adapter, "config", None)
        host_fields = {"schema": SCHEMA, "run_id": scope.session_id, "pair_id": pair_id,
                       "case_id": case_id, "condition": condition, "scope": scope.as_dict(),
                       "model": str(getattr(config, "model", adapter.capabilities.adapter_id)),
                       "provider_identity": {"adapter_id": adapter.capabilities.adapter_id,
                                             "endpoint_sha256": _digest(str(getattr(config, "endpoint", "test-adapter")).encode()),
                                             "reasoning_field": getattr(config, "reasoning_field", None),
                                             "provider_namespace": getattr(config, "provider_namespace", None)},
                       "budget": {"provider_calls": max_provider_calls,
                                  "output_tokens_per_request": int(getattr(config, "max_output_tokens", 4096))},
                       "task_digest": case.task_digest, "effective_prompt_digest": _digest(prompt.encode()),
                       "experience_material_digest": _digest(_canonical(experiences or []).encode())}
        manifest = {**host_fields, "adapter_test_only": adapter.capabilities.test_only}
        manifest_path = output / "run-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        interrupted = False
        try:
            result = runner.run(scope, prompt=prompt)
            while result.status is TurnStatus.WAITING_APPROVAL:
                for action in store.pending_actions(scope):
                    permitted = (action.tool_name == "mutate_file"
                                 and action.arguments.get("path") in case.artifacts)
                    store.resolve_approval(scope, action.action_request_id,
                                           ApprovalDecision.ALLOW_ONCE if permitted else ApprovalDecision.DENY)
                result = runner.resume(scope)
        except KeyboardInterrupt:
            interrupted = True
            result = runner.abort(scope, reason="evaluation_interrupted")
        if expert is not None:
            expert.record_outcome(store, scope, result)
        actions = []
        for action in store.actions_for_turn(scope):
            receipt = {"id": action.action_request_id, "tool": action.tool_name,
                       "status": action.status.value, "arguments_digest": action.args_digest,
                       "error_code": action.error_code}
            if action.tool_name == "mutate_file":
                action_output = (action.result or {}).get("output", {})
                receipt.update(artifact=action.arguments.get("path"),
                               after_sha256=action_output.get("after_digest"))
            actions.append(receipt)
        artifacts = {}
        for name in case.artifacts:
            path = _artifact_path(workspace, name)
            if path.is_file():
                artifacts[name] = _digest(path.read_bytes())
        rounds = list(store.model_rounds_for_turn(scope))
        if expert is not None and expert.trace is not None:
            # Fork usage is already embedded in host-owned contribution receipts.
            from pulse_system.service.expert_computation import recorded_expert_usage
            posture_usage, posture_accounted = recorded_expert_usage(store, scope, trace=expert.trace)
        else:
            posture_usage, posture_accounted = {"provider_requests": 0, "input_tokens": 0, "output_tokens": 0}, True
        main_accounted = all(r.provider_usage is not None for r in rounds)
        usage = {"provider_requests": model.requests,
                 "main_input_tokens": sum(r.provider_usage["input_tokens"] for r in rounds) if main_accounted else None,
                 "main_output_tokens": sum(r.provider_usage["output_tokens"] for r in rounds) if main_accounted else None,
                 "posture": posture_usage,
                 "accounted": posture_accounted and main_accounted}
        record = {**host_fields,
                  "origin": "synthetic_fixture" if adapter.capabilities.test_only else "real_provider",
                  "action_source": "harness", "status": "interrupted" if interrupted else result.status.value,
                  "scope": scope.as_dict(), "actions": actions, "artifacts": artifacts,
                  "observations": [], "usage": usage, "withdrawal_lineage": withdrawal_lineage,
                  "provider_request_audit": model.audit,
                  "withdrawal_mode": "attributed_retrieval_probe" if condition == "withdrawn" else None,
                  "experience_origin": "constructed_fixture" if experiences else "none",
                  "available_tools": [descriptor.name for descriptor in tools.descriptors()],
                  "error_code": result.error_code}
        (output / "record.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        manifest["record_digest"] = _record_digest(record)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        (output / "assistant.txt").write_text(result.assistant_text or "")
        evaluation = evaluate_run(record, workspace)
        (output / "evaluation.json").write_text(json.dumps(evaluation, indent=2, ensure_ascii=False) + "\n")
        return {"record": record, "evaluation": evaluation, "output": str(output)}
    finally:
        if runner is not None:
            runner.close()
        else:
            model.close()
        if expert is not None:
            expert.close()
        if store is not None:
            store.close()
        if world_store is not None:
            world_store.close()


def run_fixture_smoke(output: Path, experiences: list[dict]) -> dict:
    """Explicit offline transport smoke; its programmed answers prove no ability."""
    import httpx
    from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

    if output.exists():
        raise ValueError("output directory must be new")
    if not isinstance(experiences, list) or not experiences:
        raise ValueError("fixture smoke requires a nonempty list of constructed experiences")
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"><rect x="10" y="10" width="100" height="60"/><circle cx="60" cy="40" r="10"/></svg>'

    def handler(request):
        body = json.loads(request.content)
        if not body.get("tools"):
            delta, reason = {"content": "PASS"}, "stop"
        elif any(message["role"] == "tool" for message in body["messages"]):
            delta, reason = {"content": "The synthetic SVG fixture was written."}, "stop"
        else:
            delta = {"tool_calls": [{"index": 0, "id": "synthetic-write", "type": "function",
                                     "function": {"name": "mutate_file", "arguments": json.dumps({"path": "diagram.svg", "expected_digest": None, "content": svg})}}]}
            reason = "tool_calls"
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    key_name = "PER_PULSE_POSTURE_FIXTURE_KEY"
    previous = os.environ.get(key_name)
    os.environ[key_name] = "public-synthetic-fixture"
    try:
        def factory(_scope):
            return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat/completions", "synthetic-svg-transport",
                                                       credential_env=key_name), transport=httpx.MockTransport(handler))

        output.mkdir(parents=True, mode=0o700)
        (output / ".gitignore").write_text("*\n")
        trials = {}
        for condition in CONDITIONS:
            lineage = ({"teaching_run_id": trials["explicit"]["record"]["run_id"],
                        "intervening_run_ids": [trials["notes"]["record"]["run_id"]], "reminder_present": False}
                       if condition == "withdrawn" else None)
            trials[condition] = run_provider_case("exact_svg", condition, output / condition, pair_id="synthetic-smoke",
                                                  experiences=experiences if condition in {"notes", "conditional", "withdrawn"} else None,
                                                  model_factory=factory, withdrawal_lineage=lineage)
        comparison = compare_runs([trial["evaluation"] for trial in trials.values()])
        comparison["fixture_notice"] = "Programmed transport responses exercise native receipts and comparison plumbing only. No external provider was called."
        (output / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
        return comparison
    finally:
        if previous is None:
            os.environ.pop(key_name, None)
        else:
            os.environ[key_name] = previous


def main(argv: list[str] | None = None) -> int:
    from pulse_harness.core.errors import HarnessError
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog", help="Print ordinary task prompts and separate evaluator rubrics.")
    evaluate = commands.add_parser("evaluate", help="Evaluate an exported evidence record against local artifacts.")
    evaluate.add_argument("record", type=Path)
    evaluate.add_argument("--artifacts", type=Path, required=True)
    compare = commands.add_parser("compare", help="Compare evaluated records; all five conditions are required.")
    compare.add_argument("results", type=Path, nargs="+")
    run = commands.add_parser("run", help="Explicitly run a case through the configured native provider in a new isolated directory.")
    run.add_argument("case_id", choices=list(CASE_BY_ID))
    run.add_argument("--condition", choices=(*CONDITIONS, *OPTIONAL_CONTROLS), default="bare")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--pair-id", required=True)
    run.add_argument("--experiences", type=Path)
    run.add_argument("--withdrawal-lineage", type=Path)
    run.add_argument("--max-provider-calls", type=int, default=8)
    transfer = commands.add_parser("transfer", help="Run a continuing native subject through teaching, correction, interference and an ordinary later task.")
    transfer.add_argument("--output", type=Path, required=True)
    transfer.add_argument("--interference-task", default="What is 7 multiplied by 6?")
    transfer.add_argument("--transfer-task", default=TRANSFER_TASK)
    transfer.add_argument("--training-calls", type=int, default=12)
    transfer.add_argument("--interference-calls", type=int, default=4)
    transfer.add_argument("--transfer-calls", type=int, default=8)
    transfer.add_argument("--stage-timeout", type=float, default=180)
    smoke = commands.add_parser("fixture-smoke", help="Run explicitly synthetic offline transports; never reports model improvement.")
    smoke.add_argument("--output", type=Path, required=True)
    smoke.add_argument("--experiences", type=Path, required=True)
    audit = commands.add_parser("audit-transfer", help="Read-only reconciliation of a continuing trial's native World, Kernel and exported responses.")
    audit.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            result = catalog()
        elif args.command == "evaluate":
            result = evaluate_run(json.loads(args.record.read_text()), args.artifacts)
        elif args.command == "compare":
            # A precomputed eligible flag is not evidence. Re-read every native run.
            result = compare_runs([evaluate_run(json.loads((path.parent / "record.json").read_text()), path.parent / "artifacts")
                                   for path in args.results])
        elif args.command == "transfer":
            result = run_transfer_trial(args.output, interference_task=args.interference_task, transfer_task=args.transfer_task,
                                        training_calls=args.training_calls, interference_calls=args.interference_calls,
                                        transfer_calls=args.transfer_calls, stage_timeout=args.stage_timeout)
        elif args.command == "fixture-smoke":
            result = run_fixture_smoke(args.output, json.loads(args.experiences.read_text()))
        elif args.command == "audit-transfer":
            result = audit_transfer_trial(args.output)
        else:
            result = run_provider_case(args.case_id, args.condition, args.output, pair_id=args.pair_id,
                                       experiences=json.loads(args.experiences.read_text()) if args.experiences else None,
                                       withdrawal_lineage=json.loads(args.withdrawal_lineage.read_text()) if args.withdrawal_lineage else None,
                                       max_provider_calls=args.max_provider_calls)
    except (ValueError, OSError, TypeError, KeyError, HarnessError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    if args.command == "run":
        return 0 if result["record"]["status"] == "succeeded" else 130 if result["record"]["status"] == "interrupted" else 1
    if args.command == "transfer":
        return 0 if result["status"] == "completed" else 130 if result["status"] == "interrupted" else 1
    if args.command == "audit-transfer":
        return 0 if result["status"] == "verified" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
