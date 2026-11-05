"""A PDF can be attached and read; its original bytes stay as they were.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

PDFs are synthetic, built in the test. Model decisions come from an explicit
scripted HTTP fixture; uploads, the workspace, the reading tool and the
Kernel are real.
"""
from __future__ import annotations

from io import BytesIO
import json
import time

import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from per_pulse.materials import material_footer
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.document_text import MAX_PDF_TEXT_CHARS, read_pdf
from pulse_system.product.files import MAX_FILE_BYTES
from pdf_samples import amplifying_pdf, synthetic_pdf
from test_material_downloads import download as download_material, identity
from test_materials import upload
from test_pages import client_for, send, wait_for

REPORT = synthetic_pdf(["Quarterly revenue grew 12 percent.", "Costs fell in the second half."])


def _reading_model(monkeypatch, seen, tool="reading", extra=None):
    """Read the attached file with ``tool``, then answer with what it returned."""
    monkeypatch.setenv("PER_PULSE_PDF_FIXTURE_KEY", "synthetic-test-credential")

    def respond(delta, finish):
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")

    def handler(request):
        messages = json.loads(request.content)["messages"]
        current = max(i for i, m in enumerate(messages) if m["role"] == "user")
        task = str(messages[current]["content"])
        if "[Attached workspace materials]" not in task:
            return respond({"content": "Ready. [Synthetic fixture]"}, "stop")
        results = [json.loads(m["content"]) for m in messages[current + 1:] if m["role"] == "tool"]
        if not results:
            source = json.loads(task.rsplit("[Attached workspace materials]\n", 1)[1].splitlines()[0])["files"][0]
            arguments = ({"op": "open", "path": source["path"], "expected_digest": source["sha256"]}
                         if tool == "reading" else {"path": source["path"]})
            arguments.update(extra or {})
            return respond({"tool_calls": [{"index": 0, "id": "pdf-open", "type": "function", "function": {
                "name": tool, "arguments": json.dumps(arguments)}}]}, "tool_calls")
        seen.append(results[-1])
        return respond({"content": "Read it. [Synthetic fixture]"}, "stop")

    def factory(_scope):
        return ChatHTTPModelAdapter(
            ChatHTTPConfig("https://fixture.invalid/chat/completions", "pdf-fixture",
                           credential_env="PER_PULSE_PDF_FIXTURE_KEY"),
            transport=httpx.MockTransport(handler))
    return factory


def test_a_pdf_attaches_previews_as_text_and_downloads_unchanged(tmp_path):
    with client_for(tmp_path, model_factory=None) as client:
        reply = upload(client, "home", "Q3 report.pdf", REPORT)
        assert reply.status_code == 201, reply.text
        material = reply.json()["material"]
        assert (tmp_path / "workspace" / material["path"]).read_bytes() == REPORT
        preview = client.get(f"/api/pages/home/materials/{material['id']}").json()
        assert preview["format"] == "pdf_text"
        assert preview["content"] == ("[Page 1]\nQuarterly revenue grew 12 percent.\n\n"
                                      "[Page 2]\nCosts fell in the second half.")
        download = download_material(client, "home", material)
        assert download.status_code == 200 and download.content == REPORT
        listed = client.get("/api/workspace/files/content", params={"path": material["path"]},
                            headers=identity(client)).json()
        assert listed["format"] == "pdf_text" and "Costs fell" in listed["content"]


@pytest.mark.parametrize("tool", ["reading", "read_file"])
def test_the_model_reads_an_attached_pdf_by_page(tmp_path, monkeypatch, tool):
    seen = []
    with client_for(tmp_path, model_factory=_reading_model(monkeypatch, seen, tool)) as client:
        material = upload(client, "home", "report.pdf", REPORT).json()["material"]
        send(client, "home", "Summarize the report.", materials=[material["id"]])
        wait_for(client, "home", lambda view: any(m["role"] == "assistant" for m in view["messages"]), timeout=15)
    assert seen and seen[-1]["status"] == "succeeded", seen
    passage = json.dumps(seen[-1]["output"])
    assert "[Page 1]" in passage and "Quarterly revenue grew 12 percent." in passage
    assert "[Page 2]" in passage and "Costs fell in the second half." in passage


def test_a_pdf_may_be_larger_than_a_text_file(tmp_path):
    data = synthetic_pdf(["Appendix."], padding=2 * MAX_FILE_BYTES)
    with client_for(tmp_path, model_factory=None) as client:
        reply = upload(client, "home", "appendix.pdf", data)
        assert reply.status_code == 201, reply.text
        preview = client.get(f"/api/pages/home/materials/{reply.json()['material']['id']}").json()
        assert preview["content"] == "[Page 1]\nAppendix."
        assert upload(client, "home", "notes.txt", b"a" * (MAX_FILE_BYTES + 1)).status_code == 413


def test_a_broken_or_locked_pdf_is_refused_with_a_reason(tmp_path):
    writer = PdfWriter(clone_from=PdfReader(BytesIO(REPORT)))
    writer.encrypt("secret-password")
    locked = BytesIO()
    writer.write(locked)
    with client_for(tmp_path, model_factory=None) as client:
        broken = upload(client, "home", "broken.pdf", b"%PDF-1.4\nnot really a pdf\n")
        assert broken.status_code == 415
        assert broken.json()["error"]["code"] == "material_pdf_unreadable"
        assert "could not be read" in broken.json()["error"]["message"]
        refused = upload(client, "home", "locked.pdf", locked.getvalue())
        assert refused.status_code == 415
        assert refused.json()["error"]["code"] == "material_pdf_encrypted"
        assert "password" in refused.json()["error"]["message"]
        assert client.get("/api/pages/home/materials").json()["materials"] == []


