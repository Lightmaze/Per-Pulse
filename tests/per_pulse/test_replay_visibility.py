"""Persisted visibility remains canonical when already-limited text is reused.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
All examples are synthetic; no actual credentials are used.
"""
import pytest

from pulse_harness.core.replay import bound_payload, bound_text


@pytest.mark.parametrize("declared", [0, 1])
@pytest.mark.parametrize("preview", ["Ordinary excerpt", "An existing [REDACTED] marker"])
def test_existing_truncation_text_keeps_its_declared_visibility(declared, preview):
    # Earlier versions could legitimately persist redacted=0 when an original
    # source literally contained a marker. Its canonical bounded representation
    # must not acquire different row flags simply by being reopened.
    text = f"[TRUNCATED sha256={'a' * 64} redacted={declared} preview={preview}]"
    assert bound_text(text, 1024) == (text, bool(declared), True)


def test_a_previously_hidden_native_payload_retains_flags_when_bounded_again():
    payload = {"output": {"content": "Visible before.\n[REDACTED]\nVisible after."}}
    clean, redacted, truncated = bound_payload(payload, 1024)
    assert clean == payload and redacted and not truncated
    assert bound_payload(clean, 1024) == (clean, True, False)
