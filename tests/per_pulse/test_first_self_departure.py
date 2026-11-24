"""An enabled fresh World starts from SELF and retains its original receipt.

The HTTP model reply is explicitly synthetic; World/Kernel/history are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import pytest

from per_pulse.service import PageService, PulseSettings
from pulse_system.core.pulse.context import TickError
from test_autonomous_examples import run_event
from test_pulse_integration import TransportFixture


def test_first_self_origin_needs_no_user_message_and_reopens_without_replay(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=0, max_background_pulses_per_hour=1)
    pages = PageService(tmp_path, model_factory=fixture.factory, pulse=settings)
    try:
        runtime = pages.runtime
        assert pages.view("home")["messages"] == []
        first = runtime.context_pulse.request_first_departure()
        assert first.source.value == "self" and first.kind.value == "spontaneous"
        assert first.parent_event_id is None and first.center_id is None
        assert first.engram_id == runtime.continuity_engram_id
        assert runtime.context_pulse.request_first_departure().id == first.id
        result = run_event(pages, first)
        assert result.parent_event_id == first.id
        assert len(fixture.requests) == 1
        assert runtime.context_pulse.request_first_departure().id == first.id
        messages = pages.view("home")["messages"]
        assert [message["role"] for message in messages] == ["system", "assistant"]
        assert messages[0]["activation"] == "self_departure"
        assert messages[-1]["id"] == result.id
        assert runtime.context_pulse.snapshot()["budget"]["used"] == 1
    finally:
        runtime.close()

    reopened = PageService(tmp_path, model_factory=None, pulse=settings)
    try:
        retained = reopened.runtime.context_pulse.request_first_departure()
        assert retained.id == first.id and retained.status.value == "settled"
        assert retained.content == first.content
        assert reopened.view("home")["messages"] == messages
        assert len(fixture.requests) == 1
    finally:
        reopened.runtime.close()


def test_first_departure_respects_disabled_profile_and_native_hourly_hold(tmp_path):
    for name, settings, error in (
        ("disabled", PulseSettings(enabled=False), TickError),
        ("held", PulseSettings(enabled=True, max_background_pulses_per_hour=0), TickError),
    ):
        pages = PageService(tmp_path / name, model_factory=None, pulse=settings)
        try:
            with pytest.raises(error):
                pages.runtime.context_pulse.request_first_departure()
            assert pages.view("home")["messages"] == []
            assert not pages.runtime.causal_ledger.list_events(
                world_id=pages.runtime.world_id, kind="spontaneous", limit=10)
        finally:
            pages.runtime.close()