def test_the_footer_tells_the_model_to_use_the_reading_tool_for_any_material():
    footer = material_footer([{"id": "m", "name": "report.pdf", "path": "uploads/report.pdf",
                               "sha256": "0" * 64, "bytes": 10}])
    assert "reading.open(path, expected_digest=sha256)" in footer


def test_a_small_pdf_that_expands_into_huge_text_is_read_only_up_to_a_bound(tmp_path):
    bomb = amplifying_pdf(repeats=3000)  # about 8 KB that expands to about 3 million characters
    assert len(bomb) < 16 * 1024
    started = time.monotonic()
    result = read_pdf(bomb)
    assert time.monotonic() - started < 20
    assert not result.complete and result.pages_read == 1
    assert len(result.text) <= MAX_PDF_TEXT_CHARS + 200
    assert result.text.endswith("Split the PDF to read the rest.]")
    with client_for(tmp_path, model_factory=None) as client:
        reply = upload(client, "home", "expanding.pdf", bomb)
        assert reply.status_code == 201, reply.text
        material = reply.json()["material"]
        assert (tmp_path / "workspace" / material["path"]).read_bytes() == bomb
        preview = client.get(f"/api/pages/home/materials/{material['id']}").json()
        assert preview["format"] == "pdf_text" and preview["complete"] is False
        assert preview["truncated"] is True
        assert download_material(client, "home", material).content == bomb


def test_reading_stops_at_the_page_bound_and_says_where():
    result = read_pdf(synthetic_pdf([f"Page {n} text." for n in range(1, 6)]), max_pages=3)
    assert not result.complete and (result.pages_read, result.pages) == (3, 5)
    assert "[Page 3]\nPage 3 text." in result.text and "[Page 4]" not in result.text
    assert result.text.endswith("[Text stops here, on page 3 of 5: the rest of this PDF is beyond "
                                "the reading limit. Split the PDF to read the rest.]")
    whole = read_pdf(synthetic_pdf(["Only page."]))
    assert whole.complete and whole.text == "[Page 1]\nOnly page."


def test_nontext_pages_are_not_silent_search_negatives(tmp_path, monkeypatch):
    """A complete text-layer extraction is not a search of an unreadable image page."""
    scanned = synthetic_pdf(["", "Visible text on page two."])
    extraction = read_pdf(scanned)
    assert extraction.complete is True and extraction.pages_without_selectable_text is True
    assert "[Page 1]" in extraction.text
    assert extraction.text.startswith("[Page 1]\n\n[Page 2]\nVisible text on page two.")
    found, read = [], []
    with client_for(tmp_path, model_factory=_reading_model(
            monkeypatch, found, extra={"op": "find", "query": "UNSEEN SCAN MARKER"})) as client:
        material = upload(client, "home", "scanned.pdf", scanned).json()["material"]
        send(client, "home", "Find the marker.", materials=[material["id"]])
        wait_for(client, "home", lambda view: any(m["role"] == "assistant" for m in view["messages"]), timeout=15)
    result = found[-1]["output"]
    assert result["source"]["source_complete"] is True
    assert result["source"]["pages_without_selectable_text"] is True
    assert result["searched_entire_source"] is False
    assert result["matches"] == []
    with client_for(tmp_path / "read", model_factory=_reading_model(
            monkeypatch, read, tool="read_file")) as client:
        material = upload(client, "home", "scanned.pdf", scanned).json()["material"]
        send(client, "home", "Read the file.", materials=[material["id"]])
        wait_for(client, "home", lambda view: any(m["role"] == "assistant" for m in view["messages"]), timeout=15)
    assert read[-1]["output"]["pdf_pages_without_selectable_text"] is True
    assert "[Page 1]" in read[-1]["output"]["content"]


def test_a_search_of_a_pdf_read_only_in_part_says_it_is_partial(tmp_path, monkeypatch):
    """A miss in the first part of a long PDF is not presented as a whole-document negative."""
    bomb = amplifying_pdf(repeats=3000, later_page="LATER PAGE MARKER")
    assert read_pdf(bomb).complete is False and "LATER PAGE MARKER" not in read_pdf(bomb).text
    found, read = [], []
    with client_for(tmp_path, model_factory=_reading_model(
            monkeypatch, found, extra={"op": "find", "query": "LATER PAGE MARKER"})) as client:
        material = upload(client, "home", "long.pdf", bomb).json()["material"]
        send(client, "home", "Find the marker.", materials=[material["id"]])
        wait_for(client, "home", lambda view: any(m["role"] == "assistant" for m in view["messages"]), timeout=30)
    result = found[-1]["output"]
    assert result["kind"] == "pulse.reading-find.v1" and result["matches"] == []
    assert result["source"]["source_complete"] is False
    assert result["searched_entire_source"] is False
    with client_for(tmp_path / "second", model_factory=_reading_model(monkeypatch, read, tool="read_file")) as client:
        material = upload(client, "home", "long.pdf", bomb).json()["material"]
        send(client, "home", "Read it.", materials=[material["id"]])
        wait_for(client, "home", lambda view: any(m["role"] == "assistant" for m in view["messages"]), timeout=30)
    assert read[-1]["output"]["format"] == "pdf_text" and read[-1]["output"]["pdf_complete"] is False
    whole = []
    with client_for(tmp_path / "third", model_factory=_reading_model(monkeypatch, whole, tool="read_file")) as client:
        material = upload(client, "home", "short.pdf", REPORT).json()["material"]
        send(client, "home", "Read it.", materials=[material["id"]])
        wait_for(client, "home", lambda view: any(m["role"] == "assistant" for m in view["messages"]), timeout=15)
    assert whole[-1]["output"]["pdf_complete"] is True
