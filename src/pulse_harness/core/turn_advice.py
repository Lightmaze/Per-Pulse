"""Optional, durable contributions at native model boundaries."""
from dataclasses import dataclass, field
import json
from threading import Event
from typing import Any, Mapping, Protocol

from .provider import ModelRequest


TURN_ADVICE_PREFIX = "turn-advice:"


@dataclass(frozen=True, slots=True)
class TurnAdvice:
    context: str | None = None
    receipt: Mapping[str, Any] = field(default_factory=dict)


class TurnAdvisor(Protocol):
    # Existing advisors run once. A continuing advisor can decline later
    # boundaries with None; that consumes no provider call or transcript item.
    def advise(self, request: ModelRequest, *, cancel_event: Event) -> TurnAdvice | None: ...


def encode_advice(scope_digest: str, advice: TurnAdvice) -> str:
    value = json.dumps({"kind": "pulse.turn_advice.v1", "scope_digest": scope_digest,
                        "context": advice.context, "receipt": dict(advice.receipt)},
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    decode_advice(value)
    return value


def decode_advice(content: str) -> dict:
    value = json.loads(content)
    if (not isinstance(value, dict) or set(value) != {"kind", "scope_digest", "context", "receipt"}
            or value["kind"] != "pulse.turn_advice.v1"
            or not isinstance(value["scope_digest"], str) or not value["scope_digest"]
            or not isinstance(value["receipt"], dict)
            or (value["context"] is not None and (not isinstance(value["context"], str) or not value["context"].strip()))):
        raise ValueError("invalid turn advice envelope")
    if json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) != content:
        raise ValueError("noncanonical turn advice envelope")
    return value
