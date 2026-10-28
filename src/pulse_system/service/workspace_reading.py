"""Bounded reading from source observations, kept in the existing tool history.

A receipt names an exposed passage, not comprehension or a new task. Continuing
verifies the whole source and redacted-view digests before exposing another
passage. No sidecar bookmark store, semantic scheduler or file write is involved.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from pulse_harness.adapters.workspace import (
    StructuredWorkspaceToolProvider, WorkspaceAdapterError, WorkspaceDigestConflict,
    _normalize_relative_path,
)
from pulse_harness.core.contracts import ActionStatus, HarnessScope, ToolCall, ToolDescriptor
from pulse_harness.core.errors import ProviderProtocolError, ScopeConflict
from pulse_harness.core.provider import CancellationToken
from pulse_harness.core.replay import redact_text
from pulse_harness.core.secrets import looks_like_bare_credential

NAME = "reading"
KIND = "pulse.reading-passage.v1"
VIEW = "utf8-line-redacted.v1"
MAX_CHARS = 8000
MAX_RECENT_ACTIONS = 128
SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["open", "continue", "recent", "find"]},
        "path": {"type": "string", "maxLength": 2048,
                 "description": "Workspace-relative UTF-8 text path, for open or find."},
        "expected_digest": {"type": "string", "pattern": "^[a-f0-9]{64}$",
                            "description": "Optional original SHA-256, for open/find; refuse changed bytes before exposing text."},
        "receipt_id": {"type": "string", "maxLength": 256,
                       "description": "Exact saved receipt_id for continue; never invent offsets."},
        "max_chars": {"type": "integer", "minimum": 1, "maximum": MAX_CHARS},
        "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        "query": {"type": "string", "minLength": 2, "maxLength": 120},
        "case_sensitive": {"type": "boolean"},
        "max_matches": {"type": "integer", "minimum": 1, "maximum": 8},
    },
    "required": ["op"], "additionalProperties": False,
}


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        NAME,
        "Read a text material in manageable passages. open(path, max_chars?) starts "
        "at its beginning; pass expected_digest when an input specifies a file version. "
        "continue(receipt_id, max_chars?) reads after that actual "
        "saved passage, refusing changed source content. recent(limit?) finds the "
        "latest exposed positions in THIS logical session, including after reopen, "
        "without opening files. Use an older receipt to reread its following page; "
        "use open to deliberately start a changed version. "
        "find(path, query, expected_digest?, max_matches?) locates exact literal "
        "phrases with bounded excerpts and source coordinates in the redacted view; "
        "it is not a semantic search or proof of reading the whole file. "
        "A passage receipt is not proof of understanding or a claim that earlier pages were read. "
        "No automatic next step, summary, file write, or task is created. "
        "Only bounded UTF-8 files in the configured shared workspace are supported.",
        mutable=False, requires_approval=False, capability="workspace.read",
        source="pulse.native-reading",
    )


def _integer(value: Any, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ProviderProtocolError("reading range is invalid", code="reading_arguments_invalid")
    return value


def validate(args: Mapping[str, Any]) -> str:
    if not isinstance(args, Mapping):
        raise ProviderProtocolError("reading requires an object", code="reading_arguments_invalid")
    op = args.get("op")
    allowed = {
        "open": {"op", "path", "max_chars", "expected_digest"},
        "continue": {"op", "receipt_id", "max_chars"},
        "recent": {"op", "limit"},
        "find": {"op", "path", "query", "expected_digest", "max_matches", "case_sensitive"},
    }
    if not isinstance(op, str) or op not in allowed or set(args) - allowed[op]:
        raise ProviderProtocolError("reading operation is invalid", code="reading_arguments_invalid")
    if "expected_digest" in args:
        digest = args["expected_digest"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ProviderProtocolError("reading source digest is invalid", code="reading_arguments_invalid")
    field = "path" if op in ("open", "find") else "receipt_id" if op == "continue" else None
    if field:
        value = args.get(field)
        maximum = 2048 if field == "path" else 256
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            raise ProviderProtocolError("reading reference is invalid", code="reading_arguments_invalid")
        if op == "find":
            query = args.get("query")
            if (not isinstance(query, str) or not 2 <= len(query) <= 120
                    or not query.strip() or any(ord(char) < 32 for char in query)):
                raise ProviderProtocolError("reading query is invalid", code="reading_arguments_invalid")
            if type(args.get("case_sensitive", False)) is not bool:
                raise ProviderProtocolError("reading case sensitivity is invalid", code="reading_arguments_invalid")
            _integer(args.get("max_matches", 5), 1, 8)
        else:
            _integer(args.get("max_chars", 4000), 1, MAX_CHARS)
    else:
        _integer(args.get("limit", 5), 1, 8)
    return op


def _view(text: str) -> tuple[str, bool]:
    # Sanitize BEFORE pagination, so splitting an assignment/token across page
    # boundaries cannot expose its pieces. Whole affected lines are hidden;
    # offsets explicitly belong to this view, not the raw bytes. Using a bare
    # marker also avoids repeated assignment redaction by the Kernel envelope.
    parts = []
    hidden = False
    for line in text.splitlines(keepends=True):
        if redact_text(line)[1] or looks_like_bare_credential(line):
            suffix = line[len(line.rstrip("\r\n")):]
            parts.append("[REDACTED]" + suffix)
            hidden = True
        else:
            parts.append(line)
    clean = "".join(parts)
    # Cross-line credential forms use the same existing high-confidence guard.
    if redact_text(clean)[1] or looks_like_bare_credential(clean):
        return "[REDACTED]", True
    return clean, hidden


def _size(value: Mapping[str, Any]) -> int:
    # Include escaped characters, not merely len(content). Half the actual
    # payload allowance leaves room for the normal durable tool/event envelope.
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _receipt(provider, scope, receipt_id):
    previous_scope = provider.store.action_scope_for_session(scope.session_scope(), receipt_id)
    record = provider.store.get_action(previous_scope, receipt_id)
    output = (record.result or {}).get("output")
    if (record.tool_name != NAME or record.status != ActionStatus.SUCCEEDED
            or not record.result_delivered or not isinstance(output, Mapping)
            or output.get("kind") != KIND or output.get("receipt_id") != receipt_id):
        raise ScopeConflict("reading reference is not an exposed passage in this session")
    return record, output


def _recent(provider, scope, args, cancellation):
    limit = args.get("limit", 5)
    # Bounded indexed action read; never return private tool bodies or invent a
    # globally complete reading list. These durable rows outlive event retention.
    records = provider.store.actions_for_session(
        scope, status=ActionStatus.SUCCEEDED, limit=MAX_RECENT_ACTIONS + 1)
    considered = list(reversed(records))[:MAX_RECENT_ACTIONS]
    result = {
        "kind": "pulse.recent-reading.v1", "session_id": scope.session_id,
        "passages": [], "current_files_checked": False,
        "history_partial": len(records) > MAX_RECENT_ACTIONS,
        "scope": "current_logical_session", "scanned_actions": len(considered),
    }
    seen = set()
    for record in considered:
        cancellation.raise_if_cancelled()
        output = (record.result or {}).get("output")
        if (record.tool_name != NAME or not record.result_delivered
                or not isinstance(output, Mapping) or output.get("kind") != KIND):
            continue
        path = output["source"]["path"]
        if path in seen:
            continue
        seen.add(path)
        if len(result["passages"]) >= limit:
            result["history_partial"] = True
            break
        item = {
            "receipt_id": record.action_request_id,
            "source": output["source"],
            "position": output["position"],
            "observed_at": output["observed_at"],
            "next": output["next"],
            "source_turn_id": record.scope.turn_id,
        }
        result["passages"].append(item)
        if _size(result) > provider.store.max_payload_bytes // 2:
            result["passages"].pop()
            result["history_partial"] = True
            break
    return result


def execute(
    provider: StructuredWorkspaceToolProvider, scope: HarnessScope,
    call: ToolCall, cancellation: CancellationToken, *,
    action_request_id: str | None,
) -> dict[str, Any]:
    """Expose a passage through a genuine in-progress read-only Kernel action."""
    if not isinstance(call, ToolCall) or call.name != NAME:
        raise ProviderProtocolError("unsupported reading call", code="reading_arguments_invalid")
    op = validate(call.arguments)
    provider.store.get_session(scope.session_scope())
    cancellation.raise_if_cancelled()
    if not isinstance(action_request_id, str):
        raise ScopeConflict("reading requires a real current action")
    action = provider.store.get_action(scope, action_request_id)
    if (action.tool_name != NAME or action.arguments != call.arguments
            or action.status != ActionStatus.RUNNING):
        raise ScopeConflict("reading is not the current running action")
    if op == "recent":
        result = _recent(provider, scope, call.arguments, cancellation)
        if _size(result) > provider.store.max_payload_bytes // 2:
            raise ProviderProtocolError("reading metadata exceeds session allowance",
                                        code="reading_payload_too_small")
        cancellation.raise_if_cancelled()
        return result

    prior = None
    offset = 0
    if op == "continue":
        _, prior = _receipt(provider, scope, call.arguments["receipt_id"])
        path = prior["source"]["path"]
        offset = prior["position"]["end_char"]
    else:
        path = call.arguments["path"]
    path = _normalize_relative_path(path)
    snapshot = provider._snapshot(provider._safe_target(path, allow_missing=False))
    if not snapshot.exists:
        raise WorkspaceAdapterError("reading source disappeared", code="workspace_file_not_found")
    if op in ("open", "find") and call.arguments.get("expected_digest", snapshot.digest) != snapshot.digest:
        raise WorkspaceDigestConflict("reading source differs from the supplied version", code="reading_source_changed")
    cancellation.raise_if_cancelled()
    text, redacted = _view(snapshot.text)
    source = {
        "path": path, "digest": snapshot.digest,
        "view_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "view_format": VIEW, "workspace_digest": provider._workspace_provenance,
    }
    if prior is not None and dict(prior["source"]) != source:
        raise WorkspaceDigestConflict(
            "reading source or text view changed; use open to inspect the new version",
            code="reading_source_changed")
    if op == "find":
        query = call.arguments["query"]
        maximum = call.arguments.get("max_matches", 5)
        flags = 0 if call.arguments.get("case_sensitive", False) else re.IGNORECASE
        result = {
            "kind": "pulse.reading-find.v1", "source": source,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "current_action_source", "observation_only": True,
            "redacted": redacted, "searched_entire_redacted_view": True,
            "coordinates": "unicode_characters_in_redacted_view",
            "matches": [], "more_matches": False,
        }
        limit = provider.store.max_payload_bytes // 2
        for found in re.finditer(re.escape(query), text, flags=flags):
            cancellation.raise_if_cancelled()
            if len(result["matches"]) >= maximum:
                result["more_matches"] = True
                break
            left = max(0, found.start() - 72)
            right = min(len(text), found.end() + 96)
            excerpt, clipped = _view(text[left:right])
            match = {
                "start_char": found.start(), "end_char": found.end(),
                "excerpt_start_char": left, "excerpt": excerpt,
                "excerpt_redacted": clipped,
            }
            result["matches"].append(match)
            if _size(result) > limit:
                result["matches"].pop()
                result["more_matches"] = True
                break
        if _size(result) > limit:
            raise ProviderProtocolError("session allowance cannot fit reading search evidence",
                                        code="reading_payload_too_small")
        cancellation.raise_if_cancelled()
        return result
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ScopeConflict("saved reading position is invalid")
    end = min(len(text), offset + call.arguments.get("max_chars", 4000))
    result = {
        "kind": KIND, "receipt_id": action_request_id, "source": source,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "position": {"start_char": offset, "end_char": end, "total_chars": len(text),
                     "unit": "unicode_characters_in_redacted_view"},
        "content": "", "redacted": redacted, "next": None,
        "observation_only": True,
    }

    def set_end(stop):
        # A cut can create an apparent assignment at the new boundary. Apply
        # the existing guard once more; source coordinates still describe the
        # exposed span, not the length of a possibly redacted presentation.
        content, additionally_redacted = _view(text[offset:stop])
        result["content"] = content
        result["redacted"] = redacted or additionally_redacted
        result["position"]["end_char"] = stop
        result["next"] = {"op": "continue", "receipt_id": action_request_id} if stop < len(text) else None

    set_end(end)
    limit = provider.store.max_payload_bytes // 2
    if _size(result) > limit:
        low, high = offset, end
        while low < high:
            middle = (low + high + 1) // 2
            set_end(middle)
            if _size(result) <= limit:
                low = middle
            else:
                high = middle - 1
        end = low
        set_end(end)
    if _size(result) > limit or (end == offset and offset < len(text)):
        raise ProviderProtocolError(
            "session allowance cannot fit a reading passage and its continuation",
            code="reading_payload_too_small")
    cancellation.raise_if_cancelled()
    return result
