"""Bounded, redacted replay helpers shared by durable stores and observers."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .errors import StoreUnavailable
from .secrets import (
    is_secret_ref,
    normalize_secret_refs,
    reject_bare_credentials,
)


_SECRET_WORDS = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>['\"]?\b(?:api[_-]?key|token|password|passwd|secret|authorization)\b['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"]?)(?P<value>[^\s,;}'\"]+)(?P=quote)"
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?i)(?P<prefix>['\"]?authorization['\"]?\s*[:=]\s*['\"]?)"
    r"(?:bearer|basic)\s+[^\s,;}\"']+['\"]?"
)
_TRUNCATED_TEXT = re.compile(
    r"^\[TRUNCATED sha256=[0-9a-f]{64} redacted=(?P<redacted>[01]) preview="
)


def digest_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    # Exact field names keep ordinary recoverable metadata such as
    # ``token_count`` and ``credential_status`` intact.  High-confidence raw
    # values are rejected separately by secrets.reject_bare_credentials.
    return lowered in _SECRET_WORDS


def redact_text(value: str) -> tuple[str, bool]:
    """Redact common inline secret assignments before they reach SQLite."""

    def replace_authorization(match: re.Match[str]) -> str:
        if "[REDACTED]" in match.group(0):
            return match.group(0)
        return f"{match.group('prefix')}[REDACTED]"

    def replace_assignment(match: re.Match[str]) -> str:
        if match.group("value") == "[REDACTED]":
            return match.group(0)
        return f"{match.group('prefix')}[REDACTED]"

    redacted, auth_count = _AUTHORIZATION_HEADER.subn(
        replace_authorization, value
    )
    redacted, assignment_count = _SECRET_ASSIGNMENT.subn(
        replace_assignment, redacted
    )
    return redacted, bool(auth_count or assignment_count)


def redact_value(value: Any) -> tuple[Any, bool]:
    """Return a JSON-safe value and whether a secret was redacted."""

    if is_secret_ref(value):
        return dict(value), False
    if isinstance(value, Mapping):
        changed = False
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key.lower() in {"api_key_ref", "secret_ref"}:
                # Reference fields are typed opaque pointers, not secret
                # assignments.  Preserve them for adapter-time resolution.
                result[key] = normalize_secret_refs({key: raw_value})[key]
                continue
            if _is_secret_key(key):
                result[key] = "[REDACTED]"
                changed = True
                continue
            clean, child_changed = redact_value(raw_value)
            result[key] = clean
            changed = changed or child_changed
        return result, changed
    if isinstance(value, (list, tuple)):
        values = []
        changed = False
        for item in value:
            clean, child_changed = redact_value(item)
            values.append(clean)
            changed = changed or child_changed
        return values, changed
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value, False
    return str(value), False


def stored_text_flags(value: str) -> tuple[bool, bool]:
    """Recover visibility flags from a canonical persisted text value."""

    match = _TRUNCATED_TEXT.match(value)
    if match is not None:
        return bool(int(match.group("redacted"))), True
    clean, redacted = redact_text(value)
    return redacted or "[REDACTED]" in clean, False


def _contains_redaction_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("_redacted") is True:
            return True
        return any(_contains_redaction_marker(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_redaction_marker(item) for item in value)
    return isinstance(value, str) and "[REDACTED]" in value


def _is_bounded_payload(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("_truncated") is True
        and isinstance(value.get("_redacted"), bool)
        and isinstance(value.get("original_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["original_sha256"]) is not None
        and isinstance(value.get("preview"), str)
    )


def stored_payload_flags(value: Mapping[str, Any]) -> tuple[bool, bool]:
    """Recover redaction/truncation flags from a canonical persisted payload."""

    if _is_bounded_payload(value):
        return bool(value["_redacted"]) or _contains_redaction_marker(
            value["preview"]
        ), True
    _, redacted = redact_value(value)
    truncated = isinstance(value, Mapping) and value.get("_truncated") is True
    return redacted or _contains_redaction_marker(value), truncated


def bound_payload(value: Mapping[str, Any], max_bytes: int) -> tuple[dict[str, Any], bool, bool]:
    """Redact and bound payload bytes without silently dropping evidence."""

    if max_bytes < 256:
        raise ValueError("max_bytes must be at least 256")
    if _is_bounded_payload(value):
        return dict(value), bool(value["_redacted"]) or _contains_redaction_marker(value["preview"]), True
    normalized = normalize_secret_refs(value)
    reject_bare_credentials(normalized)
    clean_value, redacted = redact_value(normalized)
    # Native tools may already have hidden sensitive lines. Their markers must
    # retain the same visibility flag through action rows and event envelopes;
    # replay validation recovers that flag from the persisted content.
    redacted = redacted or _contains_redaction_marker(clean_value)
    clean = dict(clean_value) if isinstance(clean_value, Mapping) else {"value": clean_value}
    encoded = json.dumps(
        clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    if len(encoded) <= max_bytes:
        return clean, redacted, False
    digest = hashlib.sha256(encoded).hexdigest()
    # Keep a valid, bounded JSON object rather than slicing a JSON string.
    bounded = {
        "_truncated": True,
        "_redacted": bool(redacted),
        "original_sha256": digest,
        "preview": "",
    }
    preview_budget = max(0, max_bytes - len(encode_payload(bounded).encode("utf-8")) - 4)
    bounded["preview"] = encoded[:preview_budget].decode("utf-8", errors="replace")
    if len(encode_payload(bounded).encode("utf-8")) > max_bytes and bounded["preview"]:
        # Preserve the existing 16-character clipping result exactly, without
        # repeatedly encoding a large escaped preview once per removed chunk.
        # A shorter JSON string is monotone in encoded size, so find the first
        # fitting chunk count. Persisted projections keep the same digest/text.
        preview = bounded["preview"]
        low, high = 1, (len(preview) + 15) // 16
        while low < high:
            middle = (low + high) // 2
            bounded["preview"] = preview[:max(0, len(preview) - 16 * middle)]
            if len(encode_payload(bounded).encode("utf-8")) <= max_bytes:
                high = middle
            else:
                low = middle + 1
        bounded["preview"] = preview[:max(0, len(preview) - 16 * low)]
    return bounded, redacted, True


def bound_text(value: str, max_bytes: int) -> tuple[str, bool, bool]:
    """Bound a persisted text field while retaining a digest and redaction flag."""

    if max_bytes < 128:
        raise ValueError("max_bytes must be at least 128")
    reject_bare_credentials(value)
    clean, redacted = redact_text(value)
    # Preserve a canonical old truncation marker's declared flag. Rescanning
    # its preview could change metadata for an otherwise valid saved row.
    prior_redacted, prior_truncated = stored_text_flags(clean)
    redacted = redacted or prior_redacted
    encoded = clean.encode("utf-8")
    if len(encoded) <= max_bytes:
        return clean, redacted, prior_truncated
    digest = hashlib.sha256(encoded).hexdigest()
    marker = f"[TRUNCATED sha256={digest} redacted={int(redacted)} preview="
    suffix = "]"
    budget = max(0, max_bytes - len((marker + suffix).encode("utf-8")))
    preview = encoded[:budget].decode("utf-8", errors="replace")
    result = marker + preview + suffix
    while len(result.encode("utf-8")) > max_bytes and preview:
        preview = preview[:-8]
        result = marker + preview + suffix
    return result, redacted, True


def encode_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def decode_payload(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise StoreUnavailable("malformed persisted JSON payload") from exc
    if not isinstance(decoded, Mapping):
        raise StoreUnavailable("persisted JSON payload is not an object")
    return dict(decoded)
