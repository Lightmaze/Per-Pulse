"""Provider-free command line entry point for the independent Harness.

This command creates or wakes a durable Harness session and reports the
provider gate.  It never selects a test double and never contacts a provider;
an operator must configure a real adapter through a later, explicit gate.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys

from .adapters.workspace import StructuredWorkspaceToolProvider
from .core import HarnessScope, HarnessStore, RecoveryManager, SessionStatus


KERNEL_PROTOCOL = "pulse-harness-observatory.v1"
MAX_CLI_EVENTS = 128


def _json_safe(value: object) -> object:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _json_safe(value.as_dict())
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value") and isinstance(value.value, str):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _projection(
    store: HarnessStore,
    scope: HarnessScope,
    provider: StructuredWorkspaceToolProvider,
) -> dict[str, object]:
    session = store.get_session(scope.session_scope())
    page = store.replay(
        scope.session_scope(),
        after_seq=0,
        limit=min(MAX_CLI_EVENTS, store.max_events_per_session),
    )
    recovery = RecoveryManager(store).recover(reason="cli_observe")
    session_records = tuple(
        record for record in recovery.records
        if record.scope.session_id == scope.session_id
    )
    pending_approvals = len(store.pending_actions(scope.session_scope()))
    workspace = dict(provider.preflight())
    workspace["live_gate"] = "PROVIDER_FREE_STRUCTURED_WORKSPACE"
    workspace["evidence_class"] = "LIVE_WORKSPACE_CHECKPOINTED"
    return {
        "protocol_version": KERNEL_PROTOCOL,
        "session": {
            "session_id": session.scope.session_id,
            "scope": session.scope.as_dict(),
            "status": session.status.value,
            "revision": session.revision,
            "cursor": session.cursor,
            "transcript_cursor": session.transcript_cursor,
            "transcript_head_digest": session.transcript_head_digest,
            "retained_from": session.retained_from,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        },
        "turns": [],
        "events": [_json_safe(event.as_dict()) for event in page.events],
        "replay": {
            "after_seq": 0,
            "next_seq": page.next_seq,
            "retained_from": page.retained_from,
            "has_more": len(page.events) >= min(MAX_CLI_EVENTS, store.max_events_per_session),
            "gap": page.gap,
            "truncated": page.truncated,
        },
        "workspace": _json_safe(workspace),
        "capacity": {
            "event_rows": len(page.events),
            "event_limit": store.max_events_per_session,
            "payload_limit_bytes": store.max_payload_bytes,
            "bounded": True,
        },
        "recovery": {
            "required": bool(session_records),
            "records": [_json_safe(record) for record in session_records],
            "pending_approvals": pending_approvals,
            "automatic_replay": False,
        },
        "evidence": {
            "source_kind": "provider_neutral_harness",
            "adapter": None,
            "verified": False,
            "test_only": False,
            "capabilities": [],
            "live_gate": "BLOCKED",
            "claim": "provider_unconfigured",
        },
    }


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=".pulse/harness.db")
    parser.add_argument("--workspace", default=".pulse/harness-workspace")
    parser.add_argument("--checkpoints", default=None)
    parser.add_argument("--world", default="pulse-world")
    parser.add_argument("--engram", default="pulse-subject")
    parser.add_argument("--context", default="")
    parser.add_argument("--owner", default="pulse-cli")
    parser.add_argument("--epoch", type=int, default=1)
    parser.add_argument("--capability-digest", default="")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pulse-harness",
        description="Create or wake a provider-neutral Pulse Harness session.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create or return a durable session")
    _common(create)
    create.add_argument("--session", default=None)
    wake = commands.add_parser("wake", help="wake a durable session after restart")
    _common(wake)
    wake.add_argument("--session", required=True)
    status = commands.add_parser("status", help="read a durable session projection")
    _common(status)
    status.add_argument("--session", required=True)
    return parser


def _scope(args: argparse.Namespace) -> HarnessScope:
    return HarnessScope(
        world_id=args.world,
        engram_id=args.engram,
        context_id=args.context,
        session_id=args.session or "pulse-session",
        epoch=args.epoch,
        owner_id=args.owner,
        capability_digest=args.capability_digest,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    db = Path(args.db)
    if db.name != ":memory:":
        db.parent.mkdir(parents=True, exist_ok=True)
    try:
        store = HarnessStore(
            db,
        )
        try:
            scope = _scope(args)
            if args.command == "create":
                session = store.create_session(scope.session_scope())
                if session.status is SessionStatus.CREATED:
                    store.set_session_status(scope.session_scope(), SessionStatus.ACTIVE)
                action = "created"
            else:
                store.get_session(scope.session_scope())
                action = "woken" if args.command == "wake" else "read"
            provider = StructuredWorkspaceToolProvider(
                store,
                args.workspace,
                args.checkpoints
                if args.checkpoints is not None
                else Path(args.workspace).expanduser().parent
                / f"{Path(args.workspace).expanduser().name}-checkpoints",
            )
            result = _projection(store, scope, provider)
            print(json.dumps({"command": action, **result}, ensure_ascii=False, sort_keys=True))
            return 0
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        code = getattr(exc, "code", "harness_cli_error")
        print(
            json.dumps(
                {
                    "command": args.command,
                    "accepted": False,
                    "error": code,
                    "detail": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
