"""Text of a PDF, page by page, for reading and preview.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

PDFs stay as the person's original files; only their text is read. Each page
starts with a ``[Page N]`` line so a passage can be traced back to its page.
A page without selectable text (for example a scanned image) contributes only
its marker.
"""
from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
import logging
from pathlib import PurePath

# PDFs are usually larger than text files; this bounds the original file.
MAX_PDF_BYTES = 32 * 1024 * 1024
PDF_SUFFIX = ".pdf"


class DocumentTextError(ValueError):
    """The PDF could not be read; ``code`` is a stable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_pdf_path(path: str | PurePath) -> bool:
    return PurePath(path).suffix.lower() == PDF_SUFFIX


def looks_like_pdf(data: bytes) -> bool:
    return data[:1024].lstrip(b"\x00\t\n\f\r ").startswith(b"%PDF-")


def pdf_text(data: bytes, *, should_stop: Callable[[], bool] | None = None) -> str:
    """Return the PDF's text with a ``[Page N]`` line before each page."""
    if not looks_like_pdf(data):
        raise DocumentTextError("pdf_unreadable", "This file is not a PDF.")
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    # pypdf reports recoverable structure problems as warnings; the text it
    # still extracts is what the person gets.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentTextError("pdf_encrypted", "This PDF is password-protected.")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            if should_stop is not None and should_stop():
                raise DocumentTextError("pdf_read_stopped", "Reading the PDF was stopped.")
            text = (page.extract_text() or "").strip()
            pages.append(f"[Page {number}]\n{text}" if text else f"[Page {number}]")
    except DocumentTextError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, IndexError,
            RecursionError, NotImplementedError) as error:
        raise DocumentTextError("pdf_unreadable", "This PDF could not be read.") from error
    return "\n\n".join(pages)
