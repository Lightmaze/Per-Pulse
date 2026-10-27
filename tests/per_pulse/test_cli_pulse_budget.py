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
