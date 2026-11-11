"""A read-only encounter surface over the existing shared native workspace.

Listing exposes available objects, not a task, a reading claim, or a new memory
store. Actual reading and writing stay with the existing file tools. The same
surface is available to native Home, Work and independent local contexts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Mapping

from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider
from pulse_harness.core.contracts import HarnessScope, ToolCall
from pulse_harness.core.provider import CancellationToken

from pulse_harness.adapters.workspace import WorkspaceValidationError, _normalize_relative_path
from pulse_harness.core.contracts import ToolDescriptor
from pulse_harness.core.errors import ProviderProtocolError, ScopeConflict
from pulse_system.product.files import FileAccessError, WorkspaceFiles

NAME = "browse_workspace"
SCHEMA = {
    "type": "object", "properties": {
        "path": {"type": "string", "maxLength": 2048,
                 "description": "Relative directory; empty string means the configured shared workspace."},
        "cursor": {"type": "string", "minLength": 1, "maxLength": 4096,
                   "description": "Pass the exact nextCursor from an earlier page to continue listing."},
    }, "required": ["path"], "additionalProperties": False,
}
MAX_RESULT_BYTES = 16000


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        NAME,
        "List files and directories in the workspace, including ones not mentioned in this "
        "conversation. path='' lists the root; pass a returned directory path to look "
        "inside it. Shows names and sizes only: read a file with read_file or reading to "
        "see what it says. If nextCursor is present, pass it as cursor with the same path "
        "to see further entries. A changed directory starts a fresh listing, visibly marked "
        "as restarted; earlier entries may repeat.",
        mutable=False, requires_approval=False, capability="workspace.read",
        source="pulse.native-workspace-index",
    )


def validate(arguments: Mapping[str, Any]) -> str:
    if (not isinstance(arguments, Mapping)
            or set(arguments) not in ({"path"}, {"path", "cursor"})):
        raise ProviderProtocolError("browse_workspace requires path and optional cursor",
                                    code="tool_arguments_invalid")
    if "cursor" in arguments:
        cursor = arguments["cursor"]
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
            raise ProviderProtocolError("workspace cursor is invalid", code="tool_arguments_invalid")
    path = arguments["path"]
    if not isinstance(path, str) or len(path) > 2048:
        raise ProviderProtocolError("workspace directory must be bounded text", code="tool_arguments_invalid")
    return path


def browse(
    provider: StructuredWorkspaceToolProvider, scope: HarnessScope,
    call: ToolCall, cancellation: CancellationToken,
) -> dict[str, Any]:
    """Reuse the product's handle-relative, non-recursive directory reader."""
    provider.store.get_session(scope.session_scope())
    path = validate(call.arguments)
    cancellation.raise_if_cancelled()
    if path and provider._is_protected_relative(path):
        raise ScopeConflict("workspace directory is protected")
    cursor = call.arguments.get("cursor")
    restarted = False
    # Reuse the real filesystem reader; shrink a page if long paths would
    # exceed the durable native-tool result allowance.
    for page_size in (32, 16, 8, 4, 2, 1):
        cancellation.raise_if_cancelled()
        try:
            listing = WorkspaceFiles(provider.workspace_root).list(
                path, cursor=cursor, page_size=page_size)
        except FileAccessError as exc:
            if cursor is not None and exc.code == "directory_changed_retry":
                cursor, restarted = None, True
                listing = WorkspaceFiles(provider.workspace_root).list(
                    path, page_size=page_size)
            else:
                raise ScopeConflict("workspace index unavailable: " + exc.code) from None
        result = {
            "path": path, "scope": listing["scope"],
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "entries": [], "truncated": listing["truncated"],
            "nextCursor": listing["nextCursor"],
            "restarted_due_to_change": restarted,
            "content_included": False,
            "next_action": (
                "Directory changed; listing restarted from the beginning. Earlier entries "
                "may repeat. Continue with nextCursor, if present."
                if restarted else
                "Pass nextCursor as cursor with the same path to browse further entries."
                if listing["nextCursor"] else
                "All observed entries on this listing are shown. Read files with read_file or reading."
            ),
        }
        for row in listing["entries"]:
            cancellation.raise_if_cancelled()
            try:
                _normalize_relative_path(row["path"])
                if provider._is_protected_relative(row["path"]):
                    continue
            except WorkspaceValidationError:
                continue
            result["entries"].append(row)
        limit = min(MAX_RESULT_BYTES, provider.store.max_payload_bytes)
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= limit:
            cancellation.raise_if_cancelled()
            return result
    raise ScopeConflict("workspace index metadata exceeds the session payload allowance")
