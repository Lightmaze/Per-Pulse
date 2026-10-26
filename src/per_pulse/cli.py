"""Run the local Per Pulse web application.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Per Pulse — a connected workspace for your conversations.")
    parser.add_argument("--data-dir", type=Path, help="Directory for persistent data (default: ~/.local/share/per-pulse).")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"], help="Local address to listen on.")
    parser.add_argument("--port", type=int, default=8000, help="Local HTTP port (default: 8000).")
    parser.add_argument("--example", action="store_true", help="Open a separate, clearly marked example workspace without a model.")
    parser.add_argument("--allow-workspace-actions", action="store_true", help="Enable workspace effects with exact-action approvals (off by default).")
    parser.add_argument("--pulse", action="store_true", help="Enable bounded local reentry and diffusion through explicit context connections.")
    parser.add_argument("--pulse-budget", type=int, default=12, help="Maximum background pulse admissions per rolling hour (default: 12).")
    parser.add_argument("--pulse-hourly-tokens", type=int, default=None,
                        help="Optional rolling-hour background token admission envelope (nominal tokens, not a hard provider billing cap).")
    parser.add_argument("--pulse-token-reservation", type=int, default=8192,
                        help="Tokens held for each background admission until a settled usage receipt exists (default: 8192).")
    parser.add_argument("--posture", action="store_true", default=None,
                        help="Enable attributed native experience learning (off by default).")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")
    if args.pulse_budget < 0:
        parser.error("--pulse-budget must be nonnegative.")
    if args.pulse_hourly_tokens is not None:
        if args.pulse_hourly_tokens < 0:
            parser.error("--pulse-hourly-tokens must be nonnegative.")
        if not args.pulse:
            parser.error("--pulse-hourly-tokens requires --pulse.")
    if args.pulse_token_reservation <= 0:
        parser.error("--pulse-token-reservation must be positive.")
    data_dir = args.data_dir or Path.home() / ".local" / "share" / "per-pulse"
    # An explicit base directory is respected, with examples kept under their
    # own child directory so the ordinary user World is never seeded.
    if args.example:
        data_dir = data_dir / "example"
    import uvicorn
    from .app import create_app
    from .service import PulseSettings

    address = f"[{args.host}]" if ":" in args.host else args.host
    print(f"Per Pulse: http://{address}:{args.port}")
    if args.example:
        print("Example workspace — authored sample notes, no model calls.")
    if args.pulse and not args.example:
        print(f"Pulse enabled — up to {args.pulse_budget} background pulse admissions per rolling hour when a model is connected.")
        if args.pulse_hourly_tokens is not None:
            print(f"Background token admission envelope: {args.pulse_hourly_tokens}/hour, "
                  f"{args.pulse_token_reservation} reserved/admission. "
                  "Unknown provider usage stays reserved; this is not a hard bill cap.")
    uvicorn.run(create_app(data_dir, example=args.example, posture=args.posture,
                           allow_workspace_actions=args.allow_workspace_actions,
                          pulse=PulseSettings(enabled=args.pulse,
                                              max_background_pulses_per_hour=args.pulse_budget,
                                              max_background_provider_tokens_per_hour=args.pulse_hourly_tokens,
                                              background_provider_token_reservation=args.pulse_token_reservation)),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
