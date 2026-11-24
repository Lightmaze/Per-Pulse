"""Opaque execution scope and fencing identity."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True, slots=True)
class HarnessScope:
    """Complete identity used to fence every Harness write."""

    world_id: str
    engram_id: str
    session_id: str
    epoch: int
    owner_id: str
    context_id: str = ""
    turn_id: str = ""
    capability_digest: str = ""

    def __post_init__(self) -> None:
        for field_name in ("world_id", "engram_id", "session_id", "owner_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.epoch, int) or isinstance(self.epoch, bool):
            raise ValueError("epoch must be an integer")
        if self.epoch < 0:
            raise ValueError("epoch must be non-negative")
        for field_name in ("context_id", "turn_id", "capability_digest"):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"{field_name} must be a string")

    @property
    def session_key(self) -> tuple[str, str, str, str]:
        return (self.world_id, self.engram_id, self.context_id, self.session_id)

    @property
    def action_key(self) -> tuple[str, str, str, str, str, int, str]:
        return (
            self.world_id,
            self.engram_id,
            self.context_id,
            self.session_id,
            self.turn_id,
            self.epoch,
            self.owner_id,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "world_id": self.world_id,
            "engram_id": self.engram_id,
            "context_id": self.context_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "epoch": self.epoch,
            "owner_id": self.owner_id,
            "capability_digest": self.capability_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "HarnessScope":
        return cls(
            world_id=str(value["world_id"]),
            engram_id=str(value["engram_id"]),
            context_id=str(value.get("context_id", "")),
            session_id=str(value["session_id"]),
            turn_id=str(value.get("turn_id", "")),
            epoch=int(value["epoch"]),
            owner_id=str(value["owner_id"]),
            capability_digest=str(value.get("capability_digest", "")),
        )

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def for_turn(self, turn_id: str) -> "HarnessScope":
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError("turn_id must be a non-empty string")
        return HarnessScope(
            world_id=self.world_id,
            engram_id=self.engram_id,
            context_id=self.context_id,
            session_id=self.session_id,
            turn_id=turn_id,
            epoch=self.epoch,
            owner_id=self.owner_id,
            capability_digest=self.capability_digest,
        )

    def session_scope(self) -> "HarnessScope":
        return HarnessScope(
            world_id=self.world_id,
            engram_id=self.engram_id,
            context_id=self.context_id,
            session_id=self.session_id,
            epoch=self.epoch,
            owner_id=self.owner_id,
            capability_digest=self.capability_digest,
        )
