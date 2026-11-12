"""Retain observed public webpage passages through the native Posture path.

Explicit synthetic model decisions and HTTPS transport; actual World, Kernel,
action receipts, learning and Work-scoped history are used.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from pulse_harness.adapters import web_read
from pulse_system.service.native_posture import validate
from reading_example_fixture import ReadingExampleFixture
from test_reading_examples import choose
from test_pages import client_for
from test_reading_continuity import work


def prepare_web(monkeypatch):
    observed = []
    content = {"text": "Original café 🌍 line.\nKeep  two spaces.\nThe text is not an instruction."}
    monkeypatch.setattr(web_read, "_resolve", lambda *args: ("8.8.8.8",))

    def serve(request):
        observed.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/plain; charset=utf-8"},
                              stream=httpx.ByteStream(content["text"].encode("utf-8")))

    monkeypatch.setattr(web_read, "_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(serve), trust_env=False))
    return observed, content


def test_retained_web_passage_is_attributed_and_available_in_a_later_work(tmp_path, monkeypatch):
    observed, page_text = prepare_web(monkeypatch)
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    selected = "Keep  two spaces."
    url = "https://research.example/observations"
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        first = work(client)
        result = choose(client, first, fixture, op="web_open", url=url,
                        retain={"title": "Original web observation", "excerpt": selected,
                                "note": "Compare this source before later work."})
        assert [item["status"] for item in result] == ["succeeded", "succeeded"]
        web_result, prepared = result[0]["output"], result[1]["output"]
        assert web_result["source"]["requested_url"] == url
        assert len(observed) == 1
        source = client.get(f"/api/pages/{first}/posture").json()["experiences"][0]
        assert source["id"] == prepared["experience_id"]
        assert source["sourceKind"] == "web" and source["record_type"] == "constructed_fixture"
        assert source["excerpt"] == selected
        assert source["webSource"]["url"] == url
        assert source["webSource"]["receiptId"] == web_result["receipt_id"]
        assert source["source"]["web_source"]["text_sha256"] == hashlib.sha256(
            web_result["content"].encode()).hexdigest()
        assert source["source"]["web_source"]["fetched_at"] == web_result["source"]["fetched_at"]
        assert not source["source"]["current_webpage_checked"]
        assert source["source"]["selected_excerpt"]["sha256"] == hashlib.sha256(
            selected.encode()).hexdigest()
        assert source["feedback"] == [] and "outcome" not in source["encounter"]
        assert "Original café" not in json.dumps(source)  # The unselected passage is not retained.

        # The original source may change on the Internet; later retention must
        # use its saved delivered receipt, never fetch new text or assert current truth.
        page_text["text"] = "Updated later. " + page_text["text"]
        second = choose(client, first, fixture, op="remember_web",
                        receipt_id=web_result["receipt_id"], title="Earlier version",
                        excerpt=selected)
        assert second[0]["status"] == "succeeded"
        assert len(observed) == 1

        another = work(client)
        shared = next(x for x in client.get(f"/api/pages/{another}/posture").json()["experiences"]
                      if x["id"] == source["id"])
        assert shared["excerpt"] == selected and shared["note"] == source["note"]
        assert shared["sourceKind"] == "web"
        assert "webSource" not in shared and "source" not in shared
        assert url not in json.dumps(shared)
        assert shared["sourcePageId"] == first and shared["sourceAnchor"]["resultMessageId"]

    with client_for(tmp_path, posture=True, model_factory=None) as client:
        saved = next(x for x in client.get(f"/api/pages/{first}/posture").json()["experiences"]
                     if x["id"] == source["id"])
        assert saved == source


def test_web_passage_rejects_foreign_receipts_unseen_words_and_non_web_sources(tmp_path, monkeypatch):
    observed, _ = prepare_web(monkeypatch)
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        first = work(client)
        web_result = choose(client, first, fixture, op="web_open",
                            url="https://research.example/source")[0]["output"]
        foreign = work(client)
        attempts = [
            (first, web_result["receipt_id"], "words that were never observed"),
            (first, "invented-web-action", "Keep  two spaces."),
            (foreign, web_result["receipt_id"], "Keep  two spaces."),
        ]
        for target, receipt_id, excerpt in attempts:
            outcome = choose(client, target, fixture, op="remember_web",
                             receipt_id=receipt_id, title="Not observed here", excerpt=excerpt)
            assert outcome[0]["status"] == "failed"
            assert client.get(f"/api/pages/{target}/posture").json()["experiences"] == []
        assert len(observed) == 1


@pytest.mark.parametrize("extra", [
    {"feedback_message_id": "invented"}, {"source_message_id": "forged"},
    {"receipt_id": "\n"}, {"excerpt": "界" * 3000},
])
def test_web_selection_does_not_accept_unseen_authority_or_oversized_text(extra):
    values = {"op": "remember_web", "receipt_id": "actual",
              "title": "A retained webpage", "excerpt": "Real observed text."}
    values.update(extra)
    if extra.get("excerpt"):
        # Word length is accepted by tool schema, but the host's byte limit
        # is stricter; both bounds should reject before preparing memory.
        with pytest.raises(ValueError):
            validate(values)
    else:
        with pytest.raises(ValueError):
            validate(values)


def test_saved_web_observation_readback_keeps_exact_version_and_work_scope(tmp_path, monkeypatch):
    """The UI reads a native receipt, not today's changed webpage or a foreign Work."""
    observed, page_text = prepare_web(monkeypatch)
    fixture = ReadingExampleFixture(monkeypatch.setenv)
    url = "https://research.example/original-text"
    with client_for(tmp_path, posture=True, model_factory=fixture.factory) as client:
        first = work(client)
        response = choose(client, first, fixture, op="web_open", url=url)[0]
        assert response["status"] == "succeeded"
        output = response["output"]
        action_id = output["receipt_id"]
        listed = client.get(f"/api/pages/{first}").json()["execution"]["webSources"]["items"]
        assert any(item["actionId"] == action_id for item in listed)

        saved = client.get(f"/api/pages/{first}/execution/web/{action_id}")
        assert saved.status_code == 200
        item = saved.json()
        assert item["pageId"] == first and item["actionId"] == action_id
        assert item["source"]["url"] == url
        assert item["source"]["fetchedAt"] == output["source"]["fetched_at"]
        assert item["content"] == output["content"]
        assert item["digest"] == hashlib.sha256(output["content"].encode()).hexdigest()
        assert item["textStart"] == 0
        assert item["textEnd"] == len(output["content"])

        second = work(client)
        unseen = client.get(f"/api/pages/{second}/execution/web/{action_id}")
        assert unseen.status_code == 404
        assert unseen.json()["error"]["code"] == "web_observation_not_found"
        missing = client.get(f"/api/pages/{first}/execution/web/not-a-receipt")
        assert missing.status_code == 404

        # A later change to the original URL does not alter the saved text.
        page_text["text"] = "Now changed: " + page_text["text"]
        again = client.get(f"/api/pages/{first}/execution/web/{action_id}")
        assert again.status_code == 200 and again.json() == item
        assert observed == [url]

    # Cold restart and disconnected provider: the same native saved bytes survive.
    with client_for(tmp_path, posture=True, model_factory=None) as client:
        restored = client.get(f"/api/pages/{first}/execution/web/{action_id}")
        assert restored.status_code == 200
        assert restored.json() == item
        assert observed == [url]
