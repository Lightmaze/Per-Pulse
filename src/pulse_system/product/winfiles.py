"""Handle-anchored workspace file access on Windows.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The POSIX file layer opens each path component relative to an open directory
descriptor (``openat``), so replacing a directory with a symlink after it was
checked cannot redirect a read or write. This module gives Windows the same
property: the workspace root is opened as a handle, and every further
component is opened with ``NtCreateFile`` relative to its parent's handle
(``OBJECT_ATTRIBUTES.RootDirectory``) and ``FILE_OPEN_REPARSE_POINT``.
Symlinks and junctions are therefore opened as themselves and refused, and a
directory swapped after its handle was opened is no longer reachable by name:
the open handle still refers to the original directory. Uploads are created
and renamed relative to the same directory handle.
"""
from __future__ import annotations

import ctypes
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes

if os.name != "nt":  # pragma: no cover - imported only on Windows
    raise ImportError("winfiles is Windows-only")

import msvcrt

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_SYNCHRONIZE = 0x00100000
_DELETE = 0x00010000
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_LIST_DIRECTORY = 0x0001
_FILE_TRAVERSE = 0x0020
_FILE_READ_ATTRIBUTES = 0x0080
_SHARE_ALL = 0x1 | 0x2 | 0x4
_FILE_OPEN = 1
_FILE_CREATE = 2
_FILE_OPEN_IF = 3
_FILE_DIRECTORY_FILE = 0x00000001
_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
_FILE_NON_DIRECTORY_FILE = 0x00000040
_FILE_OPEN_REPARSE_POINT = 0x00200000
_OBJ_CASE_INSENSITIVE = 0x00000040
_STATUS_NO_MORE_FILES = 0x80000006
_FILE_DIRECTORY_INFORMATION = 1
_FILE_RENAME_INFORMATION = 10
_FILE_DISPOSITION_INFORMATION = 13
_INVALID_HANDLE = ctypes.c_void_p(-1).value

# Test seam: called with (name, handle) after each directory component opens.
component_opened: Callable[[str, int], None] | None = None


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                ("Buffer", wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(_UnicodeString)), ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]


class _IoStatusBlock(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class _ByHandleInfo(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("creation", _FileTime), ("access", _FileTime),
                ("write", _FileTime), ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]


_NtCreateFile = _ntdll.NtCreateFile
_NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(_ObjectAttributes),
                          ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
                          wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
_NtCreateFile.restype = ctypes.c_long
_NtQueryDirectoryFile = _ntdll.NtQueryDirectoryFile
_NtQueryDirectoryFile.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p, wintypes.ULONG,
                                  wintypes.ULONG, wintypes.BOOLEAN, ctypes.c_void_p, wintypes.BOOLEAN]
_NtQueryDirectoryFile.restype = ctypes.c_long
_NtSetInformationFile = _ntdll.NtSetInformationFile
_NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock), ctypes.c_void_p,
                                  wintypes.ULONG, wintypes.ULONG]
_NtSetInformationFile.restype = ctypes.c_long
_RtlNtStatusToDosError = _ntdll.RtlNtStatusToDosError
_RtlNtStatusToDosError.argtypes = [wintypes.ULONG]
_RtlNtStatusToDosError.restype = wintypes.ULONG
_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_CreateFileW.restype = wintypes.HANDLE
_GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
_GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleInfo)]
_GetFileInformationByHandle.restype = wintypes.BOOL
_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


def _check(status: int, what: str) -> None:
    if status < 0:
        code = _RtlNtStatusToDosError(status & 0xFFFFFFFF)
        raise OSError(code, f"{what} failed (NTSTATUS 0x{status & 0xFFFFFFFF:08X})")


def close(handle: int) -> None:
    _CloseHandle(handle)


def attributes(handle: int) -> _ByHandleInfo:
    info = _ByHandleInfo()
    if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info


def _plain(handle: int, *, directory: bool) -> _ByHandleInfo:
    """Refuse a reparse point, or a directory where a file is expected (and back)."""
    info = attributes(handle)
    if info.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError(1920, "reparse point refused")  # ERROR_CANT_ACCESS_FILE
    if bool(info.attributes & FILE_ATTRIBUTE_DIRECTORY) != directory:
        raise OSError(267 if directory else 5, "unexpected file type")
    return info


def open_root(path: str) -> int:
    """Open the workspace root itself (not a link to it) as a directory handle."""
    handle = _CreateFileW(path, _FILE_LIST_DIRECTORY | _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                          _SHARE_ALL, None, 3, 0x02000000 | 0x00200000, None)  # OPEN_EXISTING, BACKUP, REPARSE
    if not handle or handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _plain(handle, directory=True)
    except BaseException:
        close(handle)
        raise
    return handle


