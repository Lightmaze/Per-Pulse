"""Two independent, pre-bounded ordinary-feedback trials for blinded human review.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This drives the existing native transfer experiment. It never manufactures a
lesson, treats fixture responses as model evidence, or interprets blind scores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from .blinded_review import CHECKS, grade, prepare
from .posture_transfer import TASK_ORDERS, audit_ordinary_transfer, run_ordinary_transfer

SCHEMA = "per_pulse.ordinary_posture_study.v1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive_integer(value: int, label: str, *, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be an integer between 1 and {maximum}")


def run_study(
    output: Path, *, model_factory=None, training_calls: int = 8,
    trial_calls: int = 4, max_total_requests: int = 48,
    stage_timeout: float = 180, allow_fixture: bool = False,
) -> dict:
    """Run both task orders in *separate Worlds* using one provider identity.

    Counts are upper bounds on model calls through the native adapter, not a
    monetary cap. A failed native/audit run stops further paid experiments.
    Each completed verified arm gets its *own* secret-key-separated packet.
    """
    for label, value, maximum in (
        ("training_calls", training_calls, 64),
        ("trial_calls", trial_calls, 64),
        ("max_total_requests", max_total_requests, 1024),
    ):
        _positive_integer(value, label, maximum=maximum)
    if type(stage_timeout) not in (int, float) or not 1 <= stage_timeout <= 1800:
        raise ValueError("stage_timeout must be 1..1800 seconds")
    ceiling = len(TASK_ORDERS) * (training_calls + 4 * trial_calls)
    if ceiling > max_total_requests:
        raise ValueError(f"predeclared call ceiling {ceiling} exceeds max_total_requests {max_total_requests}")
    if output.exists():
        raise ValueError("study output directory must be new")

    if model_factory is None:
        from pulse_harness.adapters.chat_http import model_factory_from_env
        model_factory = model_factory_from_env()
    if model_factory is None:
        raise ValueError("No provider configured; no trial directory or external request was created")

    from pulse_harness.core import HarnessScope
    adapter = model_factory(HarnessScope("ordinary-posture-study", "config", "config", 0, "config"))
    try:
        adapter.preflight_stream()
        origin = "synthetic_fixture" if adapter.capabilities.test_only else "real_provider"
        if origin != "real_provider" and not allow_fixture:
            raise ValueError("fixture provider is refused without --allow-fixture; this cannot establish model benefit")
        from .posture_transfer import _provider_identity
        provider_identity = _provider_identity(adapter)
    finally:
        adapter.close()

    output.mkdir(mode=0o700, parents=True)
    (output / ".gitignore").write_text("*\n", encoding="utf-8")
    result = {
        "schema": SCHEMA, "status": "running", "origin": origin,
        "provider_identity": provider_identity,
        "predeclared_orders": list(TASK_ORDERS),
        "limits": {"training_calls_per_arm": training_calls,
                   "trial_calls_per_target": trial_calls,
                   "max_total_requests": max_total_requests,
                   "total_possible_requests": ceiling,
                   "stage_timeout_seconds": stage_timeout},
        "arms": [], "model_quality_reviewed": False,
        "limitations": [
            "Each arm still runs unshared targets before shared targets; task order alone is counterbalanced.",
            "Worlds are independent, not a repeated longitudinal measurement of one subject.",
            "Conditional targets may invoke extra posture computations; calls and provider usage are not dollar caps.",
            "Capture is model-chosen; an absent lesson is a valid outcome, not a host-inserted failure correction.",
            "Human review is independent work; a verified audit or generated packet cannot assert improvement.",
        ],
    }

    def save() -> None:
        (output / "study.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    save()
    seen_worlds: set[str] = set()
    observed_calls = 0
    for order in TASK_ORDERS:
        trial_dir = output / ("trial-" + order)
        try:
            record = run_ordinary_transfer(
                trial_dir, model_factory=model_factory, training_calls=training_calls,
                trial_calls=trial_calls, stage_timeout=stage_timeout, task_order=order,
            )
        except Exception as exc:
            # Keep a resumable forensic pointer, not a traceback or provider
            # exception text: failures can include private request material.
            result.update(status="stopped", stop_reason="trial_runner_exception",
                          failed_order=order, exception_type=type(exc).__name__)
            save()
            break
        arm = {
            "task_order": order, "trial_directory": trial_dir.name,
            "status": record.get("status"), "capture_status": record.get("capture_status"),
            "origin": record.get("origin"),
            "record_sha256": _sha(trial_dir / "ordinary-transfer.json"),
            "provider_requests": record.get("provider_requests", {}),
            "usage_accounted": bool(record.get("stages")) and all(
                bool(item.get("usage", {}).get("accounted")) for item in record["stages"]
            ),
        }
        observed_calls += sum(record.get("provider_requests", {}).values())
        arm["observed_requests"] = sum(record.get("provider_requests", {}).values())
        result["arms"].append(arm)
        result["observed_requests"] = observed_calls
        if observed_calls > max_total_requests:
            result.update(status="stopped", stop_reason="declared_call_ceiling_exceeded")
            save()
            break
        if record.get("origin") != origin or record.get("provider_identity") != provider_identity:
            result.update(status="stopped", stop_reason="provider_identity_drift")
            save()
            break
        world_id = record.get("world_id")
        if not isinstance(world_id, str) or not world_id or world_id in seen_worlds:
            result.update(status="stopped", stop_reason="world_identity_not_independent")
            save()
            break
        seen_worlds.add(world_id)
        try:
            audit = audit_ordinary_transfer(trial_dir)
        except Exception as exc:
            result.update(status="stopped", stop_reason="native_audit_exception",
                          failed_order=order, exception_type=type(exc).__name__)
            save()
            break
        arm["audit_status"] = audit.get("status")
        arm["audit_errors"] = audit.get("errors", [])
        save()
        if audit.get("status") != "verified" or audit.get("errors"):
            result.update(status="stopped", stop_reason="trial_not_verified")
            save()
            break
        if record.get("status") == "completed":
            private_review = output / ("review-" + order)
            try:
                packet = prepare(trial_dir, private_review, allow_fixture=allow_fixture)
            except Exception as exc:
                # A partially written packet must never be distributed. Preserve
                # its location for private inspection, and do not run arm two.
                result.update(status="stopped", stop_reason="blind_packet_exception",
                              failed_order=order, exception_type=type(exc).__name__)
                save()
                break
            arm["blind_review_directory"] = private_review.name
            arm["reviewer_only_directory"] = private_review.name + "/reviewer"
            arm["review_packet_sha256"] = _sha(private_review / "reviewer" / "review-packet.json")
            arm["review_items"] = packet["items"]
        elif record.get("status") != "capture_absent":
            result.update(status="stopped", stop_reason="trial_not_completed")
            save()
            break
        save()
    else:
        result["status"] = "completed_with_capture_absence" if any(
            arm["status"] == "capture_absent" for arm in result["arms"]
        ) else "review_packets_ready"
        save()
    return result



def summarize_reviews(study: Path, ratings_by_order: dict[str, Path], *,
                      allow_fixture: bool = False) -> dict:
    """Reaudit both native Worlds, then reveal bound human check judgments.

    Descriptive paired review, not a causal benefit, verified reviewer
    identity, or estimate of billed money.
    """
    if set(ratings_by_order) != set(TASK_ORDERS):
        raise ValueError("ratings are required for every registered task order")
    state_path = study / "study.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("schema") != SCHEMA or state.get("status") != "review_packets_ready":
        raise ValueError("both native trial arms and blind packets must be ready")
    if state.get("origin") != "real_provider" and not allow_fixture:
        raise ValueError("synthetic fixture cannot establish real-provider model benefit")
    arms = state.get("arms", [])
    if (len(arms) != len(TASK_ORDERS)
            or {arm.get("task_order") for arm in arms} != set(TASK_ORDERS)
            or state.get("predeclared_orders") != list(TASK_ORDERS)):
        raise ValueError("study arms do not match the predeclared protocol")

    worlds: set[str] = set()
    request_total = 0
    accounted = True
    reviewed = []
    paired = {task: {check: [] for check in checks} for task, checks in CHECKS.items()}
    for arm in arms:
        order = arm["task_order"]
        trial_dir = study / ("trial-" + order)
        review_dir = study / ("review-" + order)
        if (arm.get("trial_directory") != trial_dir.name
                or arm.get("blind_review_directory") != review_dir.name
                or arm.get("reviewer_only_directory") != review_dir.name + "/reviewer"):
            raise ValueError("trial or review path differs from the registered arm")
        native_path = trial_dir / "ordinary-transfer.json"
        native_digest = _sha(native_path)
        if native_digest != arm.get("record_sha256"):
            raise ValueError("native trial changed since blind packet preparation")
        native = json.loads(native_path.read_text(encoding="utf-8"))
        if (native.get("status") != "completed"
                or native.get("capture_status") != "native_lesson_admitted"
                or native.get("task_order") != order
                or native.get("origin") != state["origin"]
                or native.get("provider_identity") != state.get("provider_identity")):
            raise ValueError("completed trial origin, protocol or identity changed")
        world_id = native.get("world_id")
        if not isinstance(world_id, str) or not world_id or world_id in worlds:
            raise ValueError("independent native World identities are required")
        worlds.add(world_id)
        audit = audit_ordinary_transfer(trial_dir)
        if audit.get("status") != "verified" or audit.get("errors"):
            raise ValueError("native trial audit is not verified")
        packet_path = review_dir / "reviewer" / "review-packet.json"
        if _sha(packet_path) != arm.get("review_packet_sha256"):
            raise ValueError("blind packet differs from the sealed study record")
        key = json.loads((review_dir / "private" / "condition-key.json").read_text(encoding="utf-8"))
        if key.get("trial_record_sha256") != native_digest:
            raise ValueError("private condition key is not bound to this native trial")
        stages = {entry["stage"]: entry for entry in native["stages"]}
        for entry in key.get("mapping", {}).values():
            source = stages.get(entry.get("stage"))
            if (source is None
                    or source.get("result_event_id") != entry.get("result_event_id")
                    or source.get("artifact", {}).get("sha256") != entry.get("response_sha256")):
                raise ValueError("private condition mapping does not match native results")
        review = grade(review_dir, ratings_by_order[order])
        if (review.get("trial_origin") != state["origin"]
                or not review.get("ratings_packet_bound")
                or not review.get("condition_mapping_bound")):
            raise ValueError("review must bind original blinded packet and key")
        for task, checks in CHECKS.items():
            observations = review["comparisons"][task]["observed_checks"]
            for check in checks:
                pair = observations[check]
                if type(pair.get("baseline")) is not bool or type(pair.get("conditional")) is not bool:
                    raise ValueError("incomplete blinded check judgment")
                paired[task][check].append(pair)

        calls = sum(native.get("provider_requests", {}).values())
        if (type(calls) is not int or calls != arm.get("observed_requests")
                or native.get("provider_requests") != arm.get("provider_requests")):
            raise ValueError("study request count does not match native trial")
        request_total += calls
        fully_accounted = bool(native["stages"]) and all(
            entry.get("usage", {}).get("accounted") is True for entry in native["stages"]
        )
        if fully_accounted != arm.get("usage_accounted"):
            raise ValueError("provider usage accounting changed since completion")
        accounted = accounted and fully_accounted
        reviewed.append({
            "task_order": order, "world_id": world_id,
            "reviewer": review["reviewer"], "ratings_packet_bound": True,
            "condition_mapping_bound": True, "observed_provider_requests": calls,
            "provider_usage_accounted": fully_accounted,
        })
    if (request_total != state.get("observed_requests")
            or request_total > state.get("limits", {}).get("max_total_requests", -1)):
        raise ValueError("combined provider requests do not match study limit")
    checks_summary = {}
    for task, checks in paired.items():
        checks_summary[task] = {}
        for check, pairs in checks.items():
            if len(pairs) != len(TASK_ORDERS):
                raise ValueError("missing review observations")
            checks_summary[task][check] = {
                "n": len(pairs),
                "baseline_passes": sum(pair["baseline"] for pair in pairs),
                "conditional_passes": sum(pair["conditional"] for pair in pairs),
                "improved_pairs": sum(not pair["baseline"] and pair["conditional"] for pair in pairs),
                "regressed_pairs": sum(pair["baseline"] and not pair["conditional"] for pair in pairs),
                "unchanged_pairs": sum(pair["baseline"] == pair["conditional"] for pair in pairs),
            }
    return {
        "schema": "per_pulse.ordinary_posture_study_review.v1",
        "study_sha256": _sha(state_path), "origin": state["origin"],
        "provider_identity": state["provider_identity"],
        "review_status": "descriptive_human_checks" if state["origin"] == "real_provider" else "synthetic_qa_only",
        "arms": reviewed, "observed_provider_requests": request_total,
        "provider_usage_accounted": accounted, "checks": checks_summary,
        "interpretation": (
            "Reviewer identity and independence are self-declared. The audits "
            "bind native outputs and rating packets, not reviewer judgment quality. "
            "Unshared baseline always precedes sharing; order and extra "
            "conditional computation remain confounded. These paired changes "
            "are descriptive, not causal learning estimates or dollar costs."
        ),
    }


def _summarize_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Reaudit and summarize two blinded study arms")
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--notice-ratings", type=Path, required=True)
    parser.add_argument("--outline-ratings", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="create a private summary JSON without overwriting it")
    parser.add_argument("--allow-fixture", action="store_true", help="synthetic QA only")
    args = parser.parse_args(argv)
    try:
        result = summarize_reviews(args.study, {
            "notice-first": args.notice_ratings, "outline-first": args.outline_ratings,
        }, allow_fixture=args.allow_fixture)
        if args.output is not None:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            args.output.chmod(0o600)
    except (ValueError, OSError, KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        print(f"ordinary posture study review: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    if actual_argv and actual_argv[0] == "summarize":
        return _summarize_main(actual_argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--training-calls", type=int, default=8)
    parser.add_argument("--trial-calls", type=int, default=4)
    parser.add_argument("--max-total-requests", type=int, default=48)
    parser.add_argument("--stage-timeout", type=float, default=180)
    parser.add_argument("--allow-fixture", action="store_true", help="QA only; cannot support provider-quality claims")
    args = parser.parse_args(actual_argv)
    try:
        result = run_study(args.output, training_calls=args.training_calls,
                           trial_calls=args.trial_calls, max_total_requests=args.max_total_requests,
                           stage_timeout=args.stage_timeout, allow_fixture=args.allow_fixture)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"ordinary posture study: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] != "stopped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
