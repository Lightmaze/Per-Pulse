"""Optional pre-publication response review, independent of any plugin.

The reviewer supplies context; only TurnRunner may admit that context into a
durable model continuation. The stored envelope is host authority, while its
``context`` remains the reviewer's explicitly labelled, untrusted material.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from threading import Event
from typing import Any, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import ModelRequest


RESPONSE_REVIEW_PREFIX = "response-review:"
_ENVELOPE_KIND = "pulse.response_review.v1"


@dataclass(frozen=True, slots=True)
class ResponseReview:
    context: str | None = None
    receipt: Mapping[str, Any] = field(default_factory=dict)


class ResponseReviewer(Protocol):
    def review(
        self,
        request: ModelRequest,
        response_text: str,
        *,
        round_id: str,
        cancel_event: Event,
    ) -> ResponseReview: ...


def encode_review_envelope(
    *,
    round_id: str,
    response_id: str,
    response_digest: str,
    candidate_text: str | None,
    context: str | None,
    receipt: Mapping[str, Any],
) -> str:
    """Encode exact candidate authority and a restart-stable review decision."""

    value = {
        "kind": _ENVELOPE_KIND,
        "round_id": round_id,
        "response_id": response_id,
        "response_digest": response_digest,
        "candidate_text": candidate_text,
        "context": context,
        "receipt": dict(receipt),
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    decode_review_envelope(encoded)
    return encoded


def decode_review_envelope(content: str) -> dict[str, Any]:
    """Reject malformed, ambiguous, or noncanonical durable review envelopes."""

    value = json.loads(content)
    expected = {
        "kind", "round_id", "response_id", "response_digest",
        "candidate_text", "context", "receipt",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("response review envelope fields are invalid")
    if value["kind"] != _ENVELOPE_KIND:
        raise ValueError("response review envelope version is invalid")
    if not all(
        isinstance(value[key], str) and value[key].strip()
        for key in ("round_id", "response_id", "response_digest")
    ) or not isinstance(value["receipt"], dict):
        raise ValueError("response review envelope identity is invalid")
    context = value["context"]
    candidate = value["candidate_text"]
    if context is None:
        if candidate is not None:
            raise ValueError("unmodified responses do not need a private candidate")
    elif (
        not isinstance(context, str) or not context.strip()
        or not isinstance(candidate, str) or not candidate.strip()
    ):
        raise ValueError("response review context requires an exact nonempty candidate")
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if canonical != content:
        raise ValueError("response review envelope is not canonical")
    return value
