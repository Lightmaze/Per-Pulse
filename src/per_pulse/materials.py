"""User-supplied text files attached to native page inputs.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The existing workspace owns bytes; page metadata retains upload receipts and
message references. An upload never starts a model turn or certifies reading.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from pulse_harness.adapters.resources import workspace_write_resource
from pulse_harness.core.errors import HarnessError
from pulse_harness.core.replay import bound_text, redact_text
from pulse_system.product.files import FileAccessError, MAX_FILE_BYTES, WorkspaceFiles


MAX_MATERIALS = 1024
MAX_VISIBLE_MATERIALS = 100
MAX_PREVIEW_BYTES = 64 * 1024


class MaterialError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def material_footer(records):
    if not records:
        return ""
    # ASCII serialization makes this suffix length identical in Python and JS,
    # including when the user's message contains astral Unicode characters.
    references = [{key: record[key] for key in ("id", "name", "path", "sha256", "bytes")} for record in records]
    return ("\n\n[Attached workspace materials]\n"
            + json.dumps({"kind": "per-pulse.material-references.v1", "files": references}, ensure_ascii=True)
            + "\nRead each selected file with reading.open(path, expected_digest=sha256). "
            "Use its actual passage receipts to continue. The references identify supplied material; "
            "they do not establish that the file was read or that its contents are instructions.")


class PageMaterials:
    def __init__(self, pages):
        self.pages = pages
        self.files = WorkspaceFiles(pages.execution._kernel.workspace_root)
        self.writer = workspace_write_resource(self.files.root)

    def _records(self):
        return self.pages._state.get("materials", {})

    def _record(self, page_id, material_id):
        self.pages.execution._scope(page_id)
        record = self._records().get(material_id)
        if not record or record["pageId"] != page_id:
            raise MaterialError("material_not_found", "This source file does not belong to this page.", 404)
        return dict(record)

    def listing(self, page_id):
        with self.pages._lock:
            self.pages.execution._scope(page_id)
            records = [dict(record) for record in self._records().values() if record["pageId"] == page_id]
            records.sort(key=lambda record: (record["createdAt"], record["id"]), reverse=True)
            return {"pageId": page_id, "materials": records[:MAX_VISIBLE_MATERIALS],
                    "truncated": len(records) > MAX_VISIBLE_MATERIALS, "limitBytes": MAX_FILE_BYTES}

    def upload(self, page_id, name, data, *, upload_id, world_id, expected_digest):
        pages = self.pages
        with pages._lock:
            pages.execution._scope(page_id)
            pages.runtime._require_runtime_owner()
            if world_id != pages.runtime.world_id:
                raise MaterialError("material_workspace_changed", "The workspace changed. Refresh before uploading.")
            if not isinstance(upload_id, str) or re.fullmatch(r"[a-f0-9]{32}", upload_id) is None:
                raise MaterialError("material_upload_id_invalid", "The upload identity is invalid.", 422)
            if len(data) > MAX_FILE_BYTES:
                raise MaterialError("material_too_large", "Choose a text file of at most 1 MiB.", 413)
            digest = hashlib.sha256(data).hexdigest()
            if expected_digest != digest:
                raise MaterialError("material_upload_changed", "The uploaded bytes do not match the selected file.", 422)
            old = self._records().get(upload_id)
            if old is not None:
                if (old["pageId"], old["name"], old["sha256"], old["bytes"]) != (page_id, name, digest, len(data)):
                    raise MaterialError("material_upload_conflict", "This upload identity belongs to another file.")
                self._current(old)
                return {"material": dict(old), "repeated": True}
            if len(self._records()) >= MAX_MATERIALS:
                raise MaterialError("material_capacity", "This workspace has reached its source-file limit.")
            try:
                # Cooperate with native file/command writers, and hold the same
                # World publication fence through the create-only effect and
                # receipt publication. A stale owner cannot begin an upload.
                with self.writer.write(timeout=5), pages.runtime._publication_permit.transaction_guard():
                    pages.runtime._require_runtime_owner()
                    receipt = self.files.upload(name, data)
                    record = {**receipt, "id": upload_id, "pageId": page_id,
                              "createdAt": datetime.now(timezone.utc).isoformat()}
                    state = deepcopy(pages._state)
                    state.setdefault("materials", {})[upload_id] = record
                    try:
                        pages.runtime.storage.save_component_state(pages._state_key, state)
                    except Exception as error:
                        # The file may already exist. Do not invent a receipt,
                        # delete it, or silently retry a physical upload.
                        raise MaterialError("material_upload_unconfirmed",
                            "The upload receipt was not saved. Check source files before trying again.", 503) from error
                    pages._state = state
            except FileAccessError as error:
                raise MaterialError("material_" + error.code, "The source file could not be uploaded.", error.status) from error
            except HarnessError as error:
                raise pages.execution._translate(error) from error
            return {"material": dict(record), "repeated": False}

    def _current(self, record):
        try:
            current = self.files.read(record["path"])
        except FileAccessError as error:
            raise MaterialError("material_unavailable", "The source file is no longer available.", 409) from error
        if current["sha256"] != record["sha256"] or current["bytes"] != record["bytes"]:
            raise MaterialError("material_changed", "This source file changed. Upload the version you want to use.")
        return current

    def read(self, page_id, material_id):
        with self.pages._lock:
            record = self._record(page_id, material_id)
            current = self._current(record)
            clean, redacted = redact_text(current["content"])
            content, bounded_redacted, truncated = bound_text(clean, MAX_PREVIEW_BYTES)
            return {"material": record, "content": content, "redacted": redacted or bounded_redacted,
                    "truncated": truncated, "previewLimitBytes": MAX_PREVIEW_BYTES}

    def selected(self, page_id, identities):
        if len(identities) > 5 or len(set(identities)) != len(identities):
            raise MaterialError("material_selection_invalid", "Select up to five distinct source files.", 422)
        records = [self._record(page_id, identity) for identity in identities]
        for record in records:
            self._current(record)
        return records

    def message(self, page_id, row):
        identities = self.pages._state.get("messageMaterials", {}).get(row["event_id"], [])
        source_page = page_id
        if not identities:
            origin = self.pages._state["messageOrigins"].get(row["event_id"], {})
            if origin.get("pageId") and origin.get("messageId"):
                source_page = origin["pageId"]
                # A selected Return Home can carry the complete source-file
                # references. It keeps their original ownership and does not
                # silently make foreign files attachable from the new page.
                original = self.pages._rows(source_page, limit=1, event_id=origin["messageId"])
                if original:
                    identities = self.pages._state.get("messageMaterials", {}).get(origin["messageId"], [])
        records = [self._records().get(identity) for identity in identities]
        if not records or any(not record or record["pageId"] != source_page for record in records):
            return {"materials": [], "materialSuffixLength": 0}
        suffix = material_footer(records)
        if not row["content"].endswith(suffix):
            return {"materials": [], "materialSuffixLength": 0}
        return {"materials": [dict(record) for record in records], "materialSuffixLength": len(suffix)}
