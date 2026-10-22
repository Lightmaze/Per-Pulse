"""SQLite-backed durable session, transcript, event and action store.

This module deliberately uses only the Python standard library.  It is the
durable spine for the independent Harness; adapters may project into it, but
the store does not know about PulseWorld, Pi, or any model provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4
from weakref import ref

from .contracts import (
    ActionClassification,
    ActionClassificationOrigin,
    ActionRecord,
    ActionStatus,
    ApprovalDecision,
    ApprovalRecord,
    ApprovalStatus,
    ChangeRecord,
    CheckpointRecord,
    HarnessEvent,
    HarnessSession,
    HarnessTurn,
    ModelRoundRecord,
    ModelRoundStatus,
    OperationRecord,
    OperationStatus,
    RecoveryRecord,
    ReplayPage,
    SessionStatus,
    TerminalOutcome,
    TurnStatus,
    utc_now,
)
from .errors import (
    ApprovalConflict,
    CapabilityDenied,
    IdempotencyConflict,
    InvalidReplayCursor,
    InvalidTransition,
    LeaseFenced,
    RedactionRequired,
    RevisionConflict,
    ScopeConflict,
    StoreUnavailable,
)
from .events import EventDraft, event_from_row
from .replay import (
    bound_payload,
    bound_text,
    decode_payload,
    digest_json,
    encode_payload,
    stored_payload_flags,
    stored_text_flags,
)
from .provider import ProviderEvidence, validate_provider_response_id
from .response_review import RESPONSE_REVIEW_PREFIX, decode_review_envelope
from .scope import HarnessScope
from .validation_memo import ValidationMemo


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    capability_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    cursor INTEGER NOT NULL,
    retained_from INTEGER NOT NULL,
    transcript_cursor INTEGER NOT NULL DEFAULT 0,
    transcript_head_digest TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    capability_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    prompt_digest TEXT NOT NULL DEFAULT '',
    prompt_redacted INTEGER NOT NULL DEFAULT 0,
    prompt_truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(session_id, turn_id)
);

CREATE TABLE IF NOT EXISTS transcript_messages (
    message_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    parent_item_id TEXT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    capability_digest TEXT NOT NULL,
    transcript_seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    redacted INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, transcript_seq),
    UNIQUE(session_id, item_id)
);

CREATE INDEX IF NOT EXISTS transcript_session_seq
    ON transcript_messages(session_id, transcript_seq);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    parent_item_id TEXT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    capability_digest TEXT NOT NULL,
    seq INTEGER NOT NULL,
    delta_seq INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    action_request_id TEXT,
    approval_id TEXT,
    operation_id TEXT,
    payload_json TEXT NOT NULL,
    event_digest TEXT NOT NULL,
    redacted INTEGER NOT NULL,
    truncated INTEGER NOT NULL,
    gap INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS events_session_seq ON events(session_id, seq);
CREATE INDEX IF NOT EXISTS events_action ON events(action_request_id, event_type);

CREATE TABLE IF NOT EXISTS actions (
    action_request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    capability_digest TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    args_digest TEXT NOT NULL,
    args_redacted INTEGER NOT NULL DEFAULT 0,
    args_truncated INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    approval_id TEXT,
    operation_id TEXT,
    result_json TEXT,
    result_digest TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    result_redacted INTEGER NOT NULL DEFAULT 0,
    result_truncated INTEGER NOT NULL DEFAULT 0,
    result_delivered INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    terminal_at TEXT
);

CREATE INDEX IF NOT EXISTS actions_turn ON actions(session_id, turn_id);
CREATE INDEX IF NOT EXISTS actions_status ON actions(status);

CREATE TABLE IF NOT EXISTS action_terminals (
    action_request_id TEXT PRIMARY KEY REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    error_code TEXT,
    terminal_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    action_request_id TEXT NOT NULL UNIQUE REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    expires_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    action_request_id TEXT NOT NULL UNIQUE REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    result_json TEXT,
    result_digest TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    result_redacted INTEGER NOT NULL DEFAULT 0,
    result_truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_terminals (
    operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
    action_request_id TEXT NOT NULL REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    error_code TEXT,
    terminal_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS changes (
    change_id TEXT PRIMARY KEY,
    action_request_id TEXT NOT NULL REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    path TEXT NOT NULL,
    path_redacted INTEGER NOT NULL DEFAULT 0,
    path_truncated INTEGER NOT NULL DEFAULT 0,
    before_digest TEXT,
    after_digest TEXT NOT NULL,
    diff TEXT NOT NULL,
    diff_redacted INTEGER NOT NULL DEFAULT 0,
    diff_truncated INTEGER NOT NULL DEFAULT 0,
    record_digest TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    action_request_id TEXT NOT NULL REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    path TEXT NOT NULL,
    path_redacted INTEGER NOT NULL DEFAULT 0,
    path_truncated INTEGER NOT NULL DEFAULT 0,
    artifact_path TEXT NOT NULL,
    artifact_redacted INTEGER NOT NULL DEFAULT 0,
    artifact_truncated INTEGER NOT NULL DEFAULT 0,
    content_digest TEXT NOT NULL,
    record_digest TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recoveries (
    recovery_id TEXT PRIMARY KEY,
    action_request_id TEXT NOT NULL UNIQUE REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT NOT NULL,
    confirmed_exit INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


_MODEL_ROUND_TABLE_SQL = """
CREATE TABLE model_rounds (
    round_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    capability_digest TEXT NOT NULL,
    round_index INTEGER NOT NULL,
    request_cursor INTEGER NOT NULL,
    terminal_cursor INTEGER NOT NULL DEFAULT -1,
    request_digest TEXT NOT NULL,
    provider_evidence_json TEXT NOT NULL,
    provider_evidence_digest TEXT NOT NULL,
    response_id TEXT NOT NULL,
    provider_response_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    delta_count INTEGER NOT NULL DEFAULT 0,
    done_count INTEGER NOT NULL DEFAULT 0,
    response_digest TEXT NOT NULL DEFAULT '',
    assistant_item_id TEXT NOT NULL DEFAULT '',
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    provider_usage_json TEXT NOT NULL DEFAULT '',
    provider_usage_digest TEXT NOT NULL DEFAULT '',
    usage_evidence_class TEXT NOT NULL DEFAULT 'KERNEL_PROVIDER_USAGE_UNACCOUNTED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, turn_id, round_index)
)
"""
_MODEL_ROUND_INDEX_SQL = """
CREATE INDEX model_rounds_turn
    ON model_rounds(session_id, turn_id, round_index)
