"""A model that refuses image input is answered without the image, and told why.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
Explicit HTTP fixtures stand in for the model; no external model is used.
"""
import json

import httpx
import pytest

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.errors import ProviderUnavailable
from pulse_harness.core.provider import ModelDeltaKind
from test_image_materials import pixels, request_for, source  # noqa: F401  (pytest fixture)

REPLY = ("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "I cannot see images."},
                                              "finish_reason": None}]}) + "\n\n"
         + "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n"
         + "data: [DONE]\n\n")


def adapter_for(refusal):
    """A text-only model: it refuses any request carrying an image with ``refusal``."""
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if pixels(body):
            return httpx.Response(refusal, json={"error": {"message": "image input is not supported"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=REPLY)

    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("http://127.0.0.1:9/chat", "text-only", allow_local_http=True,
        no_auth_local=True, max_retries=2), transport=httpx.MockTransport(handler))
    return adapter, bodies


def notes(body):
    return [part["text"] for message in body["messages"] if isinstance(message["content"], list)
            for part in message["content"] if part["type"] == "text" and "did not accept the image" in part["text"]]


@pytest.mark.parametrize("refusal", [400, 422, 500])
def test_a_refused_image_is_left_out_and_the_model_is_told(source, monkeypatch, refusal):
    provider, scope, result, data = source
    monkeypatch.setattr("pulse_harness.adapters.chat_http.retry_delay", lambda *args, **kwargs: 0)
    adapter, bodies = adapter_for(refusal)
    adapter.workspace_image_resolver = provider.resolve_image
    try:
        deltas = list(adapter.stream(request_for(scope, result)))
        assert "".join(d.text for d in deltas if d.kind is ModelDeltaKind.TEXT) == "I cannot see images."
        assert pixels(bodies[0]) == [data]
        # 5xx is first retried as usual; then once without the image.
        assert len(bodies) == (4 if refusal >= 500 else 2)
        assert pixels(bodies[-1]) == [] and notes(bodies[-1])
        assert f"refused this request while it carried an image (HTTP {refusal})" in notes(bodies[-1])[0]
        # The adapter remembers: a later image read is not sent to this model again.
        later = adapter.request_body(request_for(scope, provider.read_file(scope, "picture.png")))
        assert pixels(later) == [] and "refused an earlier request" in notes(later)[0]
    finally:
        adapter.close()


@pytest.mark.parametrize("refusal", [401, 404, 429])
def test_refusals_about_access_are_not_retried_without_the_image(source, monkeypatch, refusal):
    provider, scope, result, _ = source
    monkeypatch.setattr("pulse_harness.adapters.chat_http.retry_delay", lambda *args, **kwargs: 0)
    adapter, bodies = adapter_for(refusal)
    adapter.workspace_image_resolver = provider.resolve_image
    try:
        with pytest.raises(ProviderUnavailable) as error:
            list(adapter.stream(request_for(scope, result)))
        assert error.value.code == f"provider_http_{refusal}"
        assert all(pixels(body) for body in bodies) and adapter.images_refused is None
    finally:
        adapter.close()


def always_refused():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "context too long"}})

    return ChatHTTPModelAdapter(ChatHTTPConfig("http://127.0.0.1:9/chat", "fixture", allow_local_http=True,
        no_auth_local=True), transport=httpx.MockTransport(handler)), bodies


def test_a_request_without_images_is_not_resent(source):
    _, scope, result, _ = source
    adapter, bodies = always_refused()
    try:  # no image resolver: the tool result carries no pixels
        with pytest.raises(ProviderUnavailable) as error:
            list(adapter.stream(request_for(scope, result)))
        assert error.value.code == "provider_http_400" and len(bodies) == 1
    finally:
        adapter.close()


def test_the_image_is_only_left_out_when_the_retry_succeeds(source):
    provider, scope, result, _ = source
    adapter, bodies = always_refused()
    adapter.workspace_image_resolver = provider.resolve_image
    try:
        with pytest.raises(ProviderUnavailable) as error:
            list(adapter.stream(request_for(scope, result)))
        # One retry without the image, then the service's own refusal stands.
        assert error.value.code == "provider_http_400" and len(bodies) == 2
        assert pixels(bodies[0]) and not pixels(bodies[1])
        assert adapter.images_refused is None
    finally:
        adapter.close()
