"""Small capability/provenance contracts for fail-closed adapter selection."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Iterable, Mapping

from .errors import CapabilityDenied


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    values: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_iterable(cls, values: Iterable[str]) -> "CapabilitySet":
        clean = frozenset(value.strip() for value in values if value and value.strip())
        return cls(clean)

    def contains(self, required: str) -> bool:
        return required in self.values

    def require(self, required: str) -> None:
        if required and required not in self.values:
            raise CapabilityDenied(f"missing capability: {required}")

    def intersect(self, other: "CapabilitySet") -> "CapabilitySet":
        return CapabilitySet(self.values & other.values)

    @property
    def digest(self) -> str:
        encoded = json.dumps(sorted(self.values), separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    source_kind: str
    adapter: str
    verified: bool
    live_gate: str
    capabilities: CapabilitySet
    details: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind,
            "adapter": self.adapter,
            "verified": self.verified,
            "live_gate": self.live_gate,
            "capabilities": sorted(self.capabilities.values),
            "capability_digest": self.capabilities.digest,
            "details": dict(self.details),
        }
