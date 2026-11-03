"""Pulse CLI must not silently accept an envelope that cannot admit any work.

The admissible cases stub server startup; they do not claim a long-running
runtime, external provider request, or spending-cap verification.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import pytest

from per_pulse.cli import main


@pytest.mark.parametrize("limit,reservation", [(1, 8192), (8191, 8192), (60, 61)])
def test_rejects_positive_background_envelope_smaller_than_one_reservation(
    limit, reservation, capsys,
):
    with pytest.raises(SystemExit) as stopped:
        main(["--pulse", "--pulse-hourly-tokens", str(limit),
              "--pulse-token-reservation", str(reservation)])
    assert stopped.value.code == 2
    error = capsys.readouterr().err
    assert "otherwise no local activation can be admitted" in error
    assert "--pulse-token-reservation" in error


@pytest.mark.parametrize("limit,reservation", [(60, 60), (0, 8192)])
def test_equal_budget_or_explicit_zero_hold_remains_valid(
    limit, reservation, monkeypatch,
):
    import uvicorn
    import per_pulse.app as app

    started = []
    monkeypatch.setattr(app, "create_app", lambda *args, **kwargs: kwargs["pulse"])
    monkeypatch.setattr(uvicorn, "run", lambda settings, **kwargs: started.append(settings))

    main(["--pulse", "--pulse-hourly-tokens", str(limit),
          "--pulse-token-reservation", str(reservation)])
    assert len(started) == 1
    assert started[0].max_background_provider_tokens_per_hour == limit
    assert started[0].background_provider_token_reservation == reservation


def _captured_settings(argv, monkeypatch):
    import uvicorn
    import per_pulse.app as app

    started = []
    monkeypatch.setattr(app, "create_app", lambda *args, **kwargs: kwargs["pulse"])
    monkeypatch.setattr(uvicorn, "run", lambda settings, **kwargs: started.append(settings))
    main(argv)
    assert len(started) == 1
    return started[0]


def test_pulse_is_on_by_default_with_the_hourly_budget(monkeypatch):
    settings = _captured_settings([], monkeypatch)
    assert settings.enabled is True
    assert settings.max_background_pulses_per_hour == 12
    assert _captured_settings(["--pulse-budget", "3"], monkeypatch).max_background_pulses_per_hour == 3
    assert _captured_settings(["--pulse"], monkeypatch).enabled is True


def test_no_pulse_turns_background_continuation_off(monkeypatch, capsys):
    assert _captured_settings(["--no-pulse"], monkeypatch).enabled is False
    with pytest.raises(SystemExit) as stopped:
        main(["--no-pulse", "--pulse-hourly-tokens", "8192"])
    assert stopped.value.code == 2
    assert "--no-pulse" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--pulse", "--no-pulse"])
