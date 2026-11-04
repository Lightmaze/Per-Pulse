"""A reply is visible while the model is still writing it.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The HTTP model transport is scripted: it streams the first part of a reply and
then holds until the test releases it. The Kernel, World and page service are
real.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient
import httpx

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


def _event(delta: dict, finish: str | None = None) -> bytes:
    return ("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n").encode()


def _held_reply(release: threading.Event):
    def handler(_request):
        def body():
            yield _event({"content": "The first half"})
            assert release.wait(10), "the test never released the reply"
            yield _event({"content": " and the rest."})
            yield _event({}, "stop")
            yield b"data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())
    return handler


def _client(tmp_path, monkeypatch, release):
    monkeypatch.setenv("PER_PULSE_LIVE_REPLY_KEY", "synthetic-test-credential")

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "live-reply-fixture",
                           credential_env="PER_PULSE_LIVE_REPLY_KEY"),
            transport=httpx.MockTransport(_held_reply(release)))

    return TestClient(create_app(tmp_path, model_factory=factory), base_url="http://localhost")


def _wait_for(check, what: str, seconds: float = 10):
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value:
            return value
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _assistant(client, page_id):
    return [m for m in client.get(f"/api/pages/{page_id}").json()["messages"] if m["role"] == "assistant"]


def test_the_reply_so_far_is_readable_before_the_model_finishes(tmp_path, monkeypatch):
    release = threading.Event()
    try:
        with _client(tmp_path, monkeypatch, release) as client:
            page_id = client.post("/api/pages", json={"title": "Draft", "instruction": "Write two halves."}).json()["page"]["id"]
            read = client.app.state.pages.execution.reply_reader(page_id)
            live = _wait_for(lambda: (r := read()) and r["text"] and r, "no live reply while the model was writing")
            assert live["text"] == "The first half"
            assert live["turnId"] and live["roundId"]
            assert _assistant(client, page_id) == []
            release.set()
            final = _wait_for(lambda: _assistant(client, page_id), "the reply never finished")
            assert final[-1]["content"] == "The first half and the rest."
            _wait_for(lambda: read() is None, "the live reply outlived its turn")
    finally:
        release.set()


def test_the_reply_stream_sends_text_as_it_arrives_and_then_ends(tmp_path, monkeypatch):
    release = threading.Event()
    body: list[str] = []
    try:
        with _client(tmp_path, monkeypatch, release) as client:
            page_id = client.post("/api/pages", json={"title": "Stream", "instruction": "Write two halves."}).json()["page"]["id"]
            read = client.app.state.pages.execution.reply_reader(page_id)
            _wait_for(lambda: (r := read()) and r["text"], "no live reply while the model was writing")

            def listen():
                with client.stream("GET", f"/api/pages/{page_id}/reply/stream") as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    body.append(response.read().decode())

            listener = threading.Thread(target=listen)
            listener.start()
            time.sleep(0.5)  # let the stream send the reply so far while the model still holds
            release.set()
            listener.join(20)
            assert not listener.is_alive(), "the stream did not end after the reply finished"
            assert _wait_for(lambda: _assistant(client, page_id), "the reply never finished")
    finally:
        release.set()

    blocks = [block for block in body[0].split("\n\n") if block.strip()]
    data = [json.loads(block.split("data: ", 1)[1]) for block in blocks if block.startswith("data: ")]
    assert data[0]["text"] == "The first half"
    text = data[0]["text"] + "".join(item.get("append", "") for item in data[1:] if item.get("turnId"))
    assert "The first half and the rest.".startswith(text)
    assert {"turnId": None} in data
    assert blocks[-1].startswith("event: end")


def test_an_unknown_page_has_no_reply_stream(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, threading.Event()) as client:
        assert client.get("/api/pages/missing-page/reply/stream").status_code == 404
