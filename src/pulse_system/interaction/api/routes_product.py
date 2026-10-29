"""Product material/artefact entry points. Every content route requires a token."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pulse_system.product.files import FileAccessError, MAX_FILE_BYTES, WorkspaceFiles
from .security import LocalApiSecurity


def create_product_router(workspace: Path, security: LocalApiSecurity) -> APIRouter:
    def authorize(request: Request):
        if not security.accepts_authorization(request.headers.get("authorization")):
            raise HTTPException(401, detail="startup_token_required", headers={"Cache-Control": "no-store"})
    router = APIRouter(prefix="/v1/product", dependencies=[Depends(authorize)])
    files = WorkspaceFiles(workspace)

    def call(operation, *args):
        try:
            return operation(*args)
        except FileAccessError as exc:
            raise HTTPException(exc.status, detail=exc.code, headers={"Cache-Control": "no-store"}) from None

    @router.get("/files")
    def listing(response: Response, path: str = ""):
        response.headers["Cache-Control"] = "no-store"
        return call(files.list, path)

    @router.get("/files/content")
    def content(response: Response, path: str):
        response.headers["Cache-Control"] = "no-store"
        return call(files.read, path)

    @router.get("/files/download")
    def download(path: str):
        item = call(files.read, path)
        return Response(item["content"].encode("utf-8"), media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(Path(path).name, safe=""),
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})

    @router.post("/files/upload", status_code=201)
    async def upload(request: Request, response: Response, name: str):
        # Raw bounded request avoids a new multipart parser dependency and
        # never allocates an unbounded body, including chunked requests.
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_FILE_BYTES:
                raise HTTPException(413, detail="file_too_large")
            data.extend(chunk)
        response.headers["Cache-Control"] = "no-store"
        return call(files.upload, name, bytes(data))

    return router
