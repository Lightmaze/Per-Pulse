"""Context topology and inhibitory memory use the real durable World store.

Fixtures are synthetic and no provider calls occur.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest

from pulse_system.core.causality.ledger import CausalLedger
from pulse_system.core.connection import ContextConnectionNetwork
from pulse_system.core.types import CausalEventDomain, CausalEventKind, CausalEventSource
from pulse_system.substrate.storage.store import Storage


@pytest.fixture
def world(tmp_path):
    storage = Storage(tmp_path / "world.db")
    storage.save_component_state("pulse.world.v1", {
        "version": 1, "world_id": "world-a", "continuity_engram_id": "continuing-subject",
    })
    subject = storage.create_engram(engram_id="continuing-subject")
    first = storage.create_task_bundle_for_existing_engram("First context", subject.id)
    second = storage.create_task_bundle_for_existing_engram("Second context", subject.id)
    yield storage, first.center.id, second.center.id
    storage.close()


def settled_result(storage, *, center_id=None, content="A synthetic finding."):
    ledger = CausalLedger(storage)
    event = ledger.enqueue(
        "world-a", domain=CausalEventDomain.PULSE, kind=CausalEventKind.STIMULUS,
        source=CausalEventSource.USER, content="Explore the synthetic input.",
        engram_id="continuing-subject", center_id=center_id,
    )
    turn = ledger.begin_turn(event.id, "continuing-subject", event.content,
                             execution_context=center_id or "home")
    _, result = ledger.settle_turn(turn.id, content)
    return result


def test_context_edges_preserve_one_subject_and_survive_reopen(world, tmp_path):
    storage, first, second = world
    network = ContextConnectionNetwork(storage, "world-a")
    forward = network.connect(None, first, weight=0.65)
    lateral = network.connect(first, second, weight=0.4, kind="inhibitory")
    assert network.connect(None, first, weight=0.65) == forward
    assert network.outgoing(None) == (forward,)
    assert network.outgoing(first) == (lateral,)
    assert len(storage.list_engrams()) == 1
    assert storage.get_activity_center(first).focal_engram_id == "continuing-subject"
    assert storage.get_activity_center(second).focal_engram_id == "continuing-subject"
    reopened = Storage(tmp_path / "world.db")
    try:
        resumed = ContextConnectionNetwork(reopened, "world-a")
        assert resumed.outgoing(None) == (forward,)
        assert resumed.outgoing(first) == (lateral,)
    finally:
        reopened.close()


def test_new_link_has_durable_history_cutoff_and_reconnect_identity(world):
    storage, first, second = world
    old = settled_result(storage, center_id=first)
    network = ContextConnectionNetwork(storage, "world-a")
    edge = network.connect(first, second)
    assert edge.created_after_seq == old.seq
    later = settled_result(storage, center_id=first)
    edited = network.connect(first, second, weight=0.9)
    assert edited.id != edge.id
    assert edited.created_after_seq == later.seq > old.seq
    assert network.outgoing(first) == (edited,)
    assert network.connect(first, second, weight=0.9) == edited
    assert network.connect(first, second, weight=0.9, kind="inhibitory").id != edited.id
    assert network.disconnect(first, second)
    assert not network.disconnect(first, second)
    replacement = network.connect(first, second)
    assert replacement.id != edge.id
    assert replacement.created_after_seq == later.seq


def test_multiple_facades_do_not_overwrite_each_others_connections(world):
    storage, first, second = world
    left = ContextConnectionNetwork(storage, "world-a")
    right = ContextConnectionNetwork(storage, "world-a")
    left.connect(None, first)
    right.connect(first, second)
    left.connect(second, None)
    assert len(right.snapshot()["connections"]) == 3
    assert len(left.snapshot()["connections"]) == 3


def test_inhibition_decay_restart_and_replay_are_continuous(world):
    storage, first, second = world
    result = settled_result(storage, center_id=first)
    origin = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    network = ContextConnectionNetwork(storage, "world-a", inhibition_tau=20)
    assert network.apply_inhibition(second, 0.8, now=origin, source_event_id=result.id) == 0.8
    snapshot = storage.load_component_state(network.component_key)
    resumed = ContextConnectionNetwork(storage, "world-a", inhibition_tau=80)
    one_tau = origin + timedelta(seconds=20)
    # The saved policy governs elapsed time; restart cannot retroactively
    # apply a different decay constant or reapply a settled source result.
    assert resumed.inhibition_level(second, now=one_tau) == pytest.approx(0.8 / math.e)
    assert resumed.apply_inhibition(second, 0.8, now=one_tau, source_event_id=result.id) == pytest.approx(0.8 / math.e)
    assert storage.load_component_state(network.component_key) == snapshot
    assert resumed.inhibition_level(second, now=origin - timedelta(hours=1)) == 0.8
    next_result = settled_result(storage, center_id=first)
    added = resumed.apply_inhibition(second, 0.2, now=one_tau, source_event_id=next_result.id)
    assert added == pytest.approx(0.8 / math.e + 0.2)
    assert resumed.inhibition_level(second, now=one_tau + timedelta(seconds=80)) == pytest.approx(added / math.e)
    # High-water receipts remain after a trace has decayed to zero.
    far_later = origin + timedelta(days=5)
    assert resumed.apply_inhibition(second, 0.8, now=far_later, source_event_id=result.id) == 0


def test_failed_publication_leaves_no_phantom_edges_or_inhibition(world, monkeypatch):
    storage, first, second = world
    network = ContextConnectionNetwork(storage, "world-a")
    edge = network.connect(None, first)
    before = network.snapshot()

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic publication failure")

    monkeypatch.setattr(storage, "save_component_state", fail)
    with pytest.raises(RuntimeError, match="publication failure"):
        network.connect(first, second)
    with pytest.raises(RuntimeError, match="publication failure"):
        network.apply_inhibition(second, 0.5)
    with pytest.raises(RuntimeError, match="publication failure"):
        network.disconnect(None, first)
    assert network.snapshot() == before
    assert network.outgoing(None) == (edge,)


@pytest.mark.parametrize("value", [True, -0.1, 1.1, float("nan"), float("inf"), "0.5"])
def test_invalid_connection_weights_never_publish(world, value):
    storage, first, second = world
    network = ContextConnectionNetwork(storage, "world-a")
    with pytest.raises(ValueError, match="weight"):
        network.connect(first, second, weight=value)
    assert network.snapshot()["connections"] == []


def test_world_endpoint_and_source_validation(world):
    storage, first, second = world
    with pytest.raises(ValueError, match="PulseWorld"):
        ContextConnectionNetwork(storage, "another-world")
    network = ContextConnectionNetwork(storage, "world-a")
    with pytest.raises(ValueError, match="distinct"):
        network.connect(first, first)
    with pytest.raises(ValueError, match="does not exist"):
        network.connect(first, "another-world-center")
    other = storage.create_engram(engram_id="another-subject")
    other_context = storage.create_task_bundle_for_existing_engram("Other subject", other.id)
    with pytest.raises(ValueError, match="continuing subject"):
        network.connect(first, other_context.center.id)
    with pytest.raises(ValueError, match="kind"):
        network.connect(first, second, kind="magic")
    with pytest.raises(ValueError, match="settled result"):
        network.apply_inhibition(second, 0.3, source_event_id="missing")
    with pytest.raises(ValueError, match="timezone-aware"):
        network.apply_inhibition(second, 0.3, now=datetime(2026, 10, 23))
    assert network.snapshot()["connections"] == []
    assert network.snapshot()["inhibition"] == []


def test_snapshot_is_read_only_and_mutating_returned_data_has_no_effect(world):
    storage, first, second = world
    network = ContextConnectionNetwork(storage, "world-a")
    assert storage.load_component_state(network.component_key) is None
    assert network.snapshot()["connections"] == []
    assert network.inhibition_level(first) == 0
    assert storage.load_component_state(network.component_key) is None
    network.connect(first, second)
    view = network.snapshot()
    view["connections"].clear()
    assert len(network.outgoing(first)) == 1
