"""Local-only provider configuration diagnostics never exercise the network.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json

import pytest

from per_pulse import cli


PROVIDER_ENV = (
    "P_PULSE_CHAT_ENDPOINT", "P_PULSE_CHAT_MODEL", "P_PULSE_API_KEY",
    "P_PULSE_CREDENTIAL_ENV", "P_PULSE_CHAT_TIMEOUT", "P_PULSE_MAX_OUTPUT_TOKENS",
    "P_PULSE_MAX_REQUEST_BYTES", "P_PULSE_REASONING_FIELD",
    "P_PULSE_PROVIDER_NAMESPACE", "P_PULSE_ALLOW_LOCAL_HTTP",
    "P_PULSE_LOCAL_NO_AUTH",
)


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)


def read_report(capsys):
    report = json.loads(capsys.readouterr().out)
    assert report["network_request_sent"] is False
    assert report["remote_streaming_verified"] is False
    assert report["remote_tools_verified"] is False
    assert report["provider_usage_verified"] is False
    assert report["provider_cost_verified"] is False
    return report


def test_disconnected_mode_is_intentional_and_does_not_claim_provider(capsys):
    cli.main(["doctor", "--json"])
    result = read_report(capsys)
    assert result["state"] == "disconnected"
    assert result["credential_variable"] is None


def test_partial_provider_configuration_fails_without_echoing_values(monkeypatch, capsys):
    secret = "do-not-print-this-token"
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://provider.invalid/" + secret)
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", "--json"])
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert json.loads(out)["state"] == "invalid_config"
    assert secret not in out


def test_missing_credential_is_not_considered_ready(monkeypatch, capsys):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://provider.invalid/v1/chat/completions")
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "sample-model")
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", "--json"])
    assert exc.value.code == 2
    assert read_report(capsys)["state"] == "credential_missing"


def test_local_preflight_uses_custom_credential_name_without_network(monkeypatch, capsys):
    import httpx

    def forbid_network(*_args, **_kwargs):
        raise AssertionError("doctor must not send an HTTP request")

    monkeypatch.setattr(httpx.Client, "send", forbid_network)
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://provider.invalid/v1/chat/completions")
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "sample-model")
    monkeypatch.setenv("P_PULSE_CREDENTIAL_ENV", "PRIVATE_PERPULSE_KEY")
    monkeypatch.setenv("PRIVATE_PERPULSE_KEY", "never-publish-secret")
    cli.main(["doctor", "--json"])
    report = read_report(capsys)
    assert report["state"] == "locally_configured"
    assert report["credential_variable"] == "PRIVATE_PERPULSE_KEY"
    assert "never-publish-secret" not in json.dumps(report)


def test_invalid_endpoint_with_query_does_not_expose_embedded_token(monkeypatch, capsys):
    token = "private-token-here"
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://provider.invalid/v1/chat/completions?api_key=" + token)
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "sample-model")
    monkeypatch.setenv("P_PULSE_API_KEY", "another-private-token")
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", "--json"])
    assert exc.value.code == 2
    text = capsys.readouterr().out
    assert json.loads(text)["state"] == "invalid_config"
    assert token not in text
    assert "another-private-token" not in text

def test_explicit_loopback_works_without_an_api_key_and_sends_no_requests(
    monkeypatch, capsys,
):
    import httpx

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("doctor must not connect to the local model")

    monkeypatch.setattr(httpx.Client, "send", forbidden_network)
    monkeypatch.setenv(
        "P_PULSE_CHAT_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions"
    )
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "user-installed-local-model")
    monkeypatch.setenv("P_PULSE_ALLOW_LOCAL_HTTP", "1")
    monkeypatch.setenv("P_PULSE_LOCAL_NO_AUTH", "1")
    cli.main(["doctor", "--json"])
    report = read_report(capsys)
    assert report["state"] == "locally_configured"
    assert report["credentialless_loopback"] is True
    assert report["credential_variable"] is None


def test_no_auth_flag_with_remote_endpoint_fails_local_preflight(monkeypatch, capsys):
    monkeypatch.setenv("P_PULSE_CHAT_ENDPOINT", "https://provider.example/v1/chat/completions")
    monkeypatch.setenv("P_PULSE_CHAT_MODEL", "remote-model")
    monkeypatch.setenv("P_PULSE_LOCAL_NO_AUTH", "1")
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", "--json"])
    assert exc.value.code == 2
    report = read_report(capsys)
    assert report["state"] == "invalid_config"
    assert report["credentialless_loopback"] is False
