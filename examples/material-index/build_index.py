"""Build an offline reading index from a JSON list; never fetch its URLs.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
from html import escape
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


def link_target(value: str) -> str | None:
    """Choose clickable web addresses separately from HTML text escaping.

    This is a conservative index policy, not a destination trust assessment.
    Reject ambiguous separators, credentials, and invalid ports as well as
    non-web schemes. Keep rejected entries as text so review loses no titles.
    """
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None):
            return None
        # Accessing port validates its syntax and range.
        parts.port
    except ValueError:
        return None
    return value


def render(entries: list[dict], *, title: str = "Reading materials",
           method_note: str = "Escape displayed text and independently choose eligible link destinations.") -> str:
    if not isinstance(entries, list):
        raise ValueError("Input must be a JSON list of materials")
    cards = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(key, ""), str) for key in ("title", "url", "note")
        ) or not entry.get("title", "").strip():
            raise ValueError(f"Material {index} needs a title and string url/note fields")
        label = escape(entry["title"])
        target = link_target(entry.get("url", ""))
        heading = (f'<a href="{escape(target, quote=True)}" rel="noreferrer">{label}</a>'
                   if target else label)
        status = ("Web link · destination not verified" if target
                  else "Text only · no eligible HTTP(S) address")
        cards.append(f'<li><h2>{heading}</h2><p>{escape(entry.get("note", ""))}</p>'
                     f'<small>{status}</small></li>')
    content = "\n".join(cards) if cards else '<li>No materials yet.</li>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f4f2ec; color: #232b28; font: 17px/1.6 system-ui, sans-serif; }}
main {{ max-width: 780px; margin: auto; padding: 48px 24px; }}
h1 {{ font-size: clamp(30px, 6vw, 44px); line-height: 1.15; margin: 12px 0; }}
h2 {{ font-size: 20px; line-height: 1.4; margin: 0; }}
ul {{ list-style: none; padding: 0; margin: 32px 0; }}
li {{ border-top: 1px solid #cccfc6; padding: 24px 0; overflow-wrap: anywhere; }}
p {{ white-space: pre-wrap; margin: 10px 0; }}
a {{ color: #245c4c; text-underline-offset: .2em; }}
a:focus-visible {{ outline: 3px solid #b76f26; outline-offset: 4px; }}
small, footer {{ color: #545d56; font-size: 14px; }}
footer {{ border-top: 1px solid #cccfc6; padding-top: 24px; }}
</style></head><body><main>
<small>PER PULSE · READING WORK</small>
<h1>{escape(title)}</h1>
<p>{len(entries)} materials · Built locally. No addresses were fetched.</p>
<ul>{content}</ul>
<footer>Method: {escape(method_note)}
<br>Sources: <a href="https://docs.python.org/3.12/library/html.html#html.escape" rel="noreferrer">Python html.escape</a>
and <a href="https://docs.python.org/3.12/library/urllib.parse.html#url-parsing-security" rel="noreferrer">URL parsing security</a>.</footer>
</main></body></html>
'''


def write_index(destination: Path, content: str) -> None:
    """Make only a complete output visible, without replacing an existing file.

    Stage in the destination directory, flush the complete bytes, and create
    the final name with an atomic no-replace hard link. If publication fails,
    the original destination remains untouched. An interrupted OS process
    may leave a hidden staging file, never a partial final artifact.
    """
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Unlike os.replace/rename this fails when destination already exists.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 JSON material list")
    parser.add_argument("output", type=Path, help="New HTML file (will not overwrite)")
    parser.add_argument("--title", default="Reading materials")
    args = parser.parse_args()
    try:
        page = render(json.loads(args.input.read_text(encoding="utf-8")), title=args.title)
        write_index(args.output, page)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not build index: {error}\n")


if __name__ == "__main__":
    main()
