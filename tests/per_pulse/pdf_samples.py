"""Synthetic PDFs for tests: one Helvetica text line per page.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations


def synthetic_pdf(pages: list[str], *, padding: int = 0) -> bytes:
    """A valid PDF whose pages each carry one line of ASCII text.

    ``padding`` adds a comment of that many bytes, to make a larger file.
    """
    objects: list[bytes] = []
    page_ids = [4 + 2 * index for index in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page} 0 R" for page in page_ids).encode()
    objects.append(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(pages)).encode() + b" >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(pages):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")
        stream = b"BT /F1 12 Tf 72 720 Td (" + escaped + b") Tj ET" if text else b""
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
                       + str(page_ids[index] + 1).encode() + b" 0 R >>")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    if padding:
        out += b"%" + b"x" * padding + b"\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def amplifying_pdf(line_chars: int = 1024, repeats: int = 3000, later_page: str | None = None) -> bytes:
    """A few KB PDF whose compressed first page expands to repeats x line_chars of text.

    ``later_page`` adds a second page with that line of text.
    """
    import zlib
    line = b"BT /F1 12 Tf 72 720 Td (" + b"A" * line_chars + b") Tj ET\n"
    stream = zlib.compress(line * repeats, 9)
    kids = b"[4 0 R 6 0 R]" if later_page else b"[4 0 R]"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids " + kids + b" /Count " + (b"2" if later_page else b"1") + b" >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
               b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
               b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
               + stream + b"\nendstream"]
    if later_page:
        content = b"BT /F1 12 Tf 72 720 Td (" + later_page.encode("ascii") + b") Tj ET"
        objects += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    b"/Resources << /Font << /F1 3 0 R >> >> /Contents 7 0 R >>",
                    b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
