"""Bounded, authenticated user access to the same native tool workspace.

User uploads are explicit create-only actions, not model tool approvals. This
is not a filesystem sandbox: it protects this API from path traversal, symlinks
and special files. Raw model/tool content is never rendered as executable HTML.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Iterator
from uuid import uuid4

from pulse_harness.adapters.image_input import (
    MAX_IMAGE_BYTES, ImageInputError, image_metadata, image_data_url, is_image_path, model_image,
)
from pulse_harness.adapters.document_text import (
    MAX_PDF_BYTES, DocumentTextError, PdfText, is_pdf_path, read_pdf,
)

# POSIX opens each path component relative to an open directory descriptor.
# Windows has no dir_fd; it opens each component relative to an open
# directory handle instead (see winfiles). Elsewhere workspace files are
# unavailable rather than accessed by unanchored paths.
_WINDOWS = os.name == "nt"
_DIR_FD = (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
           and os.open in os.supports_dir_fd)
if _WINDOWS:
    from . import winfiles

MAX_FILE_BYTES = 1024 * 1024
MAX_LIST_ENTRIES = 200
MAX_SCAN_ENTRIES = 4000
# A filename extension does not establish a secret. User-selected .key text
# is handled like .env; internal state and symlink restrictions still apply.
_SECRET_SUFFIXES = (".pem", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")


class FileAccessError(ValueError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def _parts(path: str, *, root_ok: bool = False) -> list[str]:
    if root_ok and path == "":
        return []
    if not isinstance(path, str) or len(path) > 2048:
        raise FileAccessError("invalid_path")
    parts = path.split("/")
    if any(not p or p in {".", ".."} or p.casefold() in {".pulse", ".git"}
           or p.casefold().startswith((".pulse-harness-", ".upload-")) or len(p) > 240
           or any(ord(c) < 32 for c in p) or any(c in p for c in '\\:*?"<>|')
           or p.casefold().endswith(_SECRET_SUFFIXES) or p.casefold().startswith("secrets.") for p in parts):
        raise FileAccessError("protected_or_invalid_path")
    return parts


def upload_limit(name: str) -> int:
    """Largest accepted upload: text files are small; PDFs and photos are usually larger."""
    if is_pdf_path(name):
        return MAX_PDF_BYTES
    return MAX_IMAGE_BYTES if is_image_path(name) else MAX_FILE_BYTES


def _pdf_text(data: bytes) -> PdfText:
    try:
        return read_pdf(data)
    except DocumentTextError as error:
        raise FileAccessError(error.code, 415) from None


def _image(data: bytes) -> dict:
    try:
        return image_metadata(data)
    except ImageInputError as error:
        raise FileAccessError(error.code, 415) from error


def _text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileAccessError("text_files_only", 415) from None
    if "\x00" in text:
        raise FileAccessError("text_files_only", 415)
    return text


def _directory_revision(directory: int) -> list[int]:
    """Identity and entry-change stamp, read from the already anchored handle."""
    if _WINDOWS:
        info = winfiles.attributes(directory)
        return [int(info.volume), int(info.index_high), int(info.index_low),
                int(info.write.high), int(info.write.low)]
    info = os.fstat(directory)
    return [int(info.st_dev), int(info.st_ino), int(info.st_mtime_ns), int(info.st_ctime_ns)]


def _page_start(cursor: str | None, path: str, revision: list[int]) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or len(cursor) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
        raise FileAccessError("invalid_directory_cursor")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (ValueError, UnicodeError, binascii.Error):
        raise FileAccessError("invalid_directory_cursor") from None
    if (not isinstance(value, dict) or value.get("v") != 1 or value.get("path") != path
            or type(value.get("offset")) is not int or value["offset"] < 1):
        raise FileAccessError("invalid_directory_cursor")
    if value.get("revision") != revision:
        raise FileAccessError("directory_changed_retry", 409)
    return value["offset"]


def _page_cursor(path: str, offset: int, revision: list[int]) -> str:
    body = json.dumps({"v": 1, "path": path, "offset": offset, "revision": revision},
                      separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")


class WorkspaceFiles:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    @contextmanager
    def directory(self, path: str = "") -> Iterator[int]:
        parts = _parts(path, root_ok=True)
        if _WINDOWS:
            try:
                with winfiles.directory(str(self.root), parts) as handle:
                    yield handle
            except OSError:
                raise FileAccessError("path_unavailable", 404) from None
            return
        if not _DIR_FD:
            raise FileAccessError("workspace_files_unsupported", 501)
        fd = -1
        try:
            # Open each component by a directory descriptor; replacing a path
            # with a symlink while a request is in flight cannot redirect it.
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for part in parts:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            yield fd
        except OSError:
            raise FileAccessError("path_unavailable", 404) from None
        finally:
            if fd >= 0:
                os.close(fd)

    def list(self, path: str = "", *, cursor: str | None = None) -> dict:
        if _WINDOWS:
            return self._list_windows(path, cursor=cursor)
        with self.directory(path) as directory:
            revision = _directory_revision(directory)
            start = _page_start(cursor, path, revision)
            rows = []
            next_offset = start
            scanned_this_page = 0
            has_more = False
            with os.scandir(directory) as entries:
                # Cursor counts raw directory entries, including protected ones.
                # This retains a bounded page without persisting a second index
                # or ever treating the first 4,000 entries as a lifetime cap.
                for offset, entry in enumerate(entries):
                    if offset < start:
                        continue
                    if scanned_this_page >= MAX_SCAN_ENTRIES or len(rows) >= MAX_LIST_ENTRIES:
                        has_more = True
                        break
                    scanned_this_page += 1
                    next_offset = offset + 1
                    relative = f"{path}/{entry.name}" if path else entry.name
                    try:
                        _parts(relative)
                        info = entry.stat(follow_symlinks=False)
                        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                            continue
                        rows.append({"name": entry.name, "path": relative,
                                     "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file",
                                     "bytes": info.st_size if stat.S_ISREG(info.st_mode) else None})
                    except (OSError, FileAccessError):
                        continue
            if _directory_revision(directory) != revision:
                raise FileAccessError("directory_changed_retry", 409)
        rows.sort(key=lambda row: (row["kind"] != "directory", row["name"].casefold(), row["name"]))
        return {"path": path, "entries": rows, "truncated": has_more,
                "nextCursor": _page_cursor(path, next_offset, revision) if has_more else None,
                "limit_bytes": MAX_FILE_BYTES, "scope": "shared_native_workspace"}

    def _list_windows(self, path: str, *, cursor: str | None = None) -> dict:
        with self.directory(path) as directory:
            revision = _directory_revision(directory)
            start = _page_start(cursor, path, revision)
            rows = []
            next_offset = start
            scanned_this_page = 0
            has_more = False
            for offset, (name, is_directory, size, reparse) in enumerate(winfiles.list_directory(directory)):
                if offset < start:
                    continue
                if scanned_this_page >= MAX_SCAN_ENTRIES or len(rows) >= MAX_LIST_ENTRIES:
                    has_more = True
                    break
                scanned_this_page += 1
                next_offset = offset + 1
                relative = f"{path}/{name}" if path else name
                if reparse:
                    continue
                try:
                    _parts(relative)
                except FileAccessError:
                    continue
                rows.append({"name": name, "path": relative,
                             "kind": "directory" if is_directory else "file",
                             "bytes": None if is_directory else size})
            if _directory_revision(directory) != revision:
                raise FileAccessError("directory_changed_retry", 409)
        rows.sort(key=lambda row: (row["kind"] != "directory", row["name"].casefold(), row["name"]))
        return {"path": path, "entries": rows, "truncated": has_more,
                "nextCursor": _page_cursor(path, next_offset, revision) if has_more else None,
                "limit_bytes": MAX_FILE_BYTES, "scope": "shared_native_workspace"}

    def read_bytes(self, path: str) -> bytes:
        """The file's original bytes, bounded by ``upload_limit``."""
        parts = _parts(path)
        limit = upload_limit(path)
        with self.directory("/".join(parts[:-1])) as parent:
            try:
                if _WINDOWS:
                    fd = winfiles.open_file_read(parent, parts[-1])
                else:
                    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise FileAccessError("not_a_single_regular_file", 403)
                    data = stream.read(limit + 1)
                    after = os.fstat(stream.fileno())
                    if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise FileAccessError("file_changed_retry", 409)
            except OSError:
                raise FileAccessError("file_unavailable", 404) from None
        if len(data) > limit:
            raise FileAccessError("file_too_large", 413)
        return data

    def read(self, path: str) -> dict:
        """Text, PDF text, or a transient original-image preview with source identity."""
        return self.read_with_bytes(path)[0]

    def read_with_bytes(self, path: str) -> tuple[dict, bytes]:
        """Validated preview and original bytes from one bounded, anchored read."""
        data = self.read_bytes(path)
        result = {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if is_image_path(path):
            metadata = _image(data)
            # The preview is what a model receives: the original, or a scaled copy of a large one.
            view = model_image(data, metadata)
            return ({**result, "content": "", "format": "image", "image": metadata,
                     **({"previewImage": {"width": view.width, "height": view.height}} if view.scaled else {}),
                     "imageDataUrl": image_data_url(view.data, view.mime)}, data)
        if not is_pdf_path(path):
            return {**result, "content": _text(data)}, data
        pdf = _pdf_text(data)
        return {**result, "content": pdf.text, "format": "pdf_text", "complete": pdf.complete}, data

    def upload(self, name: str, data: bytes) -> dict:
        parts = _parts(name)
        if len(parts) != 1:
            raise FileAccessError("filename_only")
        if len(data) > upload_limit(name):
            raise FileAccessError("file_too_large", 413)
        if is_image_path(name):
            _image(data)
        elif is_pdf_path(name):
            _pdf_text(data)
        else:
            _text(data)
        safe_name = re.sub(r"[^\w. -]", "_", name, flags=re.UNICODE)
        # The filesystem limits bytes, not characters. Leave room for the
        # unique prefix even when the original name contains multibyte text,
        # and retain a short extension so the imported material stays usable.
        suffix = Path(safe_name).suffix
        if len(suffix.encode("utf-8")) > 16:
            suffix = ""
        stem = safe_name[:-len(suffix)] if suffix else safe_name
        safe_name = stem.encode("utf-8")[:160 - len(suffix.encode("utf-8"))].decode("utf-8", errors="ignore") + suffix
        target = f"{uuid4().hex[:12]}-{safe_name}"
        if _WINDOWS:
            return self._upload_windows(name, data, target)
        with self.directory() as root:
            try:
                os.mkdir("uploads", mode=0o700, dir_fd=root)
            except FileExistsError:
                pass
        with self.directory("uploads") as parent:
            # Write under an unlisted temporary name, then publish with a
            # create-only hard link. Readers never see a partial upload and an
            # unlikely collision never overwrites user or model work.
            temp = ".upload-" + uuid4().hex
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temp, target, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.unlink(temp, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                raise FileAccessError("upload_failed_or_unconfirmed", 503) from None
            finally:
                try:
                    os.unlink(temp, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return {"path": "uploads/" + target, "name": name, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "source": "user_upload"}

    def _upload_windows(self, name: str, data: bytes, target: str) -> dict:
        """Create and publish the upload relative to the open uploads directory handle."""
        with self.directory() as root:
            try:
                uploads = winfiles.open_directory(root, "uploads", create=True)
            except OSError:
                raise FileAccessError("path_unavailable", 404) from None
            try:
                winfiles.publish(uploads, ".upload-" + uuid4().hex, target, data)
            except OSError:
                raise FileAccessError("upload_failed_or_unconfirmed", 503) from None
            finally:
                winfiles.close(uploads)
        return {"path": "uploads/" + target, "name": name, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "source": "user_upload"}