"""

_ACTION_AUTHORITY_TABLE_SQL = """
CREATE TABLE action_authorities (
    action_request_id TEXT PRIMARY KEY REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    capability_digest TEXT NOT NULL,
    authority_required INTEGER NOT NULL,
    provider_type TEXT NOT NULL,
    provider_source TEXT NOT NULL,
    descriptor_digest TEXT NOT NULL,
    authority_json TEXT NOT NULL,
    authority_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_ACTION_AUTHORITY_INDEX_SQL = """
CREATE INDEX action_authorities_session
    ON action_authorities(session_id, turn_id, action_request_id)
"""
_ACTION_CLASSIFICATION_TABLE_SQL = """
CREATE TABLE action_classifications (
    action_request_id TEXT PRIMARY KEY REFERENCES actions(action_request_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    scope_digest TEXT NOT NULL,
    world_id TEXT NOT NULL,
    engram_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    capability_digest TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_mutable INTEGER,
    classification TEXT NOT NULL,
    classification_origin TEXT NOT NULL,
    authority_required INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_ACTION_CLASSIFICATION_INDEX_SQL = """
CREATE INDEX action_classifications_session
    ON action_classifications(session_id, turn_id, action_request_id)
"""


# The durable core shape is v2. Model request/response authority and then
# action/provider authority were added through explicit schema migrations.
# Keeping these DDL paths separate is
# important because CREATE TABLE IF NOT EXISTS would silently alter an
# existing schema while leaving PRAGMA user_version unchanged.
_PRE_MODEL_SCHEMA_VERSION = 2
_MODEL_SCHEMA_VERSION = 3
_ACTION_AUTHORITY_SCHEMA_VERSION = 4
_ACTION_CLASSIFICATION_SCHEMA_VERSION = 5
_PROVIDER_USAGE_SCHEMA_VERSION = 6
_PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION = 7
_SCHEMA_VERSION = _PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION
_BASE_SCHEMA_TABLES = frozenset(
    {
        "sessions",
        "turns",
        "transcript_messages",
        "events",
        "actions",
        "action_terminals",
        "approvals",
        "operations",
        "operation_terminals",
        "changes",
        "checkpoints",
        "recoveries",
    }
)
_MODEL_ROUND_SCHEMA_MARKERS = frozenset(
    {
        "round_id",
        "session_id",
        "world_id",
        "engram_id",
        "context_id",
        "turn_id",
        "epoch",
        "owner_id",
        "capability_digest",
        "round_index",
        "request_cursor",
        "terminal_cursor",
        "request_digest",
        "provider_evidence_json",
        "provider_evidence_digest",
        "response_id",
        "provider_response_id",
        "status",
        "delta_count",
        "done_count",
        "response_digest",
        "assistant_item_id",
        "tool_call_count",
        "provider_usage_json",
        "provider_usage_digest",
        "usage_evidence_class",
        "created_at",
        "updated_at",
    }
)
_MODEL_ROUND_SCHEMA_V6_MARKERS = _MODEL_ROUND_SCHEMA_MARKERS.difference(
    {"provider_response_id"}
)
_MODEL_ROUND_SCHEMA_V5_MARKERS = _MODEL_ROUND_SCHEMA_MARKERS.difference(
    {
        "provider_response_id",
        "provider_usage_json",
        "provider_usage_digest",
        "usage_evidence_class",
    }
)
_ACTION_AUTHORITY_SCHEMA_MARKERS = frozenset(
    {
        "action_request_id",
        "session_id",
        "scope_digest",
        "world_id",
        "engram_id",
        "context_id",
        "turn_id",
        "epoch",
        "owner_id",
        "capability_digest",
        "authority_required",
        "provider_type",
        "provider_source",
        "descriptor_digest",
        "authority_json",
        "authority_digest",
        "created_at",
        "updated_at",
    }
)
_ACTION_CLASSIFICATION_SCHEMA_MARKERS = frozenset(
    {
        "action_request_id",
        "session_id",
        "scope_digest",
        "world_id",
        "engram_id",
        "context_id",
        "turn_id",
        "epoch",
        "owner_id",
        "capability_digest",
        "tool_name",
        "tool_mutable",
        "classification",
        "classification_origin",
        "authority_required",
        "created_at",
        "updated_at",
    }
)
_AUTHORITY_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTION_AUTHORITY_FIELDS = frozenset(
    {
        "version",
        "required",
        "provider_type",
        "provider_source",
        "descriptor_digest",
        "identity",
    }
)
_CURRENT_SCHEMA_MARKERS = {
    "sessions": frozenset({"transcript_cursor", "transcript_head_digest"}),
    "turns": frozenset({"prompt_digest", "prompt_redacted", "prompt_truncated"}),
    "transcript_messages": frozenset({"event_id", "event_seq"}),
    "actions": frozenset(
        {
            "args_redacted",
            "args_truncated",
            "result_redacted",
            "result_truncated",
            "result_digest",
        }
    ),
    "operations": frozenset(
        {"result_redacted", "result_truncated", "result_digest"}
    ),
    "changes": frozenset(
        {
            "path_redacted",
            "path_truncated",
            "diff_redacted",
            "diff_truncated",
            "record_digest",
        }
    ),
    "checkpoints": frozenset(
        {
            "path_redacted",
            "path_truncated",
            "artifact_redacted",
            "artifact_truncated",
            "record_digest",
        }
    ),
}
_KNOWN_EVENT_STATUSES = frozenset(
    {
        *(status.value for status in SessionStatus),
        *(status.value for status in TurnStatus),
        *(status.value for status in ActionStatus),
        *(status.value for status in ApprovalStatus),
        *(status.value for status in OperationStatus),
        "appended",
        "accepted",
        "aborted",
        "applied",
        "cancel_requested",
        "checkpointed",
        "created",
        "delta",
        "opened",
        "ok",
        "preview",
        "queued",
        "rejected",
        "resumed",
        "settled",
        "steered",
        "streaming",
        "terminal",
    }
)
_KNOWN_TRANSCRIPT_ROLES = frozenset(
    {"system", "user", "assistant", "tool", "reasoning"}
)
_KNOWN_MODEL_ROUND_STATUSES = frozenset(status.value for status in ModelRoundStatus)


class _ModelRoundWriterBinding:
    """Store-owned metadata for one exact TurnRunner/permit binding."""

    __slots__ = ("can_write", "provider_evidence_digest")

    def __init__(self, *, can_write: bool, provider_evidence_digest: str | None) -> None:
        self.can_write = can_write
        self.provider_evidence_digest = provider_evidence_digest


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _scope_from_row(row: sqlite3.Row, *, turn_column: str = "turn_id") -> HarnessScope:
    return HarnessScope(
        world_id=row["world_id"],
        engram_id=row["engram_id"],
        context_id=row["context_id"],
        session_id=row["session_id"],
        turn_id=row[turn_column],
        epoch=int(row["epoch"]),
        owner_id=row["owner_id"],
        capability_digest=row["capability_digest"],
    )


def _normalise_action_authority(
    value: Mapping[str, Any],
    max_payload_bytes: int,
) -> tuple[dict[str, Any], str]:
    """Validate the provider-neutral durable authority envelope.

    The envelope intentionally stores an identity supplied by the effect
    adapter, not a Python object or a process-local token.  The latter can
    only be useful as a live binding and cannot survive restart.  A missing
    identity is still recorded when a mutable provider is unavailable so a
    caller cannot silently fall back to an unbound same-name tool.
    """

    if not isinstance(value, Mapping) or set(value) != _ACTION_AUTHORITY_FIELDS:
        raise CapabilityDenied("action authority envelope is invalid")
    if value.get("version") != 1 or not isinstance(value.get("required"), bool):
        raise CapabilityDenied("action authority envelope version or flag is invalid")
    for key in ("provider_type", "provider_source"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise CapabilityDenied("action authority provider identity is invalid")
    descriptor_digest = value.get("descriptor_digest")
    if not isinstance(descriptor_digest, str) or _AUTHORITY_DIGEST.fullmatch(
        descriptor_digest
    ) is None:
        raise CapabilityDenied("action authority descriptor digest is invalid")
    identity = value.get("identity")
    if identity is not None and not isinstance(identity, Mapping):
        raise CapabilityDenied("action authority adapter identity is invalid")
    clean, redacted, truncated = bound_payload(dict(value), max_payload_bytes)
    if redacted or truncated or clean != dict(value):
        raise CapabilityDenied("action authority envelope is not bounded")
    encoded = encode_payload(clean)
    if len(encoded.encode("utf-8")) > max_payload_bytes:
        raise CapabilityDenied("action authority envelope exceeds the durable bound")
    return clean, digest_json(clean)


class HarnessStore:
    """Thread-safe durable store with complete scope and terminal fencing."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_events_per_session: int = 4096,
        max_payload_bytes: int = 64 * 1024,
        approval_ttl_sec: float = 300.0,
    ) -> None:
        if not isinstance(max_events_per_session, int) or max_events_per_session < 8:
            raise ValueError("max_events_per_session must be an integer >= 8")
        if max_payload_bytes < 256:
            raise ValueError("max_payload_bytes must be >= 256")
        if approval_ttl_sec <= 0:
            raise ValueError("approval_ttl_sec must be positive")
        self.max_events_per_session = max_events_per_session
        self.max_payload_bytes = max_payload_bytes
        self.approval_ttl_sec = float(approval_ttl_sec)
        self._lock = RLock()
        self._closed = False
        # Reuse a successful whole-store read validation only while the same
        # connection still observes the same committed database and policy.
        # This is an in-process optimization, never persisted authority.
        self._validated_read_stamp: tuple[object, ...] | None = None
        # Exact persisted bytes may reuse their pure envelope checks. This
        # never certifies a relationship, sequence, schema or mutable action.
        # The store lock owns the bounded memo; nothing is written to disk.
        self._envelope_checks = ValidationMemo()
        # Key by id and verify the weak-reference target with ``is``.  A
        # WeakKeyDictionary would permit an object with surprising equality
        # semantics to stand in for the registered TurnRunner; the writer
        # boundary is intentionally exact object identity plus this store-local
        # opaque permit.
        self._model_round_writers: dict[
            int, tuple[ref[object], object, _ModelRoundWriterBinding]
        ] = {}
        self._model_round_owners: dict[str, ref[object]] = {}
        self._provider_usage_reconcilers: dict[
            int, tuple[ref[object], object, frozenset[str]]
        ] = {}
        self.path = str(path)
        if self.path != ":memory:":
            db_path = Path(path).expanduser().resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(db_path)
        self._db = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._db.row_factory = sqlite3.Row
        with self._lock:
            try:
                version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
                if version not in {
                    0,
                    1,
                    _PRE_MODEL_SCHEMA_VERSION,
                    _MODEL_SCHEMA_VERSION,
                    _ACTION_AUTHORITY_SCHEMA_VERSION,
                    _ACTION_CLASSIFICATION_SCHEMA_VERSION,
                    _PROVIDER_USAGE_SCHEMA_VERSION,
                    _SCHEMA_VERSION,
                }:
                    raise StoreUnavailable(
                        f"unsupported HarnessStore schema version: {version}",
                        code="schema_version_unsupported",
                    )
                if version < _MODEL_SCHEMA_VERSION and self._schema_table_exists_locked(
                    "model_rounds"
                ):
                    # A model-round table in a pre-model-authority database is not an
                    # upgrade marker.  It is an ambiguous/partial shape and
                    # must not be silently accepted under the old version.
                    raise StoreUnavailable(
                        "model-round schema is present without its version bump",
                        code="schema_version_mismatch",
                    )
                if version < _ACTION_AUTHORITY_SCHEMA_VERSION and self._schema_table_exists_locked(
                    "action_authorities"
                ):
                    # An authority table without the v4 bump is as ambiguous
                    # as a model-round table without the v3 bump.  Refuse to
                    # reinterpret a partially upgraded durable authority.
                    raise StoreUnavailable(
                        "action authority schema is present without its version bump",
                        code="schema_version_mismatch",
                    )
                if version < _ACTION_CLASSIFICATION_SCHEMA_VERSION and self._schema_table_exists_locked(
                    "action_classifications"
                ):
                    # A classification table without the v5 bump is a
                    # partial authority shape.  Never reinterpret it as a
                    # v4 database with optional metadata.
                    raise StoreUnavailable(
                        "action classification schema is present without its version bump",
                        code="schema_version_mismatch",
                    )
                if version < _PROVIDER_USAGE_SCHEMA_VERSION and self._schema_table_exists_locked(
                    "model_rounds"
                ):
                    usage_columns = {
                        "provider_usage_json",
                        "provider_usage_digest",
                        "usage_evidence_class",
                    }
                    if usage_columns.intersection(
                        self._schema_table_columns_locked("model_rounds")
                    ):
                        raise StoreUnavailable(
                            "provider-usage schema is present without its version bump",
                            code="schema_version_mismatch",
                        )
                if (
                    version < _PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION
                    and self._schema_table_exists_locked("model_rounds")
                    and "provider_response_id"
                    in self._schema_table_columns_locked("model_rounds")
                ):
                    raise StoreUnavailable(
                        "provider-response identity schema is present without its version bump",
                        code="schema_version_mismatch",
                    )
                legacy_nonempty = version in {0, 1} and not self._legacy_database_is_empty_locked()
                if legacy_nonempty and not self._legacy_shape_is_current_locked():
                    raise StoreUnavailable(
                        "non-empty legacy HarnessStore is unsupported",
                        code="legacy_schema_unsupported",
                    )
                if version == _PRE_MODEL_SCHEMA_VERSION and not self._legacy_database_is_empty_locked():
                    if not self._base_schema_is_current_locked():
                        raise StoreUnavailable(
                            "non-empty v2 HarnessStore has an unsupported base shape",
                            code="schema_version_unsupported",
                        )
                if version == _MODEL_SCHEMA_VERSION and (
                    not self._base_schema_is_current_locked()
                    or not self._model_round_schema_is_v5_locked()
                ):
                    raise StoreUnavailable(
                        "v3 HarnessStore schema shape is incomplete",
                        code="schema_version_unsupported",
                    )
                if version == _ACTION_AUTHORITY_SCHEMA_VERSION and (
                    not self._base_schema_is_current_locked()
                    or not self._model_round_schema_is_v5_locked()
                    or not self._action_authority_schema_is_current_locked()
                ):
                    raise StoreUnavailable(
                        "v4 HarnessStore schema shape is incomplete",
                        code="schema_version_unsupported",
                    )
                if version == _ACTION_CLASSIFICATION_SCHEMA_VERSION and (
                    not self._base_schema_is_current_locked()
                    or not self._model_round_schema_is_v5_locked()
                    or not self._action_authority_schema_is_current_locked()
                    or not self._action_classification_schema_is_current_locked()
                ):
                    raise StoreUnavailable(
                        "v5 HarnessStore schema shape is incomplete",
                        code="schema_version_unsupported",
                    )
                if version == _PROVIDER_USAGE_SCHEMA_VERSION and (
                    not self._base_schema_is_current_locked()
                    or not self._model_round_schema_is_v6_locked()
                    or not self._action_authority_schema_is_current_locked()
                    or not self._action_classification_schema_is_current_locked()
                ):
                    raise StoreUnavailable(
                        "v6 HarnessStore schema shape is incomplete",
                        code="schema_version_unsupported",
                    )
                if version == _SCHEMA_VERSION and (
                    not self._base_schema_is_current_locked()
                    or not self._model_round_schema_is_current_locked()
                    or not self._action_authority_schema_is_current_locked()
                    or not self._action_classification_schema_is_current_locked()
                ):
                    raise StoreUnavailable(
                        "v7 HarnessStore schema shape is incomplete",
                        code="schema_version_unsupported",
                    )
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=FULL")
                self._db.executescript(_SCHEMA)
                self._ensure_schema_columns()
                if version < _MODEL_SCHEMA_VERSION:
                    self._migrate_to_v3_locked(legacy_nonempty=legacy_nonempty)
                if version < _ACTION_AUTHORITY_SCHEMA_VERSION:
                    self._migrate_to_v4_locked()
                if version < _ACTION_CLASSIFICATION_SCHEMA_VERSION:
                    self._migrate_to_v5_locked(source_version=version)
                if version < _PROVIDER_USAGE_SCHEMA_VERSION:
                    self._migrate_to_v6_locked()
                if version < _PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION:
                    self._migrate_to_v7_locked()
                self._validate_database_locked()
            except StoreUnavailable:
                self._db.close()
                self._envelope_checks.clear()
                self._closed = True
                raise
            except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
                self._db.close()
                self._closed = True
                raise StoreUnavailable("HarnessStore schema is unavailable") from exc

    def _bind_model_round_writer(
        self,
        writer: object,
        permit: object,
        legacy_handshake: object | None = None,
        *,
        can_write: bool,
        provider_evidence_digest: str | None = None,
    ) -> None:
        """Bind a TurnRunner-owned permit without issuing a permit.

        TurnRunner creates the opaque permit before this internal handshake.
        The Store only records the exact writer identity and permit identity;
        it never exposes a capability issuer/getter.  A read-only runner (no
        model adapter) may inspect/recover through normal read paths but cannot
        self-sign a model response.
        """

        with self._lock:
            self._require_open()
            if legacy_handshake is not None:
                raise CapabilityDenied("model-round writer does not accept an external handshake")
            # Keep the store independent at import time, but make the
            # production call path exact at bind time.  A plain object plus a
            # copied/private-looking token cannot self-register as a writer.
            from .turn_runner import TurnRunner, _ModelRoundWriterPermit

            if type(writer) is not TurnRunner:
                raise CapabilityDenied("model-round writer must be a canonical TurnRunner")
            if type(permit) is not _ModelRoundWriterPermit or permit is writer:
                raise CapabilityDenied("model-round writer permit is invalid")
            if not isinstance(can_write, bool):
                raise CapabilityDenied("model-round writer permission is invalid")
            if can_write and (
                not isinstance(provider_evidence_digest, str)
                or len(provider_evidence_digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in provider_evidence_digest
                )
            ):
                raise CapabilityDenied("model-round writer provider attestation is invalid")
            if not can_write and provider_evidence_digest is not None:
                raise CapabilityDenied("read-only writer cannot attest a provider")
            writer_id = id(writer)
            try:
                existing = self._model_round_writers.get(writer_id)
                if existing is not None:
                    existing_writer = existing[0]()
                    if existing_writer is not None:
                        if existing_writer is writer:
                            raise CapabilityDenied("model-round writer is already bound")
                        raise CapabilityDenied("model-round writer identity is already occupied")
                    self._model_round_writers.pop(writer_id, None)
                binding = _ModelRoundWriterBinding(
                    can_write=can_write,
                    provider_evidence_digest=provider_evidence_digest,
                )
                self._model_round_writers[writer_id] = (ref(writer), permit, binding)
            except TypeError as exc:
                raise CapabilityDenied("model-round writer is not an object identity") from exc

    def _require_model_round_writer(
        self,
        writer: object | None,
        capability: object | None,
        *,
        allow_readonly: bool = False,
    ) -> _ModelRoundWriterBinding:
        if writer is None or capability is None:
            raise CapabilityDenied(
                "model-round mutation requires the owning TurnRunner permit"
            )
        entry = self._model_round_writers.get(id(writer))
        if entry is None or entry[0]() is not writer or entry[1] is not capability:
            raise CapabilityDenied(
                "model-round mutation requires the owning TurnRunner permit"
            )
        if not entry[2].can_write and not allow_readonly:
            raise CapabilityDenied(
                "a runner without a model adapter cannot publish model authority"
            )
        return entry[2]

    def _require_model_round_owner(self, round_id: str, writer: object) -> None:
        owner = self._model_round_owners.get(round_id)
        if owner is None or owner() is not writer:
            raise CapabilityDenied(
                "model-round mutation is owned by another TurnRunner"
            )

    def _bind_provider_usage_reconciler(
        self,
        reconciler: object,
        permit: object,
        capabilities: frozenset[str],
    ) -> None:
        """Bind the canonical lookup-to-commit coordinator to this store."""

        with self._lock:
            self._require_open()
            from .usage_reconciliation import (
                ProviderUsageReconciliationManager,
                _ProviderUsageReconciliationPermit,
            )

            if type(reconciler) is not ProviderUsageReconciliationManager:
                raise CapabilityDenied(
                    "provider-usage reconciler must be the canonical manager"
                )
            if type(permit) is not _ProviderUsageReconciliationPermit:
                raise CapabilityDenied("provider-usage reconciler permit is invalid")
            if (
                type(capabilities) is not frozenset
                or not capabilities
                or any(
                    not isinstance(capability, str) or not capability.strip()
                    for capability in capabilities
                )
            ):
                raise CapabilityDenied(
                    "provider-usage reconciler capabilities are invalid"
                )

            # A service may construct a short-lived manager for each explicit
            # reconciliation request. Prune dead identities so that Python id
            # reuse cannot inherit authority and the registry cannot grow
            # without bound across those calls.
            for reconciler_id, entry in tuple(
                self._provider_usage_reconcilers.items()
            ):
                if entry[0]() is None:
                    self._provider_usage_reconcilers.pop(reconciler_id, None)

            reconciler_id = id(reconciler)
            existing = self._provider_usage_reconcilers.get(reconciler_id)
            if existing is not None:
                raise CapabilityDenied("provider-usage reconciler is already bound")
            try:
                reconciler_ref = ref(reconciler)
            except TypeError as exc:
                raise CapabilityDenied(
                    "provider-usage reconciler is not an object identity"
                ) from exc
            self._provider_usage_reconcilers[reconciler_id] = (
                reconciler_ref,
                permit,
                capabilities,
            )

    def _require_provider_usage_reconciler(
        self,
        reconciler: object | None,
        permit: object | None,
    ) -> frozenset[str]:
        if reconciler is None or permit is None:
            raise CapabilityDenied(
                "provider-usage mutation requires the reconciliation permit"
            )
        entry = self._provider_usage_reconcilers.get(id(reconciler))
        if entry is None or entry[0]() is not reconciler or entry[1] is not permit:
            raise CapabilityDenied(
                "provider-usage mutation requires the reconciliation permit"
            )
        return entry[2]

    def _schema_table_exists_locked(self, table: str) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )

    def _schema_table_names_locked(self) -> set[str]:
        return {
            str(row["name"])
            for row in self._db.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
        }

    def _schema_table_columns_locked(self, table: str) -> set[str]:
        escaped = table.replace('"', '""')
        return {
            str(row["name"])
            for row in self._db.execute(f'PRAGMA table_info("{escaped}")').fetchall()
        }

    def _schema_primary_key_columns_locked(self, table: str) -> tuple[str, ...]:
        escaped = table.replace('"', '""')
        rows = self._db.execute(f'PRAGMA table_info("{escaped}")').fetchall()
        return tuple(
            str(row["name"])
            for row in sorted(rows, key=lambda row: int(row["pk"]))
            if int(row["pk"]) > 0
        )

    def _schema_index_names_locked(self, table: str) -> set[str]:
        escaped = table.replace('"', '""')
        return {
            str(row["name"])
            for row in self._db.execute(f'PRAGMA index_list("{escaped}")').fetchall()
        }

    def _base_schema_is_current_locked(self) -> bool:
        tables = self._schema_table_names_locked()
        if not _BASE_SCHEMA_TABLES.issubset(tables):
            return False
        return all(
            markers.issubset(self._schema_table_columns_locked(table))
            for table, markers in _CURRENT_SCHEMA_MARKERS.items()
        )

    def _model_round_schema_is_current_locked(self) -> bool:
        return (
            self._schema_table_exists_locked("model_rounds")
            and _MODEL_ROUND_SCHEMA_MARKERS.issubset(
                self._schema_table_columns_locked("model_rounds")
            )
            and "model_rounds_turn" in self._schema_index_names_locked("model_rounds")
        )

    def _model_round_schema_is_v6_locked(self) -> bool:
        return (
            self._schema_table_exists_locked("model_rounds")
            and _MODEL_ROUND_SCHEMA_V6_MARKERS.issubset(
                self._schema_table_columns_locked("model_rounds")
            )
            and "model_rounds_turn" in self._schema_index_names_locked("model_rounds")
        )

    def _model_round_schema_is_v5_locked(self) -> bool:
        return (
            self._schema_table_exists_locked("model_rounds")
            and _MODEL_ROUND_SCHEMA_V5_MARKERS.issubset(
                self._schema_table_columns_locked("model_rounds")
            )
            and "model_rounds_turn" in self._schema_index_names_locked("model_rounds")
        )

    def _action_authority_schema_is_current_locked(self) -> bool:
        return (
            self._schema_table_exists_locked("action_authorities")
            and _ACTION_AUTHORITY_SCHEMA_MARKERS.issubset(
                self._schema_table_columns_locked("action_authorities")
            )
            and "action_authorities_session"
            in self._schema_index_names_locked("action_authorities")
        )

    def _action_classification_schema_is_current_locked(self) -> bool:
        return (
            self._schema_table_exists_locked("action_classifications")
            and _ACTION_CLASSIFICATION_SCHEMA_MARKERS.issubset(
                self._schema_table_columns_locked("action_classifications")
            )
            and self._schema_primary_key_columns_locked("action_classifications")
            == ("action_request_id",)
            and "action_classifications_session"
            in self._schema_index_names_locked("action_classifications")
        )

    def _create_model_round_table_locked(self) -> None:
        # Execute this DDL with execute(), not executescript(): SQLite keeps
        # it inside the surrounding migration transaction.
        self._db.execute(_MODEL_ROUND_TABLE_SQL)

    def _create_model_round_index_locked(self) -> None:
        self._db.execute(_MODEL_ROUND_INDEX_SQL)

    def _create_action_authority_table_locked(self) -> None:
        self._db.execute(_ACTION_AUTHORITY_TABLE_SQL)

    def _create_action_authority_index_locked(self) -> None:
        self._db.execute(_ACTION_AUTHORITY_INDEX_SQL)

    def _create_action_classification_table_locked(self) -> None:
        self._db.execute(_ACTION_CLASSIFICATION_TABLE_SQL)

    def _create_action_classification_index_locked(self) -> None:
        self._db.execute(_ACTION_CLASSIFICATION_INDEX_SQL)

    def _set_schema_version_locked(self, version: int) -> None:
        if version not in {
            _MODEL_SCHEMA_VERSION,
            _ACTION_AUTHORITY_SCHEMA_VERSION,
            _ACTION_CLASSIFICATION_SCHEMA_VERSION,
            _PROVIDER_USAGE_SCHEMA_VERSION,
            _PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION,
        }:
            raise ValueError("unsupported HarnessStore schema version transition")
        self._db.execute(f"PRAGMA user_version = {version}")

    def _migrate_to_v3_locked(self, *, legacy_nonempty: bool) -> None:
        """Upgrade the pre-model shape atomically, including its version bump."""

        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._create_model_round_table_locked()
            self._create_model_round_index_locked()
            self._set_schema_version_locked(_MODEL_SCHEMA_VERSION)
            self._validate_database_locked(expected_version=_MODEL_SCHEMA_VERSION)
        except BaseException as exc:
            self._db.rollback()
            raise StoreUnavailable(
                "HarnessStore schema migration failed",
                code=(
                    "legacy_schema_unsupported"
                    if legacy_nonempty
                    else "schema_migration_failed"
                ),
            ) from exc
        else:
            self._db.commit()

    def _migrate_to_v4_locked(self) -> None:
        """Add action/provider authority atomically to a schema-v3 database."""

        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._create_action_authority_table_locked()
            self._create_action_authority_index_locked()
            self._set_schema_version_locked(_ACTION_AUTHORITY_SCHEMA_VERSION)
            self._validate_database_locked(expected_version=_ACTION_AUTHORITY_SCHEMA_VERSION)
        except BaseException as exc:
            self._db.rollback()
            raise StoreUnavailable(
                "HarnessStore action authority migration failed",
                code="schema_migration_failed",
            ) from exc
        else:
            self._db.commit()

    def _migrate_to_v5_locked(self, *, source_version: int) -> None:
        """Add the closed action classification authority atomically.

        v3 actions predate provider/action authority and are intentionally
        labelled ``LEGACY_V3_UNATTRIBUTED``.  v4 actions with a durable
        authority are mutable; v4 actions with neither authority nor an
        approval are mechanically identifiable as read-only.  A v4 action
        with an approval but no authority is ambiguous because v4 did not
        durably record mutability, so that migration is rejected rather than
        guessing from a tool name.
        """

        self._db.execute("BEGIN IMMEDIATE")
        try:
            actions = self._db.execute(
                "SELECT * FROM actions ORDER BY session_id, action_request_id"
            ).fetchall()
            action_authorities = [
                (action, self._action_authority_row(action["action_request_id"]))
                for action in actions
            ]
            if source_version == _ACTION_AUTHORITY_SCHEMA_VERSION:
                if any(
                    authority is None and action["approval_id"] is not None
                    for action, authority in action_authorities
                ):
                    raise StoreUnavailable(
                        "v4 action mutability is ambiguous without authority",
                        code="schema_migration_failed",
                    )
            self._create_action_classification_table_locked()
            self._create_action_classification_index_locked()
            for action, authority in action_authorities:
                if source_version <= _MODEL_SCHEMA_VERSION:
                    classification = ActionClassification.LEGACY_V3_UNATTRIBUTED
                    classification_origin = ActionClassificationOrigin.MIGRATED_V3
                    tool_mutable = None
                    authority_required = 0
                elif authority is not None:
                    classification = ActionClassification.MUTABLE_REQUIRED
                    classification_origin = ActionClassificationOrigin.MIGRATED_V4_AUTHORITY
                    tool_mutable = 1
                    authority_required = 1
                elif action["approval_id"] is None:
                    classification = ActionClassification.READ_ONLY_NOT_REQUIRED
                    classification_origin = ActionClassificationOrigin.MIGRATED_V4_READ_ONLY
                    tool_mutable = 0
                    authority_required = 0
                else:
                    raise StoreUnavailable(
                        "v4 action mutability is ambiguous without authority",
                        code="schema_migration_failed",
                    )
                scope = _scope_from_row(action)
                self._db.execute(
                    """INSERT INTO action_classifications
                    (action_request_id, session_id, scope_digest, world_id,
                     engram_id, context_id, turn_id, epoch, owner_id,
                     capability_digest, tool_name, tool_mutable, classification,
                     classification_origin, authority_required, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        action["action_request_id"],
                        scope.session_id,
                        scope.digest,
                        scope.world_id,
                        scope.engram_id,
                        scope.context_id,
                        scope.turn_id,
                        scope.epoch,
                        scope.owner_id,
                        scope.capability_digest,
                        action["tool_name"],
                        tool_mutable,
                        classification.value,
                        classification_origin.value,
                        authority_required,
                        action["created_at"],
                        action["updated_at"],
                    ),
                )
            self._set_schema_version_locked(_ACTION_CLASSIFICATION_SCHEMA_VERSION)
            self._validate_database_locked(
                expected_version=_ACTION_CLASSIFICATION_SCHEMA_VERSION
            )
        except BaseException as exc:
            self._db.rollback()
            raise StoreUnavailable(
                "HarnessStore action classification migration failed",
                code="schema_migration_failed",
            ) from exc
        else:
            self._db.commit()

    def _migrate_to_v6_locked(self) -> None:
        """Add normalized provider usage to model-round terminal authority."""

        self._db.execute("BEGIN IMMEDIATE")
        try:
            columns = self._schema_table_columns_locked("model_rounds")
            additions = {
                "provider_usage_json": "TEXT NOT NULL DEFAULT ''",
                "provider_usage_digest": "TEXT NOT NULL DEFAULT ''",
                "usage_evidence_class": (
                    "TEXT NOT NULL DEFAULT 'KERNEL_PROVIDER_USAGE_UNACCOUNTED'"
                ),
            }
            for name, definition in additions.items():
                if name not in columns:
                    self._db.execute(
                        f"ALTER TABLE model_rounds ADD COLUMN {name} {definition}"
                    )
            self._set_schema_version_locked(_PROVIDER_USAGE_SCHEMA_VERSION)
            self._validate_database_locked(expected_version=_PROVIDER_USAGE_SCHEMA_VERSION)
        except BaseException as exc:
            self._db.rollback()
            raise StoreUnavailable(
                "HarnessStore provider usage migration failed",
                code="schema_migration_failed",
            ) from exc
        else:
            self._db.commit()

    def _migrate_to_v7_locked(self) -> None:
        """Add provider-issued response identity to model-round authority."""

        self._db.execute("BEGIN IMMEDIATE")
        try:
            columns = self._schema_table_columns_locked("model_rounds")
            if "provider_response_id" not in columns:
                self._db.execute(
                    "ALTER TABLE model_rounds ADD COLUMN "
                    "provider_response_id TEXT NOT NULL DEFAULT ''"
                )
            self._set_schema_version_locked(_PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION)
            self._validate_database_locked(
                expected_version=_PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION
            )
        except BaseException as exc:
            self._db.rollback()
            raise StoreUnavailable(
                "HarnessStore provider response identity migration failed",
                code="schema_migration_failed",
            ) from exc
        else:
            self._db.commit()

    def _legacy_database_is_empty_locked(self) -> bool:
        tables = self._db.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
        ).fetchall()
        for table in tables:
            name = str(table["name"]).replace('"', '""')
            if self._db.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone() is not None:
                return False
        return True

    def _legacy_shape_is_current_locked(self) -> bool:
        return self._base_schema_is_current_locked()

    def _ensure_schema_columns(self) -> None:
        """Add only bounded additive columns for empty/current-shape candidates."""

        additions = {
            "turns": {
                "prompt_digest": "TEXT NOT NULL DEFAULT ''",
                "prompt_redacted": "INTEGER NOT NULL DEFAULT 0",
                "prompt_truncated": "INTEGER NOT NULL DEFAULT 0",
            },
            "sessions": {
                "transcript_cursor": "INTEGER NOT NULL DEFAULT 0",
                "transcript_head_digest": "TEXT NOT NULL DEFAULT ''",
            },
            "transcript_messages": {
                "event_id": "TEXT NOT NULL DEFAULT ''",
                "event_seq": "INTEGER NOT NULL DEFAULT 0",
            },
            "actions": {
                "args_redacted": "INTEGER NOT NULL DEFAULT 0",
                "args_truncated": "INTEGER NOT NULL DEFAULT 0",
                "result_redacted": "INTEGER NOT NULL DEFAULT 0",
                "result_truncated": "INTEGER NOT NULL DEFAULT 0",
                "result_digest": "TEXT NOT NULL DEFAULT ''",
            },
            "operations": {
                "result_redacted": "INTEGER NOT NULL DEFAULT 0",
                "result_truncated": "INTEGER NOT NULL DEFAULT 0",
                "result_digest": "TEXT NOT NULL DEFAULT ''",
            },
            "changes": {
                "path_redacted": "INTEGER NOT NULL DEFAULT 0",
                "path_truncated": "INTEGER NOT NULL DEFAULT 0",
                "diff_redacted": "INTEGER NOT NULL DEFAULT 0",
                "diff_truncated": "INTEGER NOT NULL DEFAULT 0",
                "record_digest": "TEXT NOT NULL DEFAULT ''",
            },
            "checkpoints": {
                "path_redacted": "INTEGER NOT NULL DEFAULT 0",
                "path_truncated": "INTEGER NOT NULL DEFAULT 0",
                "artifact_redacted": "INTEGER NOT NULL DEFAULT 0",
                "artifact_truncated": "INTEGER NOT NULL DEFAULT 0",
                "record_digest": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in additions.items():
            present = {
                row["name"]
                for row in self._db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in present:
                    self._db.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                    )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._require_open()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.rollback()
            raise
        else:
            self._db.commit()

    def _require_open(self) -> None:
        if self._closed:
            raise StoreUnavailable("HarnessStore is closed")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._envelope_checks.clear()
                self._closed = True

    def __copy__(self) -> "HarnessStore":
        """Return a deliberately unusable copy, never a second authority."""

        clone = object.__new__(type(self))
        clone.__dict__ = self.__dict__.copy()
        clone._lock = RLock()
        clone._closed = True
        clone._envelope_checks = ValidationMemo()
        clone._model_round_writers = {}
        clone._model_round_owners = {}
        return clone

    def __deepcopy__(self, memo: dict[int, object]) -> "HarnessStore":
        del memo
        return self.__copy__()

    def __enter__(self) -> "HarnessStore":
        self._require_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _session_row(self, session_id: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ScopeConflict(f"unknown session: {session_id}")
        return row

    @staticmethod
    def _scope_mismatch(row: sqlite3.Row, scope: HarnessScope) -> str | None:
        for column in ("world_id", "engram_id", "context_id"):
            if row[column] != getattr(scope, column):
                return column
        if int(row["epoch"]) != scope.epoch:
            return "epoch"
        if row["owner_id"] != scope.owner_id:
            return "owner_id"
        if row["capability_digest"] != scope.capability_digest:
            return "capability_digest"
        return None

    def _assert_session_scope(self, scope: HarnessScope) -> sqlite3.Row:
        row = self._session_row(scope.session_id)
        mismatch = self._scope_mismatch(row, scope)
        if mismatch is not None:
            if mismatch in {"epoch", "owner_id"}:
                raise LeaseFenced(
                    f"session {scope.session_id} is fenced by {mismatch}"
                )
            raise ScopeConflict(f"session scope mismatch in {mismatch}")
        return row

    @staticmethod
    def _assert_turn_row_scope(row: sqlite3.Row, scope: HarnessScope) -> None:
        mismatch = HarnessStore._scope_mismatch(row, scope)
        if mismatch is not None:
            if mismatch in {"epoch", "owner_id"}:
                raise LeaseFenced(f"turn is fenced by {mismatch}")
            raise ScopeConflict(f"turn scope mismatch in {mismatch}")

    def _assert_turn_scope(self, scope: HarnessScope) -> sqlite3.Row:
        if not scope.turn_id:
            raise ValueError("turn scope requires turn_id")
        self._assert_session_scope(scope)
        row = self._turn_row(scope.session_id, scope.turn_id)
        self._assert_turn_row_scope(row, scope)
        return row

    @staticmethod
    def _session_status_transitions() -> dict[SessionStatus, frozenset[SessionStatus]]:
        return {
            SessionStatus.CREATED: frozenset({SessionStatus.CREATED, SessionStatus.ACTIVE}),
            SessionStatus.ACTIVE: frozenset(
                {SessionStatus.ACTIVE, SessionStatus.QUIESCED, SessionStatus.CLOSED}
            ),
            SessionStatus.QUIESCED: frozenset(
                {SessionStatus.QUIESCED, SessionStatus.ACTIVE, SessionStatus.CLOSED}
            ),
            SessionStatus.CLOSED: frozenset({SessionStatus.CLOSED}),
        }

    @staticmethod
    def _turn_status_transitions() -> dict[TurnStatus, frozenset[TurnStatus]]:
        terminal = frozenset(
            {
                TurnStatus.SUCCEEDED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }
        )
        return {
            TurnStatus.OPEN: frozenset(
                {
                    TurnStatus.OPEN,
                    TurnStatus.MODEL_STREAMING,
                    TurnStatus.WAITING_APPROVAL,
                    TurnStatus.SETTLING,
                    TurnStatus.BLOCKED,
                    *terminal,
                }
            ),
            TurnStatus.MODEL_STREAMING: frozenset(
                {
                    TurnStatus.MODEL_STREAMING,
                    TurnStatus.WAITING_APPROVAL,
                    TurnStatus.SETTLING,
                    TurnStatus.BLOCKED,
                    *terminal,
                }
            ),
            TurnStatus.WAITING_APPROVAL: frozenset(
                {
                    TurnStatus.WAITING_APPROVAL,
                    TurnStatus.MODEL_STREAMING,
                    TurnStatus.SETTLING,
                    TurnStatus.BLOCKED,
                    *terminal,
                }
            ),
            TurnStatus.SETTLING: frozenset(
                {TurnStatus.SETTLING, TurnStatus.BLOCKED, *terminal}
            ),
            TurnStatus.BLOCKED: frozenset(
                {TurnStatus.BLOCKED, TurnStatus.OPEN, TurnStatus.CANCELLED, TurnStatus.FAILED}
            ),
            TurnStatus.SUCCEEDED: frozenset({TurnStatus.SUCCEEDED}),
            TurnStatus.FAILED: frozenset({TurnStatus.FAILED}),
            TurnStatus.CANCELLED: frozenset({TurnStatus.CANCELLED}),
        }

    def _session_from_row(self, row: sqlite3.Row) -> HarnessSession:
        scope = HarnessScope(
            world_id=row["world_id"],
            engram_id=row["engram_id"],
            context_id=row["context_id"],
            session_id=row["session_id"],
            epoch=int(row["epoch"]),
            owner_id=row["owner_id"],
            capability_digest=row["capability_digest"],
        )
        return HarnessSession(
            scope=scope,
            status=SessionStatus(row["status"]),
            revision=int(row["revision"]),
            cursor=int(row["cursor"]),
            retained_from=int(row["retained_from"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            transcript_cursor=int(row["transcript_cursor"]),
            transcript_head_digest=row["transcript_head_digest"],
        )

    def create_session(self, scope: HarnessScope) -> HarnessSession:
        """Create once; an exact retry returns the original durable session."""

        with self._lock:
            with self._transaction():
                existing = self._db.execute(
                    "SELECT * FROM sessions WHERE session_id = ?", (scope.session_id,)
                ).fetchone()
                if existing is not None:
                    mismatch = self._scope_mismatch(existing, scope)
                    if mismatch is not None:
                        if mismatch in {"epoch", "owner_id"}:
                            raise LeaseFenced("session create is fenced")
                        raise ScopeConflict("session create scope collision")
                    return self._session_from_row(existing)
                now = utc_now()
                self._db.execute(
                    """INSERT INTO sessions
                    (session_id, world_id, engram_id, context_id, owner_id, epoch,
                     capability_digest, status, revision, cursor, retained_from,
                     transcript_cursor, transcript_head_digest, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1, 0, '', ?, ?)""",
                    (
                        scope.session_id,
                        scope.world_id,
                        scope.engram_id,
                        scope.context_id,
                        scope.owner_id,
                        scope.epoch,
                        scope.capability_digest,
                        SessionStatus.CREATED.value,
                        now,
                        now,
                    ),
                )
                row = self._session_row(scope.session_id)
                return self._session_from_row(row)

    def get_session(self, scope: HarnessScope) -> HarnessSession:
        with self._lock:
            self._require_open()
            row = self._assert_session_scope(scope)
            self._validate_database_locked()
            row = self._assert_session_scope(scope)
            return self._session_from_row(row)

    def sessions_for_world(
        self,
        world_id: str,
        *,
        engram_ids: tuple[str, ...] | None = None,
    ) -> tuple[HarnessSession, ...]:
        """Return durable session identities for one world in stable order.

        This is an observer/catalog surface: it never creates or wakes a
        runtime. Context-owned sessions therefore remain visible after process
        restart even when no client has been instantiated yet.
        """

        if not isinstance(world_id, str) or not world_id.strip():
            raise ValueError("world_id must be a non-empty string")
        if engram_ids is not None and any(
            not isinstance(engram_id, str) or not engram_id.strip()
            for engram_id in engram_ids
        ):
            raise ValueError("engram_ids must contain only non-empty strings")
        with self._lock:
            self._require_open()
            self._validate_database_locked()
            query = "SELECT * FROM sessions WHERE world_id = ?"
            parameters: list[object] = [world_id]
            if engram_ids is not None:
                if not engram_ids:
                    return ()
                placeholders = ", ".join("?" for _ in engram_ids)
                query += f" AND engram_id IN ({placeholders})"
                parameters.extend(engram_ids)
            query += " ORDER BY engram_id ASC, context_id ASC, session_id ASC"
            rows = self._db.execute(query, tuple(parameters)).fetchall()
            return tuple(self._session_from_row(row) for row in rows)

    def latest_work_turns(
        self, world_id: str, engram_id: str, *, limit: int = 51,
    ) -> tuple[HarnessTurn, ...]:
        """Bounded current-state catalog, not a transcript or a notification log.

        Validate once for the batch, not once per Work. Only the latest turn of
        each native Work session can be returned; active token-level progress
        does not become an attention item. The caller maps these scopes back
        to currently owned TaskFronts before exposing any navigation target.
        """
        if not world_id or not engram_id or type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("a world, Engram and bounded limit are required")
        with self._lock:
            self._require_open()
            self._validate_database_locked()
            rows = self._db.execute(
                """WITH latest AS (
                    SELECT t.*, ROW_NUMBER() OVER (
                        PARTITION BY session_id ORDER BY created_at DESC, turn_id DESC
                    ) AS position
                    FROM turns t WHERE world_id = ? AND engram_id = ?
                        AND substr(context_id, 1, 5) = 'work:'
                )
                SELECT * FROM latest WHERE position = 1 AND status IN (
                    'waiting_approval', 'failed', 'blocked', 'succeeded', 'cancelled'
                )
                ORDER BY CASE status WHEN 'waiting_approval' THEN 0
                    WHEN 'failed' THEN 1 WHEN 'blocked' THEN 1 ELSE 2 END,
                    updated_at DESC, session_id ASC
                LIMIT ?""", (world_id, engram_id, limit),
            ).fetchall()
            return tuple(self._turn_from_row(row) for row in rows)

    def turns_for_session(
        self,
        scope: HarnessScope,
        *,
        limit: int | None = None,
    ) -> tuple[HarnessTurn, ...]:
        """Read a bounded, newest-first turn catalog for an observer.

        The unbounded historical transcript remains a core continuation
        surface.  Observatory callers must opt into a finite keyset so a
        session projection cannot turn into a fetch-all query as the session
        grows.
        """

        if limit is not None and limit < 1:
            raise ValueError("turn limit must be positive")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope.session_scope())
            self._validate_database_locked()
            query = (
                "SELECT * FROM turns WHERE session_id = ? "
                "ORDER BY created_at DESC, turn_id DESC"
            )
            parameters: list[object] = [scope.session_id]
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = self._db.execute(query, tuple(parameters)).fetchall()
            # The observer renders the selected window in chronological order;
            # the SQL keyset remains newest-first so the latest turns survive
            # bounded retention.
            return tuple(self._turn_from_row(row) for row in reversed(rows))

    def set_session_status(
        self,
        scope: HarnessScope,
        status: SessionStatus,
        *,
        expected_revision: int | None = None,
    ) -> HarnessSession:
        with self._lock:
            with self._transaction():
                row = self._assert_session_scope(scope)
                revision = int(row["revision"])
                if expected_revision is not None and revision != expected_revision:
                    raise RevisionConflict("session revision mismatch")
                current = SessionStatus(row["status"])
                if status not in self._session_status_transitions()[current]:
                    raise InvalidTransition(
                        f"cannot transition session {current.value} -> {status.value}"
                    )
                if status is current:
                    return self._session_from_row(row)
                now = utc_now()
                self._db.execute(
                    "UPDATE sessions SET status = ?, revision = ?, updated_at = ? WHERE session_id = ?",
                    (status.value, revision + 1, now, scope.session_id),
                )
                return self._session_from_row(self._session_row(scope.session_id))

    def _turn_row(self, session_id: str, turn_id: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
            (session_id, turn_id),
        ).fetchone()
        if row is None:
            raise ScopeConflict(f"unknown turn: {turn_id}")
        return row

    def _turn_from_row(self, row: sqlite3.Row) -> HarnessTurn:
        return HarnessTurn(
            scope=_scope_from_row(row),
            status=TurnStatus(row["status"]),
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            prompt=row["prompt"],
        )

    @staticmethod
    def _model_round_from_row(row: sqlite3.Row) -> ModelRoundRecord:
        usage_json = row["provider_usage_json"]
        return ModelRoundRecord(
            round_id=row["round_id"],
            scope=_scope_from_row(row),
            round_index=int(row["round_index"]),
            request_cursor=int(row["request_cursor"]),
            terminal_cursor=int(row["terminal_cursor"]),
            request_digest=row["request_digest"],
            provider_evidence=decode_payload(row["provider_evidence_json"]),
            provider_evidence_digest=row["provider_evidence_digest"],
            response_id=row["response_id"],
            provider_response_id=(row["provider_response_id"] or None),
            status=ModelRoundStatus(row["status"]),
            delta_count=int(row["delta_count"]),
            done_count=int(row["done_count"]),
            response_digest=row["response_digest"],
            assistant_item_id=row["assistant_item_id"],
            tool_call_count=int(row["tool_call_count"]),
            provider_usage=(decode_payload(usage_json) if usage_json else None),
            provider_usage_digest=row["provider_usage_digest"],
            usage_evidence_class=row["usage_evidence_class"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _model_round_row(self, round_id: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM model_rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        if row is None:
            raise ScopeConflict(f"unknown model round: {round_id}")
        return row

    def next_model_round_index(self, scope: HarnessScope) -> int:
        with self._lock:
            self._require_open()
            self._assert_turn_scope(scope)
            row = self._db.execute(
                "SELECT MAX(round_index) AS max_index FROM model_rounds "
                "WHERE session_id = ? AND turn_id = ?",
                (scope.session_id, scope.turn_id),
            ).fetchone()
            return int(row["max_index"]) + 1 if row["max_index"] is not None else 0

    def model_rounds_for_turn(
        self,
        scope: HarnessScope,
        *,
        limit: int | None = None,
    ) -> tuple[ModelRoundRecord, ...]:
        if limit is not None and limit < 1:
            raise ValueError("model round limit must be positive")
        with self._lock:
            self._require_open()
            self._assert_turn_scope(scope)
            self._validate_database_locked()
            query = (
                "SELECT * FROM model_rounds WHERE session_id = ? AND turn_id = ? "
                "ORDER BY round_index DESC"
            )
            parameters: list[object] = [scope.session_id, scope.turn_id]
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = self._db.execute(query, tuple(parameters)).fetchall()
            rows = tuple(reversed(rows))
            return tuple(self._model_round_from_row(row) for row in rows)

    def model_rounds_for_session(
        self,
        scope: HarnessScope,
        *,
        limit: int | None = None,
    ) -> tuple[ModelRoundRecord, ...]:
        if limit is not None and limit < 1:
            raise ValueError("model round limit must be positive")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope.session_scope())
            self._validate_database_locked()
            query = (
                "SELECT * FROM model_rounds WHERE session_id = ? "
                "ORDER BY created_at DESC, round_id DESC"
            )
            parameters: list[object] = [scope.session_id]
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = self._db.execute(query, tuple(parameters)).fetchall()
            rows = tuple(reversed(rows))
            return tuple(self._model_round_from_row(row) for row in rows)

    def count_model_rounds_for_session(
        self,
        scope: HarnessScope,
        *,
        status: ModelRoundStatus | None = None,
    ) -> int:
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope.session_scope())
            self._validate_database_locked()
            query = "SELECT COUNT(*) AS count FROM model_rounds WHERE session_id = ?"
            parameters: list[object] = [scope.session_id]
            if status is not None:
                query += " AND status = ?"
                parameters.append(status.value)
            row = self._db.execute(query, tuple(parameters)).fetchone()
            return int(row["count"])

    def count_unaccounted_provider_usage_rounds(self) -> int:
        """Return the durable count of completed rounds without usage."""

        with self._lock:
            self._require_open()
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM model_rounds "
                "WHERE status = ? AND provider_usage_json = ''",
                (ModelRoundStatus.COMPLETED.value,),
            ).fetchone()
            return int(row["count"])

    def unaccounted_provider_usage_rounds(self) -> tuple[ModelRoundRecord, ...]:
        """Return completed gaps in a stable global reconciliation order."""

        with self._lock:
            self._require_open()
            self._validate_database_locked()
            rows = self._db.execute(
                "SELECT * FROM model_rounds "
                "WHERE status = ? AND provider_usage_json = '' "
                "ORDER BY created_at ASC, round_id ASC",
                (ModelRoundStatus.COMPLETED.value,),
            ).fetchall()
            return tuple(self._model_round_from_row(row) for row in rows)

    def reconcile_provider_usage(
        self,
        scope: HarnessScope,
        round_id: str,
        *,
        expected_provider_response_id: str,
        expected_provider_evidence_digest: str,
        provider_usage: Mapping[str, Any],
        usage_evidence_class: str,
        _reconciler: object | None = None,
        _reconciler_capability: object | None = None,
        _lookup_capability: str | None = None,
    ) -> ModelRoundRecord:
        """Atomically attach authenticated post-hoc usage to one exact round."""

        lookup_capabilities = self._require_provider_usage_reconciler(
            _reconciler, _reconciler_capability
        )
        if (
            not isinstance(_lookup_capability, str)
            or _lookup_capability not in lookup_capabilities
        ):
            raise CapabilityDenied(
                "provider-usage mutation requires a registered lookup capability"
            )
        lookup_capability = _lookup_capability
        response_id = validate_provider_response_id(expected_provider_response_id)
        if (
            not isinstance(expected_provider_evidence_digest, str)
            or len(expected_provider_evidence_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_provider_evidence_digest
            )
        ):
            raise ValueError("expected provider evidence digest is invalid")
        usage = dict(provider_usage)
        required_usage = {
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "cache_write_tokens",
        }
        if set(usage) != required_usage or any(
            type(usage[name]) is not int or usage[name] < 0
            for name in required_usage
        ):
            raise ValueError("provider usage has invalid fields or counters")
        if usage["cached_tokens"] + usage["cache_write_tokens"] > usage["input_tokens"]:
            raise ValueError("provider cache usage exceeds input tokens")
        if (
            not isinstance(usage_evidence_class, str)
            or not usage_evidence_class.strip()
            or usage_evidence_class == "KERNEL_PROVIDER_USAGE_UNACCOUNTED"
        ):
            raise ValueError("reconciled provider usage requires evidence")
        clean_usage, redacted, truncated = bound_payload(
            usage, self.max_payload_bytes
        )
        if redacted or truncated or clean_usage != usage:
            raise ValueError("provider usage must be complete and unredacted")
        usage_json = encode_payload(clean_usage)
        usage_digest = digest_json(clean_usage)

        with self._lock:
            with self._transaction():
                self._assert_turn_scope(scope)
                row = self._model_round_row(round_id)
                self._assert_turn_row_scope(row, scope)
                if (
                    row["status"] != ModelRoundStatus.COMPLETED.value
                    or row["provider_response_id"] != response_id
                    or row["provider_evidence_digest"]
                    != expected_provider_evidence_digest
                ):
                    raise IdempotencyConflict(
                        "model round identity or provider evidence changed before reconciliation"
                    )

                evidence = ProviderEvidence.from_dict(
                    decode_payload(row["provider_evidence_json"])
                )
                matches = evidence.capabilities.intersection(lookup_capabilities)
                if matches != {lookup_capability}:
                    raise CapabilityDenied(
                        "model round provider evidence does not uniquely authorize this lookup"
                    )
                if row["provider_usage_json"]:
                    if (
                        row["provider_usage_json"] == usage_json
                        and row["provider_usage_digest"] == usage_digest
                        and row["usage_evidence_class"] == usage_evidence_class
                    ):
                        return self._model_round_from_row(row)
                    raise IdempotencyConflict(
                        "model round provider usage differs on reconciliation retry"
                    )

                now = utc_now()
                self._db.execute(
                    """UPDATE model_rounds
                       SET provider_usage_json = ?, provider_usage_digest = ?,
                           usage_evidence_class = ?, updated_at = ?
                       WHERE round_id = ? AND status = ?
                         AND provider_usage_json = ''
                         AND provider_response_id = ?
                         AND provider_evidence_digest = ?""",
                    (
                        usage_json,
                        usage_digest,
                        usage_evidence_class,
                        now,
                        round_id,
                        ModelRoundStatus.COMPLETED.value,
                        response_id,
                        expected_provider_evidence_digest,
                    ),
                )
                if self._db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise IdempotencyConflict(
                        "provider-usage reconciliation claim was lost"
                    )
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="model.round.usage_reconciled",
                        source="harness",
                        phase="model",
                        status="settled",
                        payload={
                            "round_id": round_id,
                            "provider_response_id": response_id,
                            "provider_evidence_digest": expected_provider_evidence_digest,
                            "provider_usage": clean_usage,
                            "provider_usage_digest": usage_digest,
                            "usage_evidence_class": usage_evidence_class,
                            "lookup_capability": lookup_capability,
                        },
                        event_id=f"model-round-usage-reconciled:{round_id}",
                        item_id=f"model-round-item:{round_id}",
                        delta_seq=2,
                        revision=2,
                        created_at=now,
                    )
                )
                self._validate_database_locked()
                return self._model_round_from_row(self._model_round_row(round_id))

    def start_model_round(
        self,
        scope: HarnessScope,
        *,
        round_id: str,
        round_index: int,
        request_cursor: int,
        request_digest: str,
        provider_evidence: Mapping[str, Any],
        response_id: str,
        _writer: object | None = None,
        _writer_capability: object | None = None,
    ) -> ModelRoundRecord:
        """Persist one model request authority before invoking an adapter."""

        writer_permit = self._require_model_round_writer(_writer, _writer_capability)
        if not round_id.strip() or not response_id.strip():
            raise ValueError("model round and response identities must be non-empty")
        if round_index < 0 or request_cursor < 0:
            raise ValueError("model round indices and cursors must be non-negative")
        if not isinstance(request_digest, str) or not request_digest.strip():
            raise ValueError("model round request digest must be non-empty")
        if not isinstance(provider_evidence, Mapping):
            raise ValueError("provider evidence must be an object")
        clean_evidence, redacted, truncated = bound_payload(
            dict(provider_evidence), self.max_payload_bytes
        )
        if redacted or truncated or clean_evidence != dict(provider_evidence):
            raise ValueError("provider evidence must be complete and unredacted")
        if writer_permit.provider_evidence_digest != digest_json(clean_evidence):
            raise CapabilityDenied(
                "model-round provider evidence differs from the runner attestation"
            )
        evidence_json = encode_payload(clean_evidence)
        evidence_digest = digest_json(clean_evidence)
        with self._lock:
            with self._transaction():
                turn = self._assert_turn_scope(scope)
                existing = self._db.execute(
                    "SELECT * FROM model_rounds WHERE round_id = ?", (round_id,)
                ).fetchone()
                by_index = self._db.execute(
                    "SELECT * FROM model_rounds WHERE session_id = ? AND turn_id = ? "
                    "AND round_index = ?",
                    (scope.session_id, scope.turn_id, round_index),
                ).fetchone()
                if by_index is not None and (existing is None or by_index["round_id"] != round_id):
                    raise IdempotencyConflict("model round index is already used")
                if existing is not None:
                    self._require_model_round_owner(round_id, _writer)
                    self._assert_turn_row_scope(existing, scope)
                    expected = (
                        existing["round_index"] == round_index
                        and int(existing["request_cursor"]) == request_cursor
                        and existing["request_digest"] == request_digest
                        and existing["provider_evidence_json"] == evidence_json
                        and existing["response_id"] == response_id
                    )
                    if not expected:
                        raise IdempotencyConflict("model round request differs on retry")
                    return self._model_round_from_row(existing)
                # The turn may have been cancelled while its request was
                # being prepared. Admission shares the transaction lock with
                # cancellation, so a terminal turn cannot acquire a late round.
                if TurnStatus(turn["status"]) is not TurnStatus.MODEL_STREAMING:
                    raise InvalidTransition(
                        "model round admission requires a streaming turn"
                    )
                now = utc_now()
                self._db.execute(
                    """INSERT INTO model_rounds
                    (round_id, session_id, world_id, engram_id, context_id, turn_id,
                     epoch, owner_id, capability_digest, round_index, request_cursor,
                     terminal_cursor, request_digest, provider_evidence_json,
                     provider_evidence_digest, response_id, status, delta_count,
                     done_count, response_digest, assistant_item_id, tool_call_count,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, -1, ?, ?, ?, ?, ?, 0,
                            0, '', '', 0, ?, ?)""",
                    (
                        round_id,
                        scope.session_id,
                        scope.world_id,
                        scope.engram_id,
                        scope.context_id,
                        scope.turn_id,
                        scope.epoch,
                        scope.owner_id,
                        scope.capability_digest,
                        round_index,
                        request_cursor,
                        request_digest,
                        evidence_json,
                        evidence_digest,
                        response_id,
                        ModelRoundStatus.STREAMING.value,
                        now,
                        now,
                    ),
                )
                self._model_round_owners[round_id] = ref(_writer)  # type: ignore[arg-type]
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="model.round",
                        source="harness",
                        phase="model",
                        status="streaming",
                        payload={
                            "round_id": round_id,
                            "round_index": round_index,
                            "request_cursor": request_cursor,
                            "request_digest": request_digest,
                            "response_id": response_id,
                            "provider_evidence": clean_evidence,
                        },
                        event_id=f"model-round-start:{round_id}",
                        item_id=f"model-round-item:{round_id}",
                        created_at=now,
                    )
                )
                return self._model_round_from_row(self._model_round_row(round_id))

    def _terminalize_model_round_locked(
        self,
        scope: HarnessScope,
        row: sqlite3.Row,
        status: ModelRoundStatus,
        *,
        delta_count: int,
        done_count: int,
        response_digest: str = "",
        assistant_item_id: str = "",
        tool_call_count: int = 0,
        provider_usage: Mapping[str, Any] | None = None,
        usage_evidence_class: str = "KERNEL_PROVIDER_USAGE_UNACCOUNTED",
        provider_response_id: str | None = None,
        reason: str | None = None,
    ) -> sqlite3.Row:
        """CAS one streaming round to one durable terminal winner."""

        usage_json = ""
        usage_digest = ""
        if provider_usage is not None:
            usage = dict(provider_usage)
            required_usage = {
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "cache_write_tokens",
            }
            if set(usage) != required_usage or any(
                type(usage[name]) is not int or usage[name] < 0
                for name in required_usage
            ):
                raise ValueError("provider usage has invalid fields or counters")
            if usage["cached_tokens"] + usage["cache_write_tokens"] > usage["input_tokens"]:
                raise ValueError("provider cache usage exceeds input tokens")
            clean_usage, usage_redacted, usage_truncated = bound_payload(
                usage, self.max_payload_bytes
            )
            if usage_redacted or usage_truncated or clean_usage != usage:
                raise ValueError("provider usage must be complete and unredacted")
            usage_json = encode_payload(clean_usage)
            usage_digest = digest_json(clean_usage)
            if (
                not isinstance(usage_evidence_class, str)
                or not usage_evidence_class.strip()
                or usage_evidence_class == "KERNEL_PROVIDER_USAGE_UNACCOUNTED"
            ):
                raise ValueError("reported provider usage requires evidence")
        elif usage_evidence_class != "KERNEL_PROVIDER_USAGE_UNACCOUNTED":
            raise ValueError("unaccounted provider usage has invalid evidence")
        if status is not ModelRoundStatus.COMPLETED and provider_usage is not None:
            raise ValueError("non-completed model rounds cannot publish provider usage")
        durable_provider_response_id = ""
        if provider_response_id is not None:
            durable_provider_response_id = validate_provider_response_id(provider_response_id)
        if status is not ModelRoundStatus.COMPLETED and durable_provider_response_id:
            raise ValueError(
                "non-completed model rounds cannot publish provider response identity"
            )

        current = ModelRoundStatus(row["status"])
        if current is not ModelRoundStatus.STREAMING:
            if (
                current is status
                and int(row["delta_count"]) == delta_count
                and int(row["done_count"]) == done_count
                and row["response_digest"] == response_digest
                and row["assistant_item_id"] == assistant_item_id
                and int(row["tool_call_count"]) == tool_call_count
                and row["provider_usage_json"] == usage_json
                and row["provider_usage_digest"] == usage_digest
                and row["usage_evidence_class"] == usage_evidence_class
                and row["provider_response_id"] == durable_provider_response_id
            ):
                return row
            raise IdempotencyConflict("model round terminal differs on retry")
        session = self._session_row(scope.session_id)
        terminal_cursor = int(session["cursor"]) + 1
        now = utc_now()
        self._db.execute(
            """UPDATE model_rounds SET status = ?, delta_count = ?,
            done_count = ?, response_digest = ?, assistant_item_id = ?,
            tool_call_count = ?, provider_usage_json = ?,
            provider_usage_digest = ?, usage_evidence_class = ?,
            provider_response_id = ?, terminal_cursor = ?, updated_at = ?
            WHERE round_id = ? AND status = ?""",
            (
                status.value,
                delta_count,
                done_count,
                response_digest,
                assistant_item_id,
                tool_call_count,
                usage_json,
                usage_digest,
                usage_evidence_class,
                durable_provider_response_id,
                terminal_cursor,
                now,
                row["round_id"],
                ModelRoundStatus.STREAMING.value,
            ),
        )
        if self._db.execute("SELECT changes()").fetchone()[0] != 1:
            # A concurrent owner can only win while this transaction is
            # active through the same store connection/lock.  Treat any
            # unexpected zero-row update as a durable conflict.
            raise IdempotencyConflict("model round terminal claim was lost")
        payload = {
            "round_id": row["round_id"],
            "round_index": int(row["round_index"]),
            "request_cursor": int(row["request_cursor"]),
            "request_digest": row["request_digest"],
            "response_id": row["response_id"],
            "provider_response_id": durable_provider_response_id or None,
            "provider_evidence": decode_payload(row["provider_evidence_json"]),
            "status": status.value,
            "delta_count": delta_count,
            "done_count": done_count,
            "response_digest": response_digest,
            "assistant_item_id": assistant_item_id,
            "tool_call_count": tool_call_count,
            "provider_usage": dict(provider_usage) if provider_usage is not None else None,
            "provider_usage_digest": usage_digest,
            "usage_evidence_class": usage_evidence_class,
        }
        if reason is not None:
            payload["recovery_reason"] = reason
        self._append_event_locked(
            EventDraft(
                scope=scope,
                event_type="model.round",
                source="harness",
                phase="model",
                status=(
                    "settled"
                    if status is ModelRoundStatus.COMPLETED
                    else status.value
                ),
                payload=payload,
                event_id=f"model-round-terminal:{row['round_id']}",
                item_id=f"model-round-item:{row['round_id']}",
                delta_seq=1,
                revision=1,
                created_at=now,
            )
        )
        return self._model_round_row(row["round_id"])

    def finish_model_round(
        self,
        scope: HarnessScope,
        round_id: str,
        status: ModelRoundStatus,
        *,
        delta_count: int,
        done_count: int,
        response_digest: str = "",
        assistant_item_id: str = "",
        tool_call_count: int = 0,
        provider_usage: Mapping[str, Any] | None = None,
        usage_evidence_class: str = "KERNEL_PROVIDER_USAGE_UNACCOUNTED",
        provider_response_id: str | None = None,
        reason: str | None = None,
        _writer: object | None = None,
        _writer_capability: object | None = None,
    ) -> ModelRoundRecord:
        """Atomically close a model response authority and its observer event."""

        self._require_model_round_writer(_writer, _writer_capability)
        if status is ModelRoundStatus.STREAMING:
            raise ValueError("finish_model_round requires a terminal status")
        if delta_count < 0 or done_count not in {0, 1} or tool_call_count < 0:
            raise ValueError("model round counters are invalid")
        if status is ModelRoundStatus.COMPLETED and done_count != 1:
            raise ValueError("a completed model round requires exactly one DONE")
        if status is ModelRoundStatus.COMPLETED and (
            not response_digest or len(response_digest) != 64
        ):
            raise ValueError("a completed model round requires a response digest")
        with self._lock:
            with self._transaction():
                self._assert_turn_scope(scope)
                row = self._model_round_row(round_id)
                self._assert_turn_row_scope(row, scope)
                self._require_model_round_owner(round_id, _writer)  # type: ignore[arg-type]
                return self._model_round_from_row(
                    self._terminalize_model_round_locked(
                        scope,
                        row,
                        status,
                        delta_count=delta_count,
                        done_count=done_count,
                        response_digest=response_digest,
                        assistant_item_id=assistant_item_id,
                        tool_call_count=tool_call_count,
                        provider_usage=provider_usage,
                        usage_evidence_class=usage_evidence_class,
                        provider_response_id=provider_response_id,
                        reason=reason,
                    )
                )

    def complete_model_round_with_assistant(
        self,
        scope: HarnessScope,
        round_id: str,
        *,
        assistant_content: str | None,
        assistant_item_id: str,
        response_digest: str,
        delta_count: int,
        done_count: int,
        tool_call_count: int,
        provider_usage: Mapping[str, Any] | None = None,
        usage_evidence_class: str = "KERNEL_PROVIDER_USAGE_UNACCOUNTED",
        provider_response_id: str | None = None,
        review_context: str | None = None,
        _writer: object | None = None,
        _writer_capability: object | None = None,
    ) -> ModelRoundRecord:
        """Commit response truth, optional review, and model terminal atomically.

        ``review_context`` is the host's canonical review envelope. A pushed
        review holds its exact candidate here instead of publishing assistant
        truth. A declined review keeps the normal assistant and a durable
        once-per-turn receipt. Observer retention cannot erase either decision.
        """

        self._require_model_round_writer(_writer, _writer_capability)
        if assistant_content is not None:
            if not assistant_item_id.strip():
                raise ValueError("assistant content requires an authority item id")
            clean_content, redacted, truncated = bound_text(
                assistant_content, self.max_payload_bytes
            )
            if redacted:
                raise RedactionRequired("assistant response contains a raw credential")
            if truncated or clean_content != assistant_content:
                raise ValueError("assistant response exceeds the durable payload bound")
        elif assistant_item_id:
            raise ValueError("empty assistant response cannot have an authority item")
        review = None
        if review_context is not None:
            clean_review, review_redacted, review_truncated = bound_text(
                review_context, self.max_payload_bytes
            )
            if review_redacted:
                raise RedactionRequired("response review contains a raw credential")
            if review_truncated or clean_review != review_context:
                raise ValueError("response review exceeds the durable payload bound")
            review = decode_review_envelope(review_context)
            if review["round_id"] != round_id or review["response_digest"] != response_digest:
                raise ValueError("response review does not match its source round")
            if tool_call_count or done_count != 1:
                raise ValueError("response review requires a terminal no-tool candidate")
            if review["context"] is not None:
                if assistant_content is not None or assistant_item_id:
                    raise ValueError("a reviewed candidate cannot also be published")
                if digest_json({"text": review["candidate_text"], "tool_calls": []}) != response_digest:
                    raise ValueError("response review candidate does not match response authority")
            elif assistant_content is None or not assistant_content.strip():
                raise ValueError("a declined review requires the original assistant response")
        with self._lock:
            with self._transaction():
                self._assert_turn_scope(scope)
                row = self._model_round_row(round_id)
                self._assert_turn_row_scope(row, scope)
                self._require_model_round_owner(round_id, _writer)  # type: ignore[arg-type]
                review_item_id = f"{RESPONSE_REVIEW_PREFIX}{row['response_id']}"
                if review is not None and review["response_id"] != row["response_id"]:
                    raise ValueError("response review response identity differs from its source")
                current = ModelRoundStatus(row["status"])
                if current is not ModelRoundStatus.STREAMING:
                    previous_review = self._db.execute(
                        "SELECT content FROM transcript_messages WHERE session_id = ? AND item_id = ?",
                        (scope.session_id, review_item_id),
                    ).fetchone()
                    review_matches = (
                        previous_review is None if review_context is None
                        else previous_review is not None and previous_review["content"] == review_context
                    )
                    if (
                        current is ModelRoundStatus.COMPLETED
                        and review_matches
                        and row["response_digest"] == response_digest
                        and row["assistant_item_id"] == assistant_item_id
                        and int(row["delta_count"]) == delta_count
                        and int(row["done_count"]) == done_count
                        and int(row["tool_call_count"]) == tool_call_count
                        and row["provider_usage_json"]
                        == (encode_payload(dict(provider_usage)) if provider_usage is not None else "")
                        and row["usage_evidence_class"] == usage_evidence_class
                        and row["provider_response_id"] == (provider_response_id or "")
                    ):
                        return self._model_round_from_row(row)
                    raise IdempotencyConflict("model round terminal differs on retry")
                if assistant_content is not None:
                    self._append_transcript_locked(
                        scope,
                        role="assistant",
                        clean_content=clean_content,
                        redacted=False,
                        truncated=False,
                        message_id=assistant_item_id,
                        parent_item_id=None,
                        content_digest=digest_json(clean_content),
                    )
                if review_context is not None:
                    self._append_transcript_locked(
                        scope,
                        role="system",
                        clean_content=review_context,
                        redacted=False,
                        truncated=False,
                        message_id=review_item_id,
                        # Source identity is durable in the envelope; a parent
                        # observer event alone could disappear under retention.
                        parent_item_id=None,
                        content_digest=digest_json(review_context),
                    )
                terminal = self._terminalize_model_round_locked(
                    scope,
                    row,
                    ModelRoundStatus.COMPLETED,
                    delta_count=delta_count,
                    done_count=done_count,
                    response_digest=response_digest,
                    assistant_item_id=assistant_item_id,
                    tool_call_count=tool_call_count,
                    provider_usage=provider_usage,
                    usage_evidence_class=usage_evidence_class,
                    provider_response_id=provider_response_id,
                )
                if review is not None:
                    self._append_event_locked(
                        EventDraft(
                            scope=scope,
                            event_type="turn.response_review",
                            source="harness",
                            phase="model",
                            status="settled",
                            payload={
                                "round_id": round_id,
                                "response_id": row["response_id"],
                                "response_digest": response_digest,
                                "context_digest": digest_json(review["context"]),
                                "pushed": review["context"] is not None,
                                "receipt": review["receipt"],
                            },
                            event_id=f"response-review-event:{row['response_id']}",
                            item_id=f"response-review-audit:{row['response_id']}",
                            parent_item_id=review_item_id,
                        )
                    )
                self._validate_database_locked()
                return self._model_round_from_row(terminal)

    def create_turn(self, scope: HarnessScope, prompt: str) -> HarnessTurn:
        if not scope.turn_id:
            raise ValueError("turn scope requires turn_id")
        if not isinstance(prompt, str):
            raise ValueError("prompt must be text")
        clean_prompt, prompt_redacted, prompt_truncated = bound_text(
            prompt, self.max_payload_bytes
        )
        prompt_digest = digest_json(clean_prompt)
        with self._lock:
            with self._transaction():
                self._assert_session_scope(scope)
                existing = self._db.execute(
                    "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                    (scope.session_id, scope.turn_id),
                ).fetchone()
                if existing is not None:
                    self._assert_turn_row_scope(existing, scope)
                    existing_digest = existing["prompt_digest"] or digest_json(
                        existing["prompt"]
                    )
                    if existing_digest != prompt_digest:
                        raise IdempotencyConflict("turn prompt differs on retry")
                    return self._turn_from_row(existing)
                now = utc_now()
                self._db.execute(
                    """INSERT INTO turns
                    (turn_id, session_id, world_id, engram_id, context_id, owner_id,
                     epoch, capability_digest, status, revision, prompt,
                     prompt_digest, prompt_redacted, prompt_truncated,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)""",
                    (
                        scope.turn_id,
                        scope.session_id,
                        scope.world_id,
                        scope.engram_id,
                        scope.context_id,
                        scope.owner_id,
                        scope.epoch,
                        scope.capability_digest,
                        TurnStatus.OPEN.value,
                        clean_prompt,
                        prompt_digest,
                        int(prompt_redacted),
                        int(prompt_truncated),
                        now,
                        now,
                    ),
                )
                return self._turn_from_row(self._turn_row(scope.session_id, scope.turn_id))

    def _assert_event_references_locked(self, draft: EventDraft) -> None:
        """Validate the referential part of an event before allocating a seq."""

        self._assert_session_scope(draft.scope)
        if draft.scope.turn_id:
            self._assert_turn_scope(draft.scope)
        if draft.action_request_id is not None:
            action = self._action_row(draft.action_request_id)
            self._assert_action_scope(action, draft.scope)
        if draft.approval_id is not None:
            approval = self._db.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (draft.approval_id,)
            ).fetchone()
            # action.requested intentionally precedes the approval INSERT.  The
            # action scope check above still prevents a foreign approval id from
            # being used; later events must have the durable approval row.
            if approval is None and draft.event_type != "action.requested":
                raise ScopeConflict("event references an unknown approval")
            if approval is not None and approval["action_request_id"] != draft.action_request_id:
                raise ScopeConflict("approval reference belongs to another action")
        if draft.operation_id is not None:
            operation = self._db.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (draft.operation_id,)
            ).fetchone()
            if operation is None:
                raise ScopeConflict("event references an unknown operation")
            if operation["action_request_id"] != draft.action_request_id:
                raise ScopeConflict("operation reference belongs to another action")

        if draft.parent_item_id is None:
            return
        if draft.parent_item_id == draft.item_id:
            raise ScopeConflict("an item cannot be its own parent")
        parent = self._db.execute(
            """SELECT session_id, world_id, engram_id, context_id, turn_id, epoch,
                      owner_id, capability_digest
                 FROM events WHERE session_id = ? AND item_id = ? LIMIT 1""",
            (draft.scope.session_id, draft.parent_item_id),
        ).fetchone()
        if parent is None:
            parent = self._db.execute(
                """SELECT session_id, world_id, engram_id, context_id, turn_id, epoch,
                          owner_id, capability_digest
                     FROM transcript_messages
                    WHERE session_id = ? AND item_id = ? LIMIT 1""",
                (draft.scope.session_id, draft.parent_item_id),
            ).fetchone()
        if parent is None:
            raise ScopeConflict("parent item is outside this session")
        if parent["turn_id"] != draft.scope.turn_id:
            raise ScopeConflict("parent item is outside this turn")
        for column in (
            "world_id",
            "engram_id",
            "context_id",
            "epoch",
            "owner_id",
            "capability_digest",
        ):
            if parent[column] != getattr(draft.scope, column):
                raise ScopeConflict("parent item is outside this scope")

    @staticmethod
    def _row_scope_matches(row: sqlite3.Row, scope: HarnessScope) -> bool:
        return (
            row["session_id"] == scope.session_id
            and row["world_id"] == scope.world_id
            and row["engram_id"] == scope.engram_id
            and row["context_id"] == scope.context_id
            and row["turn_id"] == scope.turn_id
            and int(row["epoch"]) == scope.epoch
            and row["owner_id"] == scope.owner_id
            and row["capability_digest"] == scope.capability_digest
        )

    def _envelope_key(self, kind: str, *rows: sqlite3.Row) -> tuple[object, ...]:
        # Column names and unparsed values are the key, NOT the stored digest
        # or row id. A same-connection write/rollback, external edit, schema
        # migration or changed byte policy cannot disguise different content.
        return (kind, self.max_payload_bytes,
                *(part for row in rows for part in (tuple(row.keys()), tuple(row))))

    def _event_projects_transcript_locked(self, event: sqlite3.Row, transcript: sqlite3.Row) -> bool:
        key = self._envelope_key("transcript_projection", event, transcript)
        if self._envelope_checks.contains(key):
            return True
        matches = decode_payload(event["payload_json"]) == bound_payload({
            "role": transcript["role"], "content": transcript["content"],
        }, self.max_payload_bytes)[0]
        if matches:
            self._envelope_checks.remember(key)
        return matches

    def _validate_event_envelope_locked(self, row: sqlite3.Row) -> None:
        """Pure proof of this row's exact bytes, not its referenced live rows."""
        key = self._envelope_key("event", row)
        if self._envelope_checks.contains(key):
            return
        try:
            payload = decode_payload(row["payload_json"])
        except StoreUnavailable:
            raise
        try:
            draft = EventDraft(
                scope=HarnessScope(
                    world_id=row["world_id"],
                    engram_id=row["engram_id"],
                    context_id=row["context_id"],
                    session_id=row["session_id"],
                    turn_id=row["turn_id"],
                    epoch=int(row["epoch"]),
                    owner_id=row["owner_id"],
                    capability_digest=row["capability_digest"],
                ),
                event_type=row["event_type"],
                source=row["source"],
                phase=row["phase"],
                status=row["status"],
                payload=payload,
                event_id=row["event_id"],
                item_id=row["item_id"],
                parent_item_id=row["parent_item_id"],
                delta_seq=int(row["delta_seq"]),
                revision=int(row["revision"]),
                action_request_id=row["action_request_id"],
                approval_id=row["approval_id"],
                operation_id=row["operation_id"],
                gap=bool(row["gap"]),
                created_at=row["created_at"],
            )
        except (TypeError, ValueError) as exc:
            raise StoreUnavailable("event envelope is invalid") from exc
        if row["payload_json"] != encode_payload(payload):
            raise StoreUnavailable("event payload is not canonical JSON")
        clean_payload, payload_redacted, payload_truncated = bound_payload(
            payload, self.max_payload_bytes
        )
        stored_redacted, stored_truncated = stored_payload_flags(payload)
        if (
            clean_payload != payload
            or len(row["payload_json"].encode("utf-8")) > self.max_payload_bytes
            or int(row["redacted"]) != int(stored_redacted or payload_redacted)
            or int(row["truncated"]) != int(stored_truncated or payload_truncated)
        ):
            raise StoreUnavailable("event payload is not redacted or bounded")
        if row["event_digest"] != self._event_fingerprint(
            draft,
            payload,
            seq=int(row["seq"]),
            redacted=bool(row["redacted"]),
            truncated=bool(row["truncated"]),
        ):
            raise StoreUnavailable("event digest is corrupt")
        self._envelope_checks.remember(key)

    def _validate_event_rows_locked(self, session: sqlite3.Row) -> None:
        """Fail closed if the bounded observer stream is not canonical."""

        session_scope = HarnessScope(
            world_id=session["world_id"],
            engram_id=session["engram_id"],
            context_id=session["context_id"],
            session_id=session["session_id"],
            turn_id="",
            epoch=int(session["epoch"]),
            owner_id=session["owner_id"],
            capability_digest=session["capability_digest"],
        )
        rows = self._db.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY seq ASC",
            (session["session_id"],),
        ).fetchall()
        cursor = int(session["cursor"])
        retained_from = int(session["retained_from"])
        if rows:
            if int(rows[0]["seq"]) != retained_from:
                raise StoreUnavailable("event retention boundary is corrupt")
            if int(rows[-1]["seq"]) > cursor:
                raise StoreUnavailable("event cursor is behind retained events")
        elif retained_from != cursor + 1:
            raise StoreUnavailable("empty event stream has an invalid cursor")
        prior_seq = retained_from - 1
        item_turns: dict[str, str] = {}
        item_progress: dict[str, tuple[int, int]] = {}
        for row in rows:
            seq = int(row["seq"])
            if seq != prior_seq + 1:
                raise StoreUnavailable("event sequence is not contiguous")
            prior_seq = seq
            row_scope = HarnessScope(
                world_id=session_scope.world_id,
                engram_id=session_scope.engram_id,
                context_id=session_scope.context_id,
                session_id=session_scope.session_id,
                turn_id=row["turn_id"],
                epoch=session_scope.epoch,
                owner_id=session_scope.owner_id,
                capability_digest=session_scope.capability_digest,
            )
            if not self._row_scope_matches(row, row_scope):
                raise StoreUnavailable("event scope is corrupt")
            turn_id = row["turn_id"]
            if row["status"] not in _KNOWN_EVENT_STATUSES:
                raise StoreUnavailable("event status is outside the closed set")
            if int(row["delta_seq"]) < 0 or int(row["revision"]) < 0:
                raise StoreUnavailable("event delta/revision is invalid")
            if any(int(row[column]) not in {0, 1} for column in ("redacted", "truncated", "gap")):
                raise StoreUnavailable("event visibility flags are invalid")
            if turn_id:
                turn = self._db.execute(
                    "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                    (row["session_id"], turn_id),
                ).fetchone()
                if turn is None or not self._row_scope_matches(
                    turn,
                    HarnessScope(
                        world_id=session_scope.world_id,
                        engram_id=session_scope.engram_id,
                        context_id=session_scope.context_id,
                        session_id=session_scope.session_id,
                        turn_id=turn_id,
                        epoch=session_scope.epoch,
                        owner_id=session_scope.owner_id,
                        capability_digest=session_scope.capability_digest,
                    ),
                ):
                    raise StoreUnavailable("event turn reference is corrupt")
            prior_turn = item_turns.setdefault(row["item_id"], turn_id)
            if prior_turn != turn_id:
                raise StoreUnavailable("item lineage crosses turns")
            previous_progress = item_progress.get(row["item_id"])
            if previous_progress is None:
                if retained_from == 1 and (
                    int(row["delta_seq"]) != 0 or int(row["revision"]) != 0
                ):
                    raise StoreUnavailable("item delta/revision does not start at zero")
            elif (
                int(row["delta_seq"]) != previous_progress[0] + 1
                or int(row["revision"]) != previous_progress[1] + 1
            ):
                raise StoreUnavailable("item delta/revision has a gap")
            item_progress[row["item_id"]] = (
                int(row["delta_seq"]),
                int(row["revision"]),
            )
            self._validate_event_envelope_locked(row)
            if row["event_type"] == "transcript.message":
                transcript_rows = self._db.execute(
                    """SELECT * FROM transcript_messages
                       WHERE session_id = ? AND event_id = ?""",
                    (row["session_id"], row["event_id"]),
                ).fetchall()
                if len(transcript_rows) != 1:
                    raise StoreUnavailable("transcript observer event has no unique truth row")
                transcript = transcript_rows[0]
                if (
                    transcript["item_id"] != row["item_id"]
                    or transcript["turn_id"] != row["turn_id"]
                    or int(transcript["event_seq"]) != int(row["seq"])
                    or not self._row_scope_matches(
                        transcript,
                        HarnessScope(
                            world_id=row["world_id"],
                            engram_id=row["engram_id"],
                            context_id=row["context_id"],
                            session_id=row["session_id"],
                            turn_id=row["turn_id"],
                            epoch=int(row["epoch"]),
                            owner_id=row["owner_id"],
                            capability_digest=row["capability_digest"],
                        ),
                    )
                    # A truth record can fit its text bound while its JSON
                    # observer envelope exceeds the byte bound. Compare the
                    # exact deterministic projection, not the unbounded text.
                    or not self._event_projects_transcript_locked(row, transcript)
                ):
                    raise StoreUnavailable("transcript observer event does not match truth")
            if row["action_request_id"] is not None:
                action = self._db.execute(
                    "SELECT * FROM actions WHERE action_request_id = ?",
                    (row["action_request_id"],),
                ).fetchone()
                if action is None or not self._row_scope_matches(action, row_scope):
                    raise StoreUnavailable("event action reference is corrupt")
            if row["approval_id"] is not None:
                approval = self._db.execute(
                    "SELECT action_request_id FROM approvals WHERE approval_id = ?",
                    (row["approval_id"],),
                ).fetchone()
                if approval is None or approval["action_request_id"] != row["action_request_id"]:
                    raise StoreUnavailable("event approval reference is corrupt")
            if row["operation_id"] is not None:
                operation = self._db.execute(
                    "SELECT action_request_id, scope_digest FROM operations WHERE operation_id = ?",
                    (row["operation_id"],),
                ).fetchone()
                if (
                    operation is None
                    or operation["action_request_id"] != row["action_request_id"]
                    or operation["scope_digest"] != row_scope.digest
                ):
                    raise StoreUnavailable("event operation reference is corrupt")
            if row["parent_item_id"] is not None:
                parent = self._db.execute(
                    "SELECT item_id, turn_id, seq AS parent_seq FROM events WHERE session_id = ? AND item_id = ? LIMIT 1",
                    (row["session_id"], row["parent_item_id"]),
                ).fetchone()
                if parent is None:
                    parent = self._db.execute(
                        "SELECT item_id, turn_id, event_seq AS parent_seq FROM transcript_messages WHERE session_id = ? AND item_id = ? LIMIT 1",
                        (row["session_id"], row["parent_item_id"]),
                    ).fetchone()
                if parent is not None:
                    if parent["item_id"] == row["item_id"]:
                        raise StoreUnavailable("event item cannot be its own parent")
                    if parent["turn_id"] != turn_id or int(parent["parent_seq"]) >= seq:
                        raise StoreUnavailable("event parent crosses turns or time")
                if parent is None and retained_from == 1:
                    raise StoreUnavailable("event parent reference is corrupt")
        if rows and prior_seq != cursor:
            raise StoreUnavailable("session cursor does not match retained events")

    def _validate_transcript_rows_locked(self, session: sqlite3.Row) -> None:
        rows = self._db.execute(
            "SELECT * FROM transcript_messages WHERE session_id = ? ORDER BY transcript_seq ASC",
            (session["session_id"],),
        ).fetchall()
        transcript_cursor = int(session["transcript_cursor"])
        if len(rows) != transcript_cursor:
            raise StoreUnavailable("transcript truth cursor is incomplete")
        expected_seq = 1
        head_digest = ""
        for row in rows:
            if int(row["transcript_seq"]) != expected_seq:
                raise StoreUnavailable("transcript sequence is not contiguous")
            expected_seq += 1
            scope = HarnessScope(
                world_id=session["world_id"],
                engram_id=session["engram_id"],
                context_id=session["context_id"],
                session_id=session["session_id"],
                turn_id=row["turn_id"],
                epoch=int(session["epoch"]),
                owner_id=session["owner_id"],
                capability_digest=session["capability_digest"],
            )
            if not self._row_scope_matches(row, scope):
                raise StoreUnavailable("transcript scope is corrupt")
            turn = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                (row["session_id"], row["turn_id"]),
            ).fetchone()
            if turn is None or not self._row_scope_matches(turn, scope):
                raise StoreUnavailable("transcript turn reference is corrupt")
            if row["role"] not in _KNOWN_TRANSCRIPT_ROLES:
                raise StoreUnavailable("transcript role is outside the closed set")
            if row["content_digest"] != digest_json(row["content"]):
                raise StoreUnavailable("transcript content digest is corrupt")
            clean_content, content_redacted, content_truncated = bound_text(
                row["content"], self.max_payload_bytes
            )
            stored_redacted, stored_truncated = stored_text_flags(row["content"])
            if (
                clean_content != row["content"]
                or int(row["redacted"]) != int(stored_redacted or content_redacted)
                or int(row["truncated"]) != int(stored_truncated or content_truncated)
            ):
                raise StoreUnavailable("transcript content is not redacted or bounded")
            if int(row["event_seq"]) < 1 or int(row["event_seq"]) > int(session["cursor"]):
                raise StoreUnavailable("transcript observer sequence is corrupt")
            if any(int(row[column]) not in {0, 1} for column in ("redacted", "truncated")):
                raise StoreUnavailable("transcript visibility flags are invalid")
            event = self._db.execute(
                "SELECT * FROM events WHERE event_id = ?", (row["event_id"],)
            ).fetchone()
            event_at_seq = self._db.execute(
                "SELECT * FROM events WHERE session_id = ? AND seq = ?",
                (row["session_id"], row["event_seq"]),
            ).fetchone()
            # A truth row whose observer event is still within the retained
            # window must point to that exact event slot. Once observer
            # retention has legitimately removed the slot, the transcript
            # cursor and head remain the authority for the durable message.
            if int(row["event_seq"]) >= int(session["retained_from"]):
                if event_at_seq is None or event_at_seq["event_id"] != row["event_id"]:
                    raise StoreUnavailable("retained transcript observer link is corrupt")
            elif event_at_seq is not None and event_at_seq["event_id"] != row["event_id"]:
                raise StoreUnavailable("transcript observer sequence link is corrupt")
            if event is not None:
                if (
                    int(event["seq"]) != int(row["event_seq"])
                    or event["item_id"] != row["item_id"]
                    or event["turn_id"] != row["turn_id"]
                    or event["session_id"] != row["session_id"]
                    or not self._row_scope_matches(event, scope)
                    or event["event_type"] != "transcript.message"
                ):
                    raise StoreUnavailable("transcript observer link is corrupt")
            if row["parent_item_id"] is not None:
                parent = self._db.execute(
                    """SELECT *, seq AS parent_seq FROM events
                       WHERE session_id = ? AND item_id = ? LIMIT 1""",
                    (row["session_id"], row["parent_item_id"]),
                ).fetchone()
                if parent is None:
                    parent = self._db.execute(
                        """SELECT *, event_seq AS parent_seq FROM transcript_messages
                           WHERE session_id = ? AND item_id = ? LIMIT 1""",
                        (row["session_id"], row["parent_item_id"]),
                    ).fetchone()
                if parent is not None:
                    if parent["item_id"] == row["item_id"]:
                        raise StoreUnavailable("transcript item cannot be its own parent")
                    if (
                        parent["turn_id"] != row["turn_id"]
                        or int(parent["parent_seq"]) >= int(row["event_seq"])
                        or not self._row_scope_matches(parent, scope)
                    ):
                        raise StoreUnavailable("transcript parent crosses turns, scope, or time")
                elif int(session["retained_from"]) == 1:
                    raise StoreUnavailable("transcript parent reference is corrupt")
            head_digest = self._transcript_head_step(
                head_digest,
                scope=scope,
                message_id=row["message_id"],
                item_id=row["item_id"],
                parent_item_id=row["parent_item_id"],
                transcript_seq=int(row["transcript_seq"]),
                role=row["role"],
                content=row["content"],
                content_digest=row["content_digest"],
                event_id=row["event_id"],
                event_seq=int(row["event_seq"]),
                redacted=bool(row["redacted"]),
                truncated=bool(row["truncated"]),
                created_at=row["created_at"],
            )
        if head_digest != session["transcript_head_digest"]:
            raise StoreUnavailable("transcript head digest is corrupt")

    def _validate_provider_evidence_locked(self, row: sqlite3.Row) -> None:
        """Reuse pure evidence parsing, never the round's authority or state.

        Every round commonly repeats the same provider descriptor. Key only
        these exact freshly read strings and their policy in the existing
        bounded memo; round identity, usage, status and assistant links remain
        in the live validator below. No decoded descriptor escapes the memo.
        """
        raw, fingerprint = row["provider_evidence_json"], row["provider_evidence_digest"]
        if not isinstance(raw, str) or not isinstance(fingerprint, str):
            raise StoreUnavailable("model round provider evidence is invalid")
        key = ("provider_evidence", self.max_payload_bytes, raw, fingerprint)
        if self._envelope_checks.contains(key):
            return
        evidence = decode_payload(raw)
        if raw != encode_payload(evidence):
            raise StoreUnavailable("model round provider evidence is not canonical")
        clean, redacted, truncated = bound_payload(evidence, self.max_payload_bytes)
        if (clean != evidence or redacted or truncated
                or len(raw.encode("utf-8")) > self.max_payload_bytes
                or fingerprint != digest_json(evidence)):
            raise StoreUnavailable("model round provider evidence is not bounded")
        required = {"adapter_id", "adapter_version", "source", "capabilities", "verified", "test_only"}
        if set(evidence) != required:
            raise StoreUnavailable("model round provider evidence fields are invalid")
        if (
            not all(isinstance(evidence[name], str) and evidence[name].strip()
                    for name in ("adapter_id", "adapter_version", "source"))
            or not isinstance(evidence["capabilities"], list)
            or not all(isinstance(item, str) and item.strip() for item in evidence["capabilities"])
            or len(set(evidence["capabilities"])) != len(evidence["capabilities"])
            or not isinstance(evidence["verified"], bool)
            or not isinstance(evidence["test_only"], bool)
        ):
            raise StoreUnavailable("model round provider evidence values are invalid")
        self._envelope_checks.remember(key)

    def _validate_model_rounds_locked(self, session: sqlite3.Row) -> None:
        """Validate the durable request/response authority behind continuations."""

        has_provider_usage = {
            "provider_usage_json",
            "provider_usage_digest",
            "usage_evidence_class",
        }.issubset(self._schema_table_columns_locked("model_rounds"))
        has_provider_response_identity = (
            "provider_response_id"
            in self._schema_table_columns_locked("model_rounds")
        )

        rows = self._db.execute(
            "SELECT * FROM model_rounds WHERE session_id = ? "
            "ORDER BY turn_id ASC, round_index ASC",
            (session["session_id"],),
        ).fetchall()
        by_round_id = {row["round_id"]: row for row in rows}
        review_rows = self._db.execute(
            "SELECT * FROM transcript_messages WHERE session_id = ? AND item_id GLOB ?",
            (session["session_id"], f"{RESPONSE_REVIEW_PREFIX}*"),
        ).fetchall()
        reviews_by_round: dict[str, dict[str, Any]] = {}
        reviewed_turns: set[str] = set()
        for review_row in review_rows:
            try:
                review = decode_review_envelope(review_row["content"])
            except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                raise StoreUnavailable("response review envelope is corrupt") from exc
            source = by_round_id.get(review["round_id"])
            if (
                source is None
                or source["status"] != ModelRoundStatus.COMPLETED.value
                or int(source["tool_call_count"]) != 0
                or review_row["role"] != "system"
                or review_row["turn_id"] != source["turn_id"]
                or review_row["item_id"] != f"{RESPONSE_REVIEW_PREFIX}{source['response_id']}"
                or review["response_id"] != source["response_id"]
                or review["response_digest"] != source["response_digest"]
                or not int(source["request_cursor"]) < int(review_row["event_seq"]) < int(source["terminal_cursor"])
                or source["turn_id"] in reviewed_turns
                or bool(review_row["redacted"])
                or bool(review_row["truncated"])
            ):
                raise StoreUnavailable("response review source authority is corrupt")
            if (review["context"] is not None) == bool(source["assistant_item_id"]):
                raise StoreUnavailable("response review publication decision is corrupt")
            reviewed_turns.add(source["turn_id"])
            reviews_by_round[source["round_id"]] = review
        expected_by_turn: dict[str, int] = {}
        for row in rows:
            scope = HarnessScope(
                world_id=session["world_id"],
                engram_id=session["engram_id"],
                context_id=session["context_id"],
                session_id=session["session_id"],
                turn_id=row["turn_id"],
                epoch=int(session["epoch"]),
                owner_id=session["owner_id"],
                capability_digest=session["capability_digest"],
            )
            if not self._row_scope_matches(row, scope):
                raise StoreUnavailable("model round scope is corrupt")
            turn = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                (row["session_id"], row["turn_id"]),
            ).fetchone()
            if turn is None or not self._row_scope_matches(turn, scope):
                raise StoreUnavailable("model round turn reference is corrupt")
            round_index = int(row["round_index"])
            if round_index != expected_by_turn.get(row["turn_id"], 0):
                raise StoreUnavailable("model round index is not contiguous")
            expected_by_turn[row["turn_id"]] = round_index + 1
            if (
                not isinstance(row["round_id"], str)
                or not row["round_id"].strip()
                or not isinstance(row["response_id"], str)
                or not row["response_id"].strip()
                or int(row["request_cursor"]) < 0
                or int(row["terminal_cursor"]) < -1
                or int(row["delta_count"]) < 0
                or int(row["done_count"]) not in {0, 1}
                or int(row["tool_call_count"]) < 0
            ):
                raise StoreUnavailable("model round identity or counters are invalid")
            if int(row["request_cursor"]) > int(session["cursor"]):
                raise StoreUnavailable("model round request cursor is ahead of session")
            if int(row["terminal_cursor"]) > int(session["cursor"]):
                raise StoreUnavailable("model round terminal cursor is ahead of session")
            if row["status"] not in _KNOWN_MODEL_ROUND_STATUSES:
                raise StoreUnavailable("model round status is outside the closed set")
            digest_columns = [
                "request_digest",
                "provider_evidence_digest",
                "response_digest",
            ]
            if has_provider_usage:
                digest_columns.append("provider_usage_digest")
            for digest_column in digest_columns:
                value = row[digest_column]
                if value and (
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(character not in "0123456789abcdef" for character in value)
                ):
                    raise StoreUnavailable(f"model round {digest_column} is invalid")
            self._validate_provider_evidence_locked(row)
            status = ModelRoundStatus(row["status"])
            if has_provider_response_identity:
                provider_response_id = row["provider_response_id"]
                if not isinstance(provider_response_id, str):
                    raise StoreUnavailable(
                        "model round provider response identity is invalid"
                    )
                if provider_response_id:
                    validate_provider_response_id(provider_response_id)
                    if status is not ModelRoundStatus.COMPLETED:
                        raise StoreUnavailable(
                            "non-completed model round has provider response identity"
                        )
            if has_provider_usage:
                usage_json = row["provider_usage_json"]
                usage_evidence_class = row["usage_evidence_class"]
                if not isinstance(usage_evidence_class, str) or not usage_evidence_class.strip():
                    raise StoreUnavailable("model round usage evidence is invalid")
                if usage_json:
                    usage = decode_payload(usage_json)
                    required_usage = {
                        "input_tokens",
                        "output_tokens",
                        "cached_tokens",
                        "cache_write_tokens",
                    }
                    if (
                        usage_json != encode_payload(usage)
                        or not isinstance(usage, Mapping)
                        or set(usage) != required_usage
                        or any(
                            type(usage[name]) is not int or usage[name] < 0
                            for name in required_usage
                        )
                        or usage["cached_tokens"] + usage["cache_write_tokens"]
                        > usage["input_tokens"]
                        or row["provider_usage_digest"] != digest_json(usage)
                        or usage_evidence_class == "KERNEL_PROVIDER_USAGE_UNACCOUNTED"
                        or status is not ModelRoundStatus.COMPLETED
                    ):
                        raise StoreUnavailable("model round provider usage is invalid")
                elif (
                    row["provider_usage_digest"]
                    or usage_evidence_class != "KERNEL_PROVIDER_USAGE_UNACCOUNTED"
                ):
                    raise StoreUnavailable("unaccounted model round usage is invalid")
            if status is ModelRoundStatus.STREAMING:
                if int(row["terminal_cursor"]) != -1 or int(row["done_count"]) != 0:
                    raise StoreUnavailable("streaming model round has terminal fields")
                if row["response_digest"] or row["assistant_item_id"] or int(row["tool_call_count"]):
                    raise StoreUnavailable("streaming model round has response fields")
                if has_provider_response_identity and row["provider_response_id"]:
                    raise StoreUnavailable(
                        "streaming model round has provider response identity"
                    )
            else:
                if int(row["terminal_cursor"]) < int(row["request_cursor"]):
                    raise StoreUnavailable("model round terminal cursor precedes request cursor")
            if status is ModelRoundStatus.COMPLETED and int(row["done_count"]) != 1:
                raise StoreUnavailable("completed model round does not have exactly one DONE")
            if status is ModelRoundStatus.COMPLETED and not row["response_digest"]:
                raise StoreUnavailable("completed model round has no response authority")
            if int(row["tool_call_count"]) and not row["assistant_item_id"]:
                raise StoreUnavailable("tool continuation has no assistant authority item")
            if row["assistant_item_id"]:
                assistant = self._db.execute(
                    "SELECT * FROM transcript_messages WHERE session_id = ? AND item_id = ?",
                    (row["session_id"], row["assistant_item_id"]),
                ).fetchone()
                if (
                    assistant is None
                    or assistant["turn_id"] != row["turn_id"]
                    or assistant["role"] != "assistant"
                    or not self._row_scope_matches(assistant, scope)
                ):
                    raise StoreUnavailable("model round assistant authority item is missing")
            if status is ModelRoundStatus.COMPLETED:
                # The response digest is recomputed from durable assistant
                # truth.  A digest-shaped value alone is not authority: a
                # raw response-digest edit, assistant-content edit, or
                # tool-call argument edit must make every loader/replay path
                # fail closed, including terminal-turn early returns.
                response_payload: dict[str, Any] = {"text": "", "tool_calls": []}
                review = reviews_by_round.get(row["round_id"])
                if review is not None and review["context"] is not None:
                    response_payload["text"] = review["candidate_text"]
                if row["assistant_item_id"]:
                    assistant = self._db.execute(
                        "SELECT * FROM transcript_messages WHERE session_id = ? AND item_id = ?",
                        (row["session_id"], row["assistant_item_id"]),
                    ).fetchone()
                    if assistant is None:
                        raise StoreUnavailable("model round assistant authority item is missing")
                    try:
                        decoded_assistant = json.loads(assistant["content"])
                    except (TypeError, ValueError):
                        response_payload["text"] = assistant["content"]
                    else:
                        if (
                            isinstance(decoded_assistant, Mapping)
                            and "tool_calls" in decoded_assistant
                        ):
                            text_value = decoded_assistant.get("text")
                            raw_calls = decoded_assistant.get("tool_calls")
                            if not isinstance(text_value, str) or not isinstance(raw_calls, list):
                                raise StoreUnavailable(
                                    "model round assistant authority payload is malformed"
                                )
                            canonical_calls: list[dict[str, Any]] = []
                            for raw_call in raw_calls:
                                if not isinstance(raw_call, Mapping):
                                    raise StoreUnavailable(
                                        "model round assistant tool-call authority is malformed"
                                    )
                                required = {
                                    "tool_call_id",
                                    "action_request_id",
                                    "name",
                                    "arguments",
                                }
                                if set(raw_call) != required or not all(
                                    isinstance(raw_call[key], str) and raw_call[key].strip()
                                    for key in ("tool_call_id", "action_request_id", "name")
                                ) or not isinstance(raw_call["arguments"], Mapping):
                                    raise StoreUnavailable(
                                        "model round assistant tool-call authority is invalid"
                                    )
                                canonical_calls.append(
                                    {
                                        "tool_call_id": raw_call["tool_call_id"],
                                        "action_request_id": raw_call["action_request_id"],
                                        "name": raw_call["name"],
                                        "arguments": dict(raw_call["arguments"]),
                                    }
                                )
                            response_payload = {
                                "text": text_value,
                                "tool_calls": canonical_calls,
                            }
                        else:
                            response_payload["text"] = assistant["content"]
                if row["response_digest"] != digest_json(response_payload):
                    raise StoreUnavailable(
                        "model round response digest does not match assistant authority"
                    )
                if int(row["tool_call_count"]) != len(response_payload["tool_calls"]):
                    raise StoreUnavailable(
                        "model round tool-call count does not match assistant authority"
                    )

    def _validate_action_authority_rows_locked(self) -> None:
        """Validate every durable provider/action authority row and its scope."""

        rows = self._db.execute(
            "SELECT * FROM action_authorities ORDER BY session_id, action_request_id"
        ).fetchall()
        for row in rows:
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            if action is None:
                raise StoreUnavailable(
                    "action authority references an unknown action"
                )
            scope = _scope_from_row(action)
            if (
                row["session_id"] != action["session_id"]
                or row["action_request_id"] != action["action_request_id"]
                or row["scope_digest"] != scope.digest
                or not self._row_scope_matches(row, scope)
            ):
                raise StoreUnavailable("action authority scope is corrupt")
            if int(row["authority_required"]) not in {0, 1}:
                raise StoreUnavailable("action authority required flag is invalid")
            authority = decode_payload(row["authority_json"])
            try:
                clean, authority_digest = _normalise_action_authority(
                    authority, self.max_payload_bytes
                )
            except (CapabilityDenied, RedactionRequired, TypeError, ValueError) as exc:
                raise StoreUnavailable("action authority envelope is corrupt") from exc
            if (
                row["authority_json"] != encode_payload(authority)
                or clean != authority
                or row["authority_digest"] != authority_digest
                or row["provider_type"] != authority["provider_type"]
                or row["provider_source"] != authority["provider_source"]
                or row["descriptor_digest"] != authority["descriptor_digest"]
                or int(row["authority_required"]) != int(authority["required"])
            ):
                raise StoreUnavailable("action authority record is not canonical")
            if _AUTHORITY_DIGEST.fullmatch(row["authority_digest"]) is None:
                raise StoreUnavailable("action authority digest is invalid")

    def _validate_action_classification_rows_locked(self) -> None:
        """Validate the closed classification and its authority cardinality.

        The classification table is deliberately separate from the provider
        authority table.  A missing authority row therefore cannot make a
        formerly mutable action look like an ordinary read action: every
        action must first have exactly one classification row, and the
        classification determines the only permitted authority cardinality.
        """

        actions = self._db.execute(
            "SELECT * FROM actions ORDER BY session_id, action_request_id"
        ).fetchall()
        rows = self._db.execute(
            "SELECT * FROM action_classifications ORDER BY session_id, action_request_id"
        ).fetchall()
        if len(rows) != len(actions):
            raise StoreUnavailable("action classification cardinality is corrupt")
        seen: set[str] = set()
        for row in rows:
            action_id = row["action_request_id"]
            if action_id in seen:
                raise StoreUnavailable("action classification is duplicated")
            seen.add(action_id)
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?", (action_id,)
            ).fetchone()
            if action is None:
                raise StoreUnavailable("action classification references an unknown action")
            scope = _scope_from_row(action)
            if (
                row["session_id"] != action["session_id"]
                or row["scope_digest"] != scope.digest
                or not self._row_scope_matches(row, scope)
                or row["tool_name"] != action["tool_name"]
            ):
                raise StoreUnavailable("action classification scope or identity is corrupt")
            try:
                classification = ActionClassification(row["classification"])
            except ValueError as exc:
                raise StoreUnavailable(
                    "action classification is outside the closed set"
                ) from exc
            try:
                classification_origin = ActionClassificationOrigin(
                    row["classification_origin"]
                )
            except (TypeError, ValueError) as exc:
                raise StoreUnavailable(
                    "action classification origin is outside the closed set"
                ) from exc
            if classification is ActionClassification.MUTABLE_REQUIRED:
                valid_origins = {
                    ActionClassificationOrigin.CREATED_V5,
                    ActionClassificationOrigin.MIGRATED_V4_AUTHORITY,
                }
            elif classification is ActionClassification.READ_ONLY_NOT_REQUIRED:
                valid_origins = {
                    ActionClassificationOrigin.CREATED_V5,
                    ActionClassificationOrigin.MIGRATED_V4_READ_ONLY,
                }
            else:
                valid_origins = {
                    ActionClassificationOrigin.MIGRATED_V3,
                }
            if classification_origin not in valid_origins:
                raise StoreUnavailable(
                    "action classification provenance is inconsistent"
                )
            try:
                authority_required = int(row["authority_required"])
            except (TypeError, ValueError) as exc:
                raise StoreUnavailable("action classification authority flag is invalid") from exc
            if authority_required not in {0, 1}:
                raise StoreUnavailable("action classification authority flag is invalid")
            tool_mutable = row["tool_mutable"]
            if tool_mutable is not None:
                try:
                    tool_mutable = int(tool_mutable)
                except (TypeError, ValueError) as exc:
                    raise StoreUnavailable("action classification mutability flag is invalid") from exc
                if tool_mutable not in {0, 1}:
                    raise StoreUnavailable("action classification mutability flag is invalid")
            authority_count = int(
                self._db.execute(
                    "SELECT COUNT(*) FROM action_authorities WHERE action_request_id = ?",
                    (action_id,),
                ).fetchone()[0]
            )
            if classification is ActionClassification.MUTABLE_REQUIRED:
                if (
                    tool_mutable != 1
                    or authority_required != 1
                    or authority_count != 1
                ):
                    raise StoreUnavailable(
                        "mutable action authority cardinality is corrupt"
                    )
                authority = self._action_authority_row(action_id)
                if authority is None or int(authority["authority_required"]) != 1:
                    raise StoreUnavailable(
                        "mutable action authority cardinality is corrupt"
                    )
            elif classification is ActionClassification.READ_ONLY_NOT_REQUIRED:
                if (
                    tool_mutable != 0
                    or authority_required != 0
                    or authority_count != 0
                ):
                    raise StoreUnavailable(
                        "read-only action authority cardinality is corrupt"
                    )
            else:
                if (
                    tool_mutable is not None
                    or authority_required != 0
                    or authority_count != 0
                ):
                    raise StoreUnavailable(
                        "legacy action authority cardinality is corrupt"
                    )
        if seen != {str(action["action_request_id"]) for action in actions}:
            raise StoreUnavailable("action classification cardinality is corrupt")

    def _validate_integrity_locked(self, session: sqlite3.Row) -> None:
        self._validate_event_rows_locked(session)
        self._validate_transcript_rows_locked(session)
        self._validate_model_rounds_locked(session)
        turns = self._db.execute(
            "SELECT * FROM turns WHERE session_id = ?", (session["session_id"],)
        ).fetchall()
        for row in turns:
            scope = HarnessScope(
                world_id=session["world_id"],
                engram_id=session["engram_id"],
                context_id=session["context_id"],
                session_id=session["session_id"],
                turn_id=row["turn_id"],
                epoch=int(session["epoch"]),
                owner_id=session["owner_id"],
                capability_digest=session["capability_digest"],
            )
            if not self._row_scope_matches(row, scope):
                raise StoreUnavailable("turn scope is corrupt")
            if row["prompt_digest"] != digest_json(row["prompt"]):
                raise StoreUnavailable("turn prompt digest is corrupt")
            clean_prompt, prompt_redacted, prompt_truncated = bound_text(
                row["prompt"], self.max_payload_bytes
            )
            stored_redacted, stored_truncated = stored_text_flags(row["prompt"])
            if (
                clean_prompt != row["prompt"]
                or row["prompt_digest"] != digest_json(clean_prompt)
                or int(row["prompt_redacted"])
                != int(stored_redacted or prompt_redacted)
                or int(row["prompt_truncated"])
                != int(stored_truncated or prompt_truncated)
            ):
                raise StoreUnavailable("turn prompt is not redacted or bounded")

    @staticmethod
    def _change_fingerprint(
        *,
        change_id: str,
        action_request_id: str,
        scope_digest: str,
        path: str,
        before_digest: str | None,
        after_digest: str,
        diff: str,
        status: str,
    ) -> str:
        return digest_json(
            {
                "change_id": change_id,
                "action_request_id": action_request_id,
                "scope_digest": scope_digest,
                "path": path,
                "before_digest": before_digest,
                "after_digest": after_digest,
                "diff": diff,
                "status": status,
            }
        )

    @staticmethod
    def _checkpoint_fingerprint(
        *,
        checkpoint_id: str,
        action_request_id: str,
        scope_digest: str,
        path: str,
        artifact_path: str,
        content_digest: str,
        status: str,
    ) -> str:
        return digest_json(
            {
                "checkpoint_id": checkpoint_id,
                "action_request_id": action_request_id,
                "scope_digest": scope_digest,
                "path": path,
                "artifact_path": artifact_path,
                "content_digest": content_digest,
                "status": status,
            }
        )

    def _read_validation_stamp_locked(self) -> tuple[object, ...] | None:
        """Version of the committed SQLite content, not a wall-clock TTL.

        data_version changes for *other* connections; total_changes covers our
        own DML (including rolled-back writes). DDL and PRAGMA user_version need
        separate cookies. Never reuse/cache uncommitted state: a rollback can
        otherwise make a validated temporary state resemble a later transaction.
        """
        if self._db.in_transaction:
            return None
        external = int(self._db.execute("PRAGMA main.data_version").fetchone()[0])
        stamp = (
            id(self._db), self._db.total_changes, external,
            int(self._db.execute("PRAGMA main.schema_version").fetchone()[0]),
            int(self._db.execute("PRAGMA temp.schema_version").fetchone()[0]),
            int(self._db.execute("PRAGMA main.user_version").fetchone()[0]),
            self.max_payload_bytes, self.max_events_per_session,
        )
        # An external commit between reading the cookies cannot be certified.
        if external != int(self._db.execute("PRAGMA main.data_version").fetchone()[0]):
            return None
        return stamp

    def _validate_database_locked(self, *, expected_version: int | None = None) -> None:
        try:
            stamp = self._read_validation_stamp_locked() if expected_version is None else None
            if stamp is not None and stamp == self._validated_read_stamp:
                return
            self._validated_read_stamp = None
            self._validate_database_content_locked(expected_version=expected_version)
            # Keep the full validator unchanged. Only unchanged committed reads
            # reuse its result; all transactions, migrations, policy changes,
            # local writes and external commits force normal validation again.
            if stamp is not None and stamp == self._read_validation_stamp_locked():
                self._validated_read_stamp = stamp
        except StoreUnavailable:
            raise
        except (
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            OverflowError,
            RedactionRequired,
        ) as exc:
            raise StoreUnavailable("HarnessStore durable authority is corrupt") from exc

    def _validate_database_content_locked(
        self, *, expected_version: int | None = None
    ) -> None:
        """Validate the durable authority before exposing any loaded record."""

        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        valid_versions = (
            {_SCHEMA_VERSION}
            if expected_version is None
            else {expected_version}
        )
        if version not in valid_versions:
            raise StoreUnavailable(f"unsupported HarnessStore schema version: {version}")
        if version >= _PROVIDER_RESPONSE_IDENTITY_SCHEMA_VERSION:
            model_schema_ok = self._model_round_schema_is_current_locked()
        elif version >= _PROVIDER_USAGE_SCHEMA_VERSION:
            model_schema_ok = self._model_round_schema_is_v6_locked()
        else:
            model_schema_ok = self._model_round_schema_is_v5_locked()
        if not self._base_schema_is_current_locked() or not model_schema_ok:
            raise StoreUnavailable("HarnessStore schema shape is incomplete")
        if (
            version >= _ACTION_AUTHORITY_SCHEMA_VERSION
            and not self._action_authority_schema_is_current_locked()
        ):
            raise StoreUnavailable("HarnessStore action authority schema is incomplete")
        if (
            version >= _ACTION_CLASSIFICATION_SCHEMA_VERSION
            and not self._action_classification_schema_is_current_locked()
        ):
            raise StoreUnavailable(
                "HarnessStore action classification schema is incomplete"
            )
        sessions = self._db.execute("SELECT * FROM sessions").fetchall()
        for session in sessions:
            try:
                SessionStatus(session["status"])
            except ValueError as exc:
                raise StoreUnavailable("session status is outside the closed set") from exc
            if (
                int(session["revision"]) < 0
                or int(session["cursor"]) < 0
                or int(session["retained_from"]) < 1
                or int(session["transcript_cursor"]) < 0
            ):
                raise StoreUnavailable("session cursor is invalid")
            if int(session["retained_from"]) > int(session["cursor"]) + 1:
                raise StoreUnavailable("session retention cursor is invalid")
            transcript_head = session["transcript_head_digest"]
            if not isinstance(transcript_head, str):
                raise StoreUnavailable("transcript head is invalid")
            if int(session["transcript_cursor"]) == 0 and transcript_head:
                raise StoreUnavailable("empty transcript has a head digest")
            if transcript_head and len(transcript_head) != 64:
                raise StoreUnavailable("transcript head digest is invalid")
            self._validate_integrity_locked(session)

        for row in self._db.execute("SELECT * FROM turns").fetchall():
            try:
                TurnStatus(row["status"])
            except ValueError as exc:
                raise StoreUnavailable("turn status is outside the closed set") from exc
            if int(row["revision"]) < 0:
                raise StoreUnavailable("turn revision is invalid")
            session = self._db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (row["session_id"],)
            ).fetchone()
            if session is None:
                raise StoreUnavailable("turn references an unknown session")
            scope = HarnessScope(
                world_id=row["world_id"],
                engram_id=row["engram_id"],
                context_id=row["context_id"],
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                epoch=int(row["epoch"]),
                owner_id=row["owner_id"],
                capability_digest=row["capability_digest"],
            )
            if not self._row_scope_matches(row, scope):
                raise StoreUnavailable("turn scope is corrupt")
            if row["prompt_digest"] != digest_json(row["prompt"]):
                raise StoreUnavailable("turn prompt digest is corrupt")
            clean_prompt, prompt_redacted, prompt_truncated = bound_text(
                row["prompt"], self.max_payload_bytes
            )
            stored_redacted, stored_truncated = stored_text_flags(row["prompt"])
            if (
                clean_prompt != row["prompt"]
                or int(row["prompt_redacted"])
                != int(stored_redacted or prompt_redacted)
                or int(row["prompt_truncated"])
                != int(stored_truncated or prompt_truncated)
            ):
                raise StoreUnavailable("turn prompt is not redacted or bounded")
            streaming_round = self._db.execute(
                """SELECT 1 FROM model_rounds
                   WHERE session_id = ? AND turn_id = ? AND status = ?
                   LIMIT 1""",
                (
                    row["session_id"],
                    row["turn_id"],
                    ModelRoundStatus.STREAMING.value,
                ),
            ).fetchone()
            if streaming_round is not None and TurnStatus(row["status"]) is not TurnStatus.MODEL_STREAMING:
                raise StoreUnavailable(
                    "non-streaming turn has an open model round"
                )

        for table in ("events", "transcript_messages", "model_rounds"):
            for row in self._db.execute(
                f"SELECT session_id FROM {table}"
            ).fetchall():
                if self._db.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?", (row["session_id"],)
                ).fetchone() is None:
                    raise StoreUnavailable(f"{table} references an unknown session")

        for row in self._db.execute("SELECT * FROM actions").fetchall():
            try:
                action_status = ActionStatus(row["status"])
            except ValueError as exc:
                raise StoreUnavailable("action status is outside the closed set") from exc
            if not row["action_request_id"].strip() or not row["tool_name"].strip():
                raise StoreUnavailable("action identity is invalid")
            if int(row["revision"]) < 0 or int(row["result_delivered"]) not in {0, 1}:
                raise StoreUnavailable("action revision or delivery flag is invalid")
            session = self._db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (row["session_id"],)
            ).fetchone()
            if session is None:
                raise StoreUnavailable("action references an unknown session")
            scope = HarnessScope(
                world_id=row["world_id"],
                engram_id=row["engram_id"],
                context_id=row["context_id"],
                session_id=row["session_id"],
                turn_id=row["turn_id"],
                epoch=int(row["epoch"]),
                owner_id=row["owner_id"],
                capability_digest=row["capability_digest"],
            )
            if not self._row_scope_matches(row, scope):
                raise StoreUnavailable("action scope is corrupt")
            turn = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                (row["session_id"], row["turn_id"]),
            ).fetchone()
            if turn is None or not self._row_scope_matches(turn, scope):
                raise StoreUnavailable("action turn reference is corrupt")
            args = decode_payload(row["arguments_json"])
            bounded_args, args_redacted, args_truncated = bound_payload(
                args, self.max_payload_bytes
            )
            stored_args_redacted, stored_args_truncated = stored_payload_flags(args)
            if (
                row["arguments_json"] != encode_payload(args)
                or bounded_args != args
                or int(row["args_redacted"])
                != int(stored_args_redacted or args_redacted)
                or int(row["args_truncated"])
                != int(stored_args_truncated or args_truncated)
            ):
                raise StoreUnavailable("action arguments are not canonical or redacted")
            if len(row["arguments_json"].encode("utf-8")) > self.max_payload_bytes:
                raise StoreUnavailable("action arguments exceed the durable bound")
            if row["args_digest"] != digest_json(args):
                raise StoreUnavailable("action argument digest is corrupt")
            for column in (
                "args_redacted",
                "args_truncated",
                "result_redacted",
                "result_truncated",
            ):
                if int(row[column]) not in {0, 1}:
                    raise StoreUnavailable("action result flags are invalid")
            if row["result_json"] is None and any(
                int(row[column]) for column in ("result_redacted", "result_truncated")
            ):
                raise StoreUnavailable("empty action result has visibility flags")
            if row["result_json"] is not None:
                result = decode_payload(row["result_json"])
                bounded_result, result_redacted, result_truncated = bound_payload(
                    result, self.max_payload_bytes
                )
                stored_result_redacted, stored_result_truncated = stored_payload_flags(
                    result
                )
                if (
                    row["result_json"] != encode_payload(result)
                    or bounded_result != result
                    or row["result_digest"] != digest_json(result)
                    or int(row["result_redacted"])
                    != int(stored_result_redacted or result_redacted)
                    or int(row["result_truncated"])
                    != int(stored_result_truncated or result_truncated)
                ):
                    raise StoreUnavailable("action result is not canonical or redacted")
                if len(row["result_json"].encode("utf-8")) > self.max_payload_bytes:
                    raise StoreUnavailable("action result exceeds the durable bound")
            elif row["result_digest"]:
                raise StoreUnavailable("empty action result has a digest")

            terminal = self._db.execute(
                "SELECT * FROM action_terminals WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            if action_status.terminal:
                if terminal is None or terminal["status"] != action_status.value:
                    raise StoreUnavailable("action terminal cardinality is corrupt")
                if terminal["scope_digest"] != scope.digest:
                    raise StoreUnavailable("action terminal scope is corrupt")
                terminal_result = decode_payload(terminal["result_json"])
                if terminal["result_json"] != encode_payload(terminal_result):
                    raise StoreUnavailable("action terminal result is not canonical")
                clean_terminal_result, terminal_redacted, terminal_truncated = (
                    bound_payload(terminal_result, self.max_payload_bytes)
                )
                stored_terminal_redacted, stored_terminal_truncated = stored_payload_flags(
                    terminal_result
                )
                if (
                    clean_terminal_result != terminal_result
                    or len(terminal["result_json"].encode("utf-8"))
                    > self.max_payload_bytes
                    or row["result_digest"] != digest_json(terminal_result)
                    or int(row["result_redacted"])
                    != int(stored_terminal_redacted or terminal_redacted)
                    or int(row["result_truncated"])
                    != int(stored_terminal_truncated or terminal_truncated)
                ):
                    raise StoreUnavailable("action terminal result is not bounded")
                if (
                    row["result_json"] != terminal["result_json"]
                    or row["error_code"] != terminal["error_code"]
                ):
                    raise StoreUnavailable("action terminal result does not match action")
            elif terminal is not None:
                raise StoreUnavailable("non-terminal action has a terminal record")

            approvals = self._db.execute(
                "SELECT * FROM approvals WHERE action_request_id = ?", (row["action_request_id"],)
            ).fetchall()
            if row["approval_id"] is None:
                if approvals:
                    raise StoreUnavailable("action has an unreferenced approval")
            else:
                if len(approvals) != 1 or approvals[0]["approval_id"] != row["approval_id"]:
                    raise StoreUnavailable("action approval cardinality is corrupt")
                approval = approvals[0]
                if approval["session_id"] != row["session_id"] or approval["scope_digest"] != scope.digest:
                    raise StoreUnavailable("approval scope is corrupt")
                try:
                    approval_status = ApprovalStatus(approval["status"])
                except ValueError as exc:
                    raise StoreUnavailable("approval status is outside the closed set") from exc
                decision = approval["decision"]
                valid_decisions = {item.value for item in ApprovalDecision}
                if decision is not None and decision not in valid_decisions:
                    raise StoreUnavailable("approval decision is outside the closed set")
                expected_decision = {
                    ApprovalStatus.PENDING: None,
                    ApprovalStatus.ALLOWED_ONCE: ApprovalDecision.ALLOW_ONCE.value,
                    ApprovalStatus.DENIED: ApprovalDecision.DENY.value,
                    ApprovalStatus.CANCELLED: ApprovalDecision.CANCEL.value,
                    ApprovalStatus.EXPIRED: None,
                }[approval_status]
                if decision != expected_decision:
                    raise StoreUnavailable("approval decision/status pair is corrupt")
                expected_action_statuses = {
                    ApprovalStatus.PENDING: {ActionStatus.APPROVAL_PENDING},
                    ApprovalStatus.ALLOWED_ONCE: {
                        ActionStatus.STARTING,
                        ActionStatus.RUNNING,
                        ActionStatus.TERMINALIZING,
                        ActionStatus.SUCCEEDED,
                        ActionStatus.FAILED,
                        ActionStatus.CANCELLED,
                        ActionStatus.UNCERTAIN,
                    },
                    ApprovalStatus.DENIED: {ActionStatus.FAILED},
                    ApprovalStatus.CANCELLED: {ActionStatus.CANCELLED},
                    ApprovalStatus.EXPIRED: {ActionStatus.CANCELLED},
                }[approval_status]
                if action_status not in expected_action_statuses:
                    raise StoreUnavailable("approval and action states diverge")
            if row["approval_id"] is None and action_status is ActionStatus.APPROVAL_PENDING:
                raise StoreUnavailable("approval_pending action has no approval record")

            operation = None
            if row["operation_id"] is not None:
                operation = self._db.execute(
                    "SELECT * FROM operations WHERE operation_id = ?", (row["operation_id"],)
                ).fetchone()
                if operation is None or operation["action_request_id"] != row["action_request_id"]:
                    raise StoreUnavailable("action operation cardinality is corrupt")
                if operation["scope_digest"] != scope.digest:
                    raise StoreUnavailable("operation scope is corrupt")
            linked_operations = self._db.execute(
                "SELECT * FROM operations WHERE action_request_id = ?", (row["action_request_id"],)
            ).fetchall()
            if len(linked_operations) > 1 or (operation is None and linked_operations):
                raise StoreUnavailable("action has an orphan operation link")
            if operation is not None:
                try:
                    operation_status = OperationStatus(operation["status"])
                except ValueError as exc:
                    raise StoreUnavailable("operation status is outside the closed set") from exc
                operation_is_terminal = operation_status in {
                    OperationStatus.SUCCEEDED,
                    OperationStatus.FAILED,
                    OperationStatus.CANCELLED,
                    OperationStatus.UNCERTAIN,
                }
                if action_status.terminal != operation_is_terminal:
                    raise StoreUnavailable("action and operation terminal states diverge")
                operation_terminal = self._db.execute(
                    "SELECT * FROM operation_terminals WHERE operation_id = ?",
                    (operation["operation_id"],),
                ).fetchone()
                if operation_status in {
                    OperationStatus.SUCCEEDED,
                    OperationStatus.FAILED,
                    OperationStatus.CANCELLED,
                    OperationStatus.UNCERTAIN,
                }:
                    if operation_terminal is None or operation_terminal["status"] != operation_status.value:
                        raise StoreUnavailable("operation terminal cardinality is corrupt")
                    if (
                        operation_terminal["scope_digest"] != scope.digest
                        or operation_terminal["action_request_id"] != row["action_request_id"]
                        or operation_terminal["result_json"] != operation["result_json"]
                        or operation_terminal["error_code"] != operation["error_code"]
                    ):
                        raise StoreUnavailable("operation terminal result is corrupt")
                    terminal_result = decode_payload(operation_terminal["result_json"])
                    clean_terminal_result, terminal_redacted, terminal_truncated = (
                        bound_payload(terminal_result, self.max_payload_bytes)
                    )
                    stored_terminal_redacted, stored_terminal_truncated = (
                        stored_payload_flags(terminal_result)
                    )
                    if (
                        clean_terminal_result != terminal_result
                        or len(operation_terminal["result_json"].encode("utf-8"))
                        > self.max_payload_bytes
                        or operation["result_digest"] != digest_json(terminal_result)
                        or int(operation["result_redacted"])
                        != int(stored_terminal_redacted or terminal_redacted)
                        or int(operation["result_truncated"])
                        != int(stored_terminal_truncated or terminal_truncated)
                    ):
                        raise StoreUnavailable("operation terminal result is not bounded")
                elif operation_terminal is not None:
                    raise StoreUnavailable("non-terminal operation has a terminal record")

        if version >= _ACTION_CLASSIFICATION_SCHEMA_VERSION:
            self._validate_action_classification_rows_locked()
        if version >= _ACTION_AUTHORITY_SCHEMA_VERSION:
            self._validate_action_authority_rows_locked()

        for row in self._db.execute("SELECT * FROM approvals").fetchall():
            if row["decision"] == "allow_session":
                raise StoreUnavailable("allow_session is not a v0.1 decision")
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            if action is None or action["approval_id"] != row["approval_id"]:
                raise StoreUnavailable("approval references an unknown or different action")
            action_scope = _scope_from_row(action)
            if (
                row["session_id"] != action_scope.session_id
                or row["scope_digest"] != action_scope.digest
            ):
                raise StoreUnavailable("approval scope is corrupt")
            try:
                approval_status = ApprovalStatus(row["status"])
            except ValueError as exc:
                raise StoreUnavailable("approval status is outside the closed set") from exc
            if row["decision"] is not None and row["decision"] not in {
                decision.value for decision in ApprovalDecision
            }:
                raise StoreUnavailable("approval decision is outside the closed set")
            try:
                _parse_time(row["expires_at"])
            except (TypeError, ValueError) as exc:
                raise StoreUnavailable("approval expiry is invalid") from exc
            expected_decision = {
                ApprovalStatus.PENDING: None,
                ApprovalStatus.ALLOWED_ONCE: ApprovalDecision.ALLOW_ONCE.value,
                ApprovalStatus.DENIED: ApprovalDecision.DENY.value,
                ApprovalStatus.CANCELLED: ApprovalDecision.CANCEL.value,
                ApprovalStatus.EXPIRED: None,
            }[approval_status]
            if row["decision"] != expected_decision:
                raise StoreUnavailable("approval decision/status pair is corrupt")

        for row in self._db.execute("SELECT * FROM operations").fetchall():
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            if action is None or action["operation_id"] != row["operation_id"]:
                raise StoreUnavailable("operation references an unknown or different action")
            action_scope = _scope_from_row(action)
            if (
                row["session_id"] != action_scope.session_id
                or row["scope_digest"] != action_scope.digest
            ):
                raise StoreUnavailable("operation scope is corrupt")
            if int(row["revision"]) < 0:
                raise StoreUnavailable("operation revision is invalid")
            try:
                operation_status = OperationStatus(row["status"])
            except ValueError as exc:
                raise StoreUnavailable("operation status is outside the closed set") from exc
            action_status = ActionStatus(action["status"])
            operation_terminal = operation_status in {
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
                OperationStatus.UNCERTAIN,
            }
            if operation_terminal:
                if not action_status.terminal or action_status.value != operation_status.value:
                    raise StoreUnavailable("operation and action terminal states diverge")
            elif action_status not in {ActionStatus.STARTING, ActionStatus.RUNNING}:
                raise StoreUnavailable("operation is live without a live action")

        for row in self._db.execute("SELECT * FROM action_terminals").fetchall():
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            if action is None:
                raise StoreUnavailable("action terminal references an unknown action")
            if (
                row["session_id"] != action["session_id"]
                or row["scope_digest"] != _scope_from_row(action).digest
                or not ActionStatus(action["status"]).terminal
                or row["status"] != action["status"]
                or row["result_json"] != action["result_json"]
                or row["error_code"] != action["error_code"]
            ):
                raise StoreUnavailable("action terminal cardinality is corrupt")
            try:
                terminal_status = ActionStatus(row["status"])
            except ValueError as exc:
                raise StoreUnavailable("action terminal status is outside the closed set") from exc
            if not terminal_status.terminal:
                raise StoreUnavailable("action terminal status is not terminal")
            terminal_result = decode_payload(row["result_json"])
            clean_terminal, _, _ = bound_payload(
                terminal_result, self.max_payload_bytes
            )
            if (
                clean_terminal != terminal_result
                or len(row["result_json"].encode("utf-8")) > self.max_payload_bytes
            ):
                raise StoreUnavailable("action terminal result is not bounded")

        for row in self._db.execute("SELECT * FROM operation_terminals").fetchall():
            operation = self._db.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (row["operation_id"],),
            ).fetchone()
            if operation is None:
                raise StoreUnavailable("operation terminal references an unknown operation")
            if (
                row["action_request_id"] != operation["action_request_id"]
                or row["session_id"] != operation["session_id"]
                or row["scope_digest"] != operation["scope_digest"]
                or row["status"] != operation["status"]
                or row["result_json"] != operation["result_json"]
                or row["error_code"] != operation["error_code"]
            ):
                raise StoreUnavailable("operation terminal cardinality is corrupt")
            try:
                terminal_status = OperationStatus(row["status"])
            except ValueError as exc:
                raise StoreUnavailable("operation terminal status is outside the closed set") from exc
            if terminal_status not in {
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
                OperationStatus.UNCERTAIN,
            }:
                raise StoreUnavailable("operation terminal status is not terminal")

        for row in self._db.execute("SELECT * FROM recoveries").fetchall():
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            if action is None or row["session_id"] != action["session_id"]:
                raise StoreUnavailable("recovery references an unknown action")
            if row["scope_digest"] != _scope_from_row(action).digest:
                raise StoreUnavailable("recovery scope is corrupt")
            if row["state"] != "uncertain" or int(row["confirmed_exit"]) not in {0, 1}:
                raise StoreUnavailable("recovery state is outside the closed set")

        for row in self._db.execute("SELECT * FROM operations").fetchall():
            if row["result_json"] is not None:
                result = decode_payload(row["result_json"])
                bounded_result, result_redacted, result_truncated = bound_payload(
                    result, self.max_payload_bytes
                )
                stored_result_redacted, stored_result_truncated = stored_payload_flags(
                    result
                )
                if (
                    row["result_json"] != encode_payload(result)
                    or bounded_result != result
                    or row["result_digest"] != digest_json(result)
                    or int(row["result_redacted"])
                    != int(stored_result_redacted or result_redacted)
                    or int(row["result_truncated"])
                    != int(stored_result_truncated or result_truncated)
                ):
                    raise StoreUnavailable("operation result is not canonical or redacted")
                if len(row["result_json"].encode("utf-8")) > self.max_payload_bytes:
                    raise StoreUnavailable("operation result exceeds the durable bound")
            elif row["result_digest"]:
                raise StoreUnavailable("empty operation result has a digest")
            elif any(int(row[column]) for column in ("result_redacted", "result_truncated")):
                raise StoreUnavailable("empty operation result has visibility flags")
            for column in ("result_redacted", "result_truncated"):
                if int(row[column]) not in {0, 1}:
                    raise StoreUnavailable("operation result flags are invalid")

        for row in self._db.execute("SELECT * FROM changes").fetchall():
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            approved_effect = False
            if action is not None and action["approval_id"] is not None:
                approval = self._db.execute(
                    "SELECT status FROM approvals WHERE approval_id = ?",
                    (action["approval_id"],),
                ).fetchone()
                approved_effect = (
                    approval is not None
                    and approval["status"] == ApprovalStatus.ALLOWED_ONCE.value
                )
            if (
                action is None
                or action["session_id"] != row["session_id"]
                or action["operation_id"] is None
                or action["status"] in {ActionStatus.INTENT.value, ActionStatus.APPROVAL_PENDING.value}
                or (action["approval_id"] is not None and not approved_effect)
                or row["scope_digest"]
                != HarnessScope(
                    world_id=action["world_id"],
                    engram_id=action["engram_id"],
                    context_id=action["context_id"],
                    session_id=action["session_id"],
                    turn_id=action["turn_id"],
                    epoch=int(action["epoch"]),
                    owner_id=action["owner_id"],
                    capability_digest=action["capability_digest"],
                ).digest
            ):
                raise StoreUnavailable("change action chain is corrupt")
            if any(
                int(row[column]) not in {0, 1}
                for column in (
                    "path_redacted",
                    "path_truncated",
                    "diff_redacted",
                    "diff_truncated",
                )
            ):
                raise StoreUnavailable("change visibility flags are invalid")
            clean_path, path_redacted, path_truncated = bound_text(
                row["path"], self.max_payload_bytes
            )
            clean_diff, diff_redacted, diff_truncated = bound_text(
                row["diff"], self.max_payload_bytes
            )
            stored_path_redacted, stored_path_truncated = stored_text_flags(row["path"])
            stored_diff_redacted, stored_diff_truncated = stored_text_flags(row["diff"])
            if (
                clean_path != row["path"]
                or clean_diff != row["diff"]
                or int(row["path_redacted"])
                != int(stored_path_redacted or path_redacted)
                or int(row["path_truncated"])
                != int(stored_path_truncated or path_truncated)
                or int(row["diff_redacted"])
                != int(stored_diff_redacted or diff_redacted)
                or int(row["diff_truncated"])
                != int(stored_diff_truncated or diff_truncated)
            ):
                raise StoreUnavailable("change text is not redacted")
            if any(
                len(value.encode("utf-8")) > self.max_payload_bytes
                for value in (row["path"], row["diff"])
            ):
                raise StoreUnavailable("change text exceeds the durable bound")
            expected = self._change_fingerprint(
                change_id=row["change_id"],
                action_request_id=row["action_request_id"],
                scope_digest=row["scope_digest"],
                path=row["path"],
                before_digest=row["before_digest"],
                after_digest=row["after_digest"],
                diff=row["diff"],
                status=row["status"],
            )
            if row["record_digest"] != expected:
                raise StoreUnavailable("change record digest is corrupt")

        for row in self._db.execute("SELECT * FROM checkpoints").fetchall():
            action = self._db.execute(
                "SELECT * FROM actions WHERE action_request_id = ?",
                (row["action_request_id"],),
            ).fetchone()
            approved_effect = False
            if action is not None and action["approval_id"] is not None:
                approval = self._db.execute(
                    "SELECT status FROM approvals WHERE approval_id = ?",
                    (action["approval_id"],),
                ).fetchone()
                approved_effect = (
                    approval is not None
                    and approval["status"] == ApprovalStatus.ALLOWED_ONCE.value
                )
            if (
                action is None
                or action["session_id"] != row["session_id"]
                or action["operation_id"] is None
                or action["status"] in {ActionStatus.INTENT.value, ActionStatus.APPROVAL_PENDING.value}
                or (action["approval_id"] is not None and not approved_effect)
                or row["scope_digest"]
                != HarnessScope(
                    world_id=action["world_id"],
                    engram_id=action["engram_id"],
                    context_id=action["context_id"],
                    session_id=action["session_id"],
                    turn_id=action["turn_id"],
                    epoch=int(action["epoch"]),
                    owner_id=action["owner_id"],
                    capability_digest=action["capability_digest"],
                ).digest
            ):
                raise StoreUnavailable("checkpoint action chain is corrupt")
            if any(
                int(row[column]) not in {0, 1}
                for column in (
                    "path_redacted",
                    "path_truncated",
                    "artifact_redacted",
                    "artifact_truncated",
                )
            ):
                raise StoreUnavailable("checkpoint visibility flags are invalid")
            clean_path, path_redacted, path_truncated = bound_text(
                row["path"], self.max_payload_bytes
            )
            clean_artifact, artifact_redacted, artifact_truncated = bound_text(
                row["artifact_path"], self.max_payload_bytes
            )
            stored_path_redacted, stored_path_truncated = stored_text_flags(row["path"])
            stored_artifact_redacted, stored_artifact_truncated = stored_text_flags(
                row["artifact_path"]
            )
            if (
                clean_path != row["path"]
                or clean_artifact != row["artifact_path"]
                or int(row["path_redacted"])
                != int(stored_path_redacted or path_redacted)
                or int(row["path_truncated"])
                != int(stored_path_truncated or path_truncated)
                or int(row["artifact_redacted"])
                != int(stored_artifact_redacted or artifact_redacted)
                or int(row["artifact_truncated"])
                != int(stored_artifact_truncated or artifact_truncated)
            ):
                raise StoreUnavailable("checkpoint text is not redacted")
            if any(
                len(value.encode("utf-8")) > self.max_payload_bytes
                for value in (row["path"], row["artifact_path"])
            ):
                raise StoreUnavailable("checkpoint text exceeds the durable bound")
            expected = self._checkpoint_fingerprint(
                checkpoint_id=row["checkpoint_id"],
                action_request_id=row["action_request_id"],
                scope_digest=row["scope_digest"],
                path=row["path"],
                artifact_path=row["artifact_path"],
                content_digest=row["content_digest"],
                status=row["status"],
            )
            if row["record_digest"] != expected:
                raise StoreUnavailable("checkpoint record digest is corrupt")

    def get_turn(self, scope: HarnessScope) -> HarnessTurn:
        if not scope.turn_id:
            raise ValueError("turn scope requires turn_id")
        with self._lock:
            self._require_open()
            row = self._assert_turn_scope(scope)
            self._validate_database_locked()
            row = self._assert_turn_scope(scope)
            return self._turn_from_row(row)

    def set_turn_status(
        self,
        scope: HarnessScope,
        status: TurnStatus,
        *,
        expected_revision: int | None = None,
    ) -> HarnessTurn:
        with self._lock:
            with self._transaction():
                row = self._assert_turn_scope(scope)
                revision = int(row["revision"])
                if expected_revision is not None and revision != expected_revision:
                    raise RevisionConflict("turn revision mismatch")
                current = TurnStatus(row["status"])
                if status not in self._turn_status_transitions()[current]:
                    raise InvalidTransition(
                        f"cannot transition turn {current.value} -> {status.value}"
                    )
                if status is current:
                    return self._turn_from_row(row)
                if status is not TurnStatus.MODEL_STREAMING and self._db.execute(
                    """SELECT 1 FROM model_rounds
                       WHERE session_id = ? AND turn_id = ? AND status = ?
                       LIMIT 1""",
                    (scope.session_id, scope.turn_id, ModelRoundStatus.STREAMING.value),
                ).fetchone() is not None:
                    raise InvalidTransition(
                        "cannot leave a streaming turn with an open model round"
                    )
                now = utc_now()
                self._db.execute(
                    "UPDATE turns SET status = ?, revision = ?, updated_at = ? WHERE session_id = ? AND turn_id = ?",
                    (
                        status.value,
                        revision + 1,
                        now,
                        scope.session_id,
                        scope.turn_id,
                    ),
                )
                return self._turn_from_row(self._turn_row(scope.session_id, scope.turn_id))

    @staticmethod
    def _event_fingerprint(
        draft: EventDraft,
        payload: Mapping[str, Any],
        *,
        seq: int,
        redacted: bool,
        truncated: bool,
    ) -> str:
        return digest_json(
            {
                "event_id": draft.event_id,
                "item_id": draft.item_id,
                "parent_item_id": draft.parent_item_id,
                "scope": draft.scope.as_dict(),
                "event_type": draft.event_type,
                "source": draft.source,
                "phase": draft.phase,
                "status": draft.status,
                "payload": payload,
                "delta_seq": draft.delta_seq,
                "revision": draft.revision,
                "action_request_id": draft.action_request_id,
                "approval_id": draft.approval_id,
                "operation_id": draft.operation_id,
                "seq": seq,
                "redacted": redacted,
                "truncated": truncated,
                "gap": draft.gap,
            }
        )

    @staticmethod
    def _transcript_head_step(
        previous_head: str,
        *,
        scope: HarnessScope,
        message_id: str,
        item_id: str,
        parent_item_id: str | None,
        transcript_seq: int,
        role: str,
        content: str,
        content_digest: str,
        event_id: str,
        event_seq: int,
        redacted: bool,
        truncated: bool,
        created_at: str,
    ) -> str:
        """Advance the transcript completeness checksum.

        This is a relational checksum for accidental/local corruption. It
        is intentionally not a MAC and therefore does not claim to detect an
        administrator who can rewrite both rows and every stored digest.
        """

        return digest_json(
            {
                "previous": previous_head,
                "message_id": message_id,
                "item_id": item_id,
                "parent_item_id": parent_item_id,
                "scope": scope.as_dict(),
                "transcript_seq": transcript_seq,
                "role": role,
                "content": content,
                "content_digest": content_digest,
                "event_id": event_id,
                "event_seq": event_seq,
                "redacted": redacted,
                "truncated": truncated,
                "created_at": created_at,
            }
        )

    def _append_event_locked(self, draft: EventDraft) -> HarnessEvent:
        if draft.status not in _KNOWN_EVENT_STATUSES:
            raise ValueError("event status is outside the closed set")
        session = self._assert_session_scope(draft.scope)
        self._assert_event_references_locked(draft)
        existing = self._db.execute(
            "SELECT * FROM events WHERE event_id = ?", (draft.event_id,)
        ).fetchone()
        clean_payload, redacted, truncated = bound_payload(
            draft.payload, self.max_payload_bytes
        )
        if existing is not None:
            existing_scope = HarnessScope(
                world_id=existing["world_id"],
                engram_id=existing["engram_id"],
                context_id=existing["context_id"],
                session_id=existing["session_id"],
                turn_id=existing["turn_id"],
                epoch=int(existing["epoch"]),
                owner_id=existing["owner_id"],
                capability_digest=existing["capability_digest"],
            )
            if existing_scope.digest != draft.scope.digest:
                raise ScopeConflict("event id is used by another scope")
            fingerprint = self._event_fingerprint(
                draft,
                clean_payload,
                seq=int(existing["seq"]),
                redacted=bool(existing["redacted"]),
                truncated=bool(existing["truncated"]),
            )
            if existing["event_digest"] != fingerprint:
                raise IdempotencyConflict("event id retry payload differs")
            return event_from_row(existing)
        prior_item = self._db.execute(
            "SELECT MAX(delta_seq) AS max_delta, MAX(revision) AS max_revision FROM events WHERE session_id = ? AND item_id = ?",
            (draft.scope.session_id, draft.item_id),
        ).fetchone()
        if prior_item["max_delta"] is None:
            if draft.delta_seq != 0 or draft.revision != 0:
                raise RevisionConflict("new item must start at delta_seq/revision zero")
        else:
            if draft.delta_seq != int(prior_item["max_delta"]) + 1:
                raise RevisionConflict("item delta_seq must be gap-free")
            if draft.revision != int(prior_item["max_revision"]) + 1:
                raise RevisionConflict("item revision must be gap-free")
        seq = int(session["cursor"]) + 1
        now = draft.created_at or utc_now()
        fingerprint = self._event_fingerprint(
            draft,
            clean_payload,
            seq=seq,
            redacted=redacted,
            truncated=truncated,
        )
        self._db.execute(
            """INSERT INTO events
            (event_id, item_id, parent_item_id, session_id, world_id, engram_id,
             context_id, turn_id, epoch, owner_id, capability_digest, seq, delta_seq,
             revision, event_type, source, phase, status, action_request_id,
             approval_id, operation_id, payload_json, event_digest, redacted,
             truncated, gap, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draft.event_id,
                draft.item_id,
                draft.parent_item_id,
                draft.scope.session_id,
                draft.scope.world_id,
                draft.scope.engram_id,
                draft.scope.context_id,
                draft.scope.turn_id,
                draft.scope.epoch,
                draft.scope.owner_id,
                draft.scope.capability_digest,
                seq,
                draft.delta_seq,
                draft.revision,
                draft.event_type,
                draft.source,
                draft.phase,
                draft.status,
                draft.action_request_id,
                draft.approval_id,
                draft.operation_id,
                encode_payload(clean_payload),
                fingerprint,
                int(redacted),
                int(truncated),
                int(draft.gap),
                now,
            ),
        )
        self._db.execute(
            "UPDATE sessions SET cursor = ?, revision = revision + 1, updated_at = ? WHERE session_id = ?",
            (seq, now, draft.scope.session_id),
        )
        count = int(
            self._db.execute(
                "SELECT COUNT(*) AS count FROM events WHERE session_id = ?",
                (draft.scope.session_id,),
            ).fetchone()["count"]
        )
        if count > self.max_events_per_session:
            overflow = count - self.max_events_per_session
            self._db.execute(
                """DELETE FROM events WHERE event_id IN
                (SELECT event_id FROM events WHERE session_id = ? ORDER BY seq ASC LIMIT ?)""",
                (draft.scope.session_id, overflow),
            )
            retained = self._db.execute(
                "SELECT MIN(seq) AS retained FROM events WHERE session_id = ?",
                (draft.scope.session_id,),
            ).fetchone()["retained"]
            self._db.execute(
                "UPDATE sessions SET retained_from = ? WHERE session_id = ?",
                (int(retained) if retained is not None else seq + 1, draft.scope.session_id),
            )
        row = self._db.execute(
            "SELECT * FROM events WHERE event_id = ?", (draft.event_id,)
        ).fetchone()
        if row is None:
            raise StoreUnavailable("event disappeared after append")
        return event_from_row(row)

    @staticmethod
    def _transcript_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "message_id": row["message_id"],
            "item_id": row["item_id"],
            "parent_item_id": row["parent_item_id"],
            "scope": _scope_from_row(row),
            "role": row["role"],
            "content": row["content"],
            "event_id": row["event_id"],
            "seq": int(row["event_seq"]),
            "transcript_seq": int(row["transcript_seq"]),
            "redacted": bool(row["redacted"]),
            "truncated": bool(row["truncated"]),
        }

    def _append_transcript_locked(
        self,
        scope: HarnessScope,
        *,
        role: str,
        clean_content: str,
        redacted: bool,
        truncated: bool,
        message_id: str,
        parent_item_id: str | None,
        content_digest: str,
    ) -> dict[str, Any]:
        """Append transcript truth while an owning transaction is active."""

        self._assert_turn_scope(scope)
        # Do not allocate a new truth sequence from MAX(rows): a raw suffix
        # deletion must remain visible as corruption rather than becoming the
        # new append point.
        self._validate_database_locked()
        session = self._assert_session_scope(scope)
        existing = self._db.execute(
            "SELECT * FROM transcript_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if existing is None:
            existing = self._db.execute(
                "SELECT * FROM transcript_messages WHERE session_id = ? AND item_id = ?",
                (scope.session_id, message_id),
            ).fetchone()
        if existing is not None:
            if not self._row_scope_matches(existing, scope):
                raise ScopeConflict("transcript message id is used by another scope")
            if (
                existing["role"] != role
                or existing["content_digest"] != content_digest
                or existing["parent_item_id"] != parent_item_id
            ):
                raise IdempotencyConflict("transcript message differs on retry")
            return self._transcript_from_row(existing)

        transcript_seq = int(session["transcript_cursor"]) + 1
        previous_head = session["transcript_head_digest"]
        event = self._append_event_locked(
            EventDraft(
                scope=scope,
                event_type="transcript.message",
                source="harness",
                phase="transcript",
                status="appended",
                payload={"role": role, "content": clean_content},
                item_id=message_id,
                parent_item_id=parent_item_id,
            )
        )
        now = event.created_at
        self._db.execute(
            """INSERT INTO transcript_messages
            (message_id, item_id, parent_item_id, session_id, world_id,
             engram_id, context_id, turn_id, epoch, owner_id,
             capability_digest, transcript_seq, role, content,
             content_digest, event_id, event_seq, redacted, truncated,
             created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id,
                message_id,
                parent_item_id,
                scope.session_id,
                scope.world_id,
                scope.engram_id,
                scope.context_id,
                scope.turn_id,
                scope.epoch,
                scope.owner_id,
                scope.capability_digest,
                transcript_seq,
                role,
                clean_content,
                content_digest,
                event.event_id,
                event.seq,
                int(redacted),
                int(truncated),
                now,
            ),
        )
        transcript_head = self._transcript_head_step(
            previous_head,
            scope=scope,
            message_id=message_id,
            item_id=message_id,
            parent_item_id=parent_item_id,
            transcript_seq=transcript_seq,
            role=role,
            content=clean_content,
            content_digest=content_digest,
            event_id=event.event_id,
            event_seq=event.seq,
            redacted=bool(redacted),
            truncated=bool(truncated),
            created_at=now,
        )
        self._db.execute(
            "UPDATE sessions SET transcript_cursor = ?, transcript_head_digest = ? WHERE session_id = ?",
            (transcript_seq, transcript_head, scope.session_id),
        )
        row = self._db.execute(
            "SELECT * FROM transcript_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            raise StoreUnavailable("transcript message disappeared after append")
        return self._transcript_from_row(row)

    def append_transcript(
        self,
        scope: HarnessScope,
        *,
        role: str,
        content: str,
        item_id: str | None = None,
        parent_item_id: str | None = None,
    ) -> dict[str, Any]:
        """Append durable conversation truth and its bounded observer event atomically."""

        if not scope.turn_id:
            raise ValueError("transcript scope requires turn_id")
        if role not in _KNOWN_TRANSCRIPT_ROLES:
            raise ValueError("transcript role is outside the closed set")
        if not isinstance(content, str):
            raise ValueError("transcript content must be text")
        clean_content, redacted, truncated = bound_text(
            content, self.max_payload_bytes
        )
        message_id = item_id or str(uuid4())
        if not message_id.strip():
            raise ValueError("transcript item_id must be non-empty")
        if message_id.startswith(RESPONSE_REVIEW_PREFIX):
            raise ValueError("response review transcript items require model-round authority")
        content_digest = digest_json(clean_content)
        with self._lock:
            with self._transaction():
                return self._append_transcript_locked(
                    scope,
                    role=role,
                    clean_content=clean_content,
                    redacted=redacted,
                    truncated=truncated,
                    message_id=message_id,
                    parent_item_id=parent_item_id,
                    content_digest=content_digest,
                )

    def transcript_rows(self, scope: HarnessScope) -> tuple[dict[str, Any], ...]:
        with self._lock:
            self._require_open()
            session = self._assert_session_scope(scope)
            self._validate_database_locked()
            rows = self._db.execute(
                "SELECT * FROM transcript_messages WHERE session_id = ? ORDER BY transcript_seq ASC",
                (scope.session_id,),
            ).fetchall()
            result = []
            for row in rows:
                if scope.turn_id and row["turn_id"] != scope.turn_id:
                    continue
                self._assert_turn_row_scope(row, scope)
                result.append(self._transcript_from_row(row))
            return tuple(result)

    def search_transcript(
        self, scope: HarnessScope, query: str, *, after_seq: int = 0,
        limit: int = 6, exclude_turn_id: str = "",
    ) -> tuple[dict[str, Any], ...]:
        """Read saved user/assistant records, not the lossy observer ring.

        Literal, case-sensitive substring search. The caller authorizes each
        source session; this method never performs a global/cross-scope search.
        Current-turn exclusion prevents recall results from searching themselves.
        """
        if not isinstance(query, str) or len(query) > 256:
            raise ValueError("history query must be text of at most 256 characters")
        if type(after_seq) is not int or not 0 <= after_seq < 2**63:
            raise ValueError("history after_seq must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 11:
            raise ValueError("history limit must be between 1 and 11")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope)
            self._validate_database_locked()
            rows = self._db.execute(
                "SELECT * FROM transcript_messages WHERE session_id = ? "
                "AND role IN ('user', 'assistant') AND transcript_seq > ? "
                "AND turn_id != ? AND instr(content, ?) > 0 "
                "AND NOT EXISTS (SELECT 1 FROM model_rounds r WHERE r.session_id = transcript_messages.session_id "
                "AND r.assistant_item_id = transcript_messages.item_id AND r.tool_call_count > 0) "
                "ORDER BY transcript_seq LIMIT ?",
                (scope.session_id, after_seq, exclude_turn_id, query, limit),
            ).fetchall()
            result = []
            for row in rows:
                self._assert_turn_row_scope(row, scope)
                result.append({**self._transcript_from_row(row),
                               "content_digest": row["content_digest"],
                               "created_at": row["created_at"]})
            return tuple(result)

    def read_transcript_message(
        self, scope: HarnessScope, message_id: str, *, exclude_turn_id: str = "",
    ) -> dict[str, Any] | None:
        """Return one saved conversation record without changing its history."""
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 256:
            raise ValueError("history message_id must be nonempty bounded text")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope)
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT * FROM transcript_messages WHERE session_id = ? "
                "AND message_id = ? AND role IN ('user', 'assistant') AND turn_id != ? "
                "AND NOT EXISTS (SELECT 1 FROM model_rounds r WHERE r.session_id = transcript_messages.session_id "
                "AND r.assistant_item_id = transcript_messages.item_id AND r.tool_call_count > 0)",
                (scope.session_id, message_id, exclude_turn_id),
            ).fetchone()
            if row is None:
                return None
            self._assert_turn_row_scope(row, scope)
            return {**self._transcript_from_row(row),
                    "content_digest": row["content_digest"], "created_at": row["created_at"]}

    def append_event(self, draft: EventDraft) -> HarnessEvent:
        with self._lock:
            with self._transaction():
                return self._append_event_locked(draft)

    def replay(
        self,
        scope: HarnessScope,
        *,
        after_seq: int = 0,
        limit: int = 256,
    ) -> ReplayPage:
        if after_seq < 0:
            raise ValueError("after_seq must be non-negative")
        if not 1 <= limit <= self.max_events_per_session:
            raise ValueError("limit is outside the bounded replay range")
        with self._lock:
            self._require_open()
            session = self._assert_session_scope(scope)
            # Replay is a public read boundary, not merely an event-table
            # projection.  Re-run the complete authority check so a caller
            # cannot observe an apparently valid stream while an action,
            # approval, operation, or terminal ledger has been forged after
            # this connection was opened.
            self._validate_database_locked()
            session = self._session_row(scope.session_id)
            if after_seq > int(session["cursor"]):
                raise InvalidReplayCursor(
                    f"replay cursor {after_seq} is ahead of session cursor {session['cursor']}"
                )
            rows = self._db.execute(
                "SELECT * FROM events WHERE session_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
                (scope.session_id, after_seq, limit),
            ).fetchall()
            events = tuple(event_from_row(row) for row in rows)
            next_seq = events[-1].seq if events else after_seq
            retained_from = int(session["retained_from"])
            gap = after_seq < retained_from - 1
            truncated = any(event.truncated or event.gap for event in events)
            return ReplayPage(
                events=events,
                next_seq=next_seq,
                retained_from=retained_from,
                gap=gap,
                truncated=truncated,
            )

    def transcript_events(self, scope: HarnessScope) -> tuple[HarnessEvent, ...]:
        page = self.replay(scope, after_seq=0, limit=self.max_events_per_session)
        return tuple(
            event for event in page.events if event.event_type == "transcript.message"
        )

    def has_event(
        self,
        scope: HarnessScope,
        event_type: str,
        *,
        turn_id: str | None = None,
        action_request_id: str | None = None,
    ) -> bool:
        with self._lock:
            self._assert_session_scope(scope)
            self._validate_database_locked()
            clauses = ["session_id = ?", "event_type = ?"]
            params: list[object] = [scope.session_id, event_type]
            if turn_id is not None:
                clauses.append("turn_id = ?")
                params.append(turn_id)
            if action_request_id is not None:
                clauses.append("action_request_id = ?")
                params.append(action_request_id)
            row = self._db.execute(
                "SELECT 1 FROM events WHERE " + " AND ".join(clauses) + " LIMIT 1",
                tuple(params),
            ).fetchone()
            return row is not None

    def _action_row(self, action_request_id: str) -> sqlite3.Row:
        row = self._db.execute(
            "SELECT * FROM actions WHERE action_request_id = ?", (action_request_id,)
        ).fetchone()
        if row is None:
            raise ScopeConflict(f"unknown action: {action_request_id}")
        return row

    @staticmethod
    def _assert_action_scope(row: sqlite3.Row, scope: HarnessScope) -> None:
        if row["session_id"] != scope.session_id or row["turn_id"] != scope.turn_id:
            raise ScopeConflict("action id belongs to another turn or session")
        if row["world_id"] != scope.world_id or row["engram_id"] != scope.engram_id:
            raise ScopeConflict("action id belongs to another subject scope")
        if row["context_id"] != scope.context_id:
            raise ScopeConflict("action id belongs to another context")
        if int(row["epoch"]) != scope.epoch or row["owner_id"] != scope.owner_id:
            raise LeaseFenced("action is fenced by epoch or owner")
        if row["capability_digest"] != scope.capability_digest:
            raise ScopeConflict("action capability scope differs")

    def _action_authority_row(self, action_request_id: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM action_authorities WHERE action_request_id = ?",
            (action_request_id,),
        ).fetchone()

    def _action_classification_row(
        self, action_request_id: str
    ) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM action_classifications WHERE action_request_id = ?",
            (action_request_id,),
        ).fetchone()

    @staticmethod
    def _assert_action_authority_scope(
        row: sqlite3.Row, scope: HarnessScope
    ) -> None:
        if row["session_id"] != scope.session_id:
            raise ScopeConflict("action authority belongs to another session")
        if not HarnessStore._row_scope_matches(row, scope):
            raise ScopeConflict("action authority scope differs")

    @staticmethod
    def _action_authority_from_row(row: sqlite3.Row) -> dict[str, Any]:
        authority = decode_payload(row["authority_json"])
        clean, digest = _normalise_action_authority(authority, 1024 * 1024)
        if clean != authority or row["authority_digest"] != digest:
            raise StoreUnavailable("action authority record is corrupt")
        return clean

    def _insert_action_authority_locked(
        self,
        scope: HarnessScope,
        action_request_id: str,
        authority: Mapping[str, Any],
        *,
        created_at: str,
    ) -> None:
        clean, authority_digest = _normalise_action_authority(
            authority, self.max_payload_bytes
        )
        self._db.execute(
            """INSERT INTO action_authorities
            (action_request_id, session_id, scope_digest, world_id, engram_id,
            context_id, turn_id, epoch, owner_id, capability_digest, authority_required,
             provider_type, provider_source, descriptor_digest, authority_json,
             authority_digest, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_request_id,
                scope.session_id,
                scope.digest,
                scope.world_id,
                scope.engram_id,
                scope.context_id,
                scope.turn_id,
                scope.epoch,
                scope.owner_id,
                scope.capability_digest,
                int(clean["required"]),
                clean["provider_type"],
                clean["provider_source"],
                clean["descriptor_digest"],
                encode_payload(clean),
                authority_digest,
                created_at,
                created_at,
            ),
        )

    def _insert_action_classification_locked(
        self,
        scope: HarnessScope,
        action_request_id: str,
        *,
        tool_name: str,
        classification: ActionClassification,
        authority: Mapping[str, Any] | None,
        created_at: str,
    ) -> None:
        if classification is ActionClassification.MUTABLE_REQUIRED:
            if authority is None or authority.get("required") is not True:
                raise CapabilityDenied(
                    "mutable action requires one durable authority",
                    code="action_authority_required",
                )
            tool_mutable: int | None = 1
            authority_required = 1
        elif classification is ActionClassification.READ_ONLY_NOT_REQUIRED:
            if authority is not None:
                raise CapabilityDenied(
                    "read-only action cannot carry provider authority",
                    code="action_authority_not_required",
                )
            tool_mutable = 0
            authority_required = 0
        else:
            raise CapabilityDenied(
                "legacy action classification is migration-only",
                code="action_classification_legacy",
            )
        self._db.execute(
            """INSERT INTO action_classifications
            (action_request_id, session_id, scope_digest, world_id, engram_id,
             context_id, turn_id, epoch, owner_id, capability_digest, tool_name,
             tool_mutable, classification, classification_origin,
             authority_required, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                action_request_id,
                scope.session_id,
                scope.digest,
                scope.world_id,
                scope.engram_id,
                scope.context_id,
                scope.turn_id,
                scope.epoch,
                scope.owner_id,
                scope.capability_digest,
                tool_name,
                tool_mutable,
                classification.value,
                ActionClassificationOrigin.CREATED_V5.value,
                authority_required,
                created_at,
                created_at,
            ),
        )

    def get_action_authority(
        self, scope: HarnessScope, action_request_id: str
    ) -> Mapping[str, Any] | None:
        """Read the durable action/provider authority without issuing a capability."""

        with self._lock:
            self._require_open()
            action = self._action_row(action_request_id)
            self._assert_action_scope(action, scope)
            self._validate_database_locked()
            row = self._action_authority_row(action_request_id)
            if row is None:
                return None
            self._assert_action_authority_scope(row, scope)
            return self._action_authority_from_row(row)

    def validate_action_authority(
        self,
        scope: HarnessScope,
        action_request_id: str,
        authority: Mapping[str, Any],
    ) -> None:
        """Require an exact current provider authority for a mutable action."""

        clean, authority_digest = _normalise_action_authority(
            authority, self.max_payload_bytes
        )
        with self._lock:
            self._require_open()
            action = self._action_row(action_request_id)
            self._assert_action_scope(action, scope)
            self._validate_action_classification_rows_locked()
            classification = self._action_classification_row(action_request_id)
            if classification is None:
                raise CapabilityDenied(
                    "mutable action classification is missing",
                    code="action_classification_missing",
                )
            try:
                action_classification = ActionClassification(
                    classification["classification"]
                )
            except ValueError as exc:
                raise CapabilityDenied(
                    "mutable action classification is invalid",
                    code="action_classification_invalid",
                ) from exc
            if action_classification is not ActionClassification.MUTABLE_REQUIRED:
                raise CapabilityDenied(
                    "action is not classified as mutable",
                    code="action_authority_not_required",
                )
            row = self._action_authority_row(action_request_id)
            if row is None or int(row["authority_required"]) != 1:
                raise CapabilityDenied(
                    "mutable action has no durable authority",
                    code="action_authority_missing",
                )
            self._assert_action_authority_scope(row, scope)
            if (
                row["authority_digest"] != authority_digest
                or row["authority_json"] != encode_payload(clean)
                or row["provider_type"] != clean["provider_type"]
                or row["provider_source"] != clean["provider_source"]
                or row["descriptor_digest"] != clean["descriptor_digest"]
            ):
                raise CapabilityDenied(
                    "current provider authority differs from the action authority",
                    code="action_authority_mismatch",
                )

    def get_action_classification(
        self, scope: HarnessScope, action_request_id: str
    ) -> ActionClassification:
        """Read the durable closed action classification."""

        with self._lock:
            self._require_open()
            action = self._action_row(action_request_id)
            self._assert_action_scope(action, scope)
            self._validate_database_locked()
            row = self._action_classification_row(action_request_id)
            if row is None:
                raise StoreUnavailable(
                    "action classification is missing",
                    code="action_classification_missing",
                )
            self._assert_action_scope(row, scope)
            try:
                return ActionClassification(row["classification"])
            except ValueError as exc:
                raise StoreUnavailable(
                    "action classification is outside the closed set"
                ) from exc

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> ActionRecord:
        return ActionRecord(
            scope=_scope_from_row(row),
            action_request_id=row["action_request_id"],
            tool_name=row["tool_name"],
            arguments=decode_payload(row["arguments_json"]),
            args_digest=row["args_digest"],
            status=ActionStatus(row["status"]),
            revision=int(row["revision"]),
            approval_id=row["approval_id"],
            operation_id=row["operation_id"],
            result=(
                decode_payload(row["result_json"])
                if row["result_json"] is not None
                else None
            ),
            error_code=row["error_code"],
            result_delivered=bool(row["result_delivered"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            terminal_at=row["terminal_at"],
        )

    def create_action(
        self,
        scope: HarnessScope,
        *,
        action_request_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        requires_approval: bool,
        classification: ActionClassification | str,
        authority: Mapping[str, Any] | None = None,
    ) -> ActionRecord:
        if not scope.turn_id:
            raise ValueError("action scope requires turn_id")
        if not action_request_id.strip() or not tool_name.strip():
            raise ValueError("action_request_id and tool_name must be non-empty")
        try:
            action_classification = ActionClassification(classification)
        except (TypeError, ValueError) as exc:
            raise CapabilityDenied(
                "action classification is outside the closed set",
                code="action_classification_invalid",
            ) from exc
        if action_classification is ActionClassification.LEGACY_V3_UNATTRIBUTED:
            raise CapabilityDenied(
                "legacy action classification is migration-only",
                code="action_classification_legacy",
            )
        args, args_redacted, args_truncated = bound_payload(
            dict(arguments), self.max_payload_bytes
        )
        args_digest = digest_json(args)
        clean_authority: dict[str, Any] | None = None
        authority_digest: str | None = None
        if authority is not None:
            clean_authority, authority_digest = _normalise_action_authority(
                authority, self.max_payload_bytes
            )
        if action_classification is ActionClassification.MUTABLE_REQUIRED:
            if clean_authority is None or clean_authority.get("required") is not True:
                raise CapabilityDenied(
                    "mutable action requires one durable authority",
                    code="action_authority_required",
                )
        elif clean_authority is not None:
            raise CapabilityDenied(
                "read-only action cannot carry provider authority",
                code="action_authority_not_required",
            )
        with self._lock:
            with self._transaction():
                self._assert_turn_scope(scope)
                existing = self._db.execute(
                    "SELECT * FROM actions WHERE action_request_id = ?",
                    (action_request_id,),
                ).fetchone()
                if existing is not None:
                    self._assert_action_scope(existing, scope)
                    if (
                        existing["tool_name"] != tool_name
                        or existing["args_digest"] != args_digest
                        or (existing["approval_id"] is not None) != requires_approval
                    ):
                        raise IdempotencyConflict("action request payload differs")
                    existing_classification = self._action_classification_row(
                        action_request_id
                    )
                    if existing_classification is None:
                        raise StoreUnavailable(
                            "action classification is missing",
                            code="action_classification_missing",
                        )
                    if existing_classification["classification"] != action_classification.value:
                        raise IdempotencyConflict("action classification differs")
                    existing_authority = self._action_authority_row(action_request_id)
                    if action_classification is ActionClassification.MUTABLE_REQUIRED and (
                        existing_authority is None
                        or existing_authority["authority_digest"] != authority_digest
                        or existing_authority["authority_json"]
                        != encode_payload(clean_authority)
                    ):
                        raise IdempotencyConflict(
                            "action request authority differs"
                        )
                    if action_classification is ActionClassification.READ_ONLY_NOT_REQUIRED and (
                        existing_authority is not None
                    ):
                        raise IdempotencyConflict(
                            "read-only action unexpectedly has provider authority"
                        )
                    return self._action_from_row(existing)
                now = utc_now()
                approval_id = str(uuid4()) if requires_approval else None
                self._db.execute(
                    """INSERT INTO actions
                    (action_request_id, session_id, world_id, engram_id, context_id,
                    turn_id, epoch, owner_id, capability_digest, tool_name,
                     arguments_json, args_digest, args_redacted, args_truncated,
                     status, revision, approval_id,
                     operation_id, result_json, result_digest, error_code,
                     result_delivered,
                     created_at, updated_at, terminal_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?,
                            NULL, NULL, '', NULL, 0, ?, ?, NULL)""",
                    (
                        action_request_id,
                        scope.session_id,
                        scope.world_id,
                        scope.engram_id,
                        scope.context_id,
                        scope.turn_id,
                        scope.epoch,
                        scope.owner_id,
                        scope.capability_digest,
                        tool_name,
                        encode_payload(args),
                        args_digest,
                        int(args_redacted),
                        int(args_truncated),
                        ActionStatus.INTENT.value,
                        approval_id,
                        now,
                        now,
                    ),
                )
                self._insert_action_classification_locked(
                    scope,
                    action_request_id,
                    tool_name=tool_name,
                    classification=action_classification,
                    authority=clean_authority,
                    created_at=now,
                )
                if clean_authority is not None:
                    self._insert_action_authority_locked(
                        scope,
                        action_request_id,
                        clean_authority,
                        created_at=now,
                    )
                draft = EventDraft(
                    scope=scope,
                    event_type="action.requested",
                    source="harness",
                    phase="intent",
                    status=ActionStatus.INTENT.value,
                    payload={
                        "tool_name": tool_name,
                        "arguments": args,
                        "requires_approval": requires_approval,
                    },
                    action_request_id=action_request_id,
                    approval_id=approval_id,
                )
                self._append_event_locked(draft)
                if requires_approval:
                    expires = (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=self.approval_ttl_sec)
                    ).isoformat(timespec="milliseconds")
                    self._db.execute(
                        """INSERT INTO approvals
                        (approval_id, action_request_id, session_id, scope_digest,
                         status, decision, expires_at, revision, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, NULL, ?, 0, ?, ?)""",
                        (
                            approval_id,
                            action_request_id,
                            scope.session_id,
                            scope.digest,
                            ApprovalStatus.PENDING.value,
                            expires,
                            now,
                            now,
                        ),
                    )
                    self._db.execute(
                        "UPDATE actions SET status = ?, revision = 1, updated_at = ? WHERE action_request_id = ?",
                        (ActionStatus.APPROVAL_PENDING.value, now, action_request_id),
                    )
                    self._append_event_locked(
                        EventDraft(
                            scope=scope,
                            event_type="approval.pending",
                            source="harness",
                            phase="approval",
                            status=ApprovalStatus.PENDING.value,
                            payload={"expires_at": expires},
                            action_request_id=action_request_id,
                            approval_id=approval_id,
                        )
                    )
                return self._action_from_row(self._action_row(action_request_id))

    def get_action(self, scope: HarnessScope, action_request_id: str) -> ActionRecord:
        with self._lock:
            self._require_open()
            row = self._action_row(action_request_id)
            self._assert_action_scope(row, scope)
            self._validate_database_locked()
            row = self._action_row(action_request_id)
            self._assert_action_scope(row, scope)
            return self._action_from_row(row)

    def action_scope_for_session(
        self,
        session_scope: HarnessScope,
        action_request_id: str,
    ) -> HarnessScope:
        """Resolve an action from durable authority, never from observer events.

        Event rows are intentionally bounded and may be retired.  Approval and
        control requests therefore use the indexed ``actions`` identity table
        as their source of truth.  The returned scope is the exact persisted
        action scope, including its turn, owner and epoch fence.
        """

        with self._lock:
            self._require_open()
            self._assert_session_scope(session_scope.session_scope())
            row = self._action_row(action_request_id)
            if row["session_id"] != session_scope.session_id:
                raise ScopeConflict("action is not present in this durable session")
            scope = _scope_from_row(row)
            self._validate_database_locked()
            row = self._action_row(action_request_id)
            if row["session_id"] != session_scope.session_id:
                raise ScopeConflict("action is not present in this durable session")
            return _scope_from_row(row)

    def _set_action_status_locked(
        self,
        row: sqlite3.Row,
        status: ActionStatus,
        *,
        result: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        terminal_at: str | None = None,
    ) -> sqlite3.Row:
        revision = int(row["revision"]) + 1
        now = utc_now()
        clean_result, result_redacted, result_truncated = (
            bound_payload(dict(result), self.max_payload_bytes)
            if result is not None
            else (None, False, False)
        )
        self._db.execute(
            """UPDATE actions SET status = ?, revision = ?, result_json = ?,
            result_digest = ?, error_code = ?, result_redacted = ?,
            result_truncated = ?,
            updated_at = ?, terminal_at = ? WHERE action_request_id = ?""",
            (
                status.value,
                revision,
                encode_payload(clean_result) if clean_result is not None else None,
                digest_json(clean_result) if clean_result is not None else "",
                error_code,
                int(result_redacted),
                int(result_truncated),
                now,
                terminal_at,
                row["action_request_id"],
            ),
        )
        return self._action_row(row["action_request_id"])

    def claim_action_start(
        self,
        scope: HarnessScope,
        action_request_id: str,
        *,
        authority: Mapping[str, Any] | None = None,
    ) -> ActionRecord:
        """Atomically transition an approved/read action into STARTING."""

        with self._lock:
            with self._transaction():
                row = self._action_row(action_request_id)
                self._assert_action_scope(row, scope)
                status = ActionStatus(row["status"])
                self._validate_action_classification_rows_locked()
                classification_row = self._action_classification_row(action_request_id)
                if classification_row is None:
                    raise StoreUnavailable(
                        "action classification is missing",
                        code="action_classification_missing",
                    )
                try:
                    classification = ActionClassification(
                        classification_row["classification"]
                    )
                except ValueError as exc:
                    raise StoreUnavailable(
                        "action classification is outside the closed set",
                        code="action_classification_invalid",
                    ) from exc
                authority_row = self._action_authority_row(action_request_id)
                if (
                    classification is ActionClassification.LEGACY_V3_UNATTRIBUTED
                    and not status.terminal
                ):
                    raise StoreUnavailable(
                        "legacy action authority is unattributed",
                        code="legacy_action_authority_unattributed",
                    )
                if classification is ActionClassification.MUTABLE_REQUIRED:
                    if authority_row is None or int(
                        authority_row["authority_required"]
                    ) != 1:
                        raise CapabilityDenied(
                            "mutable action has no durable authority",
                            code="action_authority_missing",
                        )
                    if authority is None:
                        raise CapabilityDenied(
                            "mutable action requires its current provider authority",
                            code="action_authority_required",
                        )
                    clean_authority, authority_digest = _normalise_action_authority(
                        authority, self.max_payload_bytes
                    )
                    self._assert_action_authority_scope(authority_row, scope)
                    if (
                        authority_row["authority_digest"] != authority_digest
                        or authority_row["authority_json"]
                        != encode_payload(clean_authority)
                    ):
                        raise CapabilityDenied(
                            "current provider authority differs from the action authority",
                            code="action_authority_mismatch",
                        )
                elif authority_row is not None:
                    raise StoreUnavailable(
                        "non-mutable action has an unexpected provider authority",
                        code="action_authority_unexpected",
                    )
                if status.terminal or status in {ActionStatus.STARTING, ActionStatus.RUNNING}:
                    return self._action_from_row(row)
                if status is ActionStatus.APPROVAL_PENDING:
                    approval = self._db.execute(
                        "SELECT * FROM approvals WHERE action_request_id = ?",
                        (action_request_id,),
                    ).fetchone()
                    if approval is None:
                        raise StoreUnavailable("approval record is missing")
                    if approval["status"] != ApprovalStatus.ALLOWED_ONCE.value:
                        return self._action_from_row(row)
                    if _parse_time(approval["expires_at"]) <= datetime.now(timezone.utc):
                        raise ApprovalConflict("approval has expired")
                elif row["approval_id"] is not None:
                    raise InvalidTransition(
                        "approved action must remain approval_pending until allow_once"
                    )
                if status not in {ActionStatus.INTENT, ActionStatus.APPROVAL_PENDING}:
                    raise InvalidTransition(f"cannot start action in {status.value}")
                updated = self._set_action_status_locked(row, ActionStatus.STARTING)
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="action.starting",
                        source="harness",
                        phase="start",
                        status=ActionStatus.STARTING.value,
                        payload={"tool_name": row["tool_name"]},
                        action_request_id=action_request_id,
                        approval_id=row["approval_id"],
                        operation_id=row["operation_id"],
                    )
                )
                return self._action_from_row(updated)

    def _record_action_terminal_locked(
        self,
        scope: HarnessScope,
        row: sqlite3.Row,
        status: ActionStatus,
        *,
        result: Mapping[str, Any],
        error_code: str | None,
        terminal_at: str,
    ) -> None:
        clean_result, _, _ = bound_payload(dict(result), self.max_payload_bytes)
        existing = self._db.execute(
            "SELECT * FROM action_terminals WHERE action_request_id = ?",
            (row["action_request_id"],),
        ).fetchone()
        if existing is not None:
            if (
                existing["scope_digest"] != scope.digest
                or existing["status"] != status.value
                or existing["result_json"] != encode_payload(clean_result)
                or existing["error_code"] != error_code
            ):
                raise IdempotencyConflict("action terminal differs on retry")
            return
        self._db.execute(
            """INSERT INTO action_terminals
            (action_request_id, session_id, scope_digest, status, result_json,
             error_code, terminal_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                row["action_request_id"],
                scope.session_id,
                scope.digest,
                status.value,
                encode_payload(clean_result),
                error_code,
                terminal_at,
            ),
        )

    def _record_operation_terminal_locked(
        self,
        scope: HarnessScope,
        row: sqlite3.Row,
        status: OperationStatus,
        *,
        result: Mapping[str, Any],
        error_code: str | None,
        terminal_at: str,
    ) -> None:
        clean_result, _, _ = bound_payload(dict(result), self.max_payload_bytes)
        existing = self._db.execute(
            "SELECT * FROM operation_terminals WHERE operation_id = ?",
            (row["operation_id"],),
        ).fetchone()
        if existing is not None:
            if (
                existing["scope_digest"] != scope.digest
                or existing["status"] != status.value
                or existing["result_json"] != encode_payload(clean_result)
                or existing["error_code"] != error_code
            ):
                raise IdempotencyConflict("operation terminal differs on retry")
            return
        self._db.execute(
            """INSERT INTO operation_terminals
            (operation_id, action_request_id, session_id, scope_digest, status,
             result_json, error_code, terminal_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["operation_id"],
                row["action_request_id"],
                scope.session_id,
                scope.digest,
                status.value,
                encode_payload(clean_result),
                error_code,
                terminal_at,
            ),
        )

    def _close_operation_for_action_locked(
        self,
        scope: HarnessScope,
        action: sqlite3.Row,
        outcome: TerminalOutcome,
        *,
        result: Mapping[str, Any],
        error_code: str | None,
    ) -> None:
        if action["operation_id"] is None:
            return
        operation = self._db.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (action["operation_id"],)
        ).fetchone()
        if operation is None:
            raise StoreUnavailable("action operation record is missing")
        current = OperationStatus(operation["status"])
        if current in {
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.UNCERTAIN,
        }:
            return
        status = OperationStatus(outcome.value)
        now = utc_now()
        clean_result, result_redacted, result_truncated = bound_payload(
            dict(result), self.max_payload_bytes
        )
        self._db.execute(
            """UPDATE operations SET status = ?, revision = revision + 1,
            result_json = ?, result_digest = ?, error_code = ?, result_redacted = ?,
            result_truncated = ?, updated_at = ? WHERE operation_id = ?""",
            (
                status.value,
                encode_payload(clean_result),
                digest_json(clean_result),
                error_code,
                int(result_redacted),
                int(result_truncated),
                now,
                operation["operation_id"],
            ),
        )
        updated = self._db.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation["operation_id"],)
        ).fetchone()
        self._record_operation_terminal_locked(
            scope,
            updated,
            status,
            result=clean_result,
            error_code=error_code,
            terminal_at=now,
        )
        self._append_event_locked(
            EventDraft(
                scope=scope,
                event_type="operation.terminal",
                source="harness",
                phase="operation",
                status=status.value,
                payload={"result": clean_result, "error_code": error_code},
                action_request_id=action["action_request_id"],
                approval_id=action["approval_id"],
                operation_id=operation["operation_id"],
            )
        )

    def mark_action_running(self, scope: HarnessScope, action_request_id: str) -> ActionRecord:
        with self._lock:
            with self._transaction():
                row = self._action_row(action_request_id)
                self._assert_action_scope(row, scope)
                status = ActionStatus(row["status"])
                if status.terminal:
                    return self._action_from_row(row)
                if status is ActionStatus.RUNNING:
                    return self._action_from_row(row)
                if status is not ActionStatus.STARTING:
                    raise InvalidTransition(f"cannot mark {status.value} as running")
                updated = self._set_action_status_locked(row, ActionStatus.RUNNING)
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="action.running",
                        source="harness",
                        phase="run",
                        status=ActionStatus.RUNNING.value,
                        payload={},
                        action_request_id=action_request_id,
                        approval_id=row["approval_id"],
                        operation_id=row["operation_id"],
                    )
                )
                return self._action_from_row(updated)

    def terminal_action(
        self,
        scope: HarnessScope,
        action_request_id: str,
        outcome: TerminalOutcome,
        *,
        result: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> ActionRecord:
        target = ActionStatus(outcome.value)
        with self._lock:
            with self._transaction():
                row = self._action_row(action_request_id)
                self._assert_action_scope(row, scope)
                current = ActionStatus(row["status"])
                if current.terminal:
                    return self._action_from_row(row)
                if current is ActionStatus.TERMINALIZING:
                    # Only this transaction can enter terminalizing, so a retry
                    # can safely complete the same owner transition.
                    pass
                elif current not in {
                    ActionStatus.INTENT,
                    ActionStatus.APPROVAL_PENDING,
                    ActionStatus.STARTING,
                    ActionStatus.RUNNING,
                }:
                    raise InvalidTransition(f"cannot terminalize {current.value}")
                if current is ActionStatus.APPROVAL_PENDING:
                    if target is not ActionStatus.CANCELLED:
                        raise InvalidTransition(
                            "pending approval must be cancelled before terminalization"
                        )
                    approval = self._db.execute(
                        "SELECT * FROM approvals WHERE action_request_id = ?",
                        (action_request_id,),
                    ).fetchone()
                    if approval is None or approval["status"] != ApprovalStatus.PENDING.value:
                        raise InvalidTransition("pending action approval is not cancellable")
                    now = utc_now()
                    self._db.execute(
                        """UPDATE approvals SET status = ?, decision = ?,
                           revision = revision + 1, updated_at = ?
                           WHERE approval_id = ?""",
                        (
                            ApprovalStatus.CANCELLED.value,
                            ApprovalDecision.CANCEL.value,
                            now,
                            approval["approval_id"],
                        ),
                    )
                    self._append_event_locked(
                        EventDraft(
                            scope=scope,
                            event_type="approval.resolved",
                            source="control",
                            phase="approval",
                            status=ApprovalStatus.CANCELLED.value,
                            payload={"decision": ApprovalDecision.CANCEL.value},
                            action_request_id=action_request_id,
                            approval_id=approval["approval_id"],
                            operation_id=row["operation_id"],
                        )
                    )
                self._set_action_status_locked(row, ActionStatus.TERMINALIZING)
                terminal_at = utc_now()
                terminal_result = dict(result) if result is not None else {}
                updated = self._set_action_status_locked(
                    self._action_row(action_request_id),
                    target,
                    result=terminal_result,
                    error_code=error_code,
                    terminal_at=terminal_at,
                )
                self._record_action_terminal_locked(
                    scope,
                    updated,
                    target,
                    result=terminal_result,
                    error_code=error_code,
                    terminal_at=terminal_at,
                )
                self._close_operation_for_action_locked(
                    scope,
                    updated,
                    outcome,
                    result=terminal_result,
                    error_code=error_code,
                )
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="action.terminal",
                        source="harness",
                        phase="terminal",
                        status=target.value,
                        payload={
                            "result": terminal_result,
                            "error_code": error_code,
                        },
                        action_request_id=action_request_id,
                        approval_id=updated["approval_id"],
                        operation_id=updated["operation_id"],
                    )
                )
                return self._action_from_row(updated)

    def deliver_action_result(self, scope: HarnessScope, action_request_id: str) -> bool:
        """Mark a result as delivered exactly once to the model transcript."""

        with self._lock:
            with self._transaction():
                row = self._action_row(action_request_id)
                self._assert_action_scope(row, scope)
                if not ActionStatus(row["status"]).terminal:
                    raise InvalidTransition("cannot deliver a non-terminal action")
                if bool(row["result_delivered"]):
                    return False
                self._db.execute(
                    "UPDATE actions SET result_delivered = 1, revision = revision + 1, updated_at = ? WHERE action_request_id = ?",
                    (utc_now(), action_request_id),
                )
                return True

    def _approval_from_row(self, row: sqlite3.Row) -> ApprovalRecord:
        decision = row["decision"]
        return ApprovalRecord(
            approval_id=row["approval_id"],
            action_request_id=row["action_request_id"],
            scope=_scope_from_row(
                self._action_row(row["action_request_id"])
            ),
            status=ApprovalStatus(row["status"]),
            decision=ApprovalDecision(decision) if decision is not None else None,
            expires_at=row["expires_at"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_approval_for_action(
        self,
        scope: HarnessScope,
        action_request_id: str,
    ) -> ApprovalRecord | None:
        """Read an approval through the durable action relationship.

        This is deliberately independent of retained ``approval.*`` event
        rows.  An approval remains observable after event retention because its
        action/approval foreign-key relationship is durable and scope checked.
        """

        with self._lock:
            self._require_open()
            action = self._action_row(action_request_id)
            self._assert_action_scope(action, scope)
            self._validate_database_locked()
            action = self._action_row(action_request_id)
            self._assert_action_scope(action, scope)
            if action["approval_id"] is None:
                return None
            row = self._db.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (action["approval_id"],),
            ).fetchone()
            if row is None:
                raise StoreUnavailable("action approval record is missing")
            if (
                row["action_request_id"] != action_request_id
                or row["session_id"] != scope.session_id
                or row["scope_digest"] != scope.digest
            ):
                raise StoreUnavailable("action approval scope is corrupt")
            return self._approval_from_row(row)

    def resolve_approval(
        self,
        scope: HarnessScope,
        action_request_id: str,
        decision: ApprovalDecision,
    ) -> tuple[ApprovalRecord, ActionRecord]:
        if not isinstance(decision, ApprovalDecision):
            raise ValueError("v0.1 approval accepts only ApprovalDecision values")
        with self._lock:
            with self._transaction():
                action = self._action_row(action_request_id)
                self._assert_action_scope(action, scope)
                approval = self._db.execute(
                    "SELECT * FROM approvals WHERE action_request_id = ?",
                    (action_request_id,),
                ).fetchone()
                if approval is None:
                    raise ApprovalConflict("action does not have an approval")
                current_status = ApprovalStatus(approval["status"])
                if current_status is not ApprovalStatus.PENDING:
                    prior = approval["decision"]
                    if prior == decision.value:
                        return self._approval_from_row(approval), self._action_from_row(action)
                    raise ApprovalConflict("approval already resolved")
                if _parse_time(approval["expires_at"]) <= datetime.now(timezone.utc):
                    now = utc_now()
                    self._db.execute(
                        "UPDATE approvals SET status = ?, revision = revision + 1, updated_at = ? WHERE approval_id = ?",
                        (ApprovalStatus.EXPIRED.value, now, approval["approval_id"]),
                    )
                    action = self._action_row(action_request_id)
                    action = self._terminal_action_locked(
                        scope,
                        action,
                        TerminalOutcome.CANCELLED,
                        result={},
                        error_code="approval_expired",
                    )
                    approval = self._db.execute(
                        "SELECT * FROM approvals WHERE approval_id = ?",
                        (approval["approval_id"],),
                    ).fetchone()
                    return self._approval_from_row(approval), self._action_from_row(action)
                now = utc_now()
                status = {
                    ApprovalDecision.ALLOW_ONCE: ApprovalStatus.ALLOWED_ONCE,
                    ApprovalDecision.DENY: ApprovalStatus.DENIED,
                    ApprovalDecision.CANCEL: ApprovalStatus.CANCELLED,
                }[decision]
                self._db.execute(
                    "UPDATE approvals SET status = ?, decision = ?, revision = revision + 1, updated_at = ? WHERE approval_id = ?",
                    (status.value, decision.value, now, approval["approval_id"]),
                )
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="approval.resolved",
                        source="human",
                        phase="approval",
                        status=status.value,
                        payload={"decision": decision.value},
                        action_request_id=action_request_id,
                        approval_id=approval["approval_id"],
                        operation_id=action["operation_id"],
                    )
                )
                action = self._action_row(action_request_id)
                if decision is ApprovalDecision.ALLOW_ONCE:
                    action = self._set_action_status_locked(action, ActionStatus.STARTING)
                    self._append_event_locked(
                        EventDraft(
                            scope=scope,
                            event_type="action.starting",
                            source="harness",
                            phase="start",
                            status=ActionStatus.STARTING.value,
                            payload={"approved_once": True},
                            action_request_id=action_request_id,
                            approval_id=approval["approval_id"],
                            operation_id=action["operation_id"],
                        )
                    )
                else:
                    outcome = (
                        TerminalOutcome.FAILED
                        if decision is ApprovalDecision.DENY
                        else TerminalOutcome.CANCELLED
                    )
                    action = self._terminal_action_locked(
                        scope,
                        action,
                        outcome,
                        result={},
                        error_code=(
                            "approval_denied"
                            if decision is ApprovalDecision.DENY
                            else "approval_cancelled"
                        ),
                    )
                approval = self._db.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?",
                    (approval["approval_id"],),
                ).fetchone()
                return self._approval_from_row(approval), self._action_from_row(action)

    def _terminal_action_locked(
        self,
        scope: HarnessScope,
        row: sqlite3.Row,
        outcome: TerminalOutcome,
        *,
        result: Mapping[str, Any],
        error_code: str,
    ) -> sqlite3.Row:
        current = ActionStatus(row["status"])
        if current.terminal:
            return row
        self._set_action_status_locked(row, ActionStatus.TERMINALIZING)
        updated = self._set_action_status_locked(
            self._action_row(row["action_request_id"]),
            ActionStatus(outcome.value),
            result=result,
            error_code=error_code,
            terminal_at=utc_now(),
        )
        terminal_at = updated["terminal_at"] or utc_now()
        self._record_action_terminal_locked(
            scope,
            updated,
            ActionStatus(outcome.value),
            result=result,
            error_code=error_code,
            terminal_at=terminal_at,
        )
        self._close_operation_for_action_locked(
            scope,
            updated,
            outcome,
            result=result,
            error_code=error_code,
        )
        self._append_event_locked(
            EventDraft(
                scope=scope,
                event_type="action.terminal",
                source="harness",
                phase="terminal",
                status=outcome.value,
                payload={"result": dict(result), "error_code": error_code},
                action_request_id=row["action_request_id"],
                approval_id=row["approval_id"],
                operation_id=row["operation_id"],
            )
        )
        return updated

    def _operation_from_row(self, row: sqlite3.Row) -> OperationRecord:
        action = self._action_row(row["action_request_id"])
        return OperationRecord(
            operation_id=row["operation_id"],
            action_request_id=row["action_request_id"],
            scope=_scope_from_row(action),
            status=OperationStatus(row["status"]),
            revision=int(row["revision"]),
            result=(
                decode_payload(row["result_json"])
                if row["result_json"] is not None
                else None
            ),
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_operation(
        self,
        scope: HarnessScope,
        action_request_id: str,
        *,
        operation_id: str | None = None,
    ) -> OperationRecord:
        with self._lock:
            with self._transaction():
                action = self._action_row(action_request_id)
                self._assert_action_scope(action, scope)
                action_status = ActionStatus(action["status"])
                if action_status not in {ActionStatus.STARTING, ActionStatus.RUNNING}:
                    raise InvalidTransition(
                        "operation admission requires a starting or running action"
                    )
                if action["approval_id"] is not None:
                    approval = self._db.execute(
                        "SELECT status FROM approvals WHERE approval_id = ?",
                        (action["approval_id"],),
                    ).fetchone()
                    if approval is None:
                        raise StoreUnavailable("approved action is missing its approval")
                    if approval["status"] != ApprovalStatus.ALLOWED_ONCE.value:
                        raise InvalidTransition("operation admission requires allow_once")
                existing = self._db.execute(
                    "SELECT * FROM operations WHERE action_request_id = ?",
                    (action_request_id,),
                ).fetchone()
                if existing is not None:
                    if existing["scope_digest"] != scope.digest:
                        raise ScopeConflict("operation scope collision")
                    if operation_id is not None and existing["operation_id"] != operation_id:
                        raise IdempotencyConflict("operation id differs on retry")
                    return self._operation_from_row(existing)
                operation_id = operation_id or str(uuid4())
                by_id = self._db.execute(
                    "SELECT action_request_id, scope_digest FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if by_id is not None:
                    raise IdempotencyConflict("operation id is already used")
                now = utc_now()
                self._db.execute(
                    """INSERT INTO operations
                    (operation_id, action_request_id, session_id, scope_digest, status,
                     revision, result_json, error_code, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?)""",
                    (
                        operation_id,
                        action_request_id,
                        scope.session_id,
                        scope.digest,
                        OperationStatus.STARTING.value,
                        now,
                        now,
                    ),
                )
                self._db.execute(
                    "UPDATE actions SET operation_id = ?, revision = revision + 1, updated_at = ? WHERE action_request_id = ?",
                    (operation_id, now, action_request_id),
                )
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="operation.started",
                        source="harness",
                        phase="operation",
                        status=OperationStatus.STARTING.value,
                        payload={},
                        action_request_id=action_request_id,
                        approval_id=action["approval_id"],
                        operation_id=operation_id,
                    )
                )
                return self._operation_from_row(self._db.execute(
                    "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
                ).fetchone())

    def terminal_operation(
        self,
        scope: HarnessScope,
        operation_id: str,
        status: OperationStatus,
        *,
        result: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> OperationRecord:
        if status not in {
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.UNCERTAIN,
        }:
            raise ValueError("operation terminal status required")
        with self._lock:
            with self._transaction():
                row = self._db.execute(
                    "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
                ).fetchone()
                if row is None:
                    raise ScopeConflict("unknown operation")
                action = self._action_row(row["action_request_id"])
                self._assert_action_scope(action, scope)
                current = OperationStatus(row["status"])
                if current in {
                    OperationStatus.SUCCEEDED,
                    OperationStatus.FAILED,
                    OperationStatus.CANCELLED,
                    OperationStatus.UNCERTAIN,
                }:
                    return self._operation_from_row(row)
                now = utc_now()
                terminal_result = dict(result) if result is not None else {}
                clean_result, result_redacted, result_truncated = bound_payload(
                    terminal_result, self.max_payload_bytes
                )
                self._db.execute(
                    """UPDATE operations SET status = ?, revision = revision + 1,
                    result_json = ?, result_digest = ?, error_code = ?, result_redacted = ?,
                    result_truncated = ?, updated_at = ? WHERE operation_id = ?""",
                    (
                        status.value,
                        encode_payload(clean_result),
                        digest_json(clean_result),
                        error_code,
                        int(result_redacted),
                        int(result_truncated),
                        now,
                        operation_id,
                    ),
                )
                updated = self._db.execute(
                    "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
                ).fetchone()
                self._record_operation_terminal_locked(
                    scope,
                    updated,
                    status,
                    result=clean_result,
                    error_code=error_code,
                    terminal_at=now,
                )
                self._append_event_locked(
                    EventDraft(
                        scope=scope,
                        event_type="operation.terminal",
                        source="harness",
                        phase="operation",
                        status=status.value,
                        payload={"result": clean_result, "error_code": error_code},
                        action_request_id=row["action_request_id"],
                        approval_id=action["approval_id"],
                        operation_id=operation_id,
                    )
                )
                action = self._action_row(row["action_request_id"])
                if not ActionStatus(action["status"]).terminal:
                    action_error_code = error_code
                    if action_error_code is None and status is not OperationStatus.SUCCEEDED:
                        action_error_code = "operation_terminal"
                    self._terminal_action_locked(
                        scope,
                        action,
                        TerminalOutcome(status.value),
                        result=clean_result,
                        error_code=action_error_code,
                    )
                return self._operation_from_row(self._db.execute(
                    "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
                ).fetchone())

    def get_operation(self, scope: HarnessScope, operation_id: str) -> OperationRecord:
        with self._lock:
            self._assert_session_scope(scope)
            row = self._db.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise ScopeConflict("unknown operation")
            action = self._action_row(row["action_request_id"])
            self._assert_action_scope(action, scope)
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise StoreUnavailable("operation disappeared after validation")
            self._assert_action_scope(self._action_row(row["action_request_id"]), scope)
            return self._operation_from_row(row)

    def _assert_effect_chain_locked(
        self, scope: HarnessScope, action: sqlite3.Row
    ) -> sqlite3.Row:
        """Require approval and operation admission before persisting an effect."""

        self._assert_action_scope(action, scope)
        action_status = ActionStatus(action["status"])
        if action_status in {ActionStatus.INTENT, ActionStatus.APPROVAL_PENDING}:
            raise InvalidTransition("effect record cannot bypass action admission")
        if action["approval_id"] is not None:
            approval = self._db.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (action["approval_id"],)
            ).fetchone()
            if approval is None:
                raise StoreUnavailable("effect action approval is missing")
            if approval["status"] != ApprovalStatus.ALLOWED_ONCE.value:
                raise InvalidTransition("effect record requires allow_once approval")
        if action["operation_id"] is None:
            raise InvalidTransition("effect record requires a durable operation")
        operation = self._db.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (action["operation_id"],)
        ).fetchone()
        if operation is None or operation["action_request_id"] != action["action_request_id"]:
            raise StoreUnavailable("effect operation link is missing")
        if operation["scope_digest"] != scope.digest:
            raise ScopeConflict("effect operation scope differs")
        return operation

    @staticmethod
    def _change_from_row(row: sqlite3.Row, action: sqlite3.Row) -> ChangeRecord:
        return ChangeRecord(
            change_id=row["change_id"],
            action_request_id=row["action_request_id"],
            scope=_scope_from_row(action),
            path=row["path"],
            before_digest=row["before_digest"],
            after_digest=row["after_digest"],
            diff=row["diff"],
            status=row["status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _checkpoint_from_row(row: sqlite3.Row, action: sqlite3.Row) -> CheckpointRecord:
        return CheckpointRecord(
            checkpoint_id=row["checkpoint_id"],
            action_request_id=row["action_request_id"],
            scope=_scope_from_row(action),
            path=row["path"],
            artifact_path=row["artifact_path"],
            content_digest=row["content_digest"],
            status=row["status"],
            created_at=row["created_at"],
        )

    def record_change(self, change: ChangeRecord) -> ChangeRecord:
        clean_path, path_redacted, path_truncated = bound_text(
            change.path, self.max_payload_bytes
        )
        clean_diff, diff_redacted, diff_truncated = bound_text(
            change.diff, self.max_payload_bytes
        )
        record_digest = self._change_fingerprint(
            change_id=change.change_id,
            action_request_id=change.action_request_id,
            scope_digest=change.scope.digest,
            path=clean_path,
            before_digest=change.before_digest,
            after_digest=change.after_digest,
            diff=clean_diff,
            status=change.status,
        )
        with self._lock:
            with self._transaction():
                action = self._action_row(change.action_request_id)
                self._assert_effect_chain_locked(change.scope, action)
                existing = self._db.execute(
                    "SELECT * FROM changes WHERE change_id = ?", (change.change_id,)
                ).fetchone()
                if existing is not None:
                    existing_digest = existing["record_digest"] or self._change_fingerprint(
                        change_id=existing["change_id"],
                        action_request_id=existing["action_request_id"],
                        scope_digest=existing["scope_digest"],
                        path=existing["path"],
                        before_digest=existing["before_digest"],
                        after_digest=existing["after_digest"],
                        diff=existing["diff"],
                        status=existing["status"],
                    )
                    if existing_digest != record_digest:
                        raise IdempotencyConflict("change id differs on retry")
                    return self._change_from_row(existing, action)
                self._db.execute(
                    """INSERT INTO changes
                    (change_id, action_request_id, session_id, scope_digest, path,
                     path_redacted, path_truncated, before_digest, after_digest, diff,
                     diff_redacted, diff_truncated, record_digest, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        change.change_id,
                        change.action_request_id,
                        change.scope.session_id,
                        change.scope.digest,
                        clean_path,
                        int(path_redacted),
                        int(path_truncated),
                        change.before_digest,
                        change.after_digest,
                        clean_diff,
                        int(diff_redacted),
                        int(diff_truncated),
                        record_digest,
                        change.status,
                        change.created_at,
                    ),
                )
                self._append_event_locked(
                    EventDraft(
                        scope=change.scope,
                        event_type=f"change.{change.status}",
                        source="harness",
                        phase="change",
                        status=change.status,
                        payload={
                            "change_id": change.change_id,
                            "path": clean_path,
                            "before_digest": change.before_digest,
                            "after_digest": change.after_digest,
                            "diff": clean_diff,
                        },
                        action_request_id=change.action_request_id,
                        operation_id=action["operation_id"],
                    )
                )
                return self._change_from_row(
                    self._db.execute(
                        "SELECT * FROM changes WHERE change_id = ?", (change.change_id,)
                    ).fetchone(),
                    action,
                )

    def record_checkpoint(self, checkpoint: CheckpointRecord) -> CheckpointRecord:
        clean_path, path_redacted, path_truncated = bound_text(
            checkpoint.path, self.max_payload_bytes
        )
        clean_artifact, artifact_redacted, artifact_truncated = bound_text(
            checkpoint.artifact_path, self.max_payload_bytes
        )
        record_digest = self._checkpoint_fingerprint(
            checkpoint_id=checkpoint.checkpoint_id,
            action_request_id=checkpoint.action_request_id,
            scope_digest=checkpoint.scope.digest,
            path=clean_path,
            artifact_path=clean_artifact,
            content_digest=checkpoint.content_digest,
            status=checkpoint.status,
        )
        with self._lock:
            with self._transaction():
                action = self._action_row(checkpoint.action_request_id)
                self._assert_effect_chain_locked(checkpoint.scope, action)
                existing = self._db.execute(
                    "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                    (checkpoint.checkpoint_id,),
                ).fetchone()
                if existing is not None:
                    existing_digest = existing["record_digest"] or self._checkpoint_fingerprint(
                        checkpoint_id=existing["checkpoint_id"],
                        action_request_id=existing["action_request_id"],
                        scope_digest=existing["scope_digest"],
                        path=existing["path"],
                        artifact_path=existing["artifact_path"],
                        content_digest=existing["content_digest"],
                        status=existing["status"],
                    )
                    if existing_digest != record_digest:
                        raise IdempotencyConflict("checkpoint id differs on retry")
                    return self._checkpoint_from_row(existing, action)
                self._db.execute(
                    """INSERT INTO checkpoints
                    (checkpoint_id, action_request_id, session_id, scope_digest, path,
                     path_redacted, path_truncated, artifact_path, artifact_redacted,
                     artifact_truncated, content_digest, record_digest, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.action_request_id,
                        checkpoint.scope.session_id,
                        checkpoint.scope.digest,
                        clean_path,
                        int(path_redacted),
                        int(path_truncated),
                        clean_artifact,
                        int(artifact_redacted),
                        int(artifact_truncated),
                        checkpoint.content_digest,
                        record_digest,
                        checkpoint.status,
                        checkpoint.created_at,
                    ),
                )
                self._append_event_locked(
                    EventDraft(
                        scope=checkpoint.scope,
                        event_type="checkpoint.created",
                        source="harness",
                        phase="checkpoint",
                        status=checkpoint.status,
                        payload={
                            "checkpoint_id": checkpoint.checkpoint_id,
                            "path": clean_path,
                            "artifact_path": clean_artifact,
                            "content_digest": checkpoint.content_digest,
                        },
                        action_request_id=checkpoint.action_request_id,
                        operation_id=action["operation_id"],
                    )
                )
                return self._checkpoint_from_row(
                    self._db.execute(
                        "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                        (checkpoint.checkpoint_id,),
                    ).fetchone(),
                    action,
                )

    def pending_actions(
        self,
        scope: HarnessScope,
        *,
        limit: int | None = None,
    ) -> tuple[ActionRecord, ...]:
        if limit is not None and limit < 1:
            raise ValueError("pending action limit must be positive")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope)
            self._validate_database_locked()
            query = (
                "SELECT * FROM actions WHERE session_id = ? AND status = ? "
                "ORDER BY created_at DESC, action_request_id DESC"
            )
            parameters: list[object] = [
                scope.session_id,
                ActionStatus.APPROVAL_PENDING.value,
            ]
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = self._db.execute(query, tuple(parameters)).fetchall()
            result = []
            for row in rows:
                self._assert_action_scope(row, scope.for_turn(row["turn_id"]))
                result.append(self._action_from_row(row))
            return tuple(reversed(result))

    def actions_for_turn(
        self,
        scope: HarnessScope,
        *,
        limit: int | None = None,
    ) -> tuple[ActionRecord, ...]:
        if not scope.turn_id:
            raise ValueError("turn scope requires turn_id")
        if limit is not None and limit < 1:
            raise ValueError("action limit must be positive")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope)
            self._validate_database_locked()
            query = (
                "SELECT * FROM actions WHERE session_id = ? AND turn_id = ? "
                "ORDER BY created_at DESC, action_request_id DESC"
            )
            parameters: list[object] = [scope.session_id, scope.turn_id]
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = self._db.execute(query, tuple(parameters)).fetchall()
            result = []
            for row in rows:
                self._assert_action_scope(row, scope)
                result.append(self._action_from_row(row))
            return tuple(reversed(result))

    def count_actions_for_turn(self, scope: HarnessScope) -> int:
        if not scope.turn_id:
            raise ValueError("turn scope requires turn_id")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope)
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM actions "
                "WHERE session_id = ? AND turn_id = ?",
                (scope.session_id, scope.turn_id),
            ).fetchone()
            return int(row["count"])

    def actions_for_session(
        self,
        scope: HarnessScope,
        *,
        status: ActionStatus | None = None,
        limit: int | None = None,
    ) -> tuple[ActionRecord, ...]:
        """Read a bounded action keyset directly from durable action identity."""

        if limit is not None and limit < 1:
            raise ValueError("action limit must be positive")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope.session_scope())
            self._validate_database_locked()
            query = (
                "SELECT * FROM actions WHERE session_id = ?"
            )
            parameters: list[object] = [scope.session_id]
            if status is not None:
                query += " AND status = ?"
                parameters.append(status.value)
            query += " ORDER BY updated_at DESC, action_request_id DESC"
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = self._db.execute(query, tuple(parameters)).fetchall()
            result = []
            for row in rows:
                self._assert_action_scope(row, _scope_from_row(row))
                result.append(self._action_from_row(row))
            return tuple(reversed(result))

    def count_actions_for_session(
        self,
        scope: HarnessScope,
        *,
        status: ActionStatus | None = None,
    ) -> int:
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope.session_scope())
            self._validate_database_locked()
            query = "SELECT COUNT(*) AS count FROM actions WHERE session_id = ?"
            parameters: list[object] = [scope.session_id]
            if status is not None:
                query += " AND status = ?"
                parameters.append(status.value)
            row = self._db.execute(query, tuple(parameters)).fetchone()
            return int(row["count"])

    def count_events_for_turn(self, scope: HarnessScope) -> int:
        if not scope.turn_id:
            raise ValueError("turn scope requires turn_id")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope)
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM events "
                "WHERE session_id = ? AND turn_id = ?",
                (scope.session_id, scope.turn_id),
            ).fetchone()
            return int(row["count"])

    def pending_approval_count(self) -> int:
        with self._lock:
            self._require_open()
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM approvals WHERE status = ?",
                (ApprovalStatus.PENDING.value,),
            ).fetchone()
            return int(row["count"])

    def pending_approval_count_for_session(self, scope: HarnessScope) -> int:
        """Count pending approvals for exactly one validated session."""

        with self._lock:
            self._require_open()
            self._assert_session_scope(scope.session_scope())
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM approvals "
                "WHERE session_id = ? AND status = ?",
                (scope.session_id, ApprovalStatus.PENDING.value),
            ).fetchone()
            return int(row["count"])

    def recovery_records_for_session(
        self,
        scope: HarnessScope,
        *,
        limit: int = 128,
    ) -> tuple[RecoveryRecord, ...]:
        """Observe recovery rows without performing recovery mutation.

        Startup owns ``recover_inflight``.  HTTP/Workbench inspection must use
        this read-only indexed view so asking about session A cannot reconcile
        an in-flight effect belonging to session B.
        """

        if limit < 1:
            raise ValueError("recovery limit must be positive")
        with self._lock:
            self._require_open()
            self._assert_session_scope(scope.session_scope())
            self._validate_database_locked()
            rows = self._db.execute(
                "SELECT * FROM recoveries WHERE session_id = ? "
                "ORDER BY created_at DESC, recovery_id DESC LIMIT ?",
                (scope.session_id, limit),
            ).fetchall()
            records: list[RecoveryRecord] = []
            for row in rows:
                action = self._action_row(row["action_request_id"])
                action_scope = _scope_from_row(action)
                if (
                    action_scope.session_id != scope.session_id
                    or row["scope_digest"] != action_scope.digest
                    or row["state"] != "uncertain"
                ):
                    raise StoreUnavailable("recovery scope is corrupt")
                records.append(
                    RecoveryRecord(
                        recovery_id=row["recovery_id"],
                        action_request_id=row["action_request_id"],
                        scope=action_scope,
                        state=row["state"],
                        reason=row["reason"],
                        confirmed_exit=bool(row["confirmed_exit"]),
                        created_at=row["created_at"],
                    )
                )
            return tuple(records)

    def _terminal_recovery_locked(
        self, row: sqlite3.Row, *, reason: str
    ) -> RecoveryRecord:
        scope = _scope_from_row(row)
        action = self._action_row(row["action_request_id"])
        if not ActionStatus(action["status"]).terminal:
            updated = self._terminal_action_locked(
                scope,
                action,
                TerminalOutcome.UNCERTAIN,
                result={},
                error_code="recovery_unknown_effect",
            )
            self._append_event_locked(
                EventDraft(
                    scope=scope,
                    event_type="recovery.action_uncertain",
                    source="recovery",
                    phase="terminal",
                    status=ActionStatus.UNCERTAIN.value,
                    payload={"error_code": "recovery_unknown_effect", "reason": reason},
                    action_request_id=row["action_request_id"],
                    approval_id=updated["approval_id"],
                    operation_id=updated["operation_id"],
                )
            )
        else:
            updated = action
            self._close_operation_for_action_locked(
                scope,
                updated,
                TerminalOutcome.UNCERTAIN,
                result={},
                error_code="recovery_orphan_operation",
            )
        existing = self._db.execute(
            "SELECT * FROM recoveries WHERE action_request_id = ?",
            (row["action_request_id"],),
        ).fetchone()
        if existing is not None:
            return RecoveryRecord(
                recovery_id=existing["recovery_id"],
                action_request_id=existing["action_request_id"],
                scope=scope,
                state=existing["state"],
                reason=existing["reason"],
                confirmed_exit=bool(existing["confirmed_exit"]),
                created_at=existing["created_at"],
            )
        recovery_id = str(uuid4())
        now = utc_now()
        self._db.execute(
            """INSERT INTO recoveries
            (recovery_id, action_request_id, session_id, scope_digest, state, reason,
             confirmed_exit, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                recovery_id,
                row["action_request_id"],
                row["session_id"],
                scope.digest,
                "uncertain",
                reason,
                now,
            ),
        )
        self._append_event_locked(
            EventDraft(
                scope=scope,
                event_type="recovery.uncertain",
                source="recovery",
                phase="recovery",
                status="uncertain",
                payload={"recovery_id": recovery_id, "reason": reason},
                action_request_id=row["action_request_id"],
                operation_id=row["operation_id"],
            )
        )
        return RecoveryRecord(
            recovery_id=recovery_id,
            action_request_id=row["action_request_id"],
            scope=scope,
            state="uncertain",
            reason=reason,
            confirmed_exit=False,
            created_at=now,
        )

    def _reconcile_model_rounds_locked(
        self,
        *,
        reason: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        status: ModelRoundStatus = ModelRoundStatus.UNCERTAIN,
        writer: object | None = None,
    ) -> tuple[ModelRoundRecord, ...]:
        """Close every open provider attempt before a turn can be terminal."""

        clauses = ["status = ?"]
        parameters: list[object] = [ModelRoundStatus.STREAMING.value]
        if session_id is not None:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if turn_id is not None:
            clauses.append("turn_id = ?")
            parameters.append(turn_id)
        rows = self._db.execute(
            "SELECT * FROM model_rounds WHERE "
            + " AND ".join(clauses)
            + " ORDER BY session_id, turn_id, round_index",
            tuple(parameters),
        ).fetchall()
        terminal: list[ModelRoundRecord] = []
        cancelled_turns: set[tuple[str, str]] = set()
        for row in rows:
            if writer is not None:
                self._require_model_round_owner(row["round_id"], writer)
            scope = _scope_from_row(row)
            closed = self._terminalize_model_round_locked(
                scope,
                row,
                status,
                delta_count=int(row["delta_count"]),
                done_count=0,
                reason=reason,
            )
            terminal.append(self._model_round_from_row(closed))
            self._append_event_locked(
                EventDraft(
                    scope=scope,
                    event_type="model.round.recovery",
                    source="recovery",
                    phase="recovery",
                    status=status.value,
                    payload={
                        "round_id": row["round_id"],
                        "status": status.value,
                        "reason": reason,
                    },
                    event_id=f"model-round-recovery:{row['round_id']}",
                    item_id=f"model-round-recovery-item:{row['round_id']}",
                )
            )
            cancelled_turns.add((row["session_id"], row["turn_id"]))

        for session_id_value, turn_id_value in cancelled_turns:
            turn = self._turn_row(session_id_value, turn_id_value)
            turn_status = TurnStatus(turn["status"])
            if turn_status in {
                TurnStatus.SUCCEEDED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }:
                continue
            now = utc_now()
            self._db.execute(
                """UPDATE turns SET status = ?, revision = ?, updated_at = ?
                   WHERE session_id = ? AND turn_id = ?""",
                (
                    TurnStatus.CANCELLED.value,
                    int(turn["revision"]) + 1,
                    now,
                    session_id_value,
                    turn_id_value,
                ),
            )
            turn_scope = _scope_from_row(turn)
            self._append_event_locked(
                EventDraft(
                    scope=turn_scope,
                    event_type="turn.recovery",
                    source="recovery",
                    phase="recovery",
                    status=TurnStatus.CANCELLED.value,
                    payload={"reason": reason, "status": TurnStatus.CANCELLED.value},
                    event_id=f"turn-recovery:{session_id_value}:{turn_id_value}:{turn['revision'] + 1}",
                    item_id=f"turn-recovery-item:{session_id_value}:{turn_id_value}:{turn['revision'] + 1}",
                    created_at=now,
                )
            )
        return tuple(terminal)

    def reconcile_model_rounds(
        self,
        scope: HarnessScope,
        *,
        reason: str = "abort",
        status: ModelRoundStatus = ModelRoundStatus.UNCERTAIN,
        cancel_turn: bool = False,
        _writer: object | None = None,
        _writer_capability: object | None = None,
    ) -> tuple[ModelRoundRecord, ...]:
        """Fence open attempts, optionally closing admission even before one exists."""

        self._require_model_round_writer(
            _writer,
            _writer_capability,
            allow_readonly=True,
        )
        if status is ModelRoundStatus.STREAMING:
            raise ValueError("model round reconciliation requires a terminal status")
        with self._lock:
            with self._transaction():
                self._assert_turn_scope(scope)
                self._validate_database_locked()
                result = self._reconcile_model_rounds_locked(
                    reason=reason,
                    session_id=scope.session_id,
                    turn_id=scope.turn_id,
                    status=status,
                    writer=_writer,
                )
                if cancel_turn:
                    turn = self._turn_row(scope.session_id, scope.turn_id)
                    if TurnStatus(turn["status"]) not in {
                        TurnStatus.SUCCEEDED,
                        TurnStatus.FAILED,
                        TurnStatus.CANCELLED,
                    }:
                        # With no admitted round, reconciliation has nothing
                        # to close. Cancel this turn in the same transaction:
                        # a prepared request must not enter after this winner.
                        revision = int(turn["revision"]) + 1
                        now = utc_now()
                        self._db.execute(
                            """UPDATE turns SET status = ?, revision = ?, updated_at = ?
                               WHERE session_id = ? AND turn_id = ?""",
                            (
                                TurnStatus.CANCELLED.value,
                                revision,
                                now,
                                scope.session_id,
                                scope.turn_id,
                            ),
                        )
                        self._append_event_locked(
                            EventDraft(
                                scope=scope,
                                event_type="turn.status",
                                source="harness",
                                phase="turn",
                                status=TurnStatus.CANCELLED.value,
                                payload={
                                    "status": TurnStatus.CANCELLED.value,
                                    "revision": revision,
                                },
                                event_id=f"turn-status:{scope.turn_id}:{revision}",
                                item_id=f"turn-status-item:{scope.turn_id}:{revision}",
                                created_at=now,
                            )
                        )
                        self._append_event_locked(
                            EventDraft(
                                scope=scope,
                                event_type="control.abort",
                                source="harness",
                                phase="control",
                                status="aborted",
                                payload={"reason": reason},
                                created_at=now,
                            )
                        )
                self._validate_database_locked()
                return result

    def recover_inflight(self, *, reason: str = "process_restart") -> tuple[RecoveryRecord, ...]:
        """Fence every in-flight effect once; never replay it automatically."""

        with self._lock:
            with self._transaction():
                self._validate_database_locked()
                self._reconcile_model_rounds_locked(reason=reason)
                rows = self._db.execute(
                    """SELECT * FROM actions WHERE status IN (?, ?, ?) ORDER BY created_at""",
                    (
                        ActionStatus.STARTING.value,
                        ActionStatus.RUNNING.value,
                        ActionStatus.TERMINALIZING.value,
                    ),
                ).fetchall()
                records = tuple(
                    self._terminal_recovery_locked(row, reason=reason) for row in rows
                )
                self._validate_database_locked()
                return records

    def action_terminal_count(self, scope: HarnessScope, action_request_id: str) -> int:
        with self._lock:
            self._assert_session_scope(scope)
            action = self._action_row(action_request_id)
            self._assert_action_scope(action, scope)
            self._validate_database_locked()
            row = self._db.execute(
                "SELECT COUNT(*) AS count FROM action_terminals WHERE session_id = ? AND action_request_id = ?",
                (scope.session_id, action_request_id),
            ).fetchone()
            return int(row["count"])
