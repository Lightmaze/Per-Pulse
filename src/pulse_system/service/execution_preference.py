"""Opt-in outcome-conditioned choice inside the existing native team TUNNEL.

Adapted from the upstream P-Pulse RIM PR #44 (head
0c696b015c9a94961ffddc6ae87c3bd9e56b6506), not a wholesale RIM import.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No second executor, model call, memory store, or inferred reward.
"""
from __future__ import annotations

import json
import math
import os
import random
import time

from pulse_harness.core.errors import ScopeConflict
from pulse_system.core.nn.features import stable_text_embedding

OUTCOMES = {"adopted": 1.0, "revised": 0.0, "discarded": -1.0}
_HALF_LIFE = 7 * 24 * 3600
_KL_LIMIT = 0.15


def enabled() -> bool:
    setting = os.environ.get("P_PULSE_RIM_POLICY", "0")
    if setting not in {"0", "1"}:
        raise ValueError("P_PULSE_RIM_POLICY must be 0 or 1")
    return setting == "1"


def _vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 256:
        raise ValueError("invalid persisted task representation")
    numbers = [float(v) for v in value]
    if not all(math.isfinite(n) for n in numbers):
        raise ValueError("invalid persisted task values")
    length = math.sqrt(math.fsum(n * n for n in numbers))
    return tuple(n / length for n in numbers) if length else tuple(numbers)


def _verified_outcome(team, owner, front, record):
    """Consume only a native owned Work's settled, operator-rated causal outcome."""
    runtime = team.runtime
    actual, request, origin = team._record(owner, record["id"])
    if (origin.id != front.id or actual.get("outcome") not in OUTCOMES
            or actual.get("completed_at") is None
            or actual.get("result_summary") is None):
        raise ValueError("not a completed, rated outcome of this Work")
    outcome_id = runtime.storage.find_delegation_outcome_event_id(actual["id"])
    event = runtime.causal_ledger.get_event(outcome_id) if outcome_id else None
    if event is None:
        raise ValueError("missing native outcome")
    meta = event.metadata
    if not (
        event.world_id == runtime.world_id and event.center_id == front.center_id
        and event.engram_id == owner and event.kind.value == "system"
        and event.source.value == "delegation" and event.status.value == "settled"
        and event.flow.value == "tunnel" and meta.get("record_id") == actual["id"]
        and meta.get("outcome") == actual["outcome"]
        and meta.get("delegation_mode") == "durable_pi"
        and meta.get("learning_signal_kind") == "delegation_outcome"
    ):
        raise ValueError("outcome is not native source-bound evidence")
    parent = runtime.causal_ledger.get_event(event.parent_event_id)
    if parent is None or not (
        parent.world_id == runtime.world_id and parent.center_id == front.center_id
        and parent.engram_id == owner and parent.kind.value == "delegation_result"
        and parent.source.value == "delegation" and parent.status.value == "settled"
        and parent.flow.value == "tunnel" and parent.causal_id == event.causal_id
        and parent.metadata.get("record_id") == actual["id"]
    ):
        raise ValueError("missing settled original contribution")
    representation = _vector(json.loads(actual["task_embedding"]))
    observed = (event.settled_at or event.updated_at).timestamp()
    return actual, representation, observed, (event.id, parent.id)


def _kl(values, prior):
    return math.fsum(p * math.log(p / prior[k]) for k, p in values.items() if p > 0)


