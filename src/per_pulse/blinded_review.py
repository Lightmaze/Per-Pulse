"""Blind review of native ordinary-feedback transfer outputs.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This never calls a model or changes a World. An auditor binds the exported
responses to native turns; a separate reviewer judges their usefulness without
receiving the condition mapping. A single blinded comparison is not a causal
estimate and review identities are self-declared.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys

from .posture_transfer import audit_ordinary_transfer

SCHEMA = "per_pulse.ordinary_transfer_blind_review.v1"
PAIRS = (
    ("notice", "bare_transfer", "conditional_transfer", "A", "B"),
    ("outline", "bare_negative", "conditional_negative", "C", "D"),
)
CHECKS = {
    "notice": ("finished_notice", "availability_first", "facts_preserved"),
    "outline": ("outline_not_finished_notice", "facts_preserved"),
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_private(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    path.chmod(0o600)


def _verified_output(trial: Path) -> tuple[dict, dict]:
    audit = audit_ordinary_transfer(trial)
    if audit.get("status") != "verified" or audit.get("errors"):
        raise ValueError(f"native trial audit not verified: {audit.get('errors', audit.get('status'))}")
    record = _load_json(trial / "ordinary-transfer.json")
    if record.get("status") != "completed" or record.get("origin") not in {"real_provider", "synthetic_fixture"}:
        raise ValueError("the ordinary-feedback trial is not completed with a declared origin")
    return record, audit


def prepare(trial: Path, output: Path, *, allow_fixture: bool = False) -> dict:
    """Create reviewer-only packet and separate condition key after native audit."""
    record, _ = _verified_output(trial)
    if record["origin"] != "real_provider" and not allow_fixture:
        raise ValueError("synthetic fixture is not real-provider evidence (use --allow-fixture only for QA)")
    stage_map = {entry["stage"]: entry for entry in record["stages"]}
    protocol = {entry["stage"]: entry["prompt"] for entry in record["protocol"]}
    packet = {"schema": SCHEMA, "review_instruction": "Rate the delivered output against the requested task; do not infer its memory condition.",
              "items": []}
    key = {"schema": SCHEMA, "trial_origin": record["origin"],
           "trial_record_sha256": _sha((trial / "ordinary-transfer.json").read_bytes()),
           "mapping": {}, "conditions_disclosed_to_reviewer": False,
           "limitations": ["Reviewer identity and blindness are not independently authenticated.",
                           "Baseline was run before conditional sharing; order and compute are not controlled.",
                           "One trial cannot establish a general behavioral or causal improvement."]}
    for task, baseline, conditional, left, right in PAIRS:
        stages = [baseline, conditional]
        if secrets.randbelow(2):
            stages.reverse()
        for label, stage_name in zip((left, right), stages):
            entry = stage_map[stage_name]
            attachment = entry["artifact"]
            filename = attachment["path"]
            if Path(filename).name != filename or not filename.endswith("-response.md"):
                raise ValueError("unexpected artifact path")
            raw = (trial / filename).read_bytes()
            if _sha(raw) != attachment["sha256"]:
                raise ValueError("native response artifact digest mismatch")
            packet["items"].append({"label": label, "task": task, "prompt": protocol[stage_name],
                                     "response": raw.decode("utf-8"), "checks": list(CHECKS[task])})
            key["mapping"][label] = {"condition": "conditional" if stage_name.startswith("conditional") else "baseline",
                                      "stage": stage_name, "result_event_id": entry["result_event_id"],
                                      "response_sha256": attachment["sha256"], "usage": entry.get("usage", {})}
    if output.exists():
        raise ValueError("review output directory must be new")
    output.mkdir(parents=True, mode=0o700)
    (output / ".gitignore").write_text("*\n", encoding="utf-8")
    (output / ".gitignore").chmod(0o600)
    try:
        _write_private(output / "review-packet.json", packet)
        key["review_packet_sha256"] = _sha((output / "review-packet.json").read_bytes())
        _write_private(output / "condition-key.json", key)
        _write_private(output / "ratings-template.json", {"schema": SCHEMA, "reviewer": "",
            "ratings": {item["label"]: {check: None for check in item["checks"]} for item in packet["items"]}})
    except Exception:
        # Do not silently reuse partially created blind assignments.
        raise
    return {"output": str(output), "items": len(packet["items"]), "origin": record["origin"],
            "review_file": "review-packet.json", "private_key_file": "condition-key.json"}


def grade(review_dir: Path, ratings_file: Path) -> dict:
    """Reveal paired descriptive differences only after all ratings are complete."""
    packet = _load_json(review_dir / "review-packet.json")
    key = _load_json(review_dir / "condition-key.json")
    ratings = _load_json(ratings_file)
    if packet.get("schema") != SCHEMA or key.get("schema") != SCHEMA or ratings.get("schema") != SCHEMA:
        raise ValueError("review schema mismatch")
    if _sha((review_dir / "review-packet.json").read_bytes()) != key.get("review_packet_sha256"):
        raise ValueError("review packet changed since condition mapping was sealed")
    if not isinstance(ratings.get("reviewer"), str) or not ratings["reviewer"].strip():
        raise ValueError("a reviewer identifier is required")
    items = packet["items"]
    expected = {item["label"]: set(item["checks"]) for item in items}
    if set(ratings.get("ratings", {})) != set(expected) or set(key["mapping"]) != set(expected):
        raise ValueError("all blinded items must be rated exactly once")
    for label, checks in expected.items():
        entry = ratings["ratings"][label]
        if not isinstance(entry, dict) or set(entry) != checks or any(type(v) is not bool for v in entry.values()):
            raise ValueError(f"item {label} needs one boolean observation per check")
    comparisons = {}
    for task, _, _, left, right in PAIRS:
        group = {key["mapping"][label]["condition"]: label for label in (left, right)}
        if set(group) != {"baseline", "conditional"}:
            raise ValueError("invalid condition mapping")
        b, c = group["baseline"], group["conditional"]
        comparisons[task] = {"baseline_label": b, "conditional_label": c,
                             "observed_checks": {check: {"baseline": ratings["ratings"][b][check],
                                                        "conditional": ratings["ratings"][c][check]}
                                                 for check in CHECKS[task]},
                             "usage_accounted": {condition: key["mapping"][label]["usage"].get("accounted")
                                                 for condition, label in (("baseline", b), ("conditional", c))}}
    return {"schema": SCHEMA, "reviewer": ratings["reviewer"], "trial_origin": key["trial_origin"],
            "blindness_self_report_only": True, "comparisons": comparisons,
            "interpretation": "Descriptive paired reviewer judgments only; not proof of causal improvement."
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="create a condition-blind response review packet")
    p.add_argument("trial", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--allow-fixture", action="store_true", help="only for synthetic QA; not model evidence")
    g = sub.add_parser("grade", help="reveal descriptive pairs after all checks are scored")
    g.add_argument("review_dir", type=Path)
    g.add_argument("ratings", type=Path)
    try:
        args = parser.parse_args(argv)
        result = prepare(args.trial, args.output, allow_fixture=args.allow_fixture) if args.command == "prepare" else grade(args.review_dir, args.ratings)
    except (ValueError, OSError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"blinded review: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
