"""Text of a PDF, page by page, for reading and preview.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

PDFs stay as the person's original files; only their text is read. Each page
starts with a ``[Page N]`` line so a passage can be traced back to its page.
A page without selectable text (for example a scanned image) contributes only
its marker.

A small PDF can expand into far more text than its size suggests, so reading
is bounded: each decompressed stream, the number of pages and the total text.
When a bound is reached the text ends with a line that says where it stops,
and the result is marked incomplete; it never claims a complete read.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from io import BytesIO
import logging
from pathlib import PurePath
import threading

# PDFs are usually larger than text files; this bounds the original file.
MAX_PDF_BYTES = 32 * 1024 * 1024
# A long book is a few million characters; reading stops at these bounds.
MAX_PDF_PAGES = 2000
MAX_PDF_TEXT_CHARS = 2_000_000
# Ordinary page content streams are far below this once decompressed.
MAX_DECODED_STREAM_BYTES = 8_000_000
PDF_SUFFIX = ".pdf"

_CACHE_ENTRIES = 4
_cache: OrderedDict[tuple[str, int, int], "PdfText"] = OrderedDict()
_cache_lock = threading.Lock()


class DocumentTextError(ValueError):
    """The PDF could not be read; ``code`` is a stable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PdfText:
    text: str
    pages: int
    pages_read: int
    complete: bool
    pages_without_selectable_text: bool = False


def is_pdf_path(path: str | PurePath) -> bool:
    return PurePath(path).suffix.lower() == PDF_SUFFIX


def looks_like_pdf(data: bytes) -> bool:
    return data[:1024].lstrip(b"\x00\t\n\f\r ").startswith(b"%PDF-")


def pdf_text(data: bytes, *, should_stop: Callable[[], bool] | None = None) -> str:
    """Return the PDF's text with a ``[Page N]`` line before each page."""
    return read_pdf(data, should_stop=should_stop).text


def read_pdf(
    data: bytes,
    *,
    should_stop: Callable[[], bool] | None = None,
    max_pages: int = MAX_PDF_PAGES,
    max_chars: int = MAX_PDF_TEXT_CHARS,
) -> PdfText:
    """Read a PDF's text within the page and text bounds (see module notes)."""
    if not looks_like_pdf(data):
        raise DocumentTextError("pdf_unreadable", "This file is not a PDF.")
    key = (hashlib.sha256(data).hexdigest(), max_pages, max_chars)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    result = _read(data, should_stop, max_pages, max_chars)
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > _CACHE_ENTRIES:
            _cache.popitem(last=False)
    return result


def _read(data: bytes, should_stop, max_pages: int, max_chars: int) -> PdfText:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import PdfReadError

    # pypdf reports recoverable structure problems as warnings; the text it
    # still extracts is what the person gets.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    limits = {name: MAX_DECODED_STREAM_BYTES for name in (
        "zlib_maximum_output_length", "lzw_maximum_output_length", "run_length_maximum_output_length",
        "brotli_maximum_output_length", "jbig2_maximum_output_length",
        "array_based_stream_maximum_output_length")}
    try:
        with apply_configuration(**limits):
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise DocumentTextError("pdf_encrypted", "This PDF is password-protected.")
            total = len(reader.pages)
            pages: list[str] = []
            used = 0
            missing_text = False
            for number in range(1, total + 1):
                if should_stop is not None and should_stop():
                    raise DocumentTextError("pdf_read_stopped", "Reading the PDF was stopped.")
                if number > max_pages:
                    return _stopped(pages, number - 1, total, missing_text)
                text = (reader.pages[number - 1].extract_text() or "").strip()
                if not text:
                    missing_text = True
                entry = (f"[Page {number}]\n{text}" if text else
                         f"[Page {number}]\n[No selectable text on this page; images or scans are not read.]")
                if used + len(entry) > max_chars:
                    pages.append(entry[:max(0, max_chars - used)])
                    return _stopped(pages, number, total, missing_text)
                pages.append(entry)
                used += len(entry) + 2
    except DocumentTextError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, IndexError,
            RecursionError, NotImplementedError, MemoryError) as error:
        raise DocumentTextError("pdf_unreadable", "This PDF could not be read.") from error
    return PdfText("\n\n".join(pages), total, total, True, missing_text)


def _stopped(pages: list[str], page: int, total: int, missing_text: bool) -> PdfText:
    note = (f"[Text stops here, on page {page} of {total}: the rest of this PDF is beyond "
            "the reading limit. Split the PDF to read the rest.]")
    return PdfText("\n\n".join([*pages, note]), total, page, False, missing_text)
