"""Page-owned file results projected from the native Kernel's write receipts.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

There is no artifact database here. The structured workspace adapter emits its
successful receipt only after recording the applied ChangeRecord, checkpoint,
and operation. That receipt lives in the original ActionRecord. Listing reads
only this durable metadata; previewing checks the current file against that
recorded version and never exposes a later writer's content as the old result.

Only the existing ``mutate_file`` receipt contract is supported. Command output,
assistant claims, arbitrary workspace paths, and unconfirmed effects are not
artifacts. Preview bounds the current text. Downloads verify the complete
current file against its recorded version and preserve its UTF-8 bytes.

Undo restores the change's own checkpoint through the Kernel workspace
provider. It applies only while the file still has that change's content.
"""
from __future__ import annotations

from collections.abc import Mapping
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from pulse_harness.adapters.workspace import (
    WorkspaceAuthorityError, WorkspaceDigestConflict, WorkspaceEffectUncertain,
)
from pulse_harness.core.contracts import ActionRecord, ActionStatus
from pulse_harness.core.errors import HarnessError, ScopeConflict
from pulse_harness.core.replay import stored_payload_flags, stored_text_flags
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


def clip_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """Shorten text to at most ``max_bytes`` of UTF-8 without changing it otherwise."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


class PageArtifacts:
    """Read page-scoped write evidence without creating sessions or runners."""

    def __init__(self, runtime, *, execution: PageExecution | None = None,
                 undone_record: Any = None):
        self.runtime = runtime
        # Share the existing Home/Work -> Kernel scope mapping. This helper
        # owns no additional authority or persisted session identity.
        self.execution = execution if execution is not None else PageExecution(runtime)
        # Durable record of undone changes (``is_undone``/``mark_undone``).
        # Kernel events roll over in a long session, so they cannot be the
        # only record that a change was undone.
        self._undone_record = undone_record

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
            "undone": False,
        }

    def _undone(self, action: ActionRecord) -> bool:
        if self._undone_record is not None and self._undone_record.is_undone(action.action_request_id):
            return True
        return self.execution._kernel.store.has_event(
            action.scope.session_scope(), "checkpoint.restored",
            action_request_id=action.action_request_id,
        )

    def _mark_undone(self, action: ActionRecord) -> None:
        if self._undone_record is not None:
            self._undone_record.mark_undone(action.action_request_id)

    def _has_before_image(self, item: dict) -> bool:
        """Whether the file already has the content it had before this change."""
        path = Path(self.execution._kernel.workspace_root) / item["path"]
        try:
            regular = path.is_file() and not path.is_symlink()
            if regular and path.stat().st_size > MAX_FILE_BYTES:
                return False
            data = path.read_bytes() if regular else None
        except OSError:
            return False
        if item["beforeSha256"] is None:
            return data is None and not path.exists() and not path.is_symlink()
        return data is not None and hashlib.sha256(data).hexdigest() == item["beforeSha256"]

    @staticmethod
    def _limits() -> dict:
        return {"limitBytes": MAX_FILE_BYTES, "previewLimitBytes": MAX_PREVIEW_BYTES}

    @staticmethod
    def _cursor(session_id: str, action: ActionRecord) -> str:
        """Keep a stable position in the already-authorized durable action history."""
        data = json.dumps([1, session_id, action.updated_at, action.action_request_id],
                          separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    @staticmethod
    def _resume(cursor: str) -> tuple[str, tuple[str, str]]:
        bad = ArtifactError("artifact_cursor_invalid", "Reload the file results and try again.", 400)
        if not isinstance(cursor, str) or not (0 < len(cursor) <= 2048):
            raise bad
        if re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None:
            raise bad
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        except (ValueError, UnicodeDecodeError, binascii.Error) as error:
            raise bad from error
        if (not isinstance(data, list) or len(data) != 4 or data[0] != 1
                or any(not isinstance(value, str) or not value or len(value) > 512
                       or any(ord(ch) < 32 for ch in value) for value in data[1:])):
            raise bad
        return data[1], (data[2], data[3])

    def snapshot(self, page_id: str, *, cursor: str | None = None) -> dict:
        """Read a bounded page of durable file results, even after years of work.

        The cursor is an observation position, never a file or action authority.
        The current page's committed lineage is checked on every continuation.
        Successful non-file actions still consume the per-request scan bound.
        """
        scope = self.execution._scope(page_id)
        try:
            scopes = self.execution._history_scopes(scope)
            session_id, before = self._resume(cursor) if cursor is not None else (None, None)
            if session_id is not None and session_id not in {item.session_id for item in scopes}:
                raise ArtifactError("artifact_cursor_changed",
                                    "This file history has changed. Start again from the latest results.", 409)
            start = next((i for i, item in enumerate(scopes)
                          if item.session_id == session_id), 0)
            items: list[dict] = []
            scanned = 0
            truncated = False
            next_cursor = None
            store = self.execution._kernel.store
            for index in range(start, len(scopes)):
                remaining = MAX_ACTION_SCAN - scanned
                if remaining <= 0:
                    break
                try:
                    actions = list(reversed(store.actions_for_session(
                        scopes[index], status=ActionStatus.SUCCEEDED,
                        limit=remaining + 1, before=before if index == start else None,
                    )))
                except ScopeConflict as error:
                    if str(error).startswith("unknown session:"):
                        continue
                    raise
                # The extra row tells us whether this generation has older
                # history without doing an unbounded full-session count.
                for position, action in enumerate(actions[:remaining]):
                    scanned += 1
                    anchor = self._cursor(scopes[index].session_id, action)
                    item = self._item(page_id, action)
                    if item is not None:
                        if len(items) < MAX_ARTIFACTS:
                            item["undone"] = self._undone(action)
                            items.append(item)
                        else:
                            truncated = True
                        if len(items) >= MAX_ARTIFACTS:
                            # Every shown result is original durable action evidence.
                            # There may still be older non-file rows, or older
                            # committed generations.
                            more = position + 1 < len(actions) or index + 1 < len(scopes)
                            next_cursor = anchor if more else None
                            return {"pageId": page_id, "artifacts": items,
                                    "truncated": truncated or more,
                                    "nextCursor": next_cursor,
                                    "scanLimit": MAX_ACTION_SCAN, **self._limits()}
                    elif action.tool_name == "mutate_file" and stored_payload_flags(action.result)[1]:
                        truncated = True
                if len(actions) > remaining or scanned >= MAX_ACTION_SCAN:
                    more = len(actions) > remaining or index + 1 < len(scopes)
                    next_cursor = anchor if more and scanned else None
                    break
                before = None
            return {"pageId": page_id, "artifacts": items,
                    "truncated": truncated or next_cursor is not None,
                    "nextCursor": next_cursor,
                    "scanLimit": MAX_ACTION_SCAN, **self._limits()}
        except HarnessError as error:
            raise self.execution._translate(error) from error

    def _resolve(self, page_id: str, artifact_id: str) -> dict:
        """Resolve a confirmed receipt in this page's committed history."""
        return self._resolve_action(page_id, artifact_id)[0]

    def _resolve_action(self, page_id: str, artifact_id: str) -> tuple[dict, ActionRecord]:
        scope = self.execution._scope(page_id)
        kernel = self.execution._kernel
        missing = ArtifactError("artifact_not_found", "This file result does not belong to this page.", 404)
        if not _identifier(artifact_id):
            raise missing
        try:
            for previous in self.execution._history_scopes(scope):
                try:
                    action_scope = kernel.store.action_scope_for_session(previous, artifact_id)
                    action = kernel.store.get_action(action_scope, artifact_id)
                except ScopeConflict:
                    continue
                break
            else:
                raise missing
        except HarnessError as error:
            raise self.execution._translate(error) from error
        item = self._item(page_id, action)
        if item is None:
            raise missing
        try:
            item["undone"] = self._undone(action)
        except HarnessError as error:
            raise self.execution._translate(error) from error
        return item, action

    def undo(self, page_id: str, artifact_id: str) -> dict:
        """Put the file back as it was before this change.

        A file that changed again afterwards is left alone; undo the later
        change first. A new file that this change created is removed.
        """
        item, action = self._resolve_action(page_id, artifact_id)
        if item["undone"]:
            raise ArtifactError("artifact_already_undone", "This change has already been undone.", 409)
        try:
            self.execution._kernel.restore_checkpoint(action.scope, item["checkpointId"])
        except WorkspaceDigestConflict as error:
            if self._has_before_image(item):
                # Already back to its earlier content (for example, an undo
                # that finished before its record was saved).
                self._mark_undone(action)
                return {"pageId": page_id, "artifact": {**item, "undone": True, "state": "unchecked"}}
            raise ArtifactError(
                "artifact_version_changed",
                "This file has changed since this change. Undo the later change first.", 409,
            ) from error
        except WorkspaceAuthorityError as error:
            raise ArtifactError(
                "artifact_undo_unavailable",
                "This change was made in another workspace and cannot be undone here.", 409,
            ) from error
        except WorkspaceEffectUncertain as error:
            raise ArtifactError(
                "artifact_undo_uncertain",
                "Undo may not have finished. Check the file before trying again.", 500,
            ) from error
        except HarnessError as error:
            raise ArtifactError(
                "artifact_undo_unavailable", "This change can no longer be undone.", 409,
            ) from error
        self._mark_undone(action)
        return {"pageId": page_id, "artifact": {**item, "undone": True, "state": "unchecked"}}

    def read(self, page_id: str, artifact_id: str) -> dict:
        """Preview an exact page-owned action's current, digest-matching file.

        Original versions are not reconstructed from checkpoints: those hold
        pre-images and have separate recovery authority. A missing, replaced,
        binary, protected, or oversized file returns no content.
        """
        item = self._resolve(page_id, artifact_id)
        response = {
            "pageId": page_id,
            "artifact": item,
            "state": "unavailable",
            "content": None,
            "observedSha256": None,
            "bytes": None,
            "redacted": False,
            "truncated": False,
            "downloadable": False,
            "reason": None,
            **self._limits(),
        }
        try:
            # Reuses the product's dirfd/no-follow path traversal, regular-file
            # check, hardlink rejection and bounded UTF-8 read.
            current = WorkspaceFiles(self.execution._kernel.workspace_root).read(item["path"])
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
        # The verified file is the source, not a serialized tool preview.
        # Literal omission markers in its text do not change its identity.
        content, truncated = clip_text(current["content"], MAX_PREVIEW_BYTES)
        response.update({
            "state": "current", "content": content,
            "redacted": False,
            "truncated": truncated,
            # A clipped but otherwise complete file may still be transferred.
            "downloadable": True,
        })
        response["artifact"] = {**item, "state": "current"}
        return response

    def download(self, page_id: str, artifact_id: str) -> dict:
        """Return verified original bytes, never an encoded document preview.

        Read once through the existing no-follow descriptor path. The
        validated preview and original bytes share the same snapshot.
        The returned bytes have the receipt's digest even if a writer replaces
        the filesystem path after this read. No pathname is reopened by HTTP.
        """
        item = self._resolve(page_id, artifact_id)
        try:
            current, data = WorkspaceFiles(self.execution._kernel.workspace_root).read_with_bytes(item["path"])
        except FileAccessError as error:
            raise ArtifactError(error.code, "This recorded file version is not available for download.", error.status) from error
        if current["sha256"] != item["sha256"]:
            raise ArtifactError("artifact_version_changed", "This file has changed since this page wrote it. Refresh the file preview.", 409)
        return {"artifact": item, "content": data}
