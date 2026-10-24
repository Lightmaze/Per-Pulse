"""Local activation allowances follow derived native Work across contexts."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import time

import pytest

from per_pulse.service import PageService
from pulse_system.core.types import CausalEventStatus
from test_native_pulse_bridge import factory_for, stream_response


def pump(pages, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pages.context_body.advance()
        pages.runtime.engine.tick()
        pages.runtime._native_work.drain()
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("The bounded native Work chain did not settle.")


def test_attention_create_and_report_share_one_allowance_across_contexts(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = body["messages"]
        if messages[-1]["role"] == "tool":
            return stream_response(text="A synthetic context turn settled.")
        notice = next((m["content"] for m in reversed(messages)
                       if m["role"] == "system" and "work_report_notice" in m["content"]), None)
        if notice:
            event_id = json.loads(notice.split("\n")[-1])["event_id"]
            return stream_response(tool="work", arguments={"op": "read_report", "event_id": event_id})
        if "DERIVED_WORK_INSTRUCTION" in json.dumps(messages):
            return stream_response(tool="work", arguments={
                "op": "report", "kind": "finding", "note": "A selected derived finding.",
            })
        return stream_response(tool="work", arguments={
            "op": "create", "title": "Derived page", "instruction": "DERIVED_WORK_INSTRUCTION",
        })

    pages = PageService(tmp_path, model_factory=factory_for(monkeypatch, handler))
    runtime, body = pages.runtime, pages.context_body
    now = body.clock()
    body.clock = lambda: now
    try:
        body.hold("home", "One concrete thought worth continuing.", 1)
        body.run(1)
        attention_event = None
        for _ in range(10):
            now += timedelta(seconds=5)
            attention_event = body.advance()
            if attention_event:
                break
        assert attention_event is not None

        def requests_for(op, status=None):
            return [record for record in runtime.storage.native_work_requests(limit=32)
                    if record["arguments"]["op"] == op and (status is None or record["status"] == status)]

        pump(pages, lambda: requests_for("create") and requests_for("create")[0]["error_code"] == "attention_allowance_required")
        create = requests_for("create")[0]
        assert create["status"] == "prepared"
        assert create["event_id"] is None
        assert create["attention_root_event_id"] == attention_event
        assert len(requests) == 2  # Two model rounds, one admitted Home turn.
        assert body.view()["remaining"] == 0
        for _ in range(3):
            runtime._native_work.drain()
        assert len(requests) == 2 and requests_for("create")[0]["event_id"] is None

        # A later explicit allowance admits exactly that retained Work input.
        body.hold("home", "Quiet after the selected continuation.", 0)
        body.run(1)
        runtime._native_work.drain()
        create = requests_for("create", "delivered")[0]
        assert body.view()["remaining"] == 0
        delivered_event = create["event_id"]
        runtime._native_work.drain()
        assert body.view()["remaining"] == 0  # Receipt replay never debits twice.
        assert requests_for("create", "delivered")[0]["event_id"] == delivered_event

        pump(pages, lambda: requests_for("report") and requests_for("report")[0]["error_code"] == "attention_allowance_required")
        report = requests_for("report")[0]
        assert report["attention_root_event_id"] == attention_event
        assert report["status"] == "prepared" and report["event_id"] is None
        assert len(requests) == 4  # Work ran; its automatic Home return did not.

        body.run(1)
        runtime._native_work.drain()
        report = requests_for("report", "delivered")[0]
        assert body.view()["remaining"] == 0
        pump(pages, lambda: runtime.causal_ledger.get_event(report["event_id"]).status is CausalEventStatus.SETTLED)
        assert len(requests) == 6
        assert body.view()["remaining"] == 0
        assert runtime._native_work.attention_origin(runtime.continuity_engram_id,
                                                     report["source_turn_id"]) == attention_event
    finally:
        asyncio.run(pages.close())


def prepare_deferred_create(pages):
    body, runtime = pages.context_body, pages.runtime
    now = body.clock()
    body.clock = lambda: now
    body.hold("home", "A synthetic local source.", 1)
    body.run(1)
    for _ in range(10):
        now += timedelta(seconds=5)
        if body.advance():
            break
    pump(pages, lambda: any(record["error_code"] == "attention_allowance_required"
                           for record in runtime.storage.native_work_requests(limit=32)))
    body.hold("home", "Quiet after this source turn.", 0)
    return runtime.storage.native_work_requests(limit=32)[0]


def create_only_handler(requests):
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["messages"][-1]["role"] == "tool":
            return stream_response(text="The source context settled.")
        return stream_response(tool="work", arguments={
            "op": "create", "title": "Deferred context", "instruction": "Retain this selected work.",
        })
    return handler


def test_pause_cancels_admitted_work_descendant_but_preserves_human_input(tmp_path, monkeypatch):
    requests = []
    pages = PageService(tmp_path, model_factory=factory_for(monkeypatch, create_only_handler(requests)))
    try:
        rec = prepare_deferred_create(pages)
        pages.context_body.run(1)
        pages.runtime._native_work.drain()
        derived = pages.runtime.storage.load_component_state(rec["request_id"])["event_id"]
        human = pages.send("home", "An explicit human message remains queued.")["messageId"]
        assert pages.runtime.causal_ledger.get_event(derived).status is CausalEventStatus.QUEUED
        pages.context_body.pause()
        assert pages.runtime.causal_ledger.get_event(derived).status is CausalEventStatus.CANCELLED
        assert pages.runtime.causal_ledger.get_event(human).status is CausalEventStatus.QUEUED
        assert len(requests) == 2
    finally:
        asyncio.run(pages.close())


@pytest.mark.parametrize("admitted", [False, True])
def test_restart_preserves_deferred_request_and_cancels_admitted_descendant(tmp_path, monkeypatch, admitted):
    requests = []
    pages = PageService(tmp_path, model_factory=factory_for(monkeypatch, create_only_handler(requests)))
    try:
        rec = prepare_deferred_create(pages)
        if admitted:
            pages.context_body.run(1)
            pages.runtime._native_work.drain()
        before = pages.runtime.storage.load_component_state(rec["request_id"])
        human = pages.send("home", "Retain this explicit input across reopening.")["messageId"]
    finally:
        asyncio.run(pages.close())

    reopened = PageService(tmp_path, model_factory=None)
    try:
        body, runtime = reopened.context_body, reopened.runtime
        assert body.view()["remaining"] == 0 and body.view()["enabled"] is False
        assert runtime.causal_ledger.get_event(human).status is CausalEventStatus.QUEUED
        runtime._native_work.drain()
        after = runtime.storage.load_component_state(rec["request_id"])
        assert after["attention_root_event_id"] == before["attention_root_event_id"]
        if admitted:
            assert after["status"] == "delivered"
            assert runtime.causal_ledger.get_event(after["event_id"]).status is CausalEventStatus.CANCELLED
        else:
            assert after["status"] == "prepared"
            assert after["error_code"] == "attention_allowance_required"
            assert after["event_id"] is None
        assert len(requests) == 2
    finally:
        asyncio.run(reopened.close())
