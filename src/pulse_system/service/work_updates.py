"""A small, read-only return surface for Work owned by the selected subject.

No prompts, tool arguments, credentials or conversation content enter the feed.
The feed is navigation, never action authority, and never wakes a model.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from pulse_system.service.runtime import ServiceError

if TYPE_CHECKING:
    from pulse_system.service.runtime import RuntimeService

_KINDS = {"waiting_approval": "approval", "failed": "review", "blocked": "review",
          "succeeded": "reply", "cancelled": "stopped"}


def work_updates(runtime: RuntimeService, engram_id: str, *, limit: int = 50) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ServiceError("work_updates_invalid", "limit must be between 1 and 100",
                           "use a bounded result count", status=400)
    kernel = runtime.kernel_service
    if kernel is None:
        raise ServiceError("work_updates_unavailable", "this runtime has no native Work contexts",
                           "use the existing work list", status=409)
    if runtime.storage.get_engram(engram_id) is None:
        raise ServiceError("unknown_engram", "the selected subject does not exist",
                           "select a current conversation", status=404)
    owned = {"work:" + front["center_id"]: front for front in runtime.list_task_fronts()
             if front["focal_engram_id"] == engram_id}
    turns = kernel.store.latest_work_turns(runtime.world_id, engram_id, limit=limit + 1)
    items = []
    for turn in turns[:limit]:
        scope = turn.scope
        front = owned.get(scope.context_id)
        if front is None:
            continue
        # A context label alone is not evidence of the native session identity.
        if scope.session_scope() != runtime.harness.scope_for(engram_id, scope.context_id):
            continue
        kind = _KINDS[turn.status.value]
        token = hashlib.sha256(json.dumps([
            runtime.world_id, engram_id, scope.session_id, scope.turn_id,
            turn.revision, turn.status.value,
        ], separators=(",", ":")).encode()).hexdigest()
        items.append({
            "front_id": front["id"], "center_id": front["center_id"],
            "title": front["title"], "context_id": scope.context_id,
            "turn_id": scope.turn_id, "status": turn.status.value, "kind": kind,
            "updated_at": turn.updated_at, "token": token,
        })
    return {"schema_version": 1, "world_id": runtime.world_id, "engram_id": engram_id,
            "coverage": "latest_native_work_turns", "items": items,
            "has_more": len(turns) > limit}
