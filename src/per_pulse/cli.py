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
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")
    data_dir = args.data_dir or Path.home() / ".local" / "share" / "per-pulse"
    # An explicit base directory is respected, with examples kept under their
    # own child directory so the ordinary user World is never seeded.
    if args.example:
        data_dir = data_dir / "example"
    import uvicorn
    from .app import create_app

    address = f"[{args.host}]" if ":" in args.host else args.host
    print(f"Per Pulse: http://{address}:{args.port}")
    if args.example:
        print("Example workspace — authored sample notes, no model calls.")
    uvicorn.run(create_app(data_dir, example=args.example), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
