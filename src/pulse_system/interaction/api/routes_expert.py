"""Authenticated operator experience/feedback; deliberately not a model tool."""
import json
from fastapi import APIRouter, Depends, HTTPException, Request, Response


def create_expert_router(runtime, security):
    def authorize(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not security.accepts_authorization(request.headers.get("authorization")):
            raise HTTPException(401, "startup_token_required", headers={"Cache-Control": "no-store"})
        if request.method != "GET" and not security.write_enabled:
            raise HTTPException(403, "profile_is_read_only")

    router = APIRouter(prefix="/v1/product/expert", dependencies=[Depends(authorize)])

    def store():
        harness = getattr(runtime, "harness", None)
        expert = getattr(harness, "expert", None)
        if expert is None:
            raise HTTPException(409, "expert_computation_disabled")
        harness._check()
        return expert.store

    def invoke(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError):
            raise HTTPException(400, "invalid_expert_input_or_conflicting_judgment") from None

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 4 * 1024 * 1024:
                raise HTTPException(413, "expert_input_too_large")
            data.extend(chunk)
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "invalid_expert_json") from None
        if not isinstance(value, dict):
            raise HTTPException(400, "expert_object_required")
        return value

    @router.get("/status")
    def status():
        return store().status()

    @router.get("/decisions")
    def decisions(limit: int = 20):
        return invoke(store().decisions, limit=limit)

    @router.get("/decisions/{identity}")
    def decision(identity: str):
        return invoke(store().decision, identity)

    @router.post("/episodes")
    async def episodes(request: Request):
        value = await body(request)
        if set(value) != {"episodes", "owner"}:
            raise HTTPException(400, "expert_episode_fields_invalid")
        return {"imported": invoke(store().import_episodes, value["episodes"], owner=value["owner"])}

    @router.post("/decisions/{identity}/feedback")
    async def feedback(identity: str, request: Request):
        value = await body(request)
        if not {"score", "reason", "evidence"} <= set(value) or set(value) - {"score", "reason", "evidence", "preferred_episode"}:
            raise HTTPException(400, "expert_feedback_fields_invalid")
        return invoke(store().feedback, identity, **value)

    @router.post("/decisions/{identity}/capture")
    async def capture(identity: str, request: Request):
        value = await body(request)
        if set(value) != {"episode_id", "guidance", "project"}:
            raise HTTPException(400, "expert_capture_fields_invalid")
        return invoke(store().capture, identity, **value)

    return router