def _open_relative(parent: int, name: str, access: int, disposition: int, options: int) -> int:
    if not name or "\\" in name or "/" in name or name in {".", ".."}:
        raise OSError(123, "invalid name")  # ERROR_INVALID_NAME
    buffer = ctypes.create_unicode_buffer(name)
    # UNICODE_STRING counts UTF-16 bytes, including both surrogates of emoji.
    length = len(name.encode("utf-16-le", errors="surrogatepass"))
    text = _UnicodeString(length, length + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attrs = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(text),
                              _OBJ_CASE_INSENSITIVE, None, None)
    handle, io = wintypes.HANDLE(), _IoStatusBlock()
    _check(_NtCreateFile(ctypes.byref(handle), access | _SYNCHRONIZE, ctypes.byref(attrs), ctypes.byref(io),
                         None, 0x80, _SHARE_ALL, disposition,
                         options | _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT, None, 0),
           "NtCreateFile")
    return handle.value


def open_directory(parent: int, name: str, *, create: bool = False) -> int:
    handle = _open_relative(parent, name, _FILE_LIST_DIRECTORY | _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES,
                            _FILE_OPEN_IF if create else _FILE_OPEN, _FILE_DIRECTORY_FILE)
    try:
        _plain(handle, directory=True)
    except BaseException:
        close(handle)
        raise
    if component_opened is not None:
        component_opened(name, handle)
    return handle


@contextmanager
def directory(root: str, parts: list[str]) -> Iterator[int]:
    """Yield a handle for ``root/parts...``, each component opened relative to its parent."""
    handle = open_root(root)
    try:
        for part in parts:
            child = open_directory(handle, part)
            close(handle)
            handle = child
        yield handle
    finally:
        close(handle)


def open_file_read(parent: int, name: str) -> int:
    """Return a read-only file descriptor for a plain (non-reparse) file in ``parent``.

    The caller checks the link count with ``os.fstat``, as on POSIX.
    """
    handle = _open_relative(parent, name, _GENERIC_READ | _FILE_READ_ATTRIBUTES, _FILE_OPEN,
                            _FILE_NON_DIRECTORY_FILE)
    try:
        _plain(handle, directory=False)
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise


def list_directory(handle: int) -> Iterator[tuple[str, bool, int, bool]]:
    """Yield (name, is_directory, size, is_reparse_point) for entries of an open directory."""
    buffer = ctypes.create_string_buffer(64 * 1024)
    restart = True
    while True:
        io = _IoStatusBlock()
        status = _NtQueryDirectoryFile(handle, None, None, None, ctypes.byref(io), buffer, len(buffer),
                                       _FILE_DIRECTORY_INFORMATION, False, None, restart)
        restart = False
        if status & 0xFFFFFFFF == _STATUS_NO_MORE_FILES:
            return
        _check(status, "NtQueryDirectoryFile")
        offset = 0
        while True:
            base = ctypes.addressof(buffer) + offset
            next_offset = ctypes.c_ulong.from_address(base).value
            size = ctypes.c_longlong.from_address(base + 40).value
            attrs = ctypes.c_ulong.from_address(base + 56).value
            length = ctypes.c_ulong.from_address(base + 60).value
            name = ctypes.wstring_at(base + 64, length // 2)
            if name not in {".", ".."}:
                yield (name, bool(attrs & FILE_ATTRIBUTE_DIRECTORY), size,
                       bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT))
            if not next_offset:
                break
            offset += next_offset


def _rename_into(file_handle: int, directory_handle: int, name: str) -> None:
    """Rename an open file to ``name`` inside ``directory_handle``; never replace an existing name."""
    encoded = name.encode("utf-16-le")

    class _RenameInfo(ctypes.Structure):
        _fields_ = [("ReplaceIfExists", wintypes.BOOLEAN), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.ULONG), ("FileName", ctypes.c_ubyte * len(encoded))]

    info = _RenameInfo(False, directory_handle, len(encoded))
    # Copy the UTF-16 bytes directly: a c_char field would stop at the first NUL byte.
    ctypes.memmove(ctypes.addressof(info) + _RenameInfo.FileName.offset, encoded, len(encoded))
    io = _IoStatusBlock()
    _check(_NtSetInformationFile(file_handle, ctypes.byref(io), ctypes.byref(info), ctypes.sizeof(info),
                                 _FILE_RENAME_INFORMATION), "rename")


def _delete_open(file_handle: int) -> None:
    flag = wintypes.BOOLEAN(True)
    io = _IoStatusBlock()
    _NtSetInformationFile(file_handle, ctypes.byref(io), ctypes.byref(flag), 1, _FILE_DISPOSITION_INFORMATION)


def publish(directory_handle: int, temp_name: str, target_name: str, data: bytes) -> None:
    """Write ``data`` under ``temp_name`` in the directory, then rename it to ``target_name`` (create-only)."""
    handle = _open_relative(directory_handle, temp_name, _GENERIC_WRITE | _DELETE | _FILE_READ_ATTRIBUTES,
                            _FILE_CREATE, _FILE_NON_DIRECTORY_FILE)
    fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    renamed = False
    try:
        written = 0
        view = memoryview(data)
        while written < len(data):
            written += os.write(fd, view[written:])
        os.fsync(fd)
        _rename_into(handle, directory_handle, target_name)
        renamed = True
    finally:
        if not renamed:
            _delete_open(handle)
        os.close(fd)
