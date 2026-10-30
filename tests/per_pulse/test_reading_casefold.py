"""Original-coordinate Unicode search against an independent substring oracle.

Native model choices are explicit fixtures; source files, receipts, follow and
reopening use the real World and Kernel.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import hashlib
from itertools import product
import json
import random

import pytest

from pulse_harness.core.errors import ProviderProtocolError
from pulse_harness.core.provider import CancellationRequested, CancellationToken
from pulse_system.service.workspace_reading import _casefold_spans, _view, validate
from test_materials import upload
from test_pages import client_for
from test_reading_continuity import ReadingFixture, read, work


def oracle(text, query):
    """Enumerate ORIGINAL substrings; no folded-offset or boundary-map logic."""
    result, cursor = [], 0
    while cursor < len(text):
        found = None
        for start in range(cursor, len(text)):
            for end in range(start + 1, len(text) + 1):
                if text[start:end].casefold() == query.casefold():
                    found = (start, end)
                    break
            if found is not None:
                break
        if found is None:
            break
        result.append(found)
        cursor = found[1]
    return result


def spans(text, query):
    return list(_casefold_spans(text, query, CancellationToken()))


def test_casefold_matches_independent_original_substring_oracle():
    alphabet = "sSßfﬃİiı"
    sources = ["".join(chars) for n in range(4) for chars in product(alphabet, repeat=n)]
    queries = ["".join(chars) for n in (1, 2) for chars in product(alphabet, repeat=n)]
    for text in sources:
        for query in queries:
            assert spans(text, query) == oracle(text, query), (text, query)
    rng = random.Random(20261029)
    alphabet += "eé\u0301🌍a Σσς"
    for _ in range(1000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 20)))
        start = rng.randrange(len(text))
        query = (text[start:rng.randrange(start + 1, len(text) + 1)].casefold()
                 if rng.randrange(2) else "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 5))))
        assert spans(text, query) == oracle(text, query), (text, query)


@pytest.mark.parametrize("text,query,expected", [
    ("ß Straße", "STRASSE", [(2, 8)]),
    ("🌍 Straße", "STRASSE", [(2, 8)]),
    ("sß", "ss", [(1, 2)]),
    ("ßs", "ss", [(0, 1)]),
    ("Aß", "as", []),
    ("ﬃ", "FFI", [(0, 1)]),
    ("ﬃ", "fi", []),
    ("fİ", "fi", []),
    ("İx", "i\u0307x", [(0, 2)]),
    ("ıx", "IX", []),
    ("cafe\u0301", "café", []),
    ("Σσς", "σσ", [(0, 2)]),
    ("aaa", "AA", [(0, 2)]),
    ("SS FFI", "ß", [(0, 2)]),
])
def test_expansions_have_whole_original_spans_without_normalization(text, query, expected):
    assert spans(text, query) == expected


def test_casefold_iteration_obeys_cancellation_after_a_delivered_match():
    token = CancellationToken()
    matches = _casefold_spans("Straße" + "ﬃ" * 1000 + "Straße", "STRASSE", token)
    assert next(matches) == (0, 6)
    token.cancel("The original turn stopped")
    with pytest.raises(CancellationRequested, match="original turn stopped"):
        next(matches)


def test_native_casefold_find_follow_continue_and_reopen_keep_original_coordinates(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    prefix = "\ufeffß 🌍 source\r\n" * 3000
    text = prefix + "Straße first.\r\nStraße second, with context.\r\nClosing source."
    start = len(prefix) + len("Straße first.\r\n")
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page = work(client)
        response = upload(client, page, "casefold.txt", text.encode())
        assert response.status_code == 201, response.text
        material = response.json()["material"]
        default = read(client, page, fixture, op="find", path=material["path"], query="STRASSE")["output"]
        assert default["matches"] == [] and default["case_mode"] == "regex_ignorecase"
        found = read(client, page, fixture, op="find", path=material["path"], query="STRASSE",
                     casefold=True, expected_digest=material["sha256"])["output"]
        assert found["case_mode"] == "unicode_casefold"
        assert [(m["start_char"], m["end_char"]) for m in found["matches"]] == [
            (len(prefix), len(prefix) + 6), (start, start + 6)]
        assert found["searched_entire_redacted_view"] and not found["more_matches"]
        followed = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"],
                        match_index=1, max_chars=13)["output"]
        assert followed["content"] == text[start:start + 13]
        assert followed["position"]["start_char"] == start
        assert followed["source"]["digest"] == hashlib.sha256(text.encode()).hexdigest()
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        continued = read(client, page, fixture, **followed["next"], max_chars=19)["output"]
        assert continued["content"] == text[start + 13:start + 32]
        assert continued["source"] == followed["source"]
    assert (tmp_path / "workspace" / material["path"]).read_bytes() == text.encode()


def test_native_modes_remain_distinct_and_casefold_match_budget_is_honest(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page = work(client)
        path = tmp_path / "workspace" / "modes.txt"
        path.write_text("ıx IX ix\n" + "ﬃ " * 9)
        default = read(client, page, fixture, op="find", path=path.name, query="IX")["output"]
        sensitive = read(client, page, fixture, op="find", path=path.name, query="IX", case_sensitive=True)["output"]
        folded = read(client, page, fixture, op="find", path=path.name, query="IX", casefold=True)["output"]
        assert [m["start_char"] for m in default["matches"]] == [0, 3, 6]
        assert [m["start_char"] for m in sensitive["matches"]] == [3]
        assert sensitive["case_mode"] == "case_sensitive"
        assert [m["start_char"] for m in folded["matches"]] == [3, 6]
        limited = read(client, page, fixture, op="find", path=path.name, query="FFI",
                       casefold=True, max_matches=8)["output"]
        assert len(limited["matches"]) == 8 and limited["more_matches"]
        assert not limited["searched_entire_redacted_view"]
        assert all(m["end_char"] - m["start_char"] == 1 for m in limited["matches"])
        partial = read(client, page, fixture, op="find", path=path.name, query="fi", casefold=True)["output"]
        assert partial["matches"] == [] and partial["searched_entire_redacted_view"]


def test_casefold_search_hides_credentials_and_follow_rechecks_hidden_bytes(tmp_path, monkeypatch):
    fixture = ReadingFixture(monkeypatch.setenv)
    original = "password=Straße-secret-example\r\nß 🌍 Straße source.\r\n"
    visible, hidden = _view(original)
    assert hidden
    with client_for(tmp_path, model_factory=fixture.factory) as client:
        page = work(client)
        path = tmp_path / "workspace" / "hidden.txt"
        path.write_bytes(original.encode())
        found = read(client, page, fixture, op="find", path=path.name, query="STRASSE", casefold=True)["output"]
        assert found["redacted"] and len(found["matches"]) == 1
        assert found["matches"][0]["start_char"] == visible.index("Straße")
        assert found["source"]["view_digest"] == hashlib.sha256(visible.encode()).hexdigest()
        assert "Straße-secret-example" not in json.dumps(fixture.requests, ensure_ascii=False)
        changed = original.replace("secret-example", "different-example")
        assert _view(changed) == _view(original)
        path.write_bytes(changed.encode())
        refused = read(client, page, fixture, op="follow", receipt_id=found["receipt_id"], match_index=0)
        assert refused["status"] == "failed" and refused["error_code"] == "reading_source_changed"


@pytest.mark.parametrize("options", [
    {"casefold": "true"}, {"casefold": 1}, {"casefold": None},
    {"casefold": True, "case_sensitive": True},
])
def test_casefold_rejects_ambiguous_or_untyped_modes(options):
    with pytest.raises(ProviderProtocolError):
        validate({"op": "find", "path": "source.txt", "query": "phrase", **options})


def test_casefold_flag_is_only_a_find_option():
    with pytest.raises(ProviderProtocolError):
        validate({"op": "open", "path": "source.txt", "casefold": True})
