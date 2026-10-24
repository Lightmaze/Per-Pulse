"""Local pulse dynamics over real storage, without a model provider.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest

from pulse_system.core.connection.network import ConnectionNetwork
from pulse_system.core.causality import CausalLedger
from pulse_system.core.dendrite.processor import DendriteProcessor
from pulse_system.core.engram.manager import EngramManager
from pulse_system.core.pulse.engine import PulseEngine, PulseEngineConfig, PulseEvent, PulseReason
from pulse_system.core.runtime.resource import RuntimeManager
from pulse_system.core.types import (
    CausalEventDomain, CausalEventFlow, CausalEventKind, CausalEventSource,
    ConnectionType,
)
from pulse_system.substrate.storage.store import Storage


NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


class NoModel:
    def complete(self, *args, **kwargs):
        pytest.fail("Numeric dynamics must not request a model response.")


def make_engine(storage, **config):
    network = ConnectionNetwork(storage)
    manager = EngramManager(storage, NoModel(), network)
    dendrite = DendriteProcessor(manager)
    engine = PulseEngine(
        storage, manager, network, dendrite, RuntimeManager(),
        PulseEngineConfig(**config), world_id="dynamics-fixture-world",
    )
    return engine, dendrite


@pytest.fixture
def local_world(tmp_path, monkeypatch):
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: NOW)
    storage = Storage(tmp_path / "world.db")
    for engram_id in ("source", "excited", "inhibited"):
        storage.create_engram(engram_id)
    try:
        yield storage
    finally:
        storage.close()


@pytest.mark.parametrize("max_depth,source_depth", [(0, 0), (1, 1), (2, 3)])
def test_content_depth_ceiling_preserves_numeric_inhibition(local_world, max_depth, source_depth):
    storage = local_world
    storage.create_connection("source", "excited", 0.8)
    storage.create_connection("source", "inhibited", 0.6, ConnectionType.INHIBITORY)
    engine, dendrite = make_engine(storage, max_content_propagation_depth=max_depth)
    engine._pulse_depth["source"] = source_depth

    engine.propagate("source", "This content is beyond the diffusion ceiling.")

    assert dendrite.get_queue_size("excited") == 0
    assert dendrite.get_queue_size("inhibited") == 0
    assert engine.inhibition_level("inhibited", now=NOW) == pytest.approx(0.6)
    assert storage.get_session("inhibited") == []
    assert storage.get_session("excited") == []


def test_excitation_and_inhibition_remain_distinct_below_content_ceiling(local_world):
    storage = local_world
    storage.create_connection("source", "excited", 0.8)
    storage.create_connection("source", "inhibited", 0.6, ConnectionType.INHIBITORY)
    engine, dendrite = make_engine(storage, max_content_propagation_depth=1)

    engine.propagate("source", "An active local finding.")

    assert dendrite.get_queue_size("excited") == 1
    assert dendrite.get_queue_size("inhibited") == 0
    assert engine.inhibition_level("inhibited", now=NOW) == pytest.approx(0.6)
    assert engine.inhibition_level("excited", now=NOW) == 0


def test_inhibitory_impulses_accumulate_on_the_decayed_trace(local_world, monkeypatch):
    storage = local_world
    storage.create_connection("source", "inhibited", 0.6, ConnectionType.INHIBITORY)
    engine, _ = make_engine(storage, inhibition_tau=30.0)
    engine.propagate("source", "First output.")
    later = NOW + timedelta(seconds=30)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: later)

    engine.propagate("source", "Second output.")

    expected = 0.6 * math.exp(-1) + 0.6
    assert engine.inhibition_level("inhibited", now=later) == pytest.approx(expected)
    assert engine.inhibition_level("inhibited", now=later + timedelta(seconds=30)) == pytest.approx(expected / math.e)
    reopened, _ = make_engine(storage, inhibition_tau=30.0)
    assert reopened.inhibition_level("inhibited", now=later) == pytest.approx(expected)


def test_refractory_period_uses_persisted_completion_and_expires(local_world, monkeypatch):
    storage = local_world
    engine, _ = make_engine(storage, spontaneous_refractory_seconds=20)
    untouched = storage.get_engram("source")
    assert engine._compute_spontaneous_probability("source", untouched) > 0
    storage.update_engram_metadata("source", last_pulse_at=NOW, recent_activity=0.8)
    engram = storage.get_engram("source")
    assert engine._compute_spontaneous_probability("source", engram) == 0

    reopened, _ = make_engine(storage, spontaneous_refractory_seconds=20)
    assert reopened._compute_spontaneous_probability("source", storage.get_engram("source")) == 0
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: NOW + timedelta(seconds=19))
    assert reopened._compute_spontaneous_probability("source", engram) == 0
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: NOW + timedelta(seconds=20))
    assert reopened._compute_spontaneous_probability("source", engram) > 0


def test_refractory_clock_rollback_cannot_create_an_early_opportunity(local_world, monkeypatch):
    local_world.update_engram_metadata("source", last_pulse_at=NOW)
    engine, _ = make_engine(local_world, spontaneous_refractory_seconds=20)
    monkeypatch.setattr("pulse_system.core.pulse.engine._now", lambda: NOW - timedelta(seconds=60))
    assert engine._compute_spontaneous_probability("source", local_world.get_engram("source")) == 0


def test_refractory_is_local_to_the_completed_engram(local_world):
    local_world.update_engram_metadata("source", last_pulse_at=NOW)
    engine, _ = make_engine(local_world, spontaneous_refractory_seconds=20)
    assert engine._compute_spontaneous_probability("source", local_world.get_engram("source")) == 0
    assert engine._compute_spontaneous_probability("excited", local_world.get_engram("excited")) > 0


def test_refractory_period_does_not_block_explicit_input(local_world):
    local_world.update_engram_metadata("source", last_pulse_at=NOW)
    engine, _ = make_engine(local_world, spontaneous_refractory_seconds=20)
    external = PulseEvent("source", PulseReason.EXTERNAL, 1.0, content="Continue here.")
    engine._pending_events.append(external)

    selected, reservations = engine._select_events(NOW, 1)

    assert selected == [external]
    assert reservations == {}


def test_zero_refractory_preserves_legacy_opportunities(local_world):
    local_world.update_engram_metadata("source", last_pulse_at=NOW)
    engine, _ = make_engine(local_world)
    assert engine._compute_spontaneous_probability("source", local_world.get_engram("source")) > 0


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "20"])
def test_refractory_configuration_rejects_invalid_values(local_world, value):
    with pytest.raises(ValueError, match="spontaneous refractory seconds"):
        make_engine(local_world, spontaneous_refractory_seconds=value)


def test_foreign_world_backlog_cannot_hide_local_durable_input(local_world, monkeypatch):
    storage = local_world
    ledger = CausalLedger(storage)
    engine, _ = make_engine(storage)
    engine._causal_ledger = ledger
    # These are synthetic durable input fixtures, not model requests.

    def enqueue(world_id):
        return ledger.enqueue(
            world_id=world_id, engram_id="source",
            content="A durable local input.", flow=CausalEventFlow.CONTENT,
            domain=CausalEventDomain.PULSE, kind=CausalEventKind.STIMULUS,
            source=CausalEventSource.USER,
        )

    for _ in range(500):
        enqueue("another-world")
    own_event = enqueue("dynamics-fixture-world")
    monkeypatch.setattr(
        "pulse_system.core.pulse.engine._now",
        lambda: own_event.created_at + timedelta(seconds=60),
    )

    engine._refresh_durable_event_cache()

    assert [event.event_id for event in engine._pending_events] == [own_event.id]
