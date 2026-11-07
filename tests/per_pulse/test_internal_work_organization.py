"""Internal Work organization has its own scope, separate from file effects.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Only model decisions are synthetic; World, Kernel, outbox and file reads are real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from material_fixture import MaterialFixture
from per_pulse.service import PageService, PulseSettings
from pulse_harness.core import ToolCall
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.provider import CancellationToken
from pulse_system.agent.harness.base import HarnessError
from pulse_system.service.kernel_runtime import KernelHarnessFactory, KernelHarnessRuntime
from pulse_system.service.runtime import RuntimeService, RuntimeServiceConfig
from pulse_system.core.pulse.engine import PulseEvent
from pulse_system.core.types import ActivityOrigin


class WorkFixture(MaterialFixture):
    def __init__(self, setenv):
        super().__init__(setenv)
        self.plan = []
        self.outcomes = []
        self.finish = "succeed"
        self.cancel = None

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        current = max(i for i, item in enumerate(messages) if item["role"] == "user")
        results = [json.loads(item["content"]) for item in messages[current + 1:] if item["role"] == "tool"]
        if len(results) < len(self.plan):
            name, arguments = self.plan[len(results)]
            return self.response(tool=name, arguments=arguments, sequence=len(results))
        self.outcomes = results
        if self.finish == "fail":
            return httpx.Response(400)
        if self.finish == "cancel":
            self.cancel()
        return self.response("The selected internal work is complete. [Explicit synthetic fixture]")


@pytest.fixture
def local(tmp_path, monkeypatch):
    fixture = WorkFixture(monkeypatch.setenv)
    monkeypatch.setattr("pulse_system.core.pulse.context.random.random", lambda: 0.0)
    pages = PageService(tmp_path, model_factory=fixture.factory, posture=False,
                        allow_workspace_actions=False, pulse=PulseSettings(
                            enabled=True, spontaneous_rate=1, refractory_seconds=0,
                            check_interval_seconds=0.001, adaptive_connections=False,
                            spectrum_enabled=False, max_background_pulses_per_hour=4))
    (tmp_path / "workspace" / "notes.txt").write_text("A real note to inspect without changing files.\n")
    try:
        yield pages, fixture
    finally:
        pages.runtime.close()


def run_event(runtime, event):
    result = runtime._mgr.pulse(event.engram_id, pulse_event=event,
                                execution_context=runtime._native_kernel_context_for_center(event.center_id) or "home",
                                runtime_fence=runtime._current_runtime_fence())
    return runtime.causal_ledger.get_event(result.result_event_id)


def home(pages, fixture, plan):
    fixture.plan = plan
    sent = pages.send("home", "Organize the investigation in an internal Work.")
    return run_event(pages.runtime, pages.runtime.causal_ledger.get_event(sent["messageId"]))


def create_plan():
    return [("work", {"op": "create", "title": "Read-only investigation",
                       "instruction": "Read notes.txt and report the useful finding."})]


def delivered(runtime, receipt):
    runtime._native_work.drain()
    event = runtime.causal_ledger.find_causal_event_by_idempotency(runtime.world_id, receipt["request_id"])
    assert event is not None and event.source.value == "self"
    assert event.metadata["stimulus_source_turn_id"]
    return event


def test_product_organizes_and_reports_work_with_file_actions_disabled(local):
    pages, fixture = local
    runtime = pages.runtime
    source = home(pages, fixture, create_plan())
    assert fixture.outcomes[-1]["status"] == "succeeded", fixture.outcomes[-1]
    receipt = fixture.outcomes[-1]["output"]
    assert runtime.list_task_fronts() == []  # Delivery waits for the existing outbox drain.
    incoming = delivered(runtime, receipt)
    assert runtime._native_foreground_event(PulseEvent.from_causal_event(incoming))
    front = runtime.world.get_task_front(receipt["work_id"])
    assert front.focal_engram_id == runtime.continuity_engram_id
    assert runtime.world.get_activity_center(front.center_id).origin.value == "self"
    assert incoming.center_id == front.center_id
    assert runtime.causal_ledger.get_turn_for_world(
        incoming.metadata["stimulus_source_turn_id"], runtime.world_id).result_event_id == source.id
    assert source.parent_event_id != incoming.id

    fixture.plan = [("read_file", {"path": "notes.txt"}),
                    ("work", {"op": "report", "kind": "finding", "note": "The real note is available without edits."})]
    work_result = run_event(runtime, incoming)
    assert all(result["status"] == "succeeded" for result in fixture.outcomes)
    assert fixture.outcomes[0]["output"]["content"] == "A real note to inspect without changing files.\n"
    report = delivered(runtime, fixture.outcomes[-1]["output"])
    assert report.center_id is None
    assert runtime._native_foreground_event(PulseEvent.from_causal_event(report))
    origin = runtime._native_work.report_origin(report.id, report.idempotency_key,
                                               runtime.continuity_engram_id, report.content)
    assert origin["work_id"] == front.id
    turn = runtime.causal_ledger.get_turn_for_world(origin["source_turn_id"], runtime.world_id)
    assert turn.result_event_id == work_result.id
    fixture.plan = []
    run_event(runtime, report)

    home(pages, fixture, [("work", {"op": "list"}), ("work", {"op": "inspect", "work_id": front.id}),
                          ("work", {"op": "send", "work_id": front.id, "instruction": "Inspect the note once more."})])
    assert all(result["status"] == "succeeded" for result in fixture.outcomes)
    assert fixture.outcomes[0]["output"]["works"][0]["id"] == front.id
    followup = delivered(runtime, fixture.outcomes[-1]["output"])
    assert followup.center_id == front.center_id
    assert runtime._native_foreground_event(PulseEvent.from_causal_event(followup))
    assert len(runtime.list_task_fronts()) == 1 and len(runtime.storage.list_engrams()) == 1
    for request in fixture.requests:
        names = {tool["function"]["name"] for tool in request["tools"]}
        assert "mutate_file" not in names and "run_command" not in names
    assert (pages.files.root / "notes.txt").read_text() == "A real note to inspect without changing files.\n"


def test_spontaneous_home_can_create_a_same_subject_work_without_new_user_input(local):
    pages, fixture = local
    runtime = pages.runtime
    home(pages, fixture, [])
    runtime.context_pulse.tick(now=datetime.now(timezone.utc) + timedelta(seconds=1))
    trigger, = runtime.causal_ledger.list_events(world_id=runtime.world_id, kind="spontaneous",
                                                 status="queued", limit=10)
    assert trigger.content is None and trigger.source.value == "self"
    fixture.plan = create_plan()
    run_event(runtime, trigger)
    assert fixture.outcomes[-1]["status"] == "succeeded", fixture.outcomes[-1]
    incoming = delivered(runtime, fixture.outcomes[-1]["output"])
    source_turn = runtime.causal_ledger.get_turn_for_world(
        incoming.metadata["stimulus_source_turn_id"], runtime.world_id)
    assert source_turn.event_id == trigger.id and source_turn.prompt_accepted is True
    assert incoming.source.value == "self" and len(runtime.storage.list_engrams()) == 1
    assert not runtime._native_foreground_event(PulseEvent.from_causal_event(trigger))
    assert not runtime._native_foreground_event(PulseEvent.from_causal_event(incoming))


def test_workspace_mutations_still_fail_at_the_provider_boundary(local):
    pages, fixture = local
    home(pages, fixture, [])
    runtime = pages.runtime
    scope = runtime.harness.scope_for(runtime.continuity_engram_id)
    provider = runtime.kernel_service._runtime(scope).provider
    turn_scope = runtime.kernel_service.store.turns_for_session(scope)[-1].scope
    for name, arguments in [
        ("mutate_file", {"path": "denied.txt", "expected_digest": None, "content": "Forbidden"}),
        ("run_command", {"argv": ["touch", "denied.txt"]}),
    ]:
        with pytest.raises(ScopeConflict, match="read-only"):
            provider.execute_for_scope(turn_scope, ToolCall("forged-file-effect", name, arguments), CancellationToken())
    assert not (pages.data_dir / "workspace" / "denied.txt").exists()


@pytest.mark.parametrize("finish", ["fail", "cancel"])
def test_unsuccessful_home_cannot_deliver_prepared_work(local, finish):
    pages, fixture = local
    runtime = pages.runtime
    fixture.finish = finish
    if finish == "cancel":
        def cancel():
            turn = runtime.causal_ledger.get_running_turn_for_context(runtime.continuity_engram_id, "home")
            runtime.harness.abort(runtime.continuity_engram_id, turn_id=turn.id)
        fixture.cancel = cancel
    with pytest.raises(HarnessError):
        home(pages, fixture, create_plan())
    assert fixture.outcomes[-1]["status"] == "succeeded"
    runtime._native_work.drain()
    request, = runtime.storage.native_work_requests()
    assert request["status"] == "cancelled" and request["event_id"] is None
    assert runtime.list_task_fronts() == []


@pytest.mark.parametrize("file_actions,organization,expected", [
    (False, None, False), (True, None, True), (False, True, True), (True, False, False),
])
def test_generic_factory_preserves_legacy_policy_and_accepts_an_explicit_work_capability(
    tmp_path, monkeypatch, file_actions, organization, expected,
):
    fixture = WorkFixture(monkeypatch.setenv)
    options = {} if organization is None else {"allow_work_organization": organization}
    factory = KernelHarnessFactory(db_path=tmp_path / "kernel.db", workspace=tmp_path / "files",
                                   model_factory=fixture.factory, allow_mutations=file_actions,
                                   enable_posture=False, **options)
    runtime = RuntimeService(RuntimeServiceConfig(
        db_path=tmp_path / "world.db", workspace=tmp_path / "files", harness_kind="kernel",
        base_spontaneous_rate=0, silence_threshold=0, default_max_wait=0), harness_factory=factory)
    try:
        assert runtime.harness.allow_work_organization is expected
        fixture.plan = create_plan()
        event_id = runtime.inject(runtime.continuity_engram_id, "Organize this source-bound investigation.")
        run_event(runtime, runtime.causal_ledger.get_event(event_id))
        assert (fixture.outcomes[-1]["status"] == "succeeded") is expected
        names = {tool["function"]["name"] for tool in fixture.requests[0]["tools"]}
        assert ("mutate_file" in names) is file_actions
        runtime._native_work.drain()
        assert len(runtime.list_task_fronts()) == int(expected)
    finally:
        runtime.close()


def test_example_mode_keeps_work_organization_disabled(tmp_path):
    pages = PageService(tmp_path, example=True, model_factory=None, posture=False)
    try:
        assert pages.runtime.harness.allow_work_organization is False
        assert not pages.configured and pages.runtime.tick_count == 0
    finally:
        pages.runtime.close()


def test_internal_work_setting_is_explicitly_boolean(tmp_path):
    with pytest.raises(ValueError, match="allow_work_organization"):
        KernelHarnessRuntime(db_path=tmp_path / "kernel.db", workspace=tmp_path / "files",
                             allow_unconfigured=True, allow_work_organization="enabled")


def test_internal_organization_preserves_home_and_work_operation_boundaries(local):
    pages, fixture = local
    runtime = pages.runtime
    created = pages.create_page("An existing Work", "Keep this Work's instructions local.")
    fixture.plan = create_plan()
    run_event(runtime, runtime.causal_ledger.get_event(created["initialMessageId"]))
    assert fixture.outcomes[-1]["status"] == "failed"  # create remains Home-only.
    home(pages, fixture, [("work", {"op": "report", "kind": "finding", "note": "Home cannot report as Work."})])
    assert fixture.outcomes[-1]["status"] == "failed"
    assert runtime.storage.native_work_requests() == []
    assert len(runtime.list_task_fronts()) == 1


def test_internal_organization_cannot_send_to_another_subjects_work(local):
    pages, fixture = local
    runtime = pages.runtime
    other = runtime.engrams.create(auto_name=False).id
    foreign = runtime.world.create_task_bundle_for_existing_engram("Other subject's Work", other).front
    home(pages, fixture, [("work", {"op": "send", "work_id": foreign.id,
                                   "instruction": "This source cannot direct the other subject."})])
    assert fixture.outcomes[-1]["status"] == "failed"
    assert runtime.storage.native_work_requests() == []


def test_successful_prepared_work_recovers_once_after_a_readonly_product_restart(local):
    pages, fixture = local
    home(pages, fixture, create_plan())
    assert fixture.outcomes[-1]["status"] == "succeeded"
    receipt = fixture.outcomes[-1]["output"]
    pages.runtime.close()
    reopened = PageService(pages.data_dir, model_factory=None, posture=False, allow_workspace_actions=False)
    try:
        event = delivered(reopened.runtime, receipt)
        repeated = delivered(reopened.runtime, receipt)
        assert repeated.id == event.id
        assert len(reopened.runtime.list_task_fronts()) == 1
        assert reopened.runtime.world.get_task_front(receipt["work_id"]).focal_engram_id == reopened.runtime.continuity_engram_id
    finally:
        reopened.runtime.close()


def test_model_can_browse_older_works_after_first_32(local):
    """Home must not lose older persistent Works when the subject accumulates many."""
    pages, fixture = local
    runtime = pages.runtime
    subject = runtime.continuity_engram_id
    for index in range(35):
        runtime.world.create_task_bundle_for_existing_engram(
            f"Persistent Work {index:02}", subject,
            origin=ActivityOrigin.SELF,
            front_id=f"older_work_{index:03}",
            center_id=f"older_center_{index:03}",
        )

    home(pages, fixture, [("work", {"op": "list"})])
    assert fixture.outcomes[0]["status"] == "succeeded"
    first = fixture.outcomes[0]["output"]
    assert len(first["works"]) == 32
    assert first["more_works"] is True
    assert first["next_after_work_id"] == first["works"][-1]["id"]
    assert first["complete_history"] is False

    home(pages, fixture, [("work", {
        "op": "list", "after_work_id": first["next_after_work_id"]
    })])
    assert fixture.outcomes[0]["status"] == "succeeded"
    older = fixture.outcomes[0]["output"]
    assert len(older["works"]) == 3
    assert older["more_works"] is False
    assert older["next_after_work_id"] is None
    assert older["complete_history"] is False  # Request receipts are still bounded.
    ids = [front["id"] for front in first["works"] + older["works"]]
    assert len(ids) == len(set(ids)) == 35
    assert set(ids) == {f"older_work_{index:03}" for index in range(35)}
    assert all(front["focal_engram_id"] == subject for front in first["works"] + older["works"])

    # An actual older Work can now be inspected from the model's own list result.
    home(pages, fixture, [("work", {"op": "inspect", "work_id": older["works"][-1]["id"]})])
    assert fixture.outcomes[0]["status"] == "succeeded"
    assert fixture.outcomes[0]["output"]["work"]["id"] == older["works"][-1]["id"]

    # Do not allow an unknown or other-subject cursor to reveal a list position.
    from pulse_system.service.native_work import validate
    with pytest.raises(ValueError):
        validate({"op": "list", "after_work_id": ""})

    other = runtime.engrams.create(auto_name=False).id
    foreign = runtime.world.create_task_bundle_for_existing_engram(
        "Unrelated subject Work", other,
    ).front
    home(pages, fixture, [("work", {"op": "list", "after_work_id": foreign.id})])
    assert fixture.outcomes[0]["status"] == "failed"
