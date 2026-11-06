"""Public web observations enter the actual native World/Kernel history.

Model and web HTTP responses are explicitly synthetic fixtures.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from per_pulse.service import PageService, PulseSettings
from test_autonomous_examples import run_event
from test_pulse_integration import TransportFixture
from fastapi.testclient import TestClient
from per_pulse.app import create_app
from test_autonomous_practice_api import wait_for
from web_read_fixture import WebFixture, URL, TITLE, ORIGINAL
import pytest
from pulse_harness.adapters.history import ConversationHistoryTools
from pulse_harness.core import ToolCall
from pulse_harness.core.contracts import ActionStatus
from pulse_harness.core.errors import ScopeConflict
from pulse_harness.core.provider import CancellationToken
from pulse_harness.adapters import web_read


def test_first_self_departure_has_a_native_web_reading_tool(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    pages = PageService(tmp_path, model_factory=fixture.factory, posture=True,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=True, spontaneous_rate=0))
    try:
        run_event(pages, pages.runtime.request_first_autonomous_departure())
        assert "read_web" in {tool["function"]["name"] for tool in fixture.requests[0]["tools"]}
    finally:
        pages.runtime.close()


def test_self_reading_creates_sourced_file_then_recalls_old_observation_after_restart(tmp_path, monkeypatch):
    fixture = WebFixture(monkeypatch)
    def app():
        return create_app(tmp_path, model_factory=fixture.factory, posture=True,
            allow_workspace_actions=True, auto_apply_edits=True,
            pulse=PulseSettings(enabled=True, spontaneous_rate=0))
    with TestClient(app(), base_url="http://localhost") as client:
        started = client.post("/api/pulse/practice", json={"weeklyLimit": 20})
        assert started.status_code == 200, started.text
        pages = wait_for(lambda: client.get("/api/bootstrap").json()["pages"],
            lambda rows: any(row["title"] == TITLE for row in rows))
        work = next(row["id"] for row in pages if row["title"] == TITLE)
        view = wait_for(lambda: client.get("/api/pages/" + work).json(),
            lambda row: row["page"]["status"] == "idle" and bool(row["artifacts"]))
        source, = view["execution"]["webSources"]["items"]
        assert source["url"] == URL and source["title"] == "Field observations"
        assert source["truncated"] is False
        store = client.app.state.pages.runtime.kernel_service.store
        original_actions = store.actions_for_session
        queries = []
        def counted_actions(*args, **kwargs):
            queries.append(kwargs)
            return original_actions(*args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(store, "actions_for_session", counted_actions)
            assert client.app.state.pages.execution.snapshot(work)["webSources"]["items"] == [source]
        assert sum(query.get("status") is ActionStatus.SUCCEEDED for query in queries) == 1
        text = (tmp_path / "workspace/expedition.txt").read_text()
        assert ORIGINAL in text and URL in text and "not evidence" not in text
        home = wait_for(lambda: client.get("/api/pages/home").json(),
            lambda row: any(message.get("sourcePageId") == work for message in row["messages"]))
        assert not home["execution"]["webSources"]["items"], "source observations remain in their original Work"
        usage = wait_for(lambda: client.get("/api/pulse/weekly-usage").json()["usage"],
            lambda row: row["consumed"] == len(fixture.requests) and row["states"]["completed"] == len(fixture.requests))
        assert 6 <= usage["consumed"] <= 20
        assert len(fixture.web_requests) == 1
        client.patch("/api/pulse/weekly-usage", json={"enabled": False, "weeklyLimit": 20})
        before = len(fixture.requests)
    fixture.body = "<p>The live website has changed.</p>"
    with TestClient(app(), base_url="http://localhost") as client:
        assert client.get("/api/pages/" + work).json()["execution"]["webSources"]["items"] == [source]
        assert len(fixture.requests) == before
        client.post("/api/pages/" + work + "/messages", json={"content": "Recall the original webpage from saved history."})
        recalled = wait_for(lambda: client.get("/api/pages/" + work).json(),
            lambda row: row["messages"][-1]["content"] == "Recovered the original saved webpage observation.")
        assert recalled["execution"]["webSources"]["items"] == [source]
        assert len(fixture.web_requests) == 1, "history recall must not refetch the changed website"
        assert (tmp_path / "workspace/expedition.txt").read_text() == text
        assert client.get("/api/pulse/weekly-usage").json()["usage"]["consumed"] == usage["consumed"]
        pages = client.app.state.pages
        original = next(row for row in pages.runtime.kernel_service.store.transcript_rows(pages.execution._scope(work))
                        if row["role"] == "tool" and "pulse.web-observation.v1" in row["content"])
        other = client.post("/api/pages", json={"title": "Independent", "instruction": "Independent conversation."}).json()["page"]["id"]
        other_view = wait_for(lambda: client.get("/api/pages/" + other).json(),
            lambda row: row["page"]["status"] == "idle" and bool(row["messages"]))
        assert not other_view["execution"]["webSources"]["items"]
        history = ConversationHistoryTools(pages.runtime.kernel_service.store, pages.runtime.harness._history_scopes)
        other_scope = pages.execution._scope(other).for_turn(other_view["execution"]["turnId"])
        with pytest.raises(ScopeConflict):
            history.execute_for_scope(other_scope, ToolCall("cross-work", "read_history", {
                "role": "tool", "message_id": original["message_id"]}), CancellationToken())


@pytest.mark.parametrize("material", ["chinese", "emoji", "long_links"])
def test_large_web_observation_fits_actual_native_budget_and_continues(tmp_path, monkeypatch, material):
    """PerPulse uses its declared 1 MiB allowance, not the generic Kernel default."""
    fixture = WebFixture(monkeypatch)
    if material == "long_links":
        fixture.body += "".join('<a href="https://reading.example/' + str(index) + '/'
            + "q" * 4000 + '">Source</a>' for index in range(32))
    else:
        fixture.body += "<p>" + ("山" if material == "chinese" else "🌍") * 25000 + "</p>"
    pages = PageService(tmp_path, model_factory=fixture.factory, posture=True,
        allow_workspace_actions=True, auto_apply_edits=True,
        pulse=PulseSettings(enabled=True, spontaneous_rate=0))
    try:
        created = pages.create_page(TITLE, "Read " + URL + " and save a sourced field observation.")
        page = created["page"]["id"]
        scope = pages.execution._scope(page)
        pages.runtime._mgr.pulse(pages.runtime.continuity_engram_id,
            pulse_event=pages.runtime.causal_ledger.get_event(created["initialMessageId"]),
            execution_context=scope.context_id,
            runtime_fence=pages.runtime._current_runtime_fence())
        store = pages.runtime.kernel_service.store
        assert store.max_payload_bytes == 1024 * 1024
        action, = [row for row in store.actions_for_session(scope)
                   if row.tool_name == web_read.NAME]
        assert action.status is ActionStatus.SUCCEEDED and action.result_delivered
        observed = action.result["output"]
        assert observed["source"]["truncated"] is (material != "long_links")
        assert len(observed["links"]) == (32 if material == "long_links" else 0)
        assert pages.execution.snapshot(page)["status"] == "idle"
        assert pages.view(page)["messages"][-1]["content"] == "The sourced reading is saved in expedition.txt."
        assert (tmp_path / "workspace/expedition.txt").read_text() == observed["content"] + "\nSource: " + URL + "\n"
        assert len(fixture.web_requests) == 1
    finally:
        pages.runtime.close()
