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


def review_receipts(receipt: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Read bounded host-composed receipts without losing plugin accounting."""
    if receipt.get("plugin") != "response_review_group":
        return (dict(receipt),)
    reviews = receipt.get("reviews")
    if (not isinstance(reviews, list) or len(reviews) > 8
            or any(not isinstance(item, dict) for item in reviews)):
        raise ValueError("invalid combined response review receipts")
    result = tuple(dict(item) for item in reviews)
    if receipt.get("accounting_incomplete"):
        result += ({"plugin": "response_review_group", "accounting_incomplete": True},)
    return result


class CombinedResponseReviewer:
    """One native review boundary; each independent plugin keeps its receipt.

    Plugins inspect the same exact candidate. An ordinary plugin failure does
    not suppress another plugin's contribution or erase its provider usage.
    Cancellation and revoked host authority still stop publication.
    """

    def __init__(
        self, reviewers: tuple[tuple[str, ResponseReviewer], ...],
        *, max_payload_bytes: int = 64 * 1024,
    ):
        if not 1 <= len(reviewers) <= 8:
            raise ValueError("combine between one and eight reviewers")
        if type(max_payload_bytes) is not int or max_payload_bytes < 1024:
            raise ValueError("combined review payload bound must be at least 1024 bytes")
        if any(not isinstance(name, str) or not name.strip() or len(name) > 80
               for name, _ in reviewers):
            raise ValueError("reviewer names must be bounded nonempty text")
        self.reviewers = reviewers
        self.max_payload_bytes = max_payload_bytes

    @staticmethod
    def _failed_receipt(name, reason):
        return {"plugin": name, "status": "skipped", "reason": reason,
                "accounting_incomplete": True}

    def _receipt(self, value):
        from .replay import bound_payload

        if not isinstance(value, Mapping):
            raise ValueError("invalid plugin response review receipt")
        # Round-trip before using replay helpers, which intentionally accept
        # some non-JSON values elsewhere. One NaN or object must not poison
        # another reviewer's valid cost receipt at the shared host boundary.
        encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        receipt = json.loads(encoded)
        clean, redacted, truncated = bound_payload(receipt, self.max_payload_bytes)
        if redacted or truncated or clean != receipt:
            raise ValueError("invalid or oversized plugin review receipt")
        return receipt

    def _fits(self, response_text, round_id, context, receipts):
        from .replay import bound_text

        value = encode_review_envelope(
            round_id=round_id, response_id=round_id + ":response",
            response_digest="0" * 64,
            candidate_text=response_text if context is not None else None,
            context=context,
            receipt={"plugin": "response_review_group", "reviews": receipts},
        )
        clean, redacted, truncated = bound_text(value, self.max_payload_bytes)
        return not redacted and not truncated and clean == value

    def review(self, request, response_text, *, round_id, cancel_event):
        from .errors import LeaseFenced, ScopeConflict, StoreUnavailable
        from .provider import CancellationRequested
        from .replay import bound_text

        contexts, receipts = [], []
        for name, reviewer in self.reviewers:
            if cancel_event.is_set():
                raise CancellationRequested("response review cancelled")
            try:
                result = reviewer.review(
                    request, response_text, round_id=round_id, cancel_event=cancel_event,
                )
                if not isinstance(result, ResponseReview):
                    raise ValueError("invalid plugin response review")
                receipt = self._receipt(result.receipt)
            except (CancellationRequested, LeaseFenced, ScopeConflict, StoreUnavailable):
                raise
            except Exception:
                receipts.append(self._failed_receipt(name, "review_failed_or_invalid"))
                contexts.append(None)
                continue
            context = result.context
            if context is not None:
                try:
                    if not isinstance(context, str) or not context.strip():
                        raise ValueError("invalid plugin review contribution")
                    clean, redacted, truncated = bound_text(context, self.max_payload_bytes)
                    if redacted or truncated or clean != context:
                        raise ValueError("invalid or oversized plugin review contribution")
                except Exception:
                    # The fork already ran: preserve its safe usage receipt
                    # even though its contribution cannot be admitted.
                    context = None
                    receipt.update(context_status="omitted", context_reason="invalid_context")
                else:
                    receipt.update(context_status="omitted", context_reason="review_envelope_budget")
            contexts.append(context)
            receipts.append(receipt)
        if cancel_event.is_set():
            raise CancellationRequested("response review cancelled")

        # Receipts have priority over optional contributions. If individually
        # bounded receipts exceed the aggregate bound, make the lost accounting
        # explicit rather than silently dropping every review in the runner.
        remaining = set(range(len(receipts)))
        while not self._fits(response_text, round_id, None, receipts):
            if not remaining:
                return ResponseReview(receipt={
                    "plugin": "response_review_group", "reviews": [],
                    "status": "skipped", "reason": "review_receipts_exceed_budget",
                    "accounting_incomplete": True,
                })
            index = max(remaining, key=lambda i: len(json.dumps(receipts[i], ensure_ascii=False).encode()))
            remaining.remove(index)
            receipts[index] = self._failed_receipt(self.reviewers[index][0], "review_receipt_exceeds_budget")
            contexts[index] = None

        admitted = []
        for index, context in enumerate(contexts):
            if context is None:
                continue
            receipt = receipts[index]
            receipt.update(context_status="admitted")
            receipt.pop("context_reason", None)
            candidate = "\n\n".join([*admitted, context])
            if self._fits(response_text, round_id, candidate, receipts):
                admitted.append(context)
            else:
                receipt.update(context_status="omitted", context_reason="review_envelope_budget")
        return ResponseReview(
            context="\n\n".join(admitted) or None,
            receipt={"plugin": "response_review_group", "reviews": receipts},
        )


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
