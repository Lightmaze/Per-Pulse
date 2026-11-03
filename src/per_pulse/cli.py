"""Run the local Per Pulse web application.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import zipfile


def _image_command(argv: list[str]) -> None:
    """Carry a stopped, private Context Body without starting the server."""
    parser = argparse.ArgumentParser(
        prog="per-pulse image",
        description="Save, inspect or restore a private cold Per Pulse workspace; no model calls.",
    )
    parser.add_argument("operation", choices=("save", "inspect", "restore"))
    parser.add_argument("archive", type=Path)
    parser.add_argument("--data-dir", type=Path,
                        help="Source home for save, NEW destination home for restore.")
    parser.add_argument("--include-recovered-history", action="store_true",
                        help="Save stopped process-recovery or native-timeout history whose Kernel turns are finished; keep uncertainty intact.")
    args = parser.parse_args(argv)
    if args.operation == "restore" and args.data_dir is None:
        parser.error("Image restore requires --data-dir NEW_HOME; do not silently select the default personal workspace.")
    if args.include_recovered_history and args.operation != "save":
        parser.error("--include-recovered-history applies only to image save.")
    home = args.data_dir or Path.home() / ".local" / "share" / "per-pulse"

    from pulse_system.product import image

    try:
        if args.operation == "save":
            result = image.save(home, args.archive, include_recovered_history=args.include_recovered_history)
        elif args.operation == "inspect":
            result = image.inspect(args.archive)
        else:
            # P-Pulse archives use a related native runtime, but their product
            # history is not automatically a compatible Per Pulse workspace.
            # Require the exact Per Pulse implementation to have been bound
            # when the source image was created.
            details = image.inspect(args.archive)
            if "per_pulse" not in details["implementation"]:
                parser.error("This image is not bound to a Per Pulse product implementation.")
            result = image.restore(args.archive, home)
    except image.ImageError as exc:
        parser.error(str(exc))
    except (OSError, sqlite3.Error, zipfile.BadZipFile, ValueError,
            KeyError, TypeError, RecursionError):
        parser.error("Image operation failed; inspect the archive and keep any incomplete destination.")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "doctor":
        from .doctor import run as doctor_run
        doctor_run(argv[1:])
        return
    if argv and argv[0] == "image":
        _image_command(argv[1:])
        return
    parser = argparse.ArgumentParser(description="Per Pulse — a connected workspace for your conversations.")
    parser.add_argument("--data-dir", type=Path, help="Directory for persistent data (default: ~/.local/share/per-pulse).")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"], help="Local address to listen on.")
    parser.add_argument("--port", type=int, default=8000, help="Local HTTP port (default: 8000).")
    parser.add_argument("--example", action="store_true", help="Open a separate, clearly marked example workspace without a model.")
    parser.add_argument("--allow-workspace-actions", action="store_true", help="Enable workspace effects with exact-action approvals (off by default).")
    pulse_mode = parser.add_mutually_exclusive_group()
    pulse_mode.add_argument("--pulse", dest="pulse", action="store_true", default=True,
                            help="Let pages continue in the background within the hourly budget (default when a model is connected).")
    pulse_mode.add_argument("--no-pulse", dest="pulse", action="store_false",
                            help="Turn off background continuation. Pages still answer your messages and /tick.")
    parser.add_argument("--pulse-budget", type=int, default=12, help="Maximum background turns per rolling hour (default: 12).")
    parser.add_argument("--pulse-hourly-tokens", type=int, default=None,
                        help="Optional estimated background token budget per rolling hour.")
    parser.add_argument("--pulse-token-reservation", type=int, default=8192,
                        help="Tokens reserved for each background turn until its usage is known (default: 8192).")
    parser.add_argument("--pulse-main-rounds", type=int, default=4,
                        help="Maximum model rounds in one background turn (1..16; default 4).")
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
            parser.error("--pulse-hourly-tokens cannot be combined with --no-pulse.")
    if args.pulse_token_reservation <= 0:
        parser.error("--pulse-token-reservation must be positive.")
    if (args.pulse_hourly_tokens is not None
            and 0 < args.pulse_hourly_tokens < args.pulse_token_reservation):
        parser.error(
            "--pulse-hourly-tokens must be 0 (to hold background admission) "
            "or at least --pulse-token-reservation; otherwise no local activation can be admitted."
        )
    if not 1 <= args.pulse_main_rounds <= 16:
        parser.error("--pulse-main-rounds must be between 1 and 16.")
    if args.pulse_main_rounds != 4 and not args.pulse:
        parser.error("--pulse-main-rounds cannot be combined with --no-pulse.")
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
        print(f"Pulse: up to {args.pulse_budget} background turns per hour when a model is connected (--no-pulse to turn off).")
        if args.pulse_hourly_tokens is not None:
            print(f"Background tokens: about {args.pulse_hourly_tokens} per hour, "
                  f"{args.pulse_token_reservation} reserved per turn.")
    uvicorn.run(create_app(data_dir, example=args.example, posture=args.posture,
                           allow_workspace_actions=args.allow_workspace_actions,
                          pulse=PulseSettings(enabled=args.pulse,
                                              max_background_pulses_per_hour=args.pulse_budget,
                                              max_background_provider_tokens_per_hour=args.pulse_hourly_tokens,
                                              background_provider_token_reservation=args.pulse_token_reservation,
                                              max_background_main_model_rounds_per_turn=args.pulse_main_rounds)),
                host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
