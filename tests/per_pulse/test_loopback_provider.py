"""Explicit operator opt-in to real credential-free loopback model servers.

HTTP responses here are synthetic transport fixtures. No local model is
launched or evaluated; tests guard the authority and header boundary.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import httpx
import pytest

from pulse_harness.adapters.chat_http import (
    ChatHTTPConfig, ChatHTTPModelAdapter, model_factory_from_env,
)
from pulse_harness.core import HarnessScope
from pulse_harness.core.errors import ProviderUnavailable
from pulse_harness.core.provider import ModelDeltaKind, ModelRequest


LOOPBACK = "http://127.0.0.1:11434/v1/chat/completions"
SCOPE = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("P_PULSE_CHAT_ENDPOINT", "P_PULSE_CHAT_MODEL",
                 "P_PULSE_ALLOW_LOCAL_HTTP", "P_PULSE_LOCAL_NO_AUTH",
                 "P_PULSE_API_KEY", "P_PULSE_PROVIDER_NAMESPACE",
                 "P_PULSE_CHAT_AUTH_STYLE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("endpoint,allow_http,namespace", [
    ("https://provider.example/v1/chat/completions", False, None),
    ("https://127.0.0.1/v1/chat/completions", False, None),
    ("http://host.example/v1/chat/completions", True, None),
    (LOOPBACK, False, None),
    (LOOPBACK, True, "openrouter"),
])
def test_credentialless_mode_rejects_any_non_explicit_loopback_http(
    endpoint, allow_http, namespace,
):
    with pytest.raises(ValueError, match="loopback"):
        ChatHTTPConfig(
            endpoint, "real-local-model", allow_local_http=allow_http,
            no_auth_local=True, provider_namespace=namespace,
        )


@pytest.mark.parametrize("value", [None, 1, "yes", 0])
def test_no_auth_switch_requires_exact_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        ChatHTTPConfig(LOOPBACK, "model", allow_local_http=True, no_auth_local=value)


def test_missing_key_remains_a_failure_for_the_existing_authenticated_local_path():
    adapter = ChatHTTPModelAdapter(
        ChatHTTPConfig(LOOPBACK, "model", allow_local_http=True),
        transport=httpx.MockTransport(lambda req: httpx.Response(500)),
    )
    try:
        with pytest.raises(ProviderUnavailable) as exc:
            adapter.preflight_stream()
        assert exc.value.code == "provider_credential_missing"
    finally:
        adapter.close()


def test_operator_selected_loopback_stream_has_no_authorization_header(monkeypatch):
    observed = []

    def respond(request):
        observed.append({
            "url": str(request.url),
            "authorization": request.headers.get("Authorization"),
            "accept": request.headers.get("Accept"),
            "body": json.loads(request.content),
        })
        events = [
            {"choices": [{"index": 0, "delta": {"content": "Local reply."},
                           "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        payload = "".join("data: " + json.dumps(value) + "\n\n" for value in events)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=payload + "data: [DONE]\n\n",
        )

    adapter = ChatHTTPModelAdapter(
        ChatHTTPConfig(LOOPBACK, "operator-local-model",
                       allow_local_http=True, no_auth_local=True),
        transport=httpx.MockTransport(respond),
    )
    request = ModelRequest(scope=SCOPE,
        messages=({"role": "user", "content": "Finish this Work."},), tools=(), cursor=0)
    try:
        adapter.preflight_stream()  # No credential is configured.
        events = list(adapter.stream(request))
        assert events[-1].kind is ModelDeltaKind.DONE
        assert observed == [{
            "url": LOOPBACK, "authorization": None, "accept": "text/event-stream",
            "body": adapter.request_body(request),
        }]
    finally:
        adapter.close()


def test_factory_requires_both_explicit_flags_and_a_loopback_endpoint(monkeypatch):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", LOOPBACK)
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "local-model")
    monkeypatch.setenv("P_PULSE_ALLOW_LOCAL_HTTP", "1")
    monkeypatch.setenv("P_PULSE_LOCAL_NO_AUTH", "1")
    factory = model_factory_from_env()
    adapter = factory(SCOPE)
    try:
        adapter.preflight_stream()
        assert adapter.config.no_auth_local is True
        assert adapter.config.endpoint == LOOPBACK
        assert adapter.capabilities.test_only is False
    finally:
        adapter.close()

    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://remote.example/v1/chat/completions")
    with pytest.raises(ValueError, match="loopback"):
        model_factory_from_env()
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", LOOPBACK)
    monkeypatch.delenv("P_PULSE_ALLOW_LOCAL_HTTP")
    with pytest.raises(ValueError, match="loopback"):
        model_factory_from_env()


@pytest.mark.parametrize("bad_url", [
    "https://sample.openai.azure.com/openai/v1/chat/completions?api-key=secret",
    "https://sample.openai.azure.com/openai/v1/chat/completions?api-version=v1&api-key=secret",
    "https://sample.openai.azure.com/openai/v1/chat/completions?api-version=",
    "https://sample.openai.azure.com/openai/v1/chat/completions?api-version=v1&api-version=v2",
])
def test_versioned_endpoint_rejects_unapproved_or_secret_query_parameters(bad_url):
    with pytest.raises(ValueError, match="query"):
        ChatHTTPConfig(bad_url, "model")


@pytest.mark.parametrize("bad_style", ["authorization", "api_key", "none", "API-KEY"])
def test_unknown_auth_style_is_not_silently_accepted(bad_style):
    with pytest.raises(ValueError, match="authentication style"):
        ChatHTTPConfig("https://provider.example/v1/chat/completions", "model",
                       auth_style=bad_style)


def test_api_key_authentication_preserves_versioned_endpoint_and_never_sends_bearer(monkeypatch):
    endpoint = ("https://sample.openai.azure.com/openai/deployments/choice/chat/completions"
                "?api-version=2024-10-21")
    secret = "transport-only-secret"
    monkeypatch.setenv("P_PULSE_API_KEY", secret)
    observed = []

    def respond(request):
        observed.append((str(request.url), request.headers.get("api-key"),
                         request.headers.get("Authorization"), json.loads(request.content)))
        payload = ('data: {"choices":[{"index":0,"delta":{"content":"Answer"},'
                   '"finish_reason":null}]}\n\n'
                   'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                   'data: [DONE]\n\n')
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=payload)

    adapter = ChatHTTPModelAdapter(
        ChatHTTPConfig(endpoint, "choice", auth_style="api-key"),
        transport=httpx.MockTransport(respond),
    )
    request = ModelRequest(scope=SCOPE, messages=({"role": "user", "content": "Hello"},),
                           tools=(), cursor=0)
    try:
        adapter.preflight_stream()
        events = list(adapter.stream(request))
        assert events[-1].kind is ModelDeltaKind.DONE
        assert observed == [(endpoint, secret, None, adapter.request_body(request))]
    finally:
        adapter.close()


def test_api_key_auth_style_must_not_disable_loopback_security():
    with pytest.raises(ValueError, match="loopback"):
        ChatHTTPConfig(LOOPBACK, "model", allow_local_http=True,
                       no_auth_local=True, auth_style="api-key")
