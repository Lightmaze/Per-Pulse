"""Work-level plasticity and spectrum actuation on real World/Kernel paths.

Model transport, when used, is explicitly synthetic. No paid provider calls.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pytest

from per_pulse.service import PulseSettings
from pulse_system.core.claustrum.context import ContextSpectrum
from pulse_system.core.claustrum.modulator import ClaustrumConfig, EngramState
from pulse_system.core.connection.network import ConnectionConfig, ConnectionNetwork
from pulse_system.core.dendrite.processor import DendriteConfig, DendriteProcessor
from pulse_system.core.learning_policy import OnlineLearningPolicy
from pulse_system.core.pulse.context import ContextPulseController
from pulse_system.core.pulse.engine import PulseEngine, PulseEngineConfig
from pulse_system.core.runtime.resource import RuntimeManager
from test_context_recovery import coordinator, result_for, propagated, commit_successor
from test_pulse_integration import TransportFixture, assistant_count, client_for, eventually, page, view


def edge(controller, source, target):
    return next((item for item in controller.connections.outgoing(source)
                 if item.target_center_id == target), None)


def spectrum_for(controller, **options):
    spectrum = ContextSpectrum(controller.storage, controller.world_id,
        config=ClaustrumConfig(max_slots=8, hidden_dim=8, noise_hot=0,
            noise_cold=0, hold_ticks=1, **options))
    controller._spectrum = spectrum
    return spectrum


def bias(spectrum, controller, center, head, value):
    key = controller._key(center)
    spectrum.register_engram(key)
    slot = spectrum._slots.slot_of(key, create=False)
    spectrum._mlp.b2[slot * 4 + head] = value


def test_same_engram_distinct_works_learn_a_directed_connection_from_receipts(coordinator):
    storage, ledger, _world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    first, second = result_for(ledger, a), result_for(ledger, b)
    controller.tick()
    learned = edge(controller, a, b)
    assert learned.origin == "learned"
    assert 0 < learned.weight <= 0.08
    assert learned.factory_weight == 0
    assert learned.learned_weight == learned.weight
    assert learned.learning_source_ids == [first.id, second.id]
    assert learned.created_after_seq >= second.seq
    assert controller.snapshot()["connections"][0]["aboveThreshold"] is False
    assert len(storage.list_engrams()) == 1
    assert propagated(ledger) == [], "A new edge must not relay its old training outputs."
    assert "synthetic result" not in json.dumps(controller.connections.snapshot())


def test_stdp_strengthens_forward_weakens_reverse_and_keeps_old_delivery_priority(coordinator):
    _storage, ledger, _world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    original = controller.connect(a, b, weight=0.6)
    controller.connect(b, a, weight=0.8)
    first = result_for(ledger, a)
    controller.tick()
    delivered = propagated(ledger)[0]
    result_for(ledger, b)
    controller.tick()
    forward, reverse = edge(controller, a, b), edge(controller, b, a)
    assert forward.id == original.id
    assert forward.weight > 0.6 and reverse.weight < 0.8
    assert forward.factory_weight == 0.6
    again = ledger.enqueue_context_propagation(source_result_id=first.id,
        target_center_id=b, connection_id=original.id, depth=1, priority=forward.weight)
    assert again.id == delivered.id
    assert again.metadata["priority"] == 0.6
    assert again.content == first.content


def test_learning_and_its_cursor_survive_a_failed_controller_checkpoint(coordinator, monkeypatch):
    _storage, ledger, _world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    result_for(ledger, a); result_for(ledger, b)
    def fail(_now):
        raise OSError("Synthetic checkpoint failure")
    monkeypatch.setattr(controller, "_save", fail)
    with pytest.raises(OSError, match="checkpoint"):
        controller.tick()
    before = controller.connections.snapshot()
    restored = build(spectrum_enabled=False)
    restored.tick()
    after = restored.connections.snapshot()
    assert after["connections"] == before["connections"]
    assert after["learning"]["pairs"] == before["learning"]["pairs"] == 1


def test_first_attachment_skips_imported_history_and_restart_keeps_new_unobserved_activity(coordinator):
    _storage, ledger, _world, (a, b, _), build = coordinator
    result_for(ledger, a); result_for(ledger, b)
    controller = build(spectrum_enabled=False)
    controller.tick()
    assert controller.connections.snapshot()["connections"] == []
    first, second = result_for(ledger, a), result_for(ledger, b)
    # Crash before either new result was observed by the controller.
    restored = build(spectrum_enabled=False)
    restored.tick()
    assert edge(restored, a, b).learning_source_ids == [first.id, second.id]
    assert restored.connections.snapshot()["learning"]["pairs"] == 1


def test_operator_disconnect_survives_learning_and_restart_until_explicit_reconnect(coordinator):
    _storage, ledger, _world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    result_for(ledger, a); result_for(ledger, b); controller.tick()
    assert controller.disconnect(a, b)
    restored = build(spectrum_enabled=False)
    result_for(ledger, a); result_for(ledger, b); restored.tick()
    assert edge(restored, a, b) is None
    assert [a, b] in restored.connections.snapshot()["learning"]["blocked"]
    restored.connect(a, b, weight=0.5)
    result_for(ledger, a); result_for(ledger, b); restored.tick()
    assert edge(restored, a, b).weight > 0.5
    assert [a, b] not in restored.connections.snapshot()["learning"]["blocked"]


@pytest.mark.parametrize("hold", ["paused", "held"])
def test_paused_context_does_not_form_new_associations(coordinator, hold):
    _storage, ledger, world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    result_for(ledger, a)
    if hold == "paused":
        world.update_activity_center(b, status="paused")
    else:
        controller.set_context_paused(b, True)
    result_for(ledger, b)
    controller.tick()
    assert controller.connections.snapshot()["connections"] == []


def test_replayed_result_and_other_participants_history_do_not_train_work_edges(coordinator):
    storage, ledger, world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    result_for(ledger, a)
    result_for(ledger, b, execution_regime="replay")
    storage.create_engram("other")
    foreign = world.create_task_bundle_for_existing_engram("Other owner", "other").center.id
    root = ledger.enqueue("context-world", kind="stimulus", source="user",
                          content="Synthetic foreign input", engram_id="other", center_id=foreign)
    turn = ledger.begin_turn(root.id, "other", root.content, execution_context=foreign)
    ledger.settle_turn(turn.id, "Synthetic foreign output")
    controller.tick()
    assert controller.connections.snapshot()["connections"] == []
    assert {value["center_id"] for value in controller.snapshot()["contexts"]} == {a}


def test_timing_learning_preserves_an_explicit_inhibitory_relation(coordinator):
    _storage, ledger, _world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    original = controller.connect(a, b, weight=0.7, kind="inhibitory")
    result_for(ledger, a); result_for(ledger, b); controller.tick()
    current = edge(controller, a, b)
    assert current.id == original.id and current.kind == "inhibitory"
    assert current.weight == 0.7 and current.learned_weight is None
    assert controller.connections.inhibition_level(b) > 0


def test_elapsed_time_decays_and_prunes_learned_edges_but_keeps_explicit_baselines(coordinator):
    _storage, ledger, _world, (a, b, c), build = coordinator
    controller = build(spectrum_enabled=False)
    controller.connect(a, c, weight=0.6)
    controller.connections.learning_config = ConnectionConfig(decay_rate=1, prune_threshold=0.05)
    result_for(ledger, a); result_for(ledger, b)
    now = datetime.now(timezone.utc)
    controller.tick(now=now)
    learned = edge(controller, a, b)
    assert learned is not None
    removed = controller.connections.decay_and_prune(now=now + timedelta(seconds=120))
    assert learned.id in removed
    assert edge(controller, a, b) is None
    assert edge(controller, a, c).weight == edge(controller, a, c).factory_weight == 0.6


def test_numeric_pruning_preserves_already_received_input_through_a_long_pause(coordinator):
    _storage, ledger, world, (a, b, _), build = coordinator
    controller = build(spectrum_enabled=False)
    controller.connections.learning_config = ConnectionConfig(stdp_strength=0.6,
        decay_rate=1, prune_threshold=0.3)
    result_for(ledger, a); result_for(ledger, b); controller.tick()
    original = edge(controller, a, b)
    result_for(ledger, a)
    now = datetime.now(timezone.utc)
    controller.tick(now=now)
    delivery = next(event for event in propagated(ledger) if event.center_id == b)
    world.update_activity_center(b, status="paused")
    controller.connections.decay_and_prune(now=now + timedelta(seconds=120))
    assert edge(controller, a, b).id == original.id
    assert edge(controller, a, b).weight < 0.3
    assert ledger.get_event(delivery.id).status.value == "queued"
    world.update_activity_center(b, status="active")
    turn = ledger.begin_turn(delivery.id, "subject", delivery.content, execution_context=b)
    ledger.settle_turn(turn.id, "The retained source was consumed after the pause.")
    removed = controller.connections.decay_and_prune(now=now + timedelta(seconds=180))
    assert original.id in removed


def test_disabled_learning_policy_keeps_numeric_inference_but_refuses_field_updates(coordinator):
    storage, ledger, world, (a, b, _), build = coordinator
    original = build()
    controller = ContextPulseController(storage, ledger, world, world_id="context-world",
        subject=original.subject, fence=original.fence, config=original.config,
        learning_policy=OnlineLearningPolicy.disabled())
    result_for(ledger, a); result_for(ledger, b)
    now = datetime.now(timezone.utc)
    for step in range(4):
        controller.tick(now=now + timedelta(seconds=step))
    assert controller.connections.snapshot()["connections"] == []
    channels = controller.learning_audit.snapshot(controller.learning_policy)["channels"]
    assert channels["connection_stdp"]["attempts"] > 0
    assert channels["connection_stdp"]["applied"] == 0
    assert channels["claustrum_mlp"]["attempts"] > 0
    assert channels["claustrum_mlp"]["applied"] == 0
    assert not np.any(controller._spectrum._mlp.w2)


def test_context_spectrum_changes_actual_source_propagation_threshold(coordinator):
    _storage, ledger, _world, (a, b, _), build = coordinator
    controller = build(adaptive_connections=False)
    controller.connect(a, b, weight=0.4)
    spectrum = spectrum_for(controller)
    bias(spectrum, controller, a, 2, 10)
    result_for(ledger, b); result_for(ledger, a)
    now = datetime.now(timezone.utc)
    controller.tick(now=now)
    assert controller._factors(a)[2] > 1.9
    assert propagated(ledger) == []
    bias(spectrum, controller, a, 2, 0)
    result = result_for(ledger, a)
    controller.tick(now=now + timedelta(seconds=1))
    assert len(propagated(ledger)) == 1
    assert propagated(ledger)[0].parent_event_id == result.id


@pytest.mark.parametrize("raw,admitted", [(-10, False), (10, True)])
def test_context_spectrum_changes_actual_reentry_probability(coordinator, monkeypatch, raw, admitted):
    _storage, ledger, _world, (a, _, _c), build = coordinator
    controller = build(adaptive_connections=False, spontaneous_rate=0.1)
    spectrum = spectrum_for(controller)
    bias(spectrum, controller, a, 0, raw)
    result_for(ledger, a)
    monkeypatch.setattr("pulse_system.core.pulse.context.random.random", lambda: 0.15)
    controller.tick()
    events = ledger.list_events(world_id="context-world", kind="spontaneous", limit=10)
    assert bool(events) is admitted
    if events:
        assert events[0].center_id == a and events[0].content is None


def test_work_wait_factors_reach_native_dendrite_and_existing_windows_stay_frozen(coordinator):
    storage, ledger, _world, (a, b, c), build = coordinator
    controller = build(adaptive_connections=False)
    controller.connect(a, b); controller.connect(a, c)
    spectrum = spectrum_for(controller)
    bias(spectrum, controller, b, 1, 10)
    bias(spectrum, controller, c, 1, -10)
    result_for(ledger, b); result_for(ledger, c); result_for(ledger, a)
    controller.tick()
    deliveries = propagated(ledger)
    manager = object()
    dendrite = DendriteProcessor(manager, DendriteConfig(silence_threshold=1, default_max_wait=2))
    engine = PulseEngine(storage, manager, ConnectionNetwork(storage), dendrite, RuntimeManager(),
        PulseEngineConfig(base_spontaneous_rate=0, succession_token_threshold=None),
        causal_ledger=ledger, world_id="context-world", context_wait_modifier=controller.wait_modifier)
    try:
        engine._refresh_durable_event_cache()
        policies = {delivery.center_id: ledger.ensure_dendritic_input_policy(
            delivery.id, dendrite.window_policy_snapshot("subject")) for delivery in deliveries}
        assert policies[b].wait_modifier > 1.9
        assert policies[c].wait_modifier < 0.6
        original = policies[b]
        spectrum._held_factors[controller._key(b)] = (1, 0.5, 1, 1)
        engine._refresh_durable_event_cache()
        assert ledger.ensure_dendritic_input_policy(
            next(delivery.id for delivery in deliveries if delivery.center_id == b),
            dendrite.window_policy_snapshot("subject")) == original
    finally:
        engine.close()


def test_spectrum_learns_from_work_activity_and_retains_credit_rng_and_slots(coordinator, monkeypatch):
    storage, ledger, _world, centers, build = coordinator
    controller = build(adaptive_connections=False)
    # Real accepted turns populate the Work features; only the clock is driven.
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("pulse_system.core.causality.ledger._now", lambda: now)
    for center in centers:
        result_for(ledger, center)
    config = ClaustrumConfig(max_slots=8, hidden_dim=8, hold_ticks=1,
                            learn_interval=4, noise_hot=0.4, noise_cold=0.4)
    spectrum = ContextSpectrum(storage, "context-world", config=config)
    controller._spectrum = spectrum
    controller.tick(now=now)
    for step in range(1, 4):
        now += timedelta(seconds=70)
        if step % 2:
            result_for(ledger, centers[0])
        controller.tick(now=now)
    assert spectrum._observations == 4
    assert np.any(spectrum._mlp.w2), "The spectrum learner must actually update its field weights."
    assert set(spectrum._slots.live_ids()) == {controller._key(center) for center in centers}
    assert len(storage.list_engrams()) == 1
    snapshot = spectrum.snapshot()
    restored = ContextSpectrum(storage, "context-world", config=config)
    assert restored.snapshot()["factors"] == snapshot["factors"]
    assert restored._observations == 4
    assert restored._rng.bit_generator.state == spectrum._rng.bit_generator.state
    assert len(restored._buffer) == len(spectrum._buffer)
    np.testing.assert_array_equal(restored._mlp.w2, spectrum._mlp.w2)
    prior = restored.last_sample_at
    restored.update([EngramState(controller._key(center), 1, 0, 0, 0, 0) for center in centers],
                    now=prior, active=1, interval=10)
    assert restored._observations == 4, "Restart/polling must not award the same observation twice."


def test_committed_subject_succession_keeps_work_connections_and_spectrum_slots(coordinator):
    storage, ledger, world, (a, b, _), build = coordinator
    controller = build()
    result_for(ledger, a); result_for(ledger, b); controller.tick()
    before = controller._spectrum.snapshot()
    learned = edge(controller, a, b)
    commit_successor(storage, ledger, world)
    successor = build(subject_id="successor")
    successor.tick()
    assert edge(successor, a, b).id == learned.id
    assert successor._spectrum.snapshot()["factors"] == before["factors"]
    assert set(successor._spectrum._slots.live_ids()) == set(controller._spectrum._slots.live_ids())


def test_overflow_contexts_stay_neutral_without_multiplying_rhythm_observations(coordinator):
    storage, _ledger, _world, centers, _build = coordinator
    config = ClaustrumConfig(max_slots=1, hidden_dim=4, hold_ticks=1)
    spectrum = ContextSpectrum(storage, "context-world", config=config)
    states = [EngramState("center:" + center, 1, 0, 0, 0, 0) for center in centers]
    now = datetime.now(timezone.utc)
    spectrum.update(states, now=now, active=1, interval=10)
    assert spectrum._observations == 1
    assert spectrum.factors(states[-1].engram_id) == (1, 1, 1, 1)
    restored = ContextSpectrum(storage, "context-world", config=config)
    rng = restored._rng.bit_generator.state
    restored.update(states, now=now + timedelta(seconds=1), active=1, interval=10)
    assert restored._observations == 1 and restored._rng.bit_generator.state == rng
    assert restored.capacity_snapshot()["claustrum_last_overflow"] == 2


def test_learned_work_edge_delivers_new_output_to_real_kernel_model_request(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spectrum_enabled=False, spontaneous_rate=0,
        refractory_seconds=0, check_interval_seconds=0.02,
        propagation_threshold=0.01, max_propagation_depth=1,
        max_background_pulses_per_hour=2)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        a = page(client, "First passage", "A_LOCAL_HISTORY")
        eventually(lambda: view(client, a), lambda item: assistant_count(item) == 1)
        b = page(client, "Second passage", "B_LOCAL_HISTORY")
        eventually(lambda: client.get("/api/pulse").json(), lambda state: any(
            item["sourcePageId"] == a and item["targetPageId"] == b for item in state["connections"]))
        assert len(fixture.requests) == 2
        assert client.post(f"/api/pages/{a}/messages", json={"content": "A_NEW_INPUT"}).status_code == 202
        eventually(lambda: view(client, b), lambda item: assistant_count(item) == 2)
        request = json.dumps(fixture.requests[-1]["messages"])
        assert "B_LOCAL_HISTORY" in request and "A_FIXTURE_OUTPUT" in request
        assert "A_LOCAL_HISTORY" not in request and "A_NEW_INPUT" not in request
        runtime = client.app.state.pages.runtime
        delivery = runtime.causal_ledger.list_events(kind="propagation", limit=10)[0]
        assert delivery.content == runtime.causal_ledger.get_event(delivery.parent_event_id).content
        assert len(runtime.storage.list_engrams()) == 1
