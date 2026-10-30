"""Scoped RIM candidate choice: opt-in and bounded behavior checks.

No provider or paid-model calls. Constructed evidence here is not a real
observed delegation outcome. The native adapter separately verifies sources.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import math
import pytest

from pulse_system.service.execution_preference import _distribution, _sample, enabled
from pulse_system.service.native_team import schema, validate, descriptor, _target
from per_pulse.service import PageService


def _fixture(action, value, *, similarity=1.0, age=0.0, identity="source-1"):
    return {"id": identity, "action": action, "value": value,
            "features": (similarity,) + (0.0,) * 255,
            "age_seconds": age, "sources": ["event-1", "result-1"]}


QUERY = (1.0,) + (0.0,) * 255


def test_disabled_by_default_and_invalid_flag_does_not_enable(monkeypatch):
    monkeypatch.delenv("P_PULSE_RIM_POLICY", raising=False)
    assert not enabled()
    assert "consult_adaptive" not in schema()["properties"]["op"]["enum"]
    assert "consult_adaptive" in schema(policy=True)["properties"]["op"]["enum"]
    assert "consult_adaptive" not in descriptor().description
    assert "consult_adaptive" in descriptor(policy=True).description
    request = {"op": "consult_adaptive", "candidates": {"a": 0.6, "b": 0.4},
               "instruction": "Research actual startup latency."}
    with pytest.raises(ValueError, match="disabled"):
        validate(request)
    assert validate(request, policy=True) == request
    for value in ["true", "yes", "2", ""]:
        monkeypatch.setenv("P_PULSE_RIM_POLICY", value)
        with pytest.raises(ValueError, match="must be"):
            enabled()


@pytest.mark.parametrize("weights", [
    {"a": 1}, {"a": 0.6, "b": 0},
    {"a": 1, "b": -1}, {"a": 1, "b": True},
    {"a": 1, "b": float("nan")}, {"a": 1, "b": float("inf")},
])
def test_invalid_candidate_shape_does_not_propose_work(weights):
    with pytest.raises(ValueError):
        validate({"op": "consult_adaptive", "candidates": weights,
                  "instruction": "Investigate"}, policy=True)


def test_eligible_outcomes_change_real_candidate_probability_with_bounded_kl():
    prior = {"a": 0.6, "b": 0.4}
    plain = _distribution(prior, QUERY, [])
    assert plain["prior"] == plain["probabilities"]
    assert not plain["changed"]
    changed = _distribution(prior, QUERY, [
        _fixture("a", -1.0, identity="discarded-a"),
        _fixture("b", 1.0, identity="adopted-b"),
    ])
    assert changed["probabilities"]["b"] > plain["probabilities"]["b"]
    assert changed["probabilities"]["a"] < plain["probabilities"]["a"]
    assert math.isclose(math.fsum(changed["probabilities"].values()), 1.0)
    assert 0 < changed["kl"] <= 0.15 + 1e-12
    assert {x["id"] for x in changed["evidence"]} == {"discarded-a", "adopted-b"}
    assert _sample(changed["probabilities"], 0.99999) in {"a", "b"}


def test_unrelated_or_stale_outcomes_do_not_change_choice():
    prior = {"a": 0.7, "b": 0.3}
    unrelated = _distribution(prior, QUERY, [_fixture("b", 1.0, similarity=0.0)])
    assert not unrelated["changed"] and unrelated["probabilities"] == unrelated["prior"]
    old = _distribution(prior, QUERY, [
        _fixture("b", 1.0, age=365 * 24 * 3600),
    ])
    assert old["probabilities"]["b"] - old["prior"]["b"] < 0.0000001


def test_adaptive_explicit_source_quotes_are_validated_without_expanding_authority():
    request = {"op": "consult_adaptive",
               "candidates": {"expert-a": 0.6, "expert-b": 0.4},
               "instruction": "Reconsider this startup change.",
               "contributions": [{
                   "record_id": "finished-other-work-record",
                   "excerpt": "The previous reviewer identified the actual log line.",
               }]}
    assert validate(request, policy=True) == request
    with pytest.raises(ValueError, match="disabled"):
        validate(request, policy=False)
    with pytest.raises(ValueError, match="select each contribution once"):
        validate({**request, "contributions": request["contributions"] * 2}, policy=True)
    with pytest.raises(ValueError, match="exact excerpt"):
        validate({**request, "contributions": [{
            "record_id": "finished-other-work-record", "excerpt": "",
        }]}, policy=True)
    with pytest.raises(ValueError, match="incorrect team fields"):
        validate({**request, "prior_record_id": "cannot-pick-prior-author"}, policy=True)


def test_persisted_selection_resolves_without_resampling():
    rec = {"request_id": "native-team-v1:source", "arguments": {
        "op": "consult_adaptive", "candidates": {"a": 0.6, "b": 0.4},
        "instruction": "Research startup latency."},
        "preference": {"selected_expert_id": "b", "draw": 0.8}}
    assert _target(rec) == "b"
    assert _target(rec) == "b"  # the stored choice, not an additional draw
    assert _target({"request_id": "other", "arguments": {
        "op": "consult", "expert_id": "a", "instruction": "Help."}}) == "a"


def test_same_kernel_runtime_has_opt_in_but_offline_never_executes(tmp_path, monkeypatch):
    monkeypatch.setenv("P_PULSE_RIM_POLICY", "1")
    pages = PageService(tmp_path, model_factory=None)
    try:
        assert pages.runtime.harness._rim_policy
        assert pages.runtime.harness.team is not None
        assert not pages.configured
        assert pages.runtime.tick_count == 0
    finally:
        pages.runtime.close()
