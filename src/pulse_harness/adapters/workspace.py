"""A real, structured, checkpoint-first workspace tool adapter.

This adapter is deliberately independent of ``pulse_system`` and of any
shell, PTY, process, model, or network implementation.  It exposes one
read-only tool and one structured UTF-8 file mutation tool to the independent
Harness.  The adapter is given a canonical ``HarnessScope`` by the runner for
every call; it never stores a mutable "current scope".

The workspace boundary is conservative by design:

* paths are relative to an explicitly configured root;
* absolute paths, traversal, reparse/symlink components, ``.pulse`` and
  configured protected paths are rejected;
* the expected digest is checked before checkpointing and immediately before
  replacement;
* the pre-image is checkpointed outside the workspace before the replace;
* cooperating providers in this process share one workspace write lane across
  validation, checkpointing, replacement, verification and restoration;
* replacement uses a same-directory temporary file and a fixed-parent,
  handle-relative atomic rename (POSIX ``dir_fd`` or the Windows native
  ``FILE_RENAME_INFORMATION`` primitive through ``NtSetInformationFile``);
* any failure after the physical replace is attempted is classified as
  ``UNCERTAIN`` and is never automatically replayed.

The checkpoint directory contains raw pre-image bytes only for files that pass
the high-confidence credential guard.  A file containing a detected bare
credential is readable with redacted output but cannot be mutated, preventing
the checkpoint from becoming an ungoverned secret store.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from typing import Any, Mapping, Sequence
from weakref import ref

from .resources import workspace_write_resource
from ..core.changes import ChangePreview
from ..core.contracts import (
    ActionStatus,
    ChangeRecord,
    CheckpointRecord,
    HarnessScope,
    OperationStatus,
    ToolCall,
    ToolDescriptor,
)
from ..core.errors import (
    HarnessError,
    EffectUnknown,
    IdempotencyConflict,
    PathViolation,
    ProviderProtocolError,
    RedactionRequired,
    ScopeConflict,
)
from ..core.events import EventDraft
from ..core.provider import CancellationToken, CancellationRequested
from ..core.replay import bound_text, digest_json, redact_text
from ..core.secrets import looks_like_bare_credential
from ..core.session_store import HarnessStore


# Windows uses this flag for junctions and other reparse points.  POSIX
# symlinks are detected with lstat below.  The check is intentionally applied
# to every existing component, not just to the final file.
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_DRIVE_PATH = re.compile(r"^[A-Za-z]:($|/)")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_ID = re.compile(r"^cp-[0-9a-f]{32}$")
_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()
_WINDOWS_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "clock$",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_INVALID_WINDOWS_NAME_CHARS = frozenset('<>:"/\\|?*')
_STREAM_CHUNK_BYTES = 64 * 1024

# These registries deliberately live only in the current Python process.  A
# restart may rebind after revalidating the durable authority manifest, while
# a second live Store/provider cannot silently borrow the first provider's
# effect authority.  This is an in-process trust boundary, not a MAC or an
# administrator-tamper proof.
_ACTIVE_STORAGE_OWNERS: dict[str, Any] = {}
_ACTIVE_ACTION_OWNERS: dict[str, Any] = {}


class WorkspaceAdapterError(HarnessError):
    """Base error for a structured workspace adapter request."""

    code = "workspace_adapter_error"


class WorkspaceValidationError(PathViolation):
    """The request or configured path is outside the adapter contract."""

    code = "path_violation"


class WorkspaceDigestConflict(WorkspaceAdapterError):
    """The expected file image no longer matches the workspace."""

    code = "workspace_digest_conflict"


class WorkspaceEffectUncertain(WorkspaceAdapterError):
    """The physical replace boundary was crossed without a proof of state."""

    code = "workspace_effect_uncertain"


class WorkspaceAtomicReplaceUnsupported(WorkspaceAdapterError):
    """The host cannot provide the required handle-relative replace primitive."""

    code = "workspace_atomic_replace_unsupported"


class WorkspaceEffectUnknown(EffectUnknown):
    """The effect may exist but durable terminalization was not confirmed."""

    code = "effect_unknown"


class WorkspaceAuthorityError(WorkspaceAdapterError):
    """The action/checkpoint is not bound to this exact workspace authority."""

    code = "workspace_authority_invalid"


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    exists: bool
    data: bytes
    digest: str | None
    identity: tuple[int, int, int] | None = None

    @property
    def text(self) -> str:
        if not self.exists:
            return ""
        try:
            return self.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceValidationError("workspace file is not valid UTF-8", code="file_not_utf8") from exc


@dataclass(frozen=True, slots=True)
class _MutationPlan:
    action_request_id: str
    tool_call_id: str
    relative_path: str
    target: Path
    before: _FileSnapshot
    after_data: bytes
    after_digest: str
    diff: str
    diff_digest: str
    change_id: str
    checkpoint_id: str
    operation_id: str
    parent_identity: tuple[int, int]


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_reparse(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _reject_reparse_ancestors(path: Path) -> None:
    """Reject a root whose existing parent chain contains a reparse point."""

    current = Path(os.path.abspath(path))
    while True:
        if os.path.lexists(current) and _is_reparse(current):
            raise WorkspaceValidationError(
                "workspace root ancestry contains a reparse point",
                code="reparse_root_denied",
            )
        parent = current.parent
        if parent == current:
            return
        current = parent


def _path_identity(path: Path, *, directory: bool | None = None) -> tuple[int, int, int]:
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceValidationError(
            "workspace authority path is unavailable",
            code="workspace_authority_unavailable",
        ) from exc
    if _is_reparse(path):
        raise WorkspaceValidationError(
            "workspace authority path is a reparse point",
            code="reparse_path_denied",
        )
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise WorkspaceValidationError(
            "workspace authority path is not a directory",
            code="workspace_authority_unavailable",
        )
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise WorkspaceValidationError(
            "workspace authority path is not a regular file",
            code="workspace_authority_unavailable",
        )
    if os.name == "nt":
        handle = _windows_open_handle(path, directory=directory is True, write=False)
        try:
            return _windows_handle_identity(handle)
        finally:
            _windows_close_handle(handle)
    return (
        int(getattr(info, "st_dev", 0)),
        int(getattr(info, "st_ino", 0)),
        int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))),
    )


def _parent_identity(path: Path) -> tuple[int, int]:
    if os.name == "nt":
        handle = _windows_open_handle(path, directory=True)
        try:
            identity = _windows_handle_identity(handle)
        finally:
            _windows_close_handle(handle)
        return identity[:2]
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceValidationError("workspace parent is unavailable", code="workspace_parent_unavailable") from exc
    if _is_reparse(path) or not stat.S_ISDIR(info.st_mode):
        raise WorkspaceValidationError("workspace parent is not a regular directory", code="reparse_path_denied")
    return int(getattr(info, "st_dev", 0)), int(getattr(info, "st_ino", 0))


def _stable_path_identity(path: Path, *, directory: bool) -> tuple[int, int]:
    """Return an identity that remains stable while children/content change."""

    return _path_identity(path, directory=directory)[:2]


def _stable_operation_id(scope: HarnessScope, action_request_id: str) -> str:
    seed = f"{scope.digest}:{action_request_id}".encode("utf-8")
    return f"operation-{hashlib.sha256(seed).hexdigest()[:32]}"


def _canonical_component(value: str) -> str:
    if not value:
        raise WorkspaceValidationError(
            "workspace path contains an empty component",
            code="path_invalid",
        )
    if value.endswith((".", " ")):
        raise WorkspaceValidationError(
            "workspace path uses a Win32 trailing-dot or trailing-space alias",
            code="path_alias_denied",
        )
    if any(
        character in _INVALID_WINDOWS_NAME_CHARS or ord(character) < 32
        for character in value
    ):
        raise WorkspaceValidationError(
            "workspace path contains an invalid Win32 filename character",
            code="path_invalid",
        )
    canonical = value.casefold()
    if canonical.split(".", 1)[0] in _WINDOWS_DEVICE_NAMES:
        raise WorkspaceValidationError(
            "workspace path names a reserved device",
            code="device_name_denied",
        )
    return canonical


def _canonical_relative_components(
    value: Any,
    *,
    allow_pulse: bool = False,
) -> tuple[str, tuple[str, ...]]:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceValidationError("workspace path is required", code="path_required")
    if "\x00" in value:
        raise WorkspaceValidationError("workspace path contains NUL", code="path_invalid")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//") or _DRIVE_PATH.match(normalized):
        raise WorkspaceValidationError("absolute workspace paths are denied", code="absolute_path_denied")
    parts: list[str] = []
    canonical_parts: list[str] = []
    for raw_part in normalized.split("/"):
        if raw_part in {"", "."}:
            continue
        if raw_part == "..":
            raise WorkspaceValidationError("workspace path traversal is denied", code="path_traversal_denied")
        parts.append(raw_part)
        canonical = _canonical_component(raw_part)
        canonical_parts.append(canonical)
        if not allow_pulse and canonical == ".pulse":
            raise WorkspaceValidationError(".pulse is protected", code="protected_path_denied")
    if not parts:
        raise WorkspaceValidationError("workspace path resolves to the root", code="path_invalid")
    return "/".join(parts), tuple(canonical_parts)


def _win32_long_path(path: Path) -> str | None:
    """Resolve an existing Win32 short-name alias without invoking a shell."""

    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetLongPathNameW(
            str(path), buffer, len(buffer)
        )
    except (AttributeError, OSError):
        return None
    if not length or length >= len(buffer):
        return None
    return buffer.value


def _reject_existing_aliases(root: Path, relative_path: str) -> None:
    """Reject existing 8.3/alternate components whose Win32 name differs."""

    if os.name != "nt":
        return
    current = root
    for requested in relative_path.split("/"):
        current = current / requested
        if not os.path.lexists(current):
            break
        long_name = _win32_long_path(current)
        if long_name is None:
            raise WorkspaceValidationError(
                "workspace path alias could not be verified",
                code="path_alias_unverified",
            )
        actual = os.path.basename(long_name.rstrip("\\/"))
        if actual.casefold() != requested.casefold():
            raise WorkspaceValidationError(
                "workspace path uses a Win32 short-name alias",
                code="path_alias_denied",
            )


def _windows_handle_identity(handle: Any) -> tuple[int, int, int]:
    """Read a stable volume/file identity from an already-open Windows handle."""

    if os.name != "nt":  # pragma: no cover - platform guard
        raise WorkspaceAtomicReplaceUnsupported("Windows handles are unavailable")
    try:
        import ctypes
        from ctypes import wintypes

        class _FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        class _ByHandleInfo(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("creation", _FileTime),
                ("access", _FileTime),
                ("write", _FileTime),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD),
                ("index_low", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel32.GetFileInformationByHandle
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleInfo)]
        query.restype = wintypes.BOOL
        info = _ByHandleInfo()
        if not query(handle, ctypes.byref(info)):
            error = ctypes.get_last_error()
            raise OSError(error, "GetFileInformationByHandle failed")
        file_index = (int(info.index_high) << 32) | int(info.index_low)
        creation = (int(info.creation.high) << 32) | int(info.creation.low)
        return int(info.volume), file_index, creation
    except (AttributeError, OSError) as exc:
        raise WorkspaceAtomicReplaceUnsupported(
            "Windows file identity API is unavailable",
            code="workspace_handle_identity_unsupported",
        ) from exc


def _windows_open_handle(
    path: Path,
    *,
    directory: bool,
    write: bool = False,
    delete: bool = False,
) -> Any:
    """Open a directory/file with delete sharing for handle-relative rename."""

    if os.name != "nt":  # pragma: no cover - platform guard
        raise WorkspaceAtomicReplaceUnsupported("Windows handles are unavailable")
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create.restype = wintypes.HANDLE
        generic_read = 0x80000000
        generic_write = 0x40000000
        delete_access = 0x00010000
        file_list_directory = 0x0001
        file_read_attributes = 0x0080
        share_read = 0x00000001
        share_write = 0x00000002
        share_delete = 0x00000004
        open_existing = 3
        file_flag_backup_semantics = 0x02000000
        file_flag_open_reparse_point = 0x00200000
        desired = (
            file_list_directory
            | file_read_attributes
            | (delete_access if delete else 0)
            if directory
            else generic_read
            | (delete_access if delete else 0)
            | (generic_write if write else 0)
        )
        flags = file_flag_open_reparse_point
        if directory:
            flags |= file_flag_backup_semantics
        handle = create(
            str(path),
            desired,
            share_read | share_write | share_delete,
            None,
            open_existing,
            flags,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if not handle or getattr(handle, "value", handle) == invalid:
            error = ctypes.get_last_error()
            raise OSError(error, "CreateFileW failed")
        return handle
    except (AttributeError, OSError) as exc:
        raise WorkspaceAtomicReplaceUnsupported(
            "Windows handle-relative workspace access is unavailable",
            code="workspace_handle_open_unsupported",
        ) from exc


def _windows_close_handle(handle: Any) -> bool:
    if handle is None or os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close = kernel32.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        return bool(close(handle))
    except (AttributeError, OSError):
        # A close failure is handled by the enclosing mutation as an
        # effect-uncertain condition when the rename boundary was crossed.
        return False


def _windows_rename_by_directory_handle(
    source_handle: Any,
    parent_handle: Any,
    target_name: str,
) -> None:
    """Atomically rename an opened file into a fixed directory handle."""

    if os.name != "nt":  # pragma: no cover - platform guard
        raise WorkspaceAtomicReplaceUnsupported("Windows handles are unavailable")
    try:
        import ctypes
        from ctypes import wintypes

        class _FileRenameInfo(ctypes.Structure):
            _fields_ = [
                ("replace_if_exists", ctypes.c_ubyte),
                ("padding", ctypes.c_ubyte * 7),
                ("root_directory", wintypes.HANDLE),
                ("file_name_length", wintypes.DWORD),
                ("file_name", ctypes.c_ushort * 1),
            ]

        class _IoStatusBlock(ctypes.Structure):
            _fields_ = [
                ("status", ctypes.c_long),
                ("information", ctypes.c_size_t),
            ]

        encoded_name = target_name.encode("utf-16-le")
        name_offset = _FileRenameInfo.file_name.offset
        buffer = ctypes.create_string_buffer(name_offset + len(encoded_name))
        info = ctypes.cast(buffer, ctypes.POINTER(_FileRenameInfo)).contents
        info.replace_if_exists = 1
        info.root_directory = parent_handle
        info.file_name_length = len(encoded_name)
        ctypes.memmove(
            ctypes.addressof(info) + name_offset,
            encoded_name,
            len(encoded_name),
        )
        # SetFileInformationByHandle on the current Windows/filesystem
        # combination rejects RootDirectory-relative FileRenameInfo with
        # ERROR_INVALID_PARAMETER.  NtSetInformationFile accepts the same
        # documented FILE_RENAME_INFORMATION shape and preserves the crucial
        # handle-relative destination semantics, so no path-based fallback is
        # permitted here.
        ntdll = ctypes.WinDLL("ntdll")
        set_info = ntdll.NtSetInformationFile
        set_info.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IoStatusBlock),
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        set_info.restype = ctypes.c_long
        io_status = _IoStatusBlock()
        status = set_info(
            source_handle,
            ctypes.byref(io_status),
            ctypes.byref(info),
            len(buffer),
            10,  # FileRenameInformation
        )
        if status != 0:
            raise OSError(status, "NtSetInformationFile rename failed")
    except AttributeError as exc:
        raise WorkspaceAtomicReplaceUnsupported(
            "Windows handle-relative rename API is unavailable",
            code="workspace_atomic_replace_unsupported",
        ) from exc


def _normalize_relative_path(value: Any, *, allow_pulse: bool = False) -> str:
    normalized, _ = _canonical_relative_components(value, allow_pulse=allow_pulse)
    return normalized


def _safe_observer_text(value: str, max_bytes: int) -> tuple[str, bool, bool]:
    """Redact a read/diff view without ever persisting raw high-confidence secrets."""

    clean, redacted = redact_text(value)
    if looks_like_bare_credential(clean):
        clean = "[REDACTED]"
        redacted = True
    try:
        bounded, bound_redacted, truncated = bound_text(clean, max_bytes)
    except RedactionRequired:
        bounded, bound_redacted, truncated = "[REDACTED]", True, False
    return bounded, bool(redacted or bound_redacted), truncated


def _unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write a file through a same-directory temporary and replace."""

    path.parent.mkdir(parents=False, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".pulse-harness-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class StructuredWorkspaceToolProvider:
    """Provider-neutral ToolProvider for read and structured file mutation."""

    def __init__(
        self,
        store: HarnessStore,
        workspace_root: str | os.PathLike[str],
        checkpoint_root: str | os.PathLike[str],
        *,
        protected_paths: Sequence[str] = (),
    ) -> None:
        self.store = store
        workspace_input = Path(workspace_root).expanduser()
        checkpoint_input = Path(checkpoint_root).expanduser()
        _reject_reparse_ancestors(workspace_input)
        _reject_reparse_ancestors(checkpoint_input)
        if _is_reparse(workspace_input):
            raise WorkspaceValidationError("workspace root cannot be a symlink or reparse point", code="reparse_root_denied")
        if _is_reparse(checkpoint_input):
            raise WorkspaceValidationError("checkpoint root cannot be a symlink or reparse point", code="reparse_root_denied")
        workspace_input.mkdir(parents=True, exist_ok=True)
        checkpoint_input.mkdir(parents=True, exist_ok=True)
        if _is_reparse(workspace_input) or _is_reparse(checkpoint_input):
            raise WorkspaceValidationError("workspace roots cannot be reparse points", code="reparse_root_denied")
        self.workspace_root = workspace_input.resolve()
        self.checkpoint_root = checkpoint_input.resolve()
        if not self.workspace_root.is_dir() or not self.checkpoint_root.is_dir():
            raise WorkspaceValidationError("workspace roots must be directories", code="workspace_root_invalid")
        self._write_resource = workspace_write_resource(self.workspace_root)
        if _is_within(self.checkpoint_root, self.workspace_root) or _is_within(
            self.workspace_root, self.checkpoint_root
        ):
            raise WorkspaceValidationError(
                "checkpoint root must be disjoint from workspace root",
                code="checkpoint_root_inside_workspace",
            )
        self._authority_root = self.checkpoint_root / ".pulse-harness-authority"
        if _is_reparse(self._authority_root):
            raise WorkspaceValidationError(
                "workspace authority root cannot be a reparse point",
                code="reparse_root_denied",
            )
        self._authority_root.mkdir(parents=True, exist_ok=True)
        if _is_reparse(self._authority_root):
            raise WorkspaceValidationError(
                "workspace authority root cannot be a reparse point",
                code="reparse_root_denied",
            )
        configured: set[str] = set()
        for path in protected_paths:
            _, canonical = _canonical_relative_components(path, allow_pulse=True)
            configured.add("/".join(canonical))
        self._protected_paths = frozenset(configured)
        self._authority_token = object()
        self._bound_authorities: dict[str, object] = {}
        self._max_file_bytes = max(self.store.max_payload_bytes * 16, self.store.max_payload_bytes)
        self._workspace_provenance = _digest_bytes(
            _canonical_json(self._root_identity(self.workspace_root)).encode("utf-8")
        )
        self._checkpoint_provenance = _digest_bytes(
            _canonical_json(self._root_identity(self.checkpoint_root)).encode("utf-8")
        )

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (
            ToolDescriptor(
                name="read_file",
                description="Read one bounded UTF-8 file relative to the configured workspace.",
                mutable=False,
                requires_approval=False,
                capability="workspace.read",
                source="pulse_harness.workspace",
            ),
            ToolDescriptor(
                name="mutate_file",
                description="Apply one expected-digest guarded UTF-8 file mutation after approval. "
                            "Supply either complete content or patch={edits:[{old,new},...]}, never both. "
                            "Patch revises exact, uniquely matching text in the same file, in order; "
                            "unmentioned text is retained. Read the current source/digest before editing. "
                            "The original file-size, checkpoint, approval and recovery limits still apply.",
                mutable=True,
                requires_approval=True,
                capability="workspace.write",
                source="pulse_harness.workspace",
            ),
        )

    def preflight(self) -> Mapping[str, Any]:
        """Return observable adapter capabilities; no shell or process is involved."""

        return {
            "adapter": "pulse_harness.workspace.v1",
            "workspace_root": True,
            "checkpoint_root_outside_workspace": True,
            "utf8_regular_files": True,
            "atomic_replace": self._atomic_replace_supported(),
            "atomic_replace_mode": (
                "windows_handle_relative"
                if os.name == "nt"
                else "posix_dir_fd"
            ),
            "read_file": True,
            "mutate_file": True,
            "shell": False,
            "pty": False,
            "network": False,
            "workspace_provenance": self._workspace_provenance,
            "checkpoint_provenance": self._checkpoint_provenance,
            "authority_binding": "durable_manifest+process_local_store_provider_token",
            "workspace_write_state": "quarantined" if self._write_resource.unresolved_commands else "ready",
            "workspace_unresolved_commands": len(self._write_resource.unresolved_commands),
            "max_file_bytes": self._max_file_bytes,
        }

    def action_authority_identity(
        self,
        scope: HarnessScope,
        call: ToolCall,
        *,
        action_request_id: str,
    ) -> Mapping[str, Any]:
        """Return the stable workspace identity bound to one mutable action.

        This is deliberately durable identity data, not a live capability.
        The live provider token and action owner are still required by
        ``bind_action_authority``/``_require_action_authority`` before an
        effect.  A replacement provider rooted at another directory or store
        therefore cannot reuse the old action merely because its tool name is
        the same.
        """

        if call.name != "mutate_file":
            raise ProviderProtocolError(
                "workspace authority identity is only defined for mutate_file",
                code="action_authority_unavailable",
            )
        return {
            "adapter": "pulse_harness.workspace.v1",
            "action_request_id": action_request_id,
            "scope_digest": scope.digest,
            "workspace_authority_digest": digest_json(
                dict(self._authority_payload(scope, action_request_id))
            ),
            "workspace_provenance": self._workspace_provenance,
            "checkpoint_provenance": self._checkpoint_provenance,
            "storage_identity": self._storage_identity(),
        }

    @staticmethod
    def _root_identity(path: Path) -> Mapping[str, Any]:
        return {
            "path": os.path.normcase(str(path.resolve(strict=True))),
            "identity": list(_stable_path_identity(path, directory=True)),
        }

    def _storage_identity(self) -> Mapping[str, Any]:
        if self.store.path == ":memory:":
            return {"path": ":memory:", "identity": [id(self.store)]}
        path = Path(self.store.path).resolve(strict=True)
        return {
            "path": os.path.normcase(str(path)),
            "identity": list(_stable_path_identity(path, directory=False)),
        }

    @staticmethod
    def _authority_key(scope: HarnessScope, action_request_id: str) -> str:
        return hashlib.sha256(
            f"{scope.digest}:{action_request_id}".encode("utf-8")
        ).hexdigest()

    def _authority_path(self, scope: HarnessScope, action_request_id: str) -> Path:
        return self._authority_root / f"authority-{self._authority_key(scope, action_request_id)}.json"

    def _authority_payload(
        self,
        scope: HarnessScope,
        action_request_id: str,
    ) -> Mapping[str, Any]:
        return {
            "version": 1,
            "scope_digest": scope.digest,
            "action_request_id": action_request_id,
            "storage": self._storage_identity(),
            "workspace": self._root_identity(self.workspace_root),
            "checkpoint": self._root_identity(self.checkpoint_root),
        }

    def _load_authority_manifest(self, path: Path) -> Mapping[str, Any] | None:
        if not path.exists() and not os.path.lexists(path):
            return None
        if _is_reparse(path) or not path.is_file():
            raise WorkspaceAuthorityError(
                "workspace authority manifest is not a regular file",
                code="workspace_authority_invalid",
            )
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise WorkspaceAuthorityError(
                "workspace authority manifest is unreadable",
                code="workspace_authority_invalid",
            ) from exc
        if not isinstance(manifest, Mapping):
            raise WorkspaceAuthorityError(
                "workspace authority manifest is invalid",
                code="workspace_authority_invalid",
            )
        payload = manifest.get("payload")
        digest = manifest.get("authority_digest")
        if (
            not isinstance(payload, Mapping)
            or not isinstance(digest, str)
            or digest != digest_json(dict(payload))
        ):
            raise WorkspaceAuthorityError(
                "workspace authority manifest digest is invalid",
                code="workspace_authority_invalid",
            )
        return manifest

    def _register_authority_owner(self, authority_key: str) -> None:
        storage_key = _canonical_json(self._storage_identity())
        storage_owner_ref = _ACTIVE_STORAGE_OWNERS.get(storage_key)
        storage_owner = storage_owner_ref() if storage_owner_ref is not None else None
        if (
            storage_owner is not None
            and storage_owner is not self.store
            and not bool(getattr(storage_owner, "_closed", False))
        ):
            raise WorkspaceAuthorityError(
                "workspace authority belongs to another live HarnessStore",
                code="workspace_storage_conflict",
            )
        try:
            _ACTIVE_STORAGE_OWNERS[storage_key] = ref(self.store)
        except TypeError as exc:
            raise WorkspaceAuthorityError(
                "HarnessStore cannot carry an in-process authority owner",
                code="workspace_storage_conflict",
            ) from exc

        action_owner_ref = _ACTIVE_ACTION_OWNERS.get(authority_key)
        action_owner = action_owner_ref() if action_owner_ref is not None else None
        if (
            action_owner is not None
            and action_owner is not self
            and not bool(getattr(action_owner.store, "_closed", False))
        ):
            raise WorkspaceAuthorityError(
                "workspace action authority belongs to another live provider",
                code="workspace_authority_conflict",
            )
        try:
            _ACTIVE_ACTION_OWNERS[authority_key] = ref(self)
        except TypeError as exc:
            raise WorkspaceAuthorityError(
                "workspace provider cannot carry an in-process authority owner",
                code="workspace_authority_conflict",
            ) from exc
        self._bound_authorities[authority_key] = self._authority_token

    def bind_action_authority(
        self,
        scope: HarnessScope,
        call: ToolCall,
        *,
        action_request_id: str,
    ) -> None:
        """Bind a durable mutation before approval, using a local capability token."""

        action = self.store.get_action(scope, action_request_id)
        if action.tool_name != "mutate_file":
            return
        if action.args_digest != digest_json(dict(call.arguments)):
            raise IdempotencyConflict("workspace authority call differs from its action")
        durable_authority = self.store.get_action_authority(scope, action_request_id)
        if (
            durable_authority is None
            or not durable_authority.get("required")
            or durable_authority.get("provider_type")
            != f"{type(self).__module__}.{type(self).__qualname__}"
            or durable_authority.get("provider_source") != "pulse_harness.workspace"
        ):
            raise WorkspaceAuthorityError(
                "mutable workspace action has no compatible durable authority",
                code="workspace_authority_missing",
            )
        expected_identity = self.action_authority_identity(
            scope,
            call,
            action_request_id=action_request_id,
        )
        if durable_authority.get("identity") != dict(expected_identity):
            raise WorkspaceAuthorityError(
                "workspace provider identity differs from the durable action",
                code="workspace_authority_mismatch",
            )
        authority_key = self._authority_key(scope, action_request_id)
        payload = self._authority_payload(scope, action_request_id)
        path = self._authority_path(scope, action_request_id)
        manifest = self._load_authority_manifest(path)
        if manifest is None:
            if action.status not in {ActionStatus.INTENT, ActionStatus.APPROVAL_PENDING}:
                raise WorkspaceAuthorityError(
                    "approved workspace action has no durable authority",
                    code="workspace_authority_missing",
                )
            try:
                _atomic_write_bytes(
                    path,
                    _canonical_json(
                        {
                            "payload": payload,
                            "authority_digest": digest_json(dict(payload)),
                        }
                    ).encode("utf-8"),
                )
            except OSError as exc:
                raise WorkspaceAuthorityError(
                    "workspace authority could not be persisted",
                    code="workspace_authority_persist_failed",
                ) from exc
        else:
            if manifest.get("payload") != payload:
                raise WorkspaceAuthorityError(
                    "workspace action authority does not match this workspace",
                    code="workspace_authority_mismatch",
                )
        self._register_authority_owner(authority_key)

    def _require_action_authority(
        self,
        scope: HarnessScope,
        call: ToolCall,
        action_request_id: str,
        *,
        allow_recovery_rebind: bool = False,
    ) -> None:
        action = self.store.get_action(scope, action_request_id)
        if action.tool_name != "mutate_file" or action.args_digest != digest_json(dict(call.arguments)):
            raise IdempotencyConflict("workspace authority call differs from its action")
        durable_authority = self.store.get_action_authority(scope, action_request_id)
        if (
            durable_authority is None
            or not durable_authority.get("required")
            or durable_authority.get("provider_type")
            != f"{type(self).__module__}.{type(self).__qualname__}"
            or durable_authority.get("provider_source") != "pulse_harness.workspace"
        ):
            raise WorkspaceAuthorityError(
                "workspace action has no durable provider authority",
                code="workspace_authority_missing",
            )
        expected_identity = self.action_authority_identity(
            scope,
            call,
            action_request_id=action_request_id,
        )
        if durable_authority.get("identity") != dict(expected_identity):
            raise WorkspaceAuthorityError(
                "workspace action provider identity no longer matches",
                code="workspace_authority_mismatch",
            )
        authority_key = self._authority_key(scope, action_request_id)
        if self._bound_authorities.get(authority_key) is not self._authority_token:
            if not allow_recovery_rebind:
                raise WorkspaceAuthorityError(
                    "workspace action has no process-local authority binding",
                    code="workspace_authority_missing",
                )
            manifest = self._load_authority_manifest(
                self._authority_path(scope, action_request_id)
            )
            if manifest is None or manifest.get("payload") != self._authority_payload(
                scope, action_request_id
            ):
                raise WorkspaceAuthorityError(
                    "workspace action authority cannot be recovered",
                    code="workspace_authority_mismatch",
                )
            self._register_authority_owner(authority_key)
        manifest = self._load_authority_manifest(
            self._authority_path(scope, action_request_id)
        )
        if manifest is None or manifest.get("payload") != self._authority_payload(
            scope, action_request_id
        ):
            raise WorkspaceAuthorityError(
                "workspace action authority no longer matches this workspace",
                code="workspace_authority_mismatch",
            )

    def execute(
        self,
        call: ToolCall,
        cancellation: CancellationToken,
        *,
        scope: HarnessScope | None = None,
        action_request_id: str | None = None,
    ) -> Mapping[str, Any]:
        if scope is None:
            raise WorkspaceAdapterError(
                "workspace execution requires an explicit HarnessScope",
                code="scope_required",
            )
        return self.execute_for_scope(
            scope,
            call,
            cancellation,
            action_request_id=action_request_id,
        )

    def execute_for_scope(
        self,
        scope: HarnessScope,
        call: ToolCall,
        cancellation: CancellationToken,
        *,
        action_request_id: str | None = None,
    ) -> Mapping[str, Any]:
        if not isinstance(scope, HarnessScope):
            raise ScopeConflict("workspace adapter received an invalid HarnessScope")
        if not isinstance(call, ToolCall):
            raise ProviderProtocolError("workspace adapter received an invalid ToolCall")
        cancellation.raise_if_cancelled()
        if call.name == "read_file":
            return self._read(scope, call, cancellation, action_request_id=action_request_id)
        if call.name == "mutate_file":
            return self._mutate(scope, call, cancellation, action_request_id=action_request_id)
        raise ProviderProtocolError(
            f"workspace adapter does not implement tool {call.name}",
            code="tool_not_supported",
        )

    def read_file(self, scope: HarnessScope, path: str) -> Mapping[str, Any]:
        """Direct read API used by deterministic adapter tests and diagnostics."""

        return self._read(scope, ToolCall("direct-read", "read_file", {"path": path}), CancellationToken())

    def preview_mutation(
        self,
        scope: HarnessScope,
        *,
        action_request_id: str,
        path: str,
        expected_digest: str | None,
        content: str | None = None,
        patch: Any = None,
    ) -> ChangePreview:
        """Build a deterministic preview without touching the filesystem or store."""

        call = ToolCall(
            tool_call_id=f"preview:{action_request_id}",
            name="mutate_file",
            arguments={
                "path": path,
                "expected_digest": expected_digest,
                **({"content": content} if content is not None else {"patch": patch}),
            },
        )
        plan = self._plan(scope, call, action_request_id=action_request_id)
        change = ChangeRecord(
            change_id=plan.change_id,
            action_request_id=action_request_id,
            scope=scope,
            path=plan.relative_path,
            before_digest=plan.before.digest,
            after_digest=plan.after_digest,
            diff=plan.diff,
            status="preview",
        )
        return ChangePreview(change=change, applied=False)

    def restore(self, scope: HarnessScope, checkpoint_id: str) -> Mapping[str, Any]:
        """Restore a sealed checkpoint only when the post-image is still present."""

        # Restore and ordinary mutations share the same effect lane. This is
        # independent of the runtime publication fence and does not block reads.
        with self._write_resource.write():
            return self._restore_locked(scope, checkpoint_id)

    def _restore_locked(self, scope: HarnessScope, checkpoint_id: str) -> Mapping[str, Any]:
        if not isinstance(checkpoint_id, str) or _CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
            raise WorkspaceValidationError("checkpoint id is invalid", code="checkpoint_id_invalid")
        self.store.get_session(scope.session_scope())
        metadata_path = self.checkpoint_root / checkpoint_id / "metadata.json"
        if not metadata_path.is_file() or _is_reparse(metadata_path):
            raise WorkspaceAdapterError("checkpoint is not retained", code="checkpoint_not_found")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise WorkspaceAdapterError("checkpoint metadata is unreadable", code="checkpoint_metadata_invalid") from exc
        if not isinstance(metadata, Mapping) or metadata.get("format") != "pulse-harness-workspace.v1":
            raise WorkspaceAdapterError("checkpoint metadata is invalid", code="checkpoint_metadata_invalid")
        if metadata.get("checkpoint_id") != checkpoint_id or metadata.get("state") != "sealed":
            raise WorkspaceAdapterError("checkpoint metadata identity is invalid", code="checkpoint_metadata_invalid")
        if metadata.get("scope_digest") != scope.digest:
            raise ScopeConflict("checkpoint scope differs")
        action_request_id = metadata.get("action_request_id")
        operation_id = metadata.get("operation_id")
        if (
            not isinstance(action_request_id, str)
            or not action_request_id.strip()
            or not isinstance(operation_id, str)
            or not operation_id.strip()
        ):
            raise WorkspaceAdapterError("checkpoint action identity is invalid", code="checkpoint_metadata_invalid")
        action = self.store.get_action(scope, action_request_id)
        operation = self.store.get_operation(scope, operation_id)
        if (
            action.tool_name != "mutate_file"
            or action.operation_id != operation_id
            or action.status is not ActionStatus.SUCCEEDED
            or operation.action_request_id != action_request_id
            or operation.status is not OperationStatus.SUCCEEDED
        ):
            raise WorkspaceAdapterError(
                "checkpoint is not backed by a successful mutation",
                code="checkpoint_action_invalid",
            )
        self._require_action_authority(
            scope,
            ToolCall(
                tool_call_id=f"restore:{checkpoint_id}",
                name=action.tool_name,
                arguments=action.arguments,
            ),
            action_request_id,
            allow_recovery_rebind=True,
        )
        expected_authority_digest = digest_json(
            dict(self._authority_payload(scope, action_request_id))
        )
        if metadata.get("authority_digest") != expected_authority_digest:
            raise WorkspaceAuthorityError(
                "checkpoint authority does not match this workspace",
                code="workspace_authority_mismatch",
            )
        relative_path = _normalize_relative_path(metadata.get("path"))
        before_exists = metadata.get("before_exists")
        before_digest = metadata.get("before_digest")
        after_digest = metadata.get("after_digest")
        if not isinstance(before_exists, bool) or not isinstance(after_digest, str) or _HEX_DIGEST.fullmatch(after_digest) is None:
            raise WorkspaceAdapterError("checkpoint image metadata is invalid", code="checkpoint_metadata_invalid")
        if before_exists:
            if not isinstance(before_digest, str) or _HEX_DIGEST.fullmatch(before_digest) is None:
                raise WorkspaceAdapterError("checkpoint pre-image metadata is invalid", code="checkpoint_metadata_invalid")
        elif before_digest is not None:
            raise WorkspaceAdapterError("checkpoint empty pre-image metadata is invalid", code="checkpoint_metadata_invalid")
        action_result = action.result or {}
        durable_output = action_result.get("output") if isinstance(action_result, Mapping) else None
        if not isinstance(durable_output, Mapping) or any(
            durable_output.get(key) != value
            for key, value in {
                "checkpoint_id": checkpoint_id,
                "action_request_id": action_request_id,
                "operation_id": operation_id,
                "path": relative_path,
                "before_digest": before_digest,
                "after_digest": after_digest,
            }.items()
        ):
            raise WorkspaceAdapterError(
                "checkpoint metadata does not match durable mutation result",
                code="checkpoint_metadata_invalid",
            )
        target = self._safe_target(relative_path, allow_missing=True)
        parent_identity = _parent_identity(target.parent)
        current = self._snapshot(target)
        if current.digest != after_digest:
            raise WorkspaceDigestConflict("workspace no longer has the checkpoint post-image", code="restore_conflict")
        before_path = self.checkpoint_root / checkpoint_id / "before.bin"
        try:
            if before_exists:
                if not before_path.is_file() or _is_reparse(before_path):
                    raise WorkspaceAdapterError("checkpoint pre-image is missing", code="checkpoint_metadata_invalid")
                before_snapshot = self._snapshot(before_path)
                before_data = before_snapshot.data
                if not before_snapshot.exists or before_snapshot.digest != before_digest:
                    raise WorkspaceAdapterError("checkpoint pre-image digest is corrupt", code="checkpoint_metadata_invalid")
                if _parent_identity(target.parent) != parent_identity:
                    raise WorkspaceValidationError("workspace parent changed", code="workspace_boundary_lost")
                _atomic_write_bytes(target, before_data)
            else:
                if _parent_identity(target.parent) != parent_identity:
                    raise WorkspaceValidationError("workspace parent changed", code="workspace_boundary_lost")
                if target.exists() or os.path.lexists(target):
                    if _is_reparse(target):
                        raise WorkspaceValidationError("restore target is a reparse point", code="reparse_path_denied")
                    target.unlink()
            restored = self._snapshot(target)
        except WorkspaceAdapterError:
            raise
        except OSError as exc:
            raise WorkspaceEffectUncertain("checkpoint restore crossed an unconfirmed file boundary") from exc
        expected = before_digest if before_exists else None
        if restored.digest != expected:
            raise WorkspaceEffectUncertain("checkpoint restore did not reach the recorded pre-image")
        try:
            self.store.append_event(
                EventDraft(
                    scope=scope,
                    event_type="checkpoint.restored",
                    source="workspace",
                    phase="checkpoint",
                    status="checkpointed",
                    payload={
                        "checkpoint_id": checkpoint_id,
                        "path": relative_path,
                        "restored_digest": restored.digest,
                    },
                    action_request_id=action_request_id,
                    operation_id=operation_id,
                )
            )
        except Exception as exc:
            raise WorkspaceEffectUncertain(
                "checkpoint restore completed but its durable event was not confirmed"
            ) from exc
        return {
            "status": "restored",
            "checkpoint_id": checkpoint_id,
            "path": relative_path,
            "digest": restored.digest,
        }

    def list_checkpoints(self) -> tuple[Mapping[str, Any], ...]:
        records: list[Mapping[str, Any]] = []
        for directory in sorted(self.checkpoint_root.iterdir(), key=lambda item: item.name):
            if _CHECKPOINT_ID.fullmatch(directory.name) is None:
                continue
            metadata_path = directory / "metadata.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
            if not isinstance(metadata, Mapping):
                continue
            records.append(
                {
                    "checkpoint_id": directory.name,
                    "action_request_id": metadata.get("action_request_id"),
                    "path": metadata.get("path"),
                    "before_digest": metadata.get("before_digest"),
                    "after_digest": metadata.get("after_digest"),
                    "state": metadata.get("state", "sealed"),
                }
            )
        return tuple(records)

    def _read(
        self,
        scope: HarnessScope,
        call: ToolCall,
        cancellation: CancellationToken,
        *,
        action_request_id: str | None = None,
    ) -> Mapping[str, Any]:
        self.store.get_session(scope.session_scope())
        arguments = dict(call.arguments)
        if set(arguments) != {"path"}:
            raise ProviderProtocolError("read_file arguments are not canonical", code="tool_arguments_invalid")
        relative_path = _normalize_relative_path(arguments["path"])
        target = self._safe_target(relative_path, allow_missing=False)
        cancellation.raise_if_cancelled()
        snapshot = self._snapshot(target)
        output = {
            "path": relative_path,
            "digest": snapshot.digest,
            "workspace_provenance": {
                "adapter": "pulse_harness.workspace.v1",
                "workspace_digest": self._workspace_provenance,
            },
        }
        return self._bounded_text_output(
            output, snapshot.text, text_key="content", redacted_key="redacted",
            truncated_key="truncated", tool_call_id=call.tool_call_id,
            action_request_id=action_request_id, status="succeeded",
        )

    def _mutate(
        self,
        scope: HarnessScope,
        call: ToolCall,
        cancellation: CancellationToken,
        *,
        action_request_id: str | None,
    ) -> Mapping[str, Any]:
        # Different Kernel sessions have different providers but can share this
        # workspace. Re-plan only after acquiring the common lane: digest checks
        # followed by rename alone are not compare-and-swap across two writers.
        with self._write_resource.write(cancellation):
            return self._mutate_locked(
                scope, call, cancellation, action_request_id=action_request_id,
            )

    def _mutate_locked(
        self,
        scope: HarnessScope,
        call: ToolCall,
        cancellation: CancellationToken,
        *,
        action_request_id: str | None,
    ) -> Mapping[str, Any]:
        if not isinstance(action_request_id, str) or not action_request_id.strip():
            raise ProviderProtocolError("mutate_file requires action_request_id", code="tool_arguments_invalid")
        action = self.store.get_action(scope, action_request_id)
        if action.tool_name != call.name or action.args_digest != digest_json(dict(call.arguments)):
            raise IdempotencyConflict("mutation call differs from its durable action")
        self._require_action_authority(scope, call, action_request_id)
        stable_operation_id = _stable_operation_id(scope, action_request_id)
        try:
            existing_operation = self.store.get_operation(scope, stable_operation_id)
        except ScopeConflict:
            existing_operation = None
        if action.operation_id is not None and action.operation_id != stable_operation_id:
            raise IdempotencyConflict("mutation operation identity differs from its durable action")
        if (
            action.operation_id is not None
            and existing_operation is not None
            and existing_operation.status
            not in {
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
                OperationStatus.UNCERTAIN,
            }
        ):
            raise WorkspaceAdapterError(
                "workspace operation is already in progress; recovery is required",
                code="operation_in_progress",
            )
        if existing_operation is not None and existing_operation.status in {
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.UNCERTAIN,
        }:
            stored_result = existing_operation.result or {}
            if not isinstance(stored_result, Mapping):
                raise WorkspaceAdapterError(
                    "terminal operation result is not replayable",
                    code="operation_result_invalid",
                )
            stored_output = stored_result.get("output", stored_result)
            if not isinstance(stored_output, Mapping):
                raise WorkspaceAdapterError(
                    "terminal operation output is not replayable",
                    code="operation_result_invalid",
                )
            # A retry after a durable terminal is a pure replay.  It must
            # never recreate the physical effect or a second checkpoint.
            return dict(stored_output)
        plan = self._plan(scope, call, action_request_id=action_request_id)
        # A large diff is an optional preview, not permission to discard the
        # receipt's source/version identity. Verify that the compact result can
        # fit before checkpointing or replacing the file.
        output = self._mutation_output(scope, plan, status="succeeded")
        cancellation.raise_if_cancelled()
        operation = self.store.create_operation(
            scope,
            action_request_id,
            operation_id=plan.operation_id,
        )
        if operation.status is not OperationStatus.STARTING:
            raise WorkspaceAdapterError(
                "workspace operation is already in progress; recovery is required",
                code="operation_in_progress",
            )
        del operation
        action = self.store.get_action(scope, action_request_id)
        approval_id = action.approval_id

        self._emit(
            scope,
            event_type="change.preview",
            phase="change",
            status="preview",
            payload={
                "change_id": plan.change_id,
                "path": plan.relative_path,
                "before_digest": plan.before.digest,
                "after_digest": plan.after_digest,
                "diff": _safe_observer_text(plan.diff, self.store.max_payload_bytes)[0],
            },
            action_request_id=action_request_id,
            approval_id=approval_id,
            operation_id=plan.operation_id,
        )

        replace_attempted = False
        try:
            cancellation.raise_if_cancelled()
            self._require_action_authority(scope, call, action_request_id)
            self._write_checkpoint(plan, scope, action_request_id)
            self._emit(
                scope,
                event_type="operation.progress",
                phase="operation",
                status=OperationStatus.RUNNING.value,
                payload={"stage": "checkpoint_prepared", "operation_id": plan.operation_id},
                action_request_id=action_request_id,
                approval_id=approval_id,
                operation_id=plan.operation_id,
            )
            cancellation.raise_if_cancelled()
            self._require_action_authority(scope, call, action_request_id)
            self._verify_precommit(plan)
            self._replace(plan)
            replace_attempted = True
            after = self._snapshot(plan.target)
            if after.digest != plan.after_digest:
                raise WorkspaceEffectUncertain("workspace post-image digest could not be verified")
            self._emit(
                scope,
                event_type="operation.progress",
                phase="operation",
                status=OperationStatus.RUNNING.value,
                payload={"stage": "applied", "operation_id": plan.operation_id},
                action_request_id=action_request_id,
                approval_id=approval_id,
                operation_id=plan.operation_id,
            )
            cancellation.raise_if_cancelled()
            self.store.record_change(
                ChangeRecord(
                    change_id=plan.change_id,
                    action_request_id=action_request_id,
                    scope=scope,
                    path=plan.relative_path,
                    before_digest=plan.before.digest,
                    after_digest=plan.after_digest,
                    diff=plan.diff,
                    status="applied",
                )
            )
            self.store.record_checkpoint(
                CheckpointRecord(
                    checkpoint_id=plan.checkpoint_id,
                    action_request_id=action_request_id,
                    scope=scope,
                    path=plan.relative_path,
                    artifact_path=f"{plan.checkpoint_id}/before.bin",
                    content_digest=plan.before.digest or _EMPTY_DIGEST,
                    status="checkpointed",
                )
            )
            self.store.terminal_operation(
                scope,
                plan.operation_id,
                OperationStatus.SUCCEEDED,
                result={"output": output},
            )
            return output
        except CancellationRequested:
            status = OperationStatus.UNCERTAIN if replace_attempted else OperationStatus.CANCELLED
            error_code = "workspace_effect_uncertain" if replace_attempted else "action_cancelled"
            return self._finish_operation(
                scope,
                plan,
                action_request_id,
                approval_id,
                status,
                error_code,
            )
        except WorkspaceEffectUncertain:
            return self._finish_operation(
                scope,
                plan,
                action_request_id,
                approval_id,
                OperationStatus.UNCERTAIN,
                "workspace_effect_uncertain",
            )
        except (
            WorkspaceDigestConflict,
            WorkspaceValidationError,
            WorkspaceAtomicReplaceUnsupported,
            RedactionRequired,
            ProviderProtocolError,
        ) as exc:
            if replace_attempted:
                return self._finish_operation(
                    scope,
                    plan,
                    action_request_id,
                    approval_id,
                    OperationStatus.UNCERTAIN,
                    "workspace_effect_uncertain",
                )
            return self._finish_operation(
                scope,
                plan,
                action_request_id,
                approval_id,
                OperationStatus.FAILED,
                exc.code,
            )
        except (OSError, UnicodeError) as exc:
            return self._finish_operation(
                scope,
                plan,
                action_request_id,
                approval_id,
                OperationStatus.UNCERTAIN if replace_attempted else OperationStatus.FAILED,
                "workspace_effect_uncertain" if replace_attempted else "workspace_io_failed",
            )
        except Exception:
            return self._finish_operation(
                scope,
                plan,
                action_request_id,
                approval_id,
                OperationStatus.UNCERTAIN if replace_attempted else OperationStatus.FAILED,
                "workspace_effect_uncertain" if replace_attempted else "workspace_mutation_failed",
            )

    def _finish_operation(
        self,
        scope: HarnessScope,
        plan: _MutationPlan,
        action_request_id: str,
        approval_id: str | None,
        status: OperationStatus,
        error_code: str,
    ) -> Mapping[str, Any]:
        try:
            output = self._mutation_output(scope, plan, status=status.value, error_code=error_code)
        except Exception as exc:
            # Once the file boundary may have been crossed, even constructing
            # the durable result is part of terminalization.  Do not let a
            # provenance/authority/read failure escape to TurnRunner's normal
            # HarnessError handler, which would incorrectly mark the action
            # FAILED after a possible physical effect.
            if status in {OperationStatus.SUCCEEDED, OperationStatus.UNCERTAIN}:
                raise WorkspaceEffectUnknown(
                    "workspace effect crossed its boundary but its result was not confirmed"
                ) from exc
            raise
        try:
            self.store.terminal_operation(
                scope,
                plan.operation_id,
                status,
                result={"output": output},
                error_code=error_code,
            )
        except Exception as exc:
            raise WorkspaceEffectUnknown(
                "workspace effect crossed its boundary but durable terminalization was not confirmed"
            ) from exc
        return output

    def _mutation_output(
        self,
        scope: HarnessScope,
        plan: _MutationPlan,
        *,
        status: str,
        error_code: str | None = None,
    ) -> Mapping[str, Any]:
        output: dict[str, Any] = {
            "status": status,
            "action_request_id": plan.action_request_id,
            "path": plan.relative_path,
            "before_digest": plan.before.digest,
            "after_digest": plan.after_digest,
            "diff_digest": plan.diff_digest,
            "change_id": plan.change_id,
            "checkpoint_id": plan.checkpoint_id,
            "operation_id": plan.operation_id,
            "authority_digest": digest_json(
                dict(self._authority_payload(scope, plan.action_request_id))
            ),
            "workspace_provenance": {
                "adapter": "pulse_harness.workspace.v1",
                "workspace_digest": self._workspace_provenance,
            },
        }
        if error_code is not None:
            output["error_code"] = error_code
        return self._bounded_text_output(
            output, plan.diff, text_key="diff", redacted_key="diff_redacted",
            truncated_key="diff_truncated", tool_call_id=plan.tool_call_id,
            action_request_id=plan.action_request_id, status=status,
            error_code=error_code,
        )

    def _bounded_text_output(
        self,
        metadata: Mapping[str, Any],
        text: str,
        *,
        text_key: str,
        redacted_key: str,
        truncated_key: str,
        tool_call_id: str,
        action_request_id: str | None,
        status: str,
        error_code: str | None = None,
    ) -> Mapping[str, Any]:
        """Keep exact metadata while fitting the complete Kernel tool result.

        A UTF-8 text bound alone cannot bound JSON: quotes, backslashes and control
        characters expand when escaped. Include the normal transcript envelope
        and reduce only the observer text, retaining its digest/visibility
        marker. Never raise the store's bound or truncate authority fields.
        """
        budget = self.store.max_payload_bytes
        while True:
            preview, redacted, truncated = _safe_observer_text(text, budget)
            output = {**metadata, text_key: preview,
                      redacted_key: redacted, truncated_key: truncated}
            envelope = {"tool_call_id": tool_call_id,
                        "action_request_id": action_request_id, "status": status,
                        "output": output, "error_code": error_code}
            if len(_canonical_json(envelope).encode("utf-8")) <= self.store.max_payload_bytes:
                return output
            if budget == 128:
                raise ProviderProtocolError(
                    "workspace result metadata exceeds the durable bound",
                    code="payload_too_large",
                )
            budget = max(128, budget // 2)

    def _emit(
        self,
        scope: HarnessScope,
        *,
        event_type: str,
        phase: str,
        status: str,
        payload: Mapping[str, Any],
        action_request_id: str,
        approval_id: str | None,
        operation_id: str,
    ) -> None:
        self.store.append_event(
            EventDraft(
                scope=scope,
                event_type=event_type,
                source="workspace",
                phase=phase,
                status=status,
                payload=payload,
                action_request_id=action_request_id,
                approval_id=approval_id,
                operation_id=operation_id,
            )
        )

    def _is_protected_relative(self, relative_path: str) -> bool:
        _, canonical = _canonical_relative_components(relative_path)
        candidate = "/".join(canonical)
        return any(
            candidate == protected or candidate.startswith(f"{protected}/")
            for protected in self._protected_paths
        )

    def _plan(
        self,
        scope: HarnessScope,
        call: ToolCall,
        *,
        action_request_id: str | None = None,
    ) -> _MutationPlan:
        arguments = dict(call.arguments)
        expected_keys = {"path", "expected_digest", "content", "patch"}
        if set(arguments).difference(expected_keys):
            raise ProviderProtocolError("mutate_file arguments are not canonical", code="tool_arguments_invalid")
        if "path" not in arguments or "expected_digest" not in arguments:
            raise ProviderProtocolError("mutate_file requires path and expected_digest", code="tool_arguments_invalid")
        has_content = "content" in arguments
        has_patch = "patch" in arguments
        if has_content == has_patch:
            raise ProviderProtocolError("mutate_file requires exactly one content or patch", code="tool_arguments_invalid")
        relative_path = _normalize_relative_path(arguments["path"])
        if self._is_protected_relative(relative_path):
            raise WorkspaceValidationError("configured protected path is denied", code="protected_path_denied")
        expected_digest = arguments["expected_digest"]
        if expected_digest is not None and (
            not isinstance(expected_digest, str) or _HEX_DIGEST.fullmatch(expected_digest) is None
        ):
            raise ProviderProtocolError("expected_digest must be sha256 or null", code="tool_arguments_invalid")
        target = self._safe_target(relative_path, allow_missing=True)
        before = self._snapshot(target)
        if before.digest != expected_digest:
            raise WorkspaceDigestConflict("workspace digest does not match expected_digest")
        before_text = before.text
        if before.exists and looks_like_bare_credential(before_text):
            raise RedactionRequired("workspace pre-image contains a raw credential")
        if has_content:
            content = arguments["content"]
            if not isinstance(content, str):
                raise ProviderProtocolError("content must be UTF-8 text", code="tool_arguments_invalid")
            after_text = content
        else:
            after_text = self._apply_patch(before_text, arguments["patch"])
        if looks_like_bare_credential(after_text):
            raise RedactionRequired("workspace content contains a raw credential")
        after_data = after_text.encode("utf-8")
        if len(after_data) > self.store.max_payload_bytes:
            raise ProviderProtocolError("workspace content exceeds the durable bound", code="payload_too_large")
        diff = _unified_diff(relative_path, before_text, after_text)
        diff_digest = _digest_bytes(diff.encode("utf-8"))
        stable_action = action_request_id or str(arguments.get("action_request_id", ""))
        if not stable_action.strip():
            # Direct preview callers may use the ToolCall id; production
            # mutation always supplies the canonical action id explicitly.
            stable_action = call.tool_call_id
        seed = f"{scope.digest}:{stable_action}".encode("utf-8")
        identity = hashlib.sha256(seed).hexdigest()
        return _MutationPlan(
            action_request_id=stable_action,
            tool_call_id=call.tool_call_id,
            relative_path=relative_path,
            target=target,
            before=before,
            after_data=after_data,
            after_digest=_digest_bytes(after_data),
            diff=diff,
            diff_digest=diff_digest,
            change_id=f"change-{identity[:32]}",
            checkpoint_id=f"cp-{identity[:32]}",
            operation_id=f"operation-{identity[:32]}",
            parent_identity=_parent_identity(target.parent),
        )

    @staticmethod
    def _apply_patch(before: str, patch: Any) -> str:
        if not isinstance(patch, (Mapping, list, tuple)):
            raise ProviderProtocolError("patch must be a replacement object or list", code="tool_arguments_invalid")
        edits: Any = patch.get("edits") if isinstance(patch, Mapping) else patch
        if not isinstance(edits, (list, tuple)):
            edits = [patch]
        result = before
        for edit in edits:
            if not isinstance(edit, Mapping) or set(edit) != {"old", "new"}:
                raise ProviderProtocolError("patch edit must contain old and new", code="tool_arguments_invalid")
            old, new = edit["old"], edit["new"]
            if not isinstance(old, str) or not isinstance(new, str) or not old:
                raise ProviderProtocolError("patch edit values must be non-empty text", code="tool_arguments_invalid")
            count = result.count(old)
            if count != 1:
                raise ProviderProtocolError("patch edit must match exactly once", code="patch_not_exact")
            result = result.replace(old, new, 1)
        return result

    def _safe_target(self, relative_path: str, *, allow_missing: bool) -> Path:
        if _is_reparse(self.workspace_root):
            raise WorkspaceValidationError(
                "workspace root is a symlink or reparse point",
                code="reparse_root_denied",
            )
        target = self.workspace_root.joinpath(*relative_path.split("/"))
        parent = target.parent
        if not parent.exists() or not parent.is_dir():
            raise WorkspaceValidationError("workspace parent does not exist", code="workspace_parent_missing")
        current = self.workspace_root
        for part in relative_path.split("/")[:-1]:
            current = current / part
            if _is_reparse(current):
                raise WorkspaceValidationError("workspace path contains a symlink or reparse point", code="reparse_path_denied")
        if _is_reparse(parent):
            raise WorkspaceValidationError("workspace parent is a symlink or reparse point", code="reparse_path_denied")
        if target.exists() or os.path.lexists(target):
            if _is_reparse(target):
                raise WorkspaceValidationError("workspace target is a symlink or reparse point", code="reparse_path_denied")
            if target.is_dir():
                raise WorkspaceValidationError("workspace target is a directory", code="path_invalid")
        elif not allow_missing:
            raise WorkspaceAdapterError("workspace file does not exist", code="workspace_file_not_found")
        _reject_existing_aliases(self.workspace_root, relative_path)
        try:
            resolved = target.resolve(strict=False)
        except OSError as exc:
            raise WorkspaceValidationError("workspace path cannot be resolved", code="workspace_boundary_lost") from exc
        if not _is_within(resolved, self.workspace_root):
            raise WorkspaceValidationError("workspace path escapes the configured root", code="workspace_boundary_lost")
        return target

    def _snapshot(self, target: Path) -> _FileSnapshot:
        if not target.exists() and not os.path.lexists(target):
            return _FileSnapshot(False, b"", None)
        if _is_reparse(target) or target.is_dir():
            raise WorkspaceValidationError("workspace target is not a regular file", code="path_invalid")
        try:
            before_info = os.stat(target, follow_symlinks=False)
            before_identity = _path_identity(target, directory=False)
            if int(before_info.st_size) > self._max_file_bytes:
                raise WorkspaceValidationError(
                    "workspace file exceeds the adapter size bound",
                    code="workspace_file_too_large",
                )
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            total = 0
            with target.open("rb") as handle:
                while True:
                    chunk = handle.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._max_file_bytes:
                        raise WorkspaceValidationError(
                            "workspace file exceeds the adapter size bound",
                            code="workspace_file_too_large",
                        )
                    digest.update(chunk)
                    chunks.append(chunk)
            after_info = os.stat(target, follow_symlinks=False)
            after_identity = _path_identity(target, directory=False)
            if (
                after_identity != before_identity
                or int(after_info.st_size) != int(before_info.st_size)
                or int(getattr(after_info, "st_mtime_ns", 0))
                != int(getattr(before_info, "st_mtime_ns", 0))
            ):
                raise WorkspaceDigestConflict(
                    "workspace file changed while it was being read",
                    code="workspace_read_race",
                )
        except OSError as exc:
            raise WorkspaceAdapterError("workspace file cannot be read", code="workspace_read_failed") from exc
        return _FileSnapshot(True, b"".join(chunks), digest.hexdigest(), before_identity)

    def _verify_precommit(self, plan: _MutationPlan) -> None:
        target = self._safe_target(plan.relative_path, allow_missing=True)
        if _parent_identity(target.parent) != plan.parent_identity:
            raise WorkspaceValidationError("workspace parent changed before commit", code="workspace_boundary_lost")
        current = self._snapshot(target)
        if current.digest != plan.before.digest or current.identity != plan.before.identity:
            raise WorkspaceDigestConflict("workspace changed before commit")

    @staticmethod
    def _stat_identity(info: os.stat_result) -> tuple[int, int, int]:
        return (
            int(getattr(info, "st_dev", 0)),
            int(getattr(info, "st_ino", 0)),
            int(getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000))),
        )

    def _read_fd_bounded(self, file_descriptor: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_descriptor, _STREAM_CHUNK_BYTES)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self._max_file_bytes:
                raise WorkspaceValidationError(
                    "workspace file exceeds the adapter size bound",
                    code="workspace_file_too_large",
                )
            chunks.append(chunk)

    def _verify_named_target_posix(
        self,
        parent_fd: int,
        target_name: str,
        *,
        expected: _FileSnapshot,
    ) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            target_fd = os.open(target_name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            if expected.exists:
                raise WorkspaceDigestConflict(
                    "workspace target disappeared before commit"
                )
            return b""
        try:
            info = os.fstat(target_fd)
            if not stat.S_ISREG(info.st_mode):
                raise WorkspaceValidationError(
                    "workspace target is not a regular file",
                    code="path_invalid",
                )
            if not expected.exists:
                raise WorkspaceDigestConflict("workspace target appeared before commit")
            identity = self._stat_identity(info)
            if identity != expected.identity:
                raise WorkspaceDigestConflict("workspace target identity changed before commit")
            os.lseek(target_fd, 0, os.SEEK_SET)
            data = self._read_fd_bounded(target_fd)
            if data != expected.data:
                raise WorkspaceDigestConflict("workspace target digest changed before commit")
            return data
        finally:
            os.close(target_fd)

    def _verify_named_postimage_posix(
        self,
        parent_fd: int,
        target_name: str,
        expected_data: bytes,
    ) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            target_fd = os.open(target_name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise WorkspaceEffectUncertain(
                "workspace post-image could not be opened through its parent handle"
            ) from exc
        try:
            info = os.fstat(target_fd)
            if not stat.S_ISREG(info.st_mode):
                raise WorkspaceEffectUncertain(
                    "workspace post-image is not a regular file"
                )
            os.lseek(target_fd, 0, os.SEEK_SET)
            if self._read_fd_bounded(target_fd) != expected_data:
                raise WorkspaceEffectUncertain(
                    "workspace post-image differs from the committed bytes"
                )
        except WorkspaceEffectUncertain:
            raise
        except OSError as exc:
            raise WorkspaceEffectUncertain(
                "workspace post-image could not be verified"
            ) from exc
        finally:
            os.close(target_fd)

    def _before_atomic_replace(self, plan: _MutationPlan) -> None:
        """Test seam for a deterministic late namespace race."""

        del plan

    @staticmethod
    def _atomic_replace_supported() -> bool:
        if os.name != "nt":
            return hasattr(os, "replace") and hasattr(os, "open")
        try:
            import ctypes

            # The Win32 wrapper is not sufficient for this adapter: on the
            # supported Windows host it rejects RootDirectory-relative
            # FILE_RENAME_INFORMATION with ERROR_INVALID_PARAMETER.  The
            # controlled fallback below therefore uses the native
            # NtSetInformationFile entry point with the same fixed directory
            # handle, and must be part of the explicit capability preflight.
            return hasattr(ctypes.WinDLL("ntdll"), "NtSetInformationFile")
        except (AttributeError, OSError):
            return False

    def _replace_posix(self, plan: _MutationPlan) -> None:
        target = self._safe_target(plan.relative_path, allow_missing=True)
        self._before_atomic_replace(plan)
        parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            parent_flags |= os.O_NOFOLLOW
        try:
            parent_fd = os.open(str(target.parent), parent_flags)
        except OSError as exc:
            raise WorkspaceValidationError(
                "workspace parent handle could not be opened",
                code="workspace_parent_unavailable",
            ) from exc
        temporary_name: str | None = None
        try:
            parent_info = os.fstat(parent_fd)
            parent_identity = self._stat_identity(parent_info)[:2]
            if parent_identity != plan.parent_identity:
                raise WorkspaceValidationError(
                    "workspace parent changed before handle-relative commit",
                    code="workspace_boundary_lost",
                )
            for _attempt in range(32):
                candidate = f".pulse-harness-{secrets.token_hex(16)}"
                try:
                    temporary_fd = os.open(
                        candidate,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            else:
                raise WorkspaceAdapterError(
                    "could not allocate a workspace temporary",
                    code="workspace_temp_unavailable",
                )
            try:
                view = memoryview(plan.after_data)
                while view:
                    written = os.write(temporary_fd, view)
                    if written <= 0:
                        raise OSError("workspace temporary write made no progress")
                    view = view[written:]
                os.fsync(temporary_fd)
            finally:
                os.close(temporary_fd)

            self._verify_named_target_posix(
                parent_fd,
                target.name,
                expected=plan.before,
            )
            try:
                os.replace(
                    temporary_name,
                    target.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            except OSError as exc:
                raise WorkspaceEffectUncertain(
                    "workspace handle-relative replace could not be confirmed"
                ) from exc
            except Exception as exc:
                raise WorkspaceEffectUncertain(
                    "workspace handle-relative replace could not be confirmed"
                ) from exc
            temporary_name = None
            try:
                os.fsync(parent_fd)
                self._verify_named_postimage_posix(
                    parent_fd,
                    target.name,
                    plan.after_data,
                )
            except WorkspaceEffectUncertain:
                raise
            except OSError as exc:
                raise WorkspaceEffectUncertain(
                    "workspace handle-relative replace durability is uncertain"
                ) from exc
            except Exception as exc:
                raise WorkspaceEffectUncertain(
                    "workspace handle-relative post-image could not be confirmed"
                ) from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except OSError:
                    pass
            try:
                os.close(parent_fd)
            except OSError:
                pass

    def _replace_windows(self, plan: _MutationPlan) -> None:
        target = self._safe_target(plan.relative_path, allow_missing=True)
        self._before_atomic_replace(plan)
        if not self._atomic_replace_supported():
            raise WorkspaceAtomicReplaceUnsupported(
                "the host has no handle-relative atomic replace primitive"
            )
        parent_handle = _windows_open_handle(target.parent, directory=True)
        temporary: Path | None = None
        source_handle: Any | None = None
        rename_attempted = False
        try:
            if _windows_handle_identity(parent_handle)[:2] != plan.parent_identity:
                raise WorkspaceValidationError(
                    "workspace parent changed before handle-relative commit",
                    code="workspace_boundary_lost",
                )
            if _parent_identity(target.parent) != plan.parent_identity:
                raise WorkspaceValidationError(
                    "workspace parent changed before target verification",
                    code="workspace_boundary_lost",
                )
            current = self._snapshot(target)
            if current.digest != plan.before.digest or current.identity != plan.before.identity:
                raise WorkspaceDigestConflict("workspace changed before commit")

            fd, temporary_name = tempfile.mkstemp(
                prefix=".pulse-harness-",
                dir=str(target.parent),
            )
            temporary = Path(temporary_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(plan.after_data)
                handle.flush()
                os.fsync(handle.fileno())
            # The path used to stage the source must still name the directory
            # whose identity was captured in the plan.  The eventual rename
            # is handle-relative, but accepting a source staged under a
            # replacement parent would make the pre-effect authority
            # ambiguous and would reintroduce a namespace race in the setup
            # path.
            if _parent_identity(temporary.parent) != plan.parent_identity:
                raise WorkspaceValidationError(
                    "workspace parent changed while staging the replacement",
                    code="workspace_boundary_lost",
                )
            staged_identity = _path_identity(temporary, directory=False)
            source_handle = _windows_open_handle(
                temporary,
                directory=False,
                write=True,
                delete=True,
            )
            if _windows_handle_identity(source_handle) != staged_identity:
                raise WorkspaceValidationError(
                    "workspace replacement source identity changed while opening",
                    code="workspace_boundary_lost",
                )
            rename_attempted = True
            try:
                _windows_rename_by_directory_handle(
                    source_handle,
                    parent_handle,
                    target.name,
                )
            except WorkspaceAtomicReplaceUnsupported:
                rename_attempted = False
                raise
            except OSError as exc:
                raise WorkspaceEffectUncertain(
                    "workspace handle-relative replace could not be confirmed"
                ) from exc
            except Exception as exc:
                raise WorkspaceEffectUncertain(
                    "workspace handle-relative replace could not be confirmed"
                ) from exc
            if not _windows_close_handle(source_handle):
                raise WorkspaceEffectUncertain(
                    "workspace source handle could not be closed after replace"
                )
            source_handle = None
            try:
                if _parent_identity(target.parent) != plan.parent_identity:
                    raise WorkspaceEffectUncertain(
                        "workspace parent changed before post-image verification"
                    )
                after = self._snapshot(target)
                if after.digest != plan.after_digest:
                    raise WorkspaceEffectUncertain(
                        "workspace post-image differs from the committed bytes"
                    )
            except WorkspaceEffectUncertain:
                raise
            except Exception as exc:
                raise WorkspaceEffectUncertain(
                    "workspace handle-relative post-image could not be confirmed"
                ) from exc
        finally:
            _windows_close_handle(source_handle)
            _windows_close_handle(parent_handle)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    if rename_attempted:
                        raise WorkspaceEffectUncertain(
                            "workspace temporary cleanup could not be confirmed"
                        )

    def _replace(self, plan: _MutationPlan) -> None:
        if os.name == "nt":
            self._replace_windows(plan)
        else:
            self._replace_posix(plan)

    def _write_checkpoint(
        self,
        plan: _MutationPlan,
        scope: HarnessScope,
        action_request_id: str,
    ) -> None:
        directory = self.checkpoint_root / plan.checkpoint_id
        directory.mkdir(parents=True, exist_ok=True)
        if _is_reparse(directory):
            raise WorkspaceValidationError("checkpoint directory is a reparse point", code="checkpoint_boundary_lost")
        if plan.before.exists:
            _atomic_write_bytes(directory / "before.bin", plan.before.data)
        metadata = {
            "format": "pulse-harness-workspace.v1",
            "checkpoint_id": plan.checkpoint_id,
            "action_request_id": action_request_id,
            "operation_id": plan.operation_id,
            "change_id": plan.change_id,
            "diff_digest": plan.diff_digest,
            "scope_digest": scope.digest,
            "authority_digest": digest_json(
                dict(self._authority_payload(scope, action_request_id))
            ),
            "path": plan.relative_path,
            "before_exists": plan.before.exists,
            "before_digest": plan.before.digest,
            "after_digest": plan.after_digest,
            "state": "sealed",
            "workspace_provenance": self._workspace_provenance,
        }
        _atomic_write_bytes(
            directory / "metadata.json",
            _canonical_json(metadata).encode("utf-8"),
        )


WorkspaceAdapter = StructuredWorkspaceToolProvider
CheckpointedWorkspaceAdapter = StructuredWorkspaceToolProvider
