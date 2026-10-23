"""Page-owned file results projected from the native Kernel's write receipts.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

There is no artifact database here. The structured workspace adapter emits its
successful receipt only after recording the applied ChangeRecord, checkpoint,
and operation. That receipt lives in the original ActionRecord. Listing reads
only this durable metadata; previewing checks the current file against that
recorded version and never exposes a later writer's content as the old result.

Only the existing ``mutate_file`` receipt contract is supported. Command output,
assistant claims, arbitrary workspace paths, and unconfirmed effects are not
artifacts. Text preview is bounded and redacted; this is not a download API.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from pulse_harness.core.contracts import ActionRecord, ActionStatus
from pulse_harness.core.errors import HarnessError, ScopeConflict
from pulse_harness.core.replay import bound_text, redact_text, stored_payload_flags, stored_text_flags
from pulse_system.product.files import FileAccessError, MAX_FILE_BYTES, WorkspaceFiles

from .execution import ExecutionError, PageExecution


MAX_ACTION_SCAN = 256
MAX_ARTIFACTS = 50
MAX_PREVIEW_BYTES = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ArtifactError(ExecutionError):
    """Use the same stable local API error envelope as page execution."""


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 256
        and not any(ord(character) < 32 for character in value)
        and not any(stored_text_flags(value))
    )


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


class PageArtifacts:
    """Read page-scoped write evidence without creating sessions or runners."""

    def __init__(self, runtime, *, execution: PageExecution | None = None):
        self.runtime = runtime
        # Share the existing Home/Work -> Kernel scope mapping. This helper
        # owns no additional authority or persisted session identity.
        self.execution = execution if execution is not None else PageExecution(runtime)

    @staticmethod
    def _item(page_id: str, action: ActionRecord) -> dict | None:
        if action.status is not ActionStatus.SUCCEEDED or action.tool_name != "mutate_file":
            return None
        result = action.result if isinstance(action.result, Mapping) else {}
        output = result.get("output", result)
        if not isinstance(output, Mapping):
            return None
        provenance = output.get("workspace_provenance")
        if not isinstance(provenance, Mapping):
            return None
        if (
            output.get("status") != "succeeded"
            or output.get("action_request_id") != action.action_request_id
            or output.get("operation_id") != action.operation_id
            or not _identifier(action.operation_id)
            or provenance.get("adapter") != "pulse_harness.workspace.v1"
            or not _digest(provenance.get("workspace_digest"))
            or not _digest(output.get("after_digest"))
            or output.get("before_digest") is not None and not _digest(output.get("before_digest"))
            or not _identifier(output.get("change_id"))
            or not _identifier(output.get("checkpoint_id"))
        ):
            return None
        path = output.get("path")
        # A redacted or truncated path cannot identify the original file.
        # WorkspaceFiles applies its stronger path policy at read time.
        if not isinstance(path, str) or not path or len(path) > 2048 or any(stored_text_flags(path)):
            return None
        return {
            "id": action.action_request_id,
            "pageId": page_id,
            "path": path,
            "sha256": output["after_digest"],
            "version": output["after_digest"],
            "beforeSha256": output.get("before_digest"),
            "sessionId": action.scope.session_id,
            "turnId": action.scope.turn_id,
            "actionId": action.action_request_id,
            "changeId": output["change_id"],
            "checkpointId": output["checkpoint_id"],
            "operationId": action.operation_id,
            "createdAt": action.terminal_at or action.updated_at,
            "state": "unchecked",
        }

    @staticmethod
    def _limits() -> dict:
        return {"limitBytes": MAX_FILE_BYTES, "previewLimitBytes": MAX_PREVIEW_BYTES}

    def snapshot(self, page_id: str) -> dict:
        """Return recent confirmed file versions, without opening any files.

        The bounded scan is explicit. An artifact outside this recent list is
        still addressable by its action ID through ``read`` after a restart.
        """
        scope = self.execution._scope(page_id)
        store = self.execution._kernel.store
        try:
            try:
                store.get_session(scope)
            except ScopeConflict as error:
                if str(error).startswith("unknown session:"):
                    return {
                        "pageId": page_id, "artifacts": [], "truncated": False,
                        "scanLimit": MAX_ACTION_SCAN, **self._limits(),
                    }
                raise
            actions = store.actions_for_session(
                scope, status=ActionStatus.SUCCEEDED, limit=MAX_ACTION_SCAN + 1,
            )
            truncated = len(actions) > MAX_ACTION_SCAN
            items = []
            for action in reversed(actions[-MAX_ACTION_SCAN:]):
                item = self._item(page_id, action)
                if item is not None:
                    items.append(item)
                elif action.tool_name == "mutate_file" and stored_payload_flags(action.result)[1]:
                    # The native result bound can omit a large receipt's
                    # identity fields. Do not silently claim a complete list.
                    truncated = True
            truncated = truncated or len(items) > MAX_ARTIFACTS
            return {
                "pageId": page_id, "artifacts": items[:MAX_ARTIFACTS],
                "truncated": truncated, "scanLimit": MAX_ACTION_SCAN, **self._limits(),
            }
        except HarnessError as error:
            raise self.execution._translate(error) from error

    def read(self, page_id: str, artifact_id: str) -> dict:
        """Preview an exact page-owned action's current, digest-matching file.

        Original versions are not reconstructed from checkpoints: those hold
        pre-images and have separate recovery authority. A missing, replaced,
        binary, protected, or oversized file returns no content.
        """
        scope = self.execution._scope(page_id)
        kernel = self.execution._kernel
        missing = ArtifactError("artifact_not_found", "This file result does not belong to this page.", 404)
        if not _identifier(artifact_id):
            raise missing
        try:
            action_scope = kernel.store.action_scope_for_session(scope, artifact_id)
            action = kernel.store.get_action(action_scope, artifact_id)
        except ScopeConflict:
            raise missing from None
        except HarnessError as error:
            raise self.execution._translate(error) from error
        item = self._item(page_id, action)
        if item is None:
            raise missing
        response = {
            "pageId": page_id,
            "artifact": item,
            "state": "unavailable",
            "content": None,
            "observedSha256": None,
            "bytes": None,
            "redacted": False,
            "truncated": False,
            "reason": None,
            **self._limits(),
        }
        try:
            # Reuses the product's dirfd/no-follow path traversal, regular-file
            # check, hardlink rejection, bounded UTF-8 read, and secret guard.
            current = WorkspaceFiles(kernel.workspace_root).read(item["path"])
        except FileAccessError as error:
            response["artifact"] = {**item, "state": "unavailable"}
            response["reason"] = error.code
            return response
        response["observedSha256"] = current["sha256"]
        response["bytes"] = current["bytes"]
        if current["sha256"] != item["sha256"]:
            response.update({"state": "changed", "reason": "artifact_version_changed"})
            response["artifact"] = {**item, "state": "changed"}
            return response
        # Hash the original bytes above, then redact and bound the visible
        # text. A redacted preview must never be mistaken for raw file bytes.
        clean, redacted = redact_text(current["content"])
        content, bound_redacted, truncated = bound_text(clean, MAX_PREVIEW_BYTES)
        stored_redacted, _ = stored_text_flags(content)
        response.update({
            "state": "current", "content": content,
            "redacted": bool(redacted or bound_redacted or stored_redacted),
            "truncated": truncated,
        })
        response["artifact"] = {**item, "state": "current"}
        return response
