"""Read-only local provider configuration check for first-use Per Pulse.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This intentionally never opens a stream or sends a provider request. It cannot
certify remote authentication, tool calls, token usage, or billing limits.
"""
from __future__ import annotations

import argparse
import json


SCHEMA = "per_pulse.local_provider_check.v1"


def inspect_provider() -> dict:
    """Use the production adapter's local preflight without exposing secrets."""
    from pulse_harness.adapters.chat_http import model_factory_from_env
    from pulse_harness.core import HarnessScope
    from pulse_harness.core.errors import ProviderUnavailable

    report = {
        "schema": SCHEMA,
        "state": "invalid_config",
        "credential_variable": None,
        "network_request_sent": False,
        "remote_streaming_verified": False,
        "remote_tools_verified": False,
        "provider_usage_verified": False,
        "provider_cost_verified": False,
    }
    try:
        factory = model_factory_from_env()
    except (ValueError, OverflowError):
        return report
    if factory is None:
        report["state"] = "disconnected"
        return report

    adapter = None
    try:
        adapter = factory(HarnessScope("per-pulse-doctor", "config", "config", 0, "config"))
        # This name is validated by ChatHTTPConfig and is not a credential value.
        report["credential_variable"] = adapter.config.credential_env
        adapter.preflight_stream()  # local credential presence only; no network
        report["state"] = "locally_configured"
    except ProviderUnavailable as exc:
        report["state"] = (
            "credential_missing" if exc.code == "provider_credential_missing"
            else "invalid_config"
        )
    except (ValueError, OverflowError):
        report["state"] = "invalid_config"
    finally:
        if adapter is not None:
            adapter.close()
    return report


def run(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="per-pulse doctor",
        description="Check local model configuration without sending a provider request.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable, secret-free check.")
    args = parser.parse_args(argv)
    report = inspect_provider()
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        messages = {
            "disconnected": "No model configured. The local workspace can be used disconnected.",
            "locally_configured": "Local model configuration and credential presence passed.",
            "credential_missing": "The configured credential environment variable is missing or empty.",
            "invalid_config": "Model configuration is incomplete or invalid. Check the endpoint, model and adapter options.",
        }
        print("Per Pulse provider check: " + messages[report["state"]])
        if report["state"] == "credential_missing" and report["credential_variable"]:
            print("Expected credential variable: " + report["credential_variable"])
        print("No network request sent. Streaming, tools, usage and costs are not verified.")
    if report["state"] in {"credential_missing", "invalid_config"}:
        raise SystemExit(2)
