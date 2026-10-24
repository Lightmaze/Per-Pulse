"""Independent numeric and durable-admission checks for the context body.

These fixtures run the real World and causal ledger with no provider or live
model loop. Explicit synthetic settlements exercise feedback, not intelligence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest

from per_pulse.context_body import ContextBody, ContextBodyConfig, ContextBodyError
from per_pulse.service import PageService
from pulse_harness.core.contracts import ActionClassification, TerminalOutcome, TurnStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_system.service import native_replay
from pulse_system.core.types import CausalEventStatus


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def add(self, seconds):
        self.now += timedelta(seconds=seconds)
        return self.now


class WorldFixture:
    def __init__(self, path, **config):
        self.pages = PageService(path, model_factory=None)
        self.runtime = self.pages.runtime
        self.clock = Clock()
        self.body = ContextBody(
            self.runtime, configured=True, clock=self.clock,
            config=ContextBodyConfig(**config),
        )

    def advance(self, seconds=0):
        return self.body.advance(self.clock.add(seconds))

    def field(self, page="home"):
        return next(value for value in self.body.view()["contexts"] if value["pageId"] == page)

    def settle(self, event_id, content="An explicitly synthetic settlement.", **kwargs):
        event = self.runtime.causal_ledger.get_event(event_id)
        context = "work:" + event.center_id if event.center_id else ""
        turn = self.runtime.causal_ledger.begin_turn(
            event_id, event.engram_id, execution_context=context,
        )
        return self.complete_turn(turn, content, **kwargs)

    def complete_turn(self, turn, content, *, replay=None, corrupt_replay=False):
        """Settle matching Kernel and World records without a provider call."""
        store = self.runtime.kernel_service.store
        scope = self.runtime.harness.scope_for(turn.engram_id, turn.execution_context).for_turn(turn.id)
        store.create_session(scope.session_scope())
        try:
            store.get_turn(scope)
        except ScopeConflict:
            store.create_turn(scope, "Synthetic completed Kernel turn.")
        if replay is not None:
            action_id = "synthetic-replay-" + turn.id
            arguments = {"op": "enter", "message_ids": ["synthetic-selected-history"]}
            store.create_action(
                scope, action_request_id=action_id, tool_name="replay",
                arguments=arguments, requires_approval=False,
                classification=ActionClassification.READ_ONLY_NOT_REQUIRED,
            )
            output = {
                "kind": native_replay.KIND, "entry_action_id": action_id,
                "source_scope": scope.as_dict(), "message_ids": arguments["message_ids"],
            }
            if corrupt_replay:
                output["entry_action_id"] = "a-different-action"
            store.terminal_action(
                scope, action_id, TerminalOutcome.SUCCEEDED if replay else TerminalOutcome.FAILED,
                result={"output": output},
            )
        store.set_turn_status(scope, TurnStatus.SUCCEEDED)
        return self.runtime.causal_ledger.settle_turn(turn.id, content)

    def page(self, title):
        created = self.pages.create_page(title, "Explicit synthetic opening input.")
        self.settle(created["initialMessageId"])
        self.advance()
        return created["page"]["id"]

    def stage_attention_actions(self, turn, actions):
        """Author real Kernel action receipts for deterministic settlement tests."""
        store = self.runtime.kernel_service.store
        scope = self.runtime.harness.scope_for(self.runtime.continuity_engram_id).for_turn(turn.id)
        store.create_session(scope.session_scope())
        store.create_turn(scope, "Synthetic action settlement fixture.")
        with self.body._transaction() as (_conn, state):
            revision = state["contexts"]["home"]["revision"]
            for index, (note, drive, success) in enumerate(actions):
                action_id = f"synthetic-attention-{turn.id}-{index}"
                arguments = ({"op": "release"} if not note and drive == 0 else
                             {"op": "hold", "note": note, "drive": drive})
                store.create_action(
                    scope, action_request_id=action_id, tool_name="attention",
                    arguments=arguments, requires_approval=False,
                    classification=ActionClassification.READ_ONLY_NOT_REQUIRED,
                )
                store.terminal_action(scope, action_id,
                                      TerminalOutcome.SUCCEEDED if success else TerminalOutcome.FAILED)
                state["pendingUpdates"][action_id] = {
                    "pageId": "home", "note": note, "drive": drive,
                    "turnId": turn.id, "revision": revision, "scope": scope.as_dict(),
                    "actionId": action_id, "arguments": arguments,
                }
        store.set_turn_status(scope, TurnStatus.SUCCEEDED)


@pytest.fixture
def worlds(tmp_path):
    opened = []

    def create(**config):
        world = WorldFixture(tmp_path / str(len(opened)), **config)
        opened.append(world)
        return world

    yield create
    for world in reversed(opened):
        world.runtime.close()


def test_field_evolution_is_timestep_invariant_below_live_gap_cap(worlds):
    coarse, fine = worlds(excitation_tau=7), worlds(excitation_tau=7)
    for world in (coarse, fine):
        world.body.hold("home", "A retained local thought.", 0.7)
    for _ in range(4):
        coarse.advance(5)
    for _ in range(80):
        fine.advance(0.25)

    expected = 1.4 * (1 - math.exp(-20 / 7))
    assert coarse.field()["excitation"] == pytest.approx(expected)
    assert fine.field()["excitation"] == pytest.approx(expected)
    assert coarse.field()["excitation"] == pytest.approx(fine.field()["excitation"])
    assert coarse.body.view()["sequence"] == fine.body.view()["sequence"] == 0


def test_clock_rollback_and_long_pause_do_not_replay_offline_computation(worlds):
    world = worlds(excitation_tau=10, max_elapsed_seconds=5)
    world.body.hold("home", "A slowly developing thought.", 0.8)
    world.advance(3)
    before = world.field()["excitation"]
    anchor = world.body.view()["lastAdvancedAt"]

    world.advance(-30)
    assert world.field()["excitation"] == before
    assert world.body.view()["lastAdvancedAt"] == anchor
    world.advance(3600)
    expected = 1.6 + (before - 1.6) * math.exp(-5 / 10)
    assert world.field()["excitation"] == pytest.approx(expected)
    assert world.body.view()["sequence"] == 0


def test_equal_connection_strengths_produce_opposite_local_effects(worlds):
    positive = worlds(excitation_tau=1, inhibition_tau=10)
    negative = worlds(excitation_tau=1, inhibition_tau=10)
    results = []
    for world, weight in ((positive, 0.8), (negative, -0.8)):
        source = world.page("Source context")
        target = world.page("Receiving context")
        world.body.hold(target, "A question already belonging to this context.", 0.3)
        world.advance(1)
        world.body.connect(source, target, weight)
        world.body.run(1)
        source_input = world.pages.send(source, "Produce a local finding.")["messageId"]
        _turn, result = world.settle(source_input, "SYNTHETIC_LINKED_EVIDENCE")
        activation = world.advance()
        results.append((world, target, result, activation))

    plus, target_plus, result_plus, event_plus = results[0]
    minus, target_minus, _result_minus, event_minus = results[1]
    assert event_plus is not None
    assert plus.runtime.causal_ledger.get_event(event_plus).center_id == plus.runtime.world.get_task_front(target_plus).center_id
    assert plus.field(target_plus)["evidence"] == [{"eventId": result_plus.id, "pageId": plus.body.view()["connections"][0]["sourcePageId"]}]
    assert event_minus is None
    assert minus.field(target_minus)["inhibition"] == pytest.approx(0.8)
    assert minus.field(target_minus)["evidence"] == []
    assert all("SYNTHETIC_LINKED_EVIDENCE" not in message["content"] for message in minus.pages.view(target_minus)["messages"])
    assert minus.body.view()["remaining"] == 1


def test_pending_opportunity_cannot_consume_more_local_budget(worlds):
    world = worlds(excitation_tau=1, refractory_seconds=0)
    world.body.hold("home", "One pending thought.", 1.0)
    world.body.run(2)
    first = world.advance(1)
    assert first is not None
    for _ in range(20):
        assert world.advance(5) is None
    view = world.body.view()
    assert view["sequence"] == 1
    assert view["remaining"] == 1
    assert world.field()["pendingEventId"] == first


def test_attention_insert_failure_rolls_back_budget_and_local_reset(worlds, monkeypatch):
    world = worlds(excitation_tau=1)
    world.body.hold("home", "One transactional thought.", 1.0)
    world.body.run(2)
    ledger = world.runtime.causal_ledger
    original = ledger._insert_event_uncommitted

    def fail_attention(conn, event):
        if event.metadata.get("context_body") == world.body.key:
            raise RuntimeError("Injected admission failure after budget reservation.")
        return original(conn, event)

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_insert_event_uncommitted", fail_attention)
        with pytest.raises(RuntimeError, match="Injected admission failure"):
            world.advance(1)
    view, field = world.body.view(), world.field()
    assert view["remaining"] == 2
    assert view["sequence"] == 0
    assert field["pendingEventId"] is None
    assert field["pulseCount"] == 0
    assert field["lastPulseAt"] is None
    assert field["excitation"] > 1.0

    event_id = world.advance()
    assert event_id is not None
    assert world.body.view()["sequence"] == 1
    assert world.body.view()["remaining"] == 1


def test_reopen_pauses_grant_cancels_queued_attention_and_retains_field(worlds):
    world = worlds(excitation_tau=1)
    world.body.hold("home", "Remember this local thread.", 1)
    world.body.run(2)
    attention_event = world.advance(1)
    user_event = world.pages.send("home", "A human input must survive.")["messageId"]
    before = world.field()
    world.clock.add(86400)

    reopened = ContextBody(world.runtime, configured=True, config=world.body.config, clock=world.clock)

    assert reopened.view()["enabled"] is False
    assert reopened.view()["remaining"] == 0
    field = next(value for value in reopened.view()["contexts"] if value["pageId"] == "home")
    assert field["note"] == before["note"]
    assert field["excitation"] == before["excitation"]
    assert field["pulseCount"] == before["pulseCount"]
    assert world.runtime.causal_ledger.get_event(attention_event).status is CausalEventStatus.CANCELLED
    assert world.runtime.causal_ledger.get_event(user_event).status is CausalEventStatus.QUEUED
    assert reopened.advance(world.clock()) is None
    assert reopened.view()["sequence"] == 1


def test_release_cancels_only_its_queued_attention_opportunity(worlds):
    world = worlds(excitation_tau=1)
    world.body.hold("home", "A thought that will be released.", 1)
    world.body.run(2)
    attention_event = world.advance(1)
    user_event = world.pages.send("home", "Keep this explicit user input.")["messageId"]

    world.body.hold("home", "Released by the user.", 0)

    assert world.runtime.causal_ledger.get_event(attention_event).status is CausalEventStatus.CANCELLED
    assert world.runtime.causal_ledger.get_event(user_event).status is CausalEventStatus.QUEUED
    assert world.field()["pendingEventId"] is None


def test_release_does_not_cancel_an_already_running_local_turn(worlds):
    world = worlds(excitation_tau=1)
    world.body.hold("home", "One local continuation.", 1)
    world.body.run(2)
    attention_event = world.advance(1)
    turn = world.runtime.causal_ledger.begin_turn(attention_event, world.runtime.continuity_engram_id)

    world.body.hold("home", "No further background continuation.", 0)

    assert world.runtime.causal_ledger.get_event(attention_event).status is CausalEventStatus.RUNNING
    world.complete_turn(turn, "Synthetic completion of the already running turn.")
    assert world.advance(5) is None
    assert world.field()["pendingEventId"] is None
    assert world.field()["drive"] == 0


def test_identical_tick_times_do_not_activate_a_context_without_retained_drive(worlds):
    retained, quiet = worlds(excitation_tau=1), worlds(excitation_tau=1)
    retained.body.hold("home", "A concrete unresolved thought.", 1)
    quiet.body.hold("home", "A released thought.", 0)
    for world in (retained, quiet):
        world.body.run(2)

    assert retained.advance(1) is not None
    assert quiet.advance(1) is None
    assert quiet.advance(5) is None
    assert quiet.body.view()["remaining"] == 2
    assert quiet.body.view()["sequence"] == 0


def test_feedback_and_observation_do_not_duplicate_numeric_influence(worlds):
    world = worlds()
    source = world.page("Source")
    target = world.page("Receiver")
    world.body.connect(source, target, -0.7)
    event_id = world.pages.send(source, "Create one source result.")["messageId"]
    world.settle(event_id)
    world.advance()
    before = world.runtime.storage.load_component_state(world.body.key)
    assert world.field(target)["inhibition"] == pytest.approx(0.7)
    for _ in range(5):
        world.body.view()
    assert world.runtime.storage.load_component_state(world.body.key) == before

    world.advance()

    assert world.field(target)["inhibition"] == pytest.approx(0.7)
    assert world.field(source)["resultCount"] == 2


@pytest.mark.parametrize("release_succeeds", [True, False])
def test_latest_successful_staged_attention_action_in_a_settled_turn_wins(worlds, release_succeeds):
    world = worlds()
    event_id = world.pages.send("home", "Consider and then let go.")["messageId"]
    subject = world.runtime.continuity_engram_id
    turn = world.runtime.causal_ledger.begin_turn(event_id, subject)
    world.stage_attention_actions(turn, [
        ("A provisional thought.", 0.8, True),
        ("", 0.0, release_succeeds),
    ])
    world.complete_turn(turn, "Synthetic settled decision to release.")

    world.advance()

    assert world.field()["note"] == ("" if release_succeeds else "A provisional thought.")
    assert world.field()["drive"] == (0 if release_succeeds else 0.8)


def test_later_user_hold_wins_over_staged_model_thought(worlds):
    world = worlds()
    event_id = world.pages.send("home", "Consider this context.")["messageId"]
    subject = world.runtime.continuity_engram_id
    turn = world.runtime.causal_ledger.begin_turn(event_id, subject)
    world.stage_attention_actions(turn, [("An earlier model thought.", 0.8, True)])
    world.body.hold("home", "The user's later focus.", 0.5)
    world.complete_turn(turn, "Synthetic settled response.")

    world.advance()

    assert world.field()["note"] == "The user's later focus."
    # A successful response reduces unresolved drive once; the staged update
    # must not overwrite the later user-authored revision.
    assert world.field()["drive"] <= 0.5


def test_disconnected_context_body_rejects_background_grants(worlds):
    world = worlds()
    disconnected = ContextBody(world.runtime, configured=False, clock=world.clock)
    with pytest.raises(ContextBodyError, match="Connect a model"):
        disconnected.run(1)
    assert disconnected.view()["remaining"] == 0


@pytest.mark.parametrize("replay_succeeded", [True, False])
def test_successful_replay_stays_local_and_failed_entry_stays_ordinary(worlds, replay_succeeded):
    world = worlds()
    source = world.page("Replay source")
    excited = world.page("Excitatory receiver")
    inhibited = world.page("Inhibitory receiver")
    world.body.connect(source, excited, 0.7)
    world.body.connect(source, inhibited, -0.6)
    before_excitation = world.field(excited)["excitation"]
    before_inhibition = world.field(inhibited)["inhibition"]
    event_id = world.pages.send(source, "Consider the selected previous experience.")["messageId"]
    _turn, result = world.settle(event_id, "Replay-looking text alone cannot select a regime.", replay=replay_succeeded)

    world.advance()

    assert world.field(source)["lastResultId"] == result.id
    assert world.field(source)["resultCount"] == 2
    assert world.field(excited)["excitation"] == pytest.approx(before_excitation + (0 if replay_succeeded else 0.7))
    assert world.field(inhibited)["inhibition"] == pytest.approx(before_inhibition + (0 if replay_succeeded else 0.6))
    expected_evidence = [] if replay_succeeded else [{"eventId": result.id, "pageId": source}]
    assert world.field(excited)["evidence"] == expected_evidence
    assert world.field(inhibited)["evidence"] == []


def test_corrupt_successful_replay_receipt_rolls_back_feedback_state(worlds):
    world = worlds()
    source = world.page("Corrupt replay source")
    target = world.page("Connected context")
    world.body.connect(source, target, 0.8)
    event_id = world.pages.send(source, "Replay a retained experience.")["messageId"]
    world.settle(event_id, "Explicit synthetic corrupt receipt fixture.", replay=True, corrupt_replay=True)
    before = world.runtime.storage.load_component_state(world.body.key)

    with pytest.raises(ScopeConflict, match="replay entry does not match"):
        world.advance()

    assert world.runtime.storage.load_component_state(world.body.key) == before
