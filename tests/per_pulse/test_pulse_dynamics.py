"""Deterministic dynamics checks over the original engine and SQLite state."""
from datetime import datetime, timedelta, timezone
import math

import pytest

from pulse_system.core.causality import CausalLedger
from pulse_system.core.connection.network import ConnectionNetwork
from pulse_system.core.dendrite.processor import DendriteConfig, DendriteProcessor
from pulse_system.core.pulse.engine import PulseEngine, PulseEngineConfig, PulseEvent, PulseReason
from pulse_system.core.runtime.resource import RuntimeConfig, RuntimeManager
from pulse_system.core.types import ConnectionType
from pulse_system.substrate.storage import Storage


def engine_for(storage, *, config=None, **kwargs):
    manager = object()  # Selection/sideband checks deliberately never execute a turn.
    return PulseEngine(
        storage, manager, ConnectionNetwork(storage),
        DendriteProcessor(manager, DendriteConfig(silence_threshold=0, default_max_wait=0)),
        RuntimeManager(RuntimeConfig(budget_per_tick=0)),
        config or PulseEngineConfig(base_spontaneous_rate=0, succession_token_threshold=None),
        **kwargs,
    )


def test_content_depth_ceiling_keeps_lateral_inhibition_and_its_continuity(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: now)
    path = tmp_path / "world.db"
    storage = Storage(path)
    for subject in ("source", "excited", "inhibited"):
        storage.create_engram(subject)
    storage.create_connection("source", "excited", 0.8)
    storage.create_connection("source", "inhibited", 0.9, ConnectionType.INHIBITORY)
    config = PulseEngineConfig(max_content_propagation_depth=0, inhibition_tau=30)
    engine = engine_for(storage, config=config, world_id="world")
    engine.propagate("source", "A bounded synthetic activation.")
    assert engine._dendrite.get_queue_size("excited") == 0
    assert engine.inhibition_level("inhibited", now=now) == pytest.approx(0.9)
    engine.close()
    storage.close()
    now += timedelta(seconds=30)
    reopened = Storage(path)
    restored = engine_for(reopened, config=config, world_id="world")
    assert restored.inhibition_level("inhibited", now=now) == pytest.approx(0.9 / math.e)
    assert restored._dendrite.get_queue_size("excited") == 0
    assert restored.capacity_snapshot()["worker_running"] == 0
    monkeypatch.undo()  # Shutdown's independent real clock is not simulated.
    restored.close()
    reopened.close()


def test_admission_filter_defers_one_context_without_blocking_user_or_sibling(tmp_path):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    now = datetime.now(timezone.utc)
    reopen_at = now + timedelta(seconds=15)
    background = PulseEvent("subject", PulseReason.SPONTANEOUS, 0.3, center_id="reading")
    sibling = PulseEvent("subject", PulseReason.PROPAGATION, 0.8, center_id="writing")
    user = PulseEvent("subject", PulseReason.EXTERNAL, 1.0, center_id="reading")
    engine = engine_for(
        storage,
        execution_context=lambda event: event.center_id or "home",
        admission_filter=lambda event, at: (
            event.reason is PulseReason.EXTERNAL or event.center_id != "reading" or at >= reopen_at
        ),
    )
    engine._pending_events = [background, sibling, user]
    first, _ = engine._select_events(now, 2)
    assert first == [user, sibling]
    assert engine._pending_events == [background]
    second, _ = engine._select_events(reopen_at, 2)
    assert second == [background]
    assert engine._pending_events == []
    engine.close()
    storage.close()


def test_filter_does_not_erase_durable_event_and_rejects_non_boolean_result(tmp_path):
    storage = Storage(tmp_path / "world.db")
    storage.create_engram("subject")
    ledger = CausalLedger(storage)
    engine = engine_for(
        storage, causal_ledger=ledger, world_id="world",
        admission_filter=lambda _event, _now: False,
    )
    engine.inject_external_event("subject", "Synthetic queued input.")
    engine._refresh_durable_event_cache()
    event_id = engine._pending_events[0].event_id
    assert engine._select_events(datetime.now(timezone.utc), 1)[0] == []
    engine._refresh_durable_event_cache()
    assert [event.event_id for event in engine._pending_events] == [event_id]
    engine._admission_filter = lambda _event, _now: None
    with pytest.raises(TypeError, match="must return a bool"):
        engine._select_events(datetime.now(timezone.utc), 1)
    engine.close()
    storage.close()


def test_stability_recovers_gradually_without_erasing_prior_suppression(tmp_path):
    storage = Storage(tmp_path / "world.db")
    for subject in ("active", "quiet-a", "quiet-b", "quiet-c"):
        storage.create_engram(subject)
    storage.update_engram_metadata("active", last_pulse_at=datetime.now(timezone.utc))
    engine = engine_for(storage)
    engine._spontaneous_modifier = 0.1
    engine.tick()
    assert 0.1 < engine._spontaneous_modifier < 0.2
    engine._spontaneous_modifier = 3.0
    engine.tick()
    assert 1.0 < engine._spontaneous_modifier < 3.0
    engine.close()
    storage.close()


@pytest.mark.parametrize("field,value", [
    ("activity_halflife_seconds", 0), ("tick_interval", -1),
    ("base_spontaneous_rate", float("nan")), ("propagation_threshold", float("inf")),
    ("inhibition_propagation_gate", -0.1), ("budget_per_tick", True),
    ("max_pulse_retries", -1), ("topology_interval_ticks", 0),
])
def test_invalid_dynamics_fail_before_engine_can_start(tmp_path, field, value):
    storage = Storage(tmp_path / "world.db")
    with pytest.raises(ValueError, match=field):
        engine_for(storage, config=PulseEngineConfig(**{field: value}))
    storage.close()
