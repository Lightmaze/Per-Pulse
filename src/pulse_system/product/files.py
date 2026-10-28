"""Bounded, authenticated user access to the same native tool workspace.

User uploads are explicit create-only actions, not model tool approvals. This
is not a filesystem sandbox: it protects this API from path traversal, symlinks
and special files. Raw model/tool content is never rendered as executable HTML.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Iterator
from uuid import uuid4

from pulse_harness.core.secrets import looks_like_bare_credential

MAX_FILE_BYTES = 1024 * 1024
MAX_LIST_ENTRIES = 200
MAX_SCAN_ENTRIES = 4000
_SECRET_SUFFIXES = (".pem", ".key", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")


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
    if any(not p or p in {".", ".."} or p.startswith(".") or len(p) > 240
           or any(ord(c) < 32 for c in p) or any(c in p for c in '\\:*?"<>|')
           or p.casefold().endswith(_SECRET_SUFFIXES) or p.casefold().startswith("secrets.") for p in parts):
        raise FileAccessError("protected_or_invalid_path")
    return parts


def _text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileAccessError("text_files_only", 415) from None
    if "\x00" in text:
        raise FileAccessError("text_files_only", 415)
    if looks_like_bare_credential(text):
        raise FileAccessError("credential_content_blocked", 403)
    return text


class WorkspaceFiles:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    @contextmanager
    def directory(self, path: str = "") -> Iterator[int]:
        parts = _parts(path, root_ok=True)
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

    def list(self, path: str = "") -> dict:
        rows = []
        truncated = False
        with self.directory(path) as directory:
            with os.scandir(directory) as entries:
                for scanned, entry in enumerate(entries):
                    if scanned >= MAX_SCAN_ENTRIES or len(rows) >= MAX_LIST_ENTRIES:
                        truncated = True
                        break
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
        rows.sort(key=lambda row: (row["kind"] != "directory", row["name"].casefold()))
        return {"path": path, "entries": rows, "truncated": truncated,
                "limit_bytes": MAX_FILE_BYTES, "scope": "shared_native_workspace"}

    def read(self, path: str) -> dict:
        parts = _parts(path)
        with self.directory("/".join(parts[:-1])) as parent:
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise FileAccessError("not_a_single_regular_file", 403)
                    data = stream.read(MAX_FILE_BYTES + 1)
                    after = os.fstat(stream.fileno())
                    if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise FileAccessError("file_changed_retry", 409)
            except OSError:
                raise FileAccessError("file_unavailable", 404) from None
        if len(data) > MAX_FILE_BYTES:
            raise FileAccessError("file_too_large", 413)
        return {"path": path, "content": _text(data), "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def upload(self, name: str, data: bytes) -> dict:
        parts = _parts(name)
        if len(parts) != 1:
            raise FileAccessError("filename_only")
        if len(data) > MAX_FILE_BYTES:
            raise FileAccessError("file_too_large", 413)
        _text(data)
        # Common filesystems limit a basename in bytes, not Unicode characters.
        # Leave room for the native create-only prefix without breaking UTF-8.
        safe_name = re.sub(r"[^\w. -]", "_", name, flags=re.UNICODE).encode("utf-8")[:200].decode("utf-8", errors="ignore")
        target = f"{uuid4().hex[:12]}-{safe_name}"
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
