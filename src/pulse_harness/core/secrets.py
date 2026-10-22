"""Typed opaque secret references and high-confidence write guards.

The kernel may persist a reference to an operator-managed secret, never the
secret value itself.  This module deliberately contains no resolver or
keystore: resolving a reference belongs to an adapter at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping
from typing import Any

from .errors import RedactionRequired


SECRET_REF_MARKER = "$secret_ref"
_REFERENCE_KEYS = frozenset({"api_key_ref", "secret_ref"})
_STRUCTURED_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "passwd",
        "private_key",
        "secret",
        "token",
    }
)
_BEARER_OR_BASIC = re.compile(
    r"(?i)\b(?:bearer|basic)\s+(?P<value>[A-Za-z0-9._~+/=-]{8,})\b"
)
_KNOWN_TOKEN_PREFIX = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_-]{8,}|"
    r"github_pat_[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AKIA[0-9A-Z]{16})"
)
_JWT_PREFIX = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|password|passwd|secret|authorization)\b"
    r"\s*[:=]\s*['\"]?(?P<value>[^\s,;}\"']+)"
)
_PLACEHOLDER = re.compile(r"^[A-Z0-9_.-]+$")


def _placeholder_or_marker(value: str) -> bool:
    """Recognize values that are visibly fixtures/redaction markers.

    Test fixtures often use names such as ``ACTION_SECRET``.  They are not
    treated as credentials so the legacy bounded/redacted evidence remains
    useful; real-looking tokens still fail before a database write.
    """

    upper = value.upper()
    fixture_name = bool(_PLACEHOLDER.fullmatch(value)) or value.lower().endswith(
        (".txt", ".bin")
    ) or value.lower() in {"do-not-store", "not-store"}
    return (
        value in {"[REDACTED]", "[TRUNCATED]"}
        or fixture_name
        and any(
            marker in upper
            for marker in (
                "SECRET",
                "PLACEHOLDER",
                "FIXTURE",
                "TEST_TOKEN",
                "DO-NOT-STORE",
                "NOT-STORE",
            )
        )
    )


def looks_like_bare_credential(value: str) -> bool:
    """Return true only for high-confidence credential-shaped text."""

    if not isinstance(value, str) or not value.strip() or _placeholder_or_marker(value):
        return False
    candidate = value.strip()
    bearer = _BEARER_OR_BASIC.search(candidate)
    known_token = _KNOWN_TOKEN_PREFIX.search(candidate)
    return bool(
        (bearer is not None and not _placeholder_or_marker(bearer.group("value")))
        or (known_token is not None and not _placeholder_or_marker(known_token.group(0)))
        or _JWT_PREFIX.search(candidate)
        or any(
            not _placeholder_or_marker(match.group("value"))
            and len(match.group("value")) >= 8
            for match in _SECRET_ASSIGNMENT.finditer(candidate)
        )
    )


@dataclass(frozen=True, slots=True)
class SecretRef:
    """An opaque, provider-neutral pointer to an externally held secret."""

    identifier: str
    provenance: str

    def __post_init__(self) -> None:
        for field_name in ("identifier", "provenance"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"SecretRef {field_name} must be non-empty")
            if len(value) > 512 or any(ord(char) < 32 for char in value):
                raise ValueError(f"SecretRef {field_name} is invalid")
        if looks_like_bare_credential(self.identifier) or looks_like_bare_credential(
            self.provenance
        ):
            raise RedactionRequired("secret reference metadata looks like a credential")

    def as_dict(self) -> dict[str, dict[str, str]]:
        """Return the only JSON shape admitted to durable payloads."""

        return {
            SECRET_REF_MARKER: {
                "identifier": self.identifier,
                "provenance": self.provenance,
            }
        }

    @classmethod
    def from_value(cls, value: Any, *, default_provenance: str) -> "SecretRef":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value, default_provenance)
        if isinstance(value, Mapping):
            payload = value.get(SECRET_REF_MARKER, value)
            if not isinstance(payload, Mapping):
                raise RedactionRequired("secret reference payload is not an object")
            identifier = payload.get("identifier", payload.get("id"))
            provenance = payload.get("provenance", payload.get("source", default_provenance))
            if not isinstance(identifier, str) or not isinstance(provenance, str):
                raise RedactionRequired("secret reference requires identifier and provenance")
            return cls(identifier, provenance)
        raise RedactionRequired("secret reference value is not opaque")


def is_secret_ref(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {SECRET_REF_MARKER}
        and isinstance(value.get(SECRET_REF_MARKER), Mapping)
        and set(value[SECRET_REF_MARKER]) == {"identifier", "provenance"}
    )


def normalize_secret_refs(value: Any) -> Any:
    """Normalize ``SecretRef`` and ``api_key_ref`` compatibility values."""

    if isinstance(value, SecretRef):
        return value.as_dict()
    if isinstance(value, Mapping):
        if SECRET_REF_MARKER in value:
            return SecretRef.from_value(value, default_provenance="opaque").as_dict()
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key.lower() in _REFERENCE_KEYS:
                result[key] = SecretRef.from_value(
                    raw_value,
                    default_provenance=f"compat:{key.lower()}",
                ).as_dict()
            else:
                result[key] = normalize_secret_refs(raw_value)
        return result
    if isinstance(value, (list, tuple)):
        return [normalize_secret_refs(item) for item in value]
    return value


def reject_bare_credentials(value: Any, *, path: str = "payload") -> None:
    """Reject high-confidence raw credentials before durable serialization."""

    if isinstance(value, SecretRef) or is_secret_ref(value):
        return
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            if key.lower() in _REFERENCE_KEYS:
                # normalize_secret_refs should have handled this first; a
                # non-canonical value here is never allowed to be persisted.
                if not is_secret_ref(child):
                    raise RedactionRequired(
                        f"{path}.{key} is not a canonical SecretRef"
                    )
                continue
            if key.lower() in _STRUCTURED_SECRET_KEYS:
                if is_secret_ref(child):
                    continue
                if isinstance(child, str) and (
                    not child.strip()
                    or _placeholder_or_marker(child)
                ):
                    pass
                else:
                    raise RedactionRequired(
                        f"{path}.{key} contains a raw credential; use SecretRef"
                    )
            reject_bare_credentials(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            reject_bare_credentials(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and looks_like_bare_credential(value):
        raise RedactionRequired(f"{path} contains a raw credential; use SecretRef")
