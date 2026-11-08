"""Explicit private-LAN model connections use the existing Kernel HTTP transport.

The HTTP model response is synthetic; these are transport and configuration
contracts, not a claim of reachable hardware or model behavior.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import json

import httpx
import pytest

from per_pulse.doctor import inspect_provider
from pulse_harness.adapters.chat_http import (
    ChatHTTPConfig, ChatHTTPModelAdapter, model_factory_from_env,
)
from pulse_harness.core import HarnessScope
from pulse_harness.core.errors import ProviderUnavailable
from pulse_harness.core.provider import ModelDeltaKind, ModelRequest


PRIVATE_URL = "http://192.168.1.24:11434/v1/chat/completions"
SCOPE = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    for name in (
        "P_PULSE_CHAT_ENDPOINT", "P_PULSE_CHAT_MODEL",
        "P_PULSE_ALLOW_LOCAL_HTTP", "P_PULSE_ALLOW_PRIVATE_HTTP",
        "P_PULSE_LOCAL_NO_AUTH", "P_PULSE_API_KEY",
        "P_PULSE_PROVIDER_NAMESPACE", "P_PULSE_CHAT_AUTH_STYLE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("endpoint", [
    "http://10.22.1.3:8000/v1/chat/completions",
    "http://172.16.4.2:8000/v1/chat/completions",
    PRIVATE_URL,
    "http://[fd42::42]:8000/v1/chat/completions",
])
def test_operator_opt_in_accepts_private_literal_addresses(endpoint):
    config = ChatHTTPConfig(endpoint, "lan-model", allow_private_http=True)
    assert config.endpoint == endpoint
    with pytest.raises(ValueError, match="private LAN"):
        ChatHTTPConfig(endpoint, "lan-model")


@pytest.mark.parametrize("endpoint", [
    "http://8.8.8.8:8000/v1/chat/completions",
    "http://0.0.0.0:8000/v1/chat/completions",
    "http://169.254.1.1:8000/v1/chat/completions",
    "http://100.64.0.1:8000/v1/chat/completions",
    "http://224.0.0.1:8000/v1/chat/completions",
    "http://[fe80::1]:8000/v1/chat/completions",
    "http://[::1]:8000/v1/chat/completions",
    "http://local-model.example:8000/v1/chat/completions",
])
def test_private_opt_in_cannot_turn_a_public_hostname_or_non_lan_ip_into_plain_http(endpoint):
    with pytest.raises(ValueError, match="private LAN"):
        ChatHTTPConfig(endpoint, "lan-model", allow_private_http=True, no_auth_local=True)


def test_loopback_needs_its_existing_explicit_flag_even_when_lan_is_enabled():
    with pytest.raises(ValueError, match="loopback"):
        ChatHTTPConfig("http://127.0.0.1:11434/v1/chat/completions", "local",
                       allow_private_http=True)
    assert ChatHTTPConfig(
        "http://127.0.0.1:11434/v1/chat/completions", "local",
        allow_local_http=True,
    ).allow_local_http


@pytest.mark.parametrize("flag", [None, 1, "yes"])
def test_lan_switch_requires_a_real_boolean(flag):
    with pytest.raises(ValueError, match="boolean"):
        ChatHTTPConfig(PRIVATE_URL, "model", allow_private_http=flag)


def test_private_http_does_not_silently_disable_authentication():
    adapter = ChatHTTPModelAdapter(
        ChatHTTPConfig(PRIVATE_URL, "lan-model", allow_private_http=True),
        transport=httpx.MockTransport(lambda req: httpx.Response(500)),
    )
    try:
        with pytest.raises(ProviderUnavailable) as exc:
            adapter.preflight_stream()
        assert exc.value.code == "provider_credential_missing"
    finally:
        adapter.close()


def test_credential_free_lan_request_reaches_exact_selected_ip_without_auth(monkeypatch):
    observed = []
    def handler(request):
        observed.append((str(request.url), request.headers.get("Authorization"),
                         request.headers.get("api-key"), json.loads(request.content)))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=('data: {"choices":[{"index":0,"delta":{"content":"Done."},"finish_reason":null}]}\n\n'
                     'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                     'data: [DONE]\n\n'),
        )

    adapter = ChatHTTPModelAdapter(
        ChatHTTPConfig(PRIVATE_URL, "lan-model", allow_private_http=True,
                       no_auth_local=True),
        transport=httpx.MockTransport(handler),
    )
    model_request = ModelRequest(SCOPE, ({"role": "user", "content": "Continue the Work."},), (), 0)
    try:
        adapter.preflight_stream()
        events = list(adapter.stream(model_request))
        assert events[-1].kind is ModelDeltaKind.DONE
        assert observed == [(PRIVATE_URL, None, None, adapter.request_body(model_request))]
    finally:
        adapter.close()


def test_lan_flags_reach_factory_and_local_doctor_without_network(monkeypatch):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", PRIVATE_URL)
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "lan-model")
    monkeypatch.setenv("P_PULSE_ALLOW_PRIVATE_HTTP", "1")
    monkeypatch.setenv("P_PULSE_LOCAL_NO_AUTH", "1")
    report = inspect_provider()
    assert report["state"] == "locally_configured"
    assert report["network_request_sent"] is False
    assert report["credential_variable"] is None
    assert report["credentialless_private_http"] is True
    assert report["credentialless_loopback"] is False
    factory = model_factory_from_env()
    adapter = factory(SCOPE)
    try:
        assert adapter.config.allow_private_http
        assert adapter.config.no_auth_local
    finally:
        adapter.close()

    monkeypatch.delenv("P_PULSE_ALLOW_PRIVATE_HTTP")
    assert inspect_provider()["state"] == "invalid_config"
