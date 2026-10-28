"""Bounded ordinary-feedback study coordination; no external provider calls.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from per_pulse import posture_study


class FakeAdapter:
    capabilities = SimpleNamespace(adapter_id="offline-study-adapter", test_only=False)
    config = SimpleNamespace(
        endpoint="https://provider.invalid/chat/completions", model="fake-model",
        reasoning_field=None, provider_namespace=None, max_output_tokens=100,
    )

    def preflight_stream(self):
        pass

    def close(self):
        pass


def fake_factory(_scope):
    return FakeAdapter()


def test_requires_predeclared_two_arm_ceiling_before_provider_or_disk(tmp_path):
    seen = []

    def factory(scope):
        seen.append(scope)
        return FakeAdapter()

    with pytest.raises(ValueError, match="predeclared call ceiling"):
        posture_study.run_study(tmp_path / "study", model_factory=factory, max_total_requests=47)
    assert not seen
    assert not (tmp_path / "study").exists()


@pytest.mark.parametrize("kwargs", [
    {"training_calls": True}, {"trial_calls": 0}, {"max_total_requests": 0},
    {"stage_timeout": True},
])
def test_rejects_invalid_bounds_without_trials(tmp_path, kwargs):
    with pytest.raises(ValueError):
        posture_study.run_study(tmp_path / "study", model_factory=fake_factory, **kwargs)
    assert not (tmp_path / "study").exists()


def test_fixture_needs_explicit_qa_opt_in_before_creating_files(tmp_path):
    class FixtureAdapter(FakeAdapter):
        capabilities = SimpleNamespace(adapter_id="offline-study-adapter", test_only=True)

    with pytest.raises(ValueError, match="fixture provider"):
        posture_study.run_study(tmp_path / "study", model_factory=lambda _: FixtureAdapter())
    assert not (tmp_path / "study").exists()


def test_study_keeps_two_independent_worlds_and_separate_blind_keys(tmp_path, monkeypatch):
    seen = []
    from per_pulse.posture_transfer import _provider_identity
    identity = _provider_identity(FakeAdapter())

    def fake_run(path: Path, **kwargs):
        seen.append((path.name, kwargs["task_order"]))
        path.mkdir()
        record = {
            "origin": "real_provider", "provider_identity": identity,
            "world_id": f"world-{len(seen)}", "status": "completed",
            "capture_status": "native_lesson_admitted", "stages": [{"usage": {"accounted": True}}],
            "provider_requests": {"training": 3, "bare_transfer": 1,
                                  "bare_negative": 1, "conditional_transfer": 2, "conditional_negative": 2},
        }
        (path / "ordinary-transfer.json").write_text(json.dumps(record))
        return record

    def fake_prepare(trial, destination, **kwargs):
        assert not kwargs["allow_fixture"]
        assert trial.exists()
        (destination / "reviewer").mkdir(parents=True)
        (destination / "private").mkdir()
        (destination / "reviewer" / "review-packet.json").write_text('{"items":[]}')
        (destination / "private" / "condition-key.json").write_text("SECRET")
        return {"items": 4}

    monkeypatch.setattr(posture_study, "run_ordinary_transfer", fake_run)
    monkeypatch.setattr(posture_study, "audit_ordinary_transfer", lambda _: {"status": "verified", "errors": []})
    monkeypatch.setattr(posture_study, "prepare", fake_prepare)
    root = tmp_path / "study"
    result = posture_study.run_study(root, model_factory=fake_factory)
    assert result["status"] == "review_packets_ready"
    assert result["observed_requests"] == 18
    assert result["limits"]["total_possible_requests"] == 48
    assert seen == [("trial-notice-first", "notice-first"), ("trial-outline-first", "outline-first")]
    assert len(result["arms"]) == 2
    for arm in result["arms"]:
        assert arm["audit_status"] == "verified"
        assert arm["usage_accounted"] is True
        assert (root / arm["reviewer_only_directory"] / "review-packet.json").exists()
        assert (root / arm["blind_review_directory"] / "private" / "condition-key.json").exists()
    assert result["model_quality_reviewed"] is False
    assert json.loads((root / "study.json").read_text())["status"] == "review_packets_ready"


def test_unverified_first_trial_stops_before_second_or_blind_release(tmp_path, monkeypatch):
    seen = []

    def fake_run(path, **kwargs):
        seen.append(kwargs["task_order"])
        path.mkdir()
        from per_pulse.posture_transfer import _provider_identity
        record = {"origin": "real_provider", "provider_identity": _provider_identity(FakeAdapter()),
                  "world_id": "world-only", "status": "failed", "capture_status": "pending",
                  "stages": [], "provider_requests": {"training": 1}}
        (path / "ordinary-transfer.json").write_text(json.dumps(record))
        return record

    monkeypatch.setattr(posture_study, "run_ordinary_transfer", fake_run)
    monkeypatch.setattr(posture_study, "audit_ordinary_transfer", lambda _: {"status": "incomplete", "errors": []})
    result = posture_study.run_study(tmp_path / "study", model_factory=fake_factory)
    assert result["status"] == "stopped" and result["stop_reason"] == "trial_not_verified"
    assert seen == ["notice-first"]
    assert not list((tmp_path / "study").glob("review-*"))


def test_runner_exception_preserves_private_status_without_error_text(tmp_path, monkeypatch):
    seen = []

    def broken_run(path, **kwargs):
        seen.append(kwargs["task_order"])
        raise RuntimeError("provider request may contain a SECRET")

    monkeypatch.setattr(posture_study, "run_ordinary_transfer", broken_run)
    root = tmp_path / "study"
    result = posture_study.run_study(root, model_factory=fake_factory)
    assert result["status"] == "stopped"
    assert result["stop_reason"] == "trial_runner_exception"
    assert result["exception_type"] == "RuntimeError"
    assert seen == ["notice-first"]
    assert "SECRET" not in (root / "study.json").read_text()


def test_blind_packet_exception_stops_second_arm(tmp_path, monkeypatch):
    seen = []
    from per_pulse.posture_transfer import _provider_identity

    def fake_run(path, **kwargs):
        seen.append(kwargs["task_order"])
        path.mkdir()
        record = {"origin": "real_provider", "provider_identity": _provider_identity(FakeAdapter()),
                  "world_id": "world-only", "status": "completed",
                  "stages": [{"usage": {"accounted": True}}], "provider_requests": {"training": 2}}
        (path / "ordinary-transfer.json").write_text(json.dumps(record))
        return record

    monkeypatch.setattr(posture_study, "run_ordinary_transfer", fake_run)
    monkeypatch.setattr(posture_study, "audit_ordinary_transfer", lambda _: {"status": "verified", "errors": []})
    monkeypatch.setattr(posture_study, "prepare", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("broken packet")))
    root = tmp_path / "study"
    result = posture_study.run_study(root, model_factory=fake_factory)
    assert result["status"] == "stopped" and result["stop_reason"] == "blind_packet_exception"
    assert seen == ["notice-first"]
    assert not (root / "review-outline-first").exists()