def _distribution(candidates, query, evidence):
    """Bounded feedback-proxy tilt; this is not calibrated causal advantage."""
    if not isinstance(candidates, dict) or not 2 <= len(candidates) <= 8:
        raise ValueError("2..8 candidates required")
    prior_weights = dict(sorted(candidates.items()))
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
           for v in prior_weights.values()):
        raise ValueError("positive finite candidate weights required")
    maximum = max(prior_weights.values())
    relative = {k: v / maximum for k, v in prior_weights.items()}
    total = math.fsum(relative.values())
    prior = {k: v / total for k, v in relative.items()}
    mass = {k: 0.0 for k in prior}
    value_sum = dict(mass)
    admitted = []
    for item in evidence:
        k = item["action"]
        if k not in prior:
            continue
        score = math.fsum(a * b for a, b in zip(query, item["features"]))
        relevance = min(1.0, max(0.0, (score - 0.35) / 0.65))
        weight = relevance * 2 ** (-item["age_seconds"] / _HALF_LIFE)
        if weight <= 0:
            continue
        mass[k] += weight
        value_sum[k] += weight * item["value"]
        admitted.append({"id": item["id"], "sources": item["sources"], "action": k,
                         "value": item["value"], "mass": weight})
    values = {k: value_sum[k] / (2.0 + mass[k]) for k in prior}
    mean = math.fsum(prior[k] * values[k] for k in prior)
    relative_values = {k: values[k] - mean for k in prior}

    def tilt(beta):
        if not admitted or beta == 0 or len(set(values.values())) == 1:
            return dict(prior)
        logits = {k: math.log(prior[k]) + beta * relative_values[k] for k in prior}
        peak = max(logits.values())
        weights = {k: math.exp(v - peak) for k, v in logits.items()}
        z = math.fsum(weights.values())
        return {k: 0.05 * prior[k] + 0.95 * weights[k] / z for k in prior}

    beta = 3.0 if admitted else 0.0
    probabilities = tilt(beta)
    if _kl(probabilities, prior) > _KL_LIMIT:
        lo, hi = 0.0, beta
        for _ in range(48):
            middle = (lo + hi) / 2
            if _kl(tilt(middle), prior) <= _KL_LIMIT:
                lo = middle
            else:
                hi = middle
        beta, probabilities = lo, tilt(lo)
    return {"prior": prior, "probabilities": probabilities, "support": mass,
            "feedback_values": values, "relative_values": relative_values,
            "beta": beta, "kl": _kl(probabilities, prior),
            "evidence": admitted, "changed": probabilities != prior,
            "value_kind": "operator_adoption_proxy_not_causal_gain"}


def _sample(probabilities, draw):
    if not 0 <= draw < 1:
        raise ValueError("invalid selection draw")
    total = 0.0
    for candidate, probability in probabilities.items():
        total += probability
        if draw < total:
            return candidate
    return next(reversed(probabilities))


def choose(team, scope, front, arguments):
    """Select once and persist with the native request before any TUNNEL effect."""
    candidates = arguments["candidates"]
    known = team._known(scope.engram_id)
    if any(identity not in known for identity in candidates):
        raise ScopeConflict("adaptive candidates must be known active collaborators")
    runtime = team.runtime
    now = time.time()
    query = _vector(stable_text_embedding(arguments["instruction"]).tolist())
    rows = runtime.storage.list_delegations(caller_id=scope.engram_id, limit=128)
    evidence, groups, skipped = [], set(), 0
    for row in rows:
        if row["target_id"] not in candidates or row.get("outcome") not in OUTCOMES:
            continue
        try:
            actual, features, observed, sources = _verified_outcome(
                team, scope.engram_id, front, row)
        except (ValueError, KeyError, TypeError, ScopeConflict):
            skipped += 1
            continue
        group = (actual["target_id"], actual["task"], actual.get("contract"))
        if group in groups:
            continue
        groups.add(group)
        if observed > now:
            skipped += 1
            continue
        evidence.append({"id": actual["id"], "action": actual["target_id"],
                         "features": features, "value": OUTCOMES[actual["outcome"]],
                         "age_seconds": now - observed, "sources": list(sources)})
    result = _distribution(candidates, query, evidence)
    draw = random.SystemRandom().random()
    return {**result, "selected_expert_id": _sample(result["probabilities"], draw),
            "draw": draw, "version": 1, "upstream_rim_head":
            "0c696b015c9a94961ffddc6ae87c3bd9e56b6506",
            "records_scanned": len(rows), "history_may_be_incomplete": len(rows) == 128,
            "skipped_invalid_sources": skipped,
            "representation": "host_stable_text_embedding_256_not_semantic_guarantee",
            "selection": "source_bound_operator_outcome_proxy",
            "replay": "persisted_not_resampled"}
