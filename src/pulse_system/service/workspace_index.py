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
    }, "required": ["path"], "additionalProperties": False,
}
MAX_RESULT_BYTES = 16000


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        NAME,
        "Discover files and directories in the configured shared native workspace, "
        "including materials not named in this conversation. Supply path='' to see "
        "the root or a returned directory path to look inside it. This bounded index "
        "contains names and sizes, NOT file contents; do not infer what an unread file "
        "says. Use read_file on a selected file for its actual content and digest. "
        "A listing is not a reading plan, a task assignment, or evidence of learning; "
        "nothing must be read or produced. It may be partial and may change before "
        "the next read. No shell, network, file mutation or background run is started.",
        mutable=False, requires_approval=False, capability="workspace.read",
        source="pulse.native-workspace-index",
    )


def validate(arguments: Mapping[str, Any]) -> str:
    if not isinstance(arguments, Mapping) or set(arguments) != {"path"}:
        raise ProviderProtocolError("browse_workspace requires only path", code="tool_arguments_invalid")
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
    try:
        listing = WorkspaceFiles(provider.workspace_root).list(path)
    except FileAccessError as exc:
        raise ScopeConflict("workspace index unavailable: " + exc.code) from None
    result = {
        "path": path, "scope": listing["scope"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "entries": [], "truncated": listing["truncated"],
        "content_included": False,
        "next_action": "Directories can be browsed; actual file contents require read_file. No action is required.",
    }
    # Both count/scan limits already apply in WorkspaceFiles.list. Also bound
    # the wire result by the actual Kernel payload allowance, not just a count.
    limit = min(MAX_RESULT_BYTES, provider.store.max_payload_bytes)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > limit:
        raise ScopeConflict("workspace index metadata exceeds the session payload allowance")
    for row in listing["entries"]:
        cancellation.raise_if_cancelled()
        # A native path must also satisfy the existing file adapter's stricter
        # cross-platform/protected-path rules before it is suggested for reading.
        try:
            _normalize_relative_path(row["path"])
            if provider._is_protected_relative(row["path"]):
                continue
        except WorkspaceValidationError:
            continue
        result["entries"].append(row)
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > limit:
            result["entries"].pop()
            result["truncated"] = True
            break
    cancellation.raise_if_cancelled()
    return result
