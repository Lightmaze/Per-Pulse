"""Small local HTTP adapter for Per Pulse's English session-page client.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import Annotated, Any, Literal
import unicodedata
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from pulse_system.service.runtime import ServiceError
from pulse_system.product.files import FileAccessError, MAX_FILE_BYTES

from . import __version__
from .language import ResponseLanguage
from .execution import ExecutionError
from .materials import MaterialError, MAX_FILE_BYTES
from .service import PageError, PageService, PulseSettings, _ENV_PROVIDER

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32_000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
MessageQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferenceInput(Input):
    targetPageId: Identifier
    label: Title | None = None


class NewPage(Input):
    title: Title
    instruction: Content
    sourcePageId: Identifier = "home"
    sourceMessageId: Identifier | None = None


class NewMessage(Input):
    content: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32_000)]
    references: list[ReferenceInput] = Field(default_factory=list, max_length=20)
    materials: list[Identifier] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def has_content(self):
        if not self.content and not self.materials:
            raise ValueError("Enter a message or attach a source file.")
        return self


class NewReference(ReferenceInput):
    sourceMessageId: Identifier | None = None


class ExistingMaterial(Input):
    path: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class TransferToWork(Input):
    targetPageId: Identifier
    messageId: Identifier
    excerpt: Content
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)]


class ReturnHome(Input):
    content: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32_000)]
    messageId: Identifier | None = None
    includeMaterials: bool = False

    @model_validator(mode="after")
    def has_content(self):
        if not self.content and not self.includeMaterials:
            raise ValueError("Select text or include the attached source files.")
        return self


class Rename(Input):
    title: Title


class StopTurn(Input):
    turnId: Identifier


class DecideAction(Input):
    decision: Literal["allow_once", "deny", "cancel"]


class SettingsInput(Input):
    responseLanguage: ResponseLanguage


class ConnectionPages(Input):
    sourcePageId: Identifier
    targetPageId: Identifier


class ContextConnectionInput(ConnectionPages):
    weight: float = Field(default=0.6, ge=0, le=1, allow_inf_nan=False)
    kind: Literal["excitatory", "inhibitory"] = "excitatory"


class PulseControlInput(Input):
    paused: bool = Field(strict=True)


class ExperienceShift(Input):
    understanding: Content
    candidates: Content
    delivery: Content


class NewExperience(Input):
    sourceMessageId: Identifier
    feedbackMessageId: Identifier
    title: Title
    lesson: Content
    conditions: Any
    counterexamples: list[Content] = Field(min_length=1, max_length=8)
    shift: ExperienceShift
    scope: Literal["context", "subject"] = "context"


class ReviseExperience(Input):
    expectedRevision: int = Field(ge=1, strict=True)
    changes: dict[str, Any]
    # Operator-authored evidence, not a purported new native user message.
    evidence: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)] | None = None


class NewExample(Input):
    sourceMessageId: Identifier
    feedbackMessageId: Identifier | None = None
    title: Title
    # Preserve original whitespace; the native source validator checks exact
    # membership and the stricter UTF-8 byte bound before journal admission.
    excerpt: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    note: Annotated[str, StringConstraints(max_length=4000)] = ""


class ExperienceFeedback(Input):
    expectedRevision: int = Field(ge=1, strict=True)
    verdict: Literal["useful", "not_useful", "harmful", "withdrawn"]
    evidence: Content
    resultMessageId: Identifier | None = None


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _file_download(content: bytes, name: str, digest: str, *, headers: dict | None = None) -> Response:
    """Serve already-verified bytes without reopening their filesystem path."""
    filename = "".join("_" if unicodedata.category(c).startswith("C") else c
                       for c in name.rsplit("/", 1)[-1]).strip(" .") or "result.txt"
    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)
    return Response(content, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}',
        "ETag": '"' + digest + '"', "X-Per-Pulse-Content-SHA256": digest,
        **(headers or {}),
    })


def default_web_dir() -> Path:
    """Prefer packaged assets, then a Vite build in the source checkout."""
    packaged = Path(__file__).parent / "static"
    if (packaged / "index.html").is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(
    data_dir: str | Path,
    *,
    example: bool = False,
    allow_workspace_actions: bool = False,
    web_dir: str | Path | None = None,
    model_factory: Any = _ENV_PROVIDER,
    pulse: PulseSettings | None = None,
    posture: bool | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pages = await asyncio.to_thread(
            PageService, data_dir, example=example, model_factory=model_factory, pulse=pulse,
            posture=posture, allow_workspace_actions=allow_workspace_actions,
        )
        app.state.pages = pages
        try:
            await pages.start()
            yield
        finally:
            await pages.close()

    app = FastAPI(title="Per Pulse", version=__version__, lifespan=lifespan)
    # This first release is an unauthenticated, single-user loopback app.
    # Reject DNS-rebinding Host headers instead of treating a random domain
    # resolving to localhost as a trusted browser surface.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_browser_boundary(request: Request, call_next):
        is_api = request.url.path.startswith("/api/")

        def secure_response(response):
            # The local workspace exposes approval controls. A remote page
            # must not frame them and trick a user into approving an action.
            # Apply this even to rejected requests, not only successful ones.
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            if is_api:
                response.headers["Cache-Control"] = "no-store"
            return response

        if is_api:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                host = request.headers.get("host", "")
                same_origin = parsed.netloc == host and parsed.scheme in {"http", "https"}
                dev_origin = origin in {"http://localhost:5173", "http://127.0.0.1:5173"}
                if not same_origin and not dev_origin:
                    return secure_response(_error("origin_not_allowed", "Open Per Pulse from its local address.", 403))
            if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
                file_upload = request.method == "POST" and request.url.path == "/api/workspace/files"
                material_upload = request.method == "POST" and re.fullmatch(r"/api/pages/[^/]+/materials", request.url.path)
                upload = file_upload or material_upload
                media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
                if upload and media_type != "application/octet-stream":
                    code = "file_body_required" if file_upload else "material_bytes_required"
                    return secure_response(_error(code, "Send the original file as application/octet-stream.", 415))
                if not upload and media_type != "application/json":
                    return secure_response(_error("json_required", "Send this request as JSON.", 415))
                if not upload and len(await request.body()) > 128 * 1024:
                    return secure_response(_error("request_too_large", "This request is too large.", 413))
        return secure_response(await call_next(request))

    @app.exception_handler(PageError)
    async def page_error(_request: Request, error: PageError):
        return _error(error.code, error.message, error.status)

    @app.exception_handler(ExecutionError)
    async def execution_error(_request: Request, error: ExecutionError):
        return _error(error.code, error.message, error.status)

    @app.exception_handler(MaterialError)
    async def material_error(_request: Request, error: MaterialError):
        return _error(error.code, error.message, error.status)

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, error: ServiceError):
        return _error(error.error, error.detail, error.status)

    @app.exception_handler(FileAccessError)
    async def file_error(_request: Request, error: FileAccessError):
        messages = {
            "file_too_large": "Choose a text file no larger than 1 MiB.",
            "text_files_only": "Choose a UTF-8 text file, such as Markdown, plain text or CSV.",
            "credential_content_blocked": "This file appears to contain a credential. Remove it before sharing the file.",
            "protected_or_invalid_path": "This file name or location cannot be opened here.",
            "invalid_path": "This file location is invalid.",
            "filename_only": "Choose a file name without folders.",
            "file_changed_retry": "The file changed while it was being read. Open it again.",
            "file_unavailable": "This file is no longer available.",
            "path_unavailable": "This folder is no longer available.",
            "not_a_single_regular_file": "This file cannot be opened here.",
            "upload_failed_or_unconfirmed": "The upload could not be confirmed. Check the uploads folder before trying again.",
        }
        return _error(error.code, messages.get(error.code, "This file could not be opened."), error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError):
        fields = ", ".join(dict.fromkeys(str(e["loc"][-1]) for e in error.errors()))
        return _error("invalid_request", f"Check the request fields: {fields}.", 422)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException):
        return _error("http_error", str(error.detail), error.status_code)

    def pages(request: Request) -> PageService:
        return request.app.state.pages

    @app.get("/api/health")
    def health(request: Request):
        return {"status": "ok", "providerConfigured": pages(request).configured}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        return pages(request).bootstrap()

    @app.get("/api/settings")
    def settings(request: Request):
        return pages(request).settings()

    def file_service(request: Request) -> PageService:
        service = pages(request)
        service.require_file_identity(request.headers.get("x-per-pulse-world"),
                                      request.headers.get("x-per-pulse-service-instance"))
        return service

    @app.get("/api/workspace/files")
    def list_files(request: Request, path: str = ""):
        service = file_service(request)
        return service.file_response(service.files.list(path))

    @app.get("/api/workspace/files/content")
    def read_file(request: Request, path: str):
        service = file_service(request)
        return service.file_response(service.files.read(path))

    @app.post("/api/workspace/files", status_code=201)
    async def upload_file(request: Request, name: str):
        service = file_service(request)
        # Bound streamed bodies too; Content-Length is not an authority and
        # must not turn an oversized upload into an unbounded allocation.
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_FILE_BYTES:
                raise FileAccessError("file_too_large", 413)
            data.extend(chunk)
        return await run_in_threadpool(service.upload_file, name, bytes(data),
                                      request.headers.get("x-per-pulse-world"),
                                      request.headers.get("x-per-pulse-service-instance"))

    @app.patch("/api/settings")
    def update_settings(body: SettingsInput, request: Request):
        return pages(request).update_settings(body.responseLanguage)

    @app.get("/api/pulse")
    def pulse_status(request: Request):
        return pages(request).pulse_status()

    @app.get("/api/pulse/observation")
    def pulse_observation(request: Request):
        return pages(request).pulse_observation()

    def pulse_write_service(request: Request) -> PageService:
        service = pages(request)
        service.require_pulse_identity(
            request.headers.get("x-per-pulse-world"),
            request.headers.get("x-per-pulse-service-instance"),
        )
        return service

    @app.patch("/api/pulse")
    def set_pulse_paused(body: PulseControlInput, request: Request):
        return pulse_write_service(request).set_pulse_paused(body.paused)

    @app.patch("/api/pulse/contexts/{page_id}")
    def set_pulse_context_paused(page_id: Identifier, body: PulseControlInput, request: Request):
        return pulse_write_service(request).set_context_paused(page_id, body.paused)

    @app.post("/api/pulse/connections", status_code=201)
    def connect_contexts(body: ContextConnectionInput, request: Request):
        return pulse_write_service(request).connect_pages(body.sourcePageId, body.targetPageId, body.weight, body.kind)

    @app.delete("/api/pulse/connections")
    def disconnect_contexts(body: ConnectionPages, request: Request):
        return pulse_write_service(request).disconnect_pages(body.sourcePageId, body.targetPageId)

    @app.get("/api/pages/{page_id}")
    def page(page_id: str, request: Request, sourceMessageId: Identifier | None = None):
        return pages(request).view(page_id, source_message_id=sourceMessageId)

    @app.post("/api/pages", status_code=201)
    def create(body: NewPage, request: Request):
        return pages(request).create_page(
            body.title, body.instruction, body.sourcePageId, body.sourceMessageId,
        )

    @app.post("/api/pages/{page_id}/messages", status_code=202)
    def message(page_id: str, body: NewMessage, request: Request):
        return pages(request).send(page_id, body.content, [r.model_dump() for r in body.references], body.materials)

    @app.get("/api/pages/{page_id}/materials")
    def materials(page_id: str, request: Request):
        return pages(request).materials.listing(page_id)

    @app.get("/api/pages/{page_id}/materials/{material_id}")
    def material(page_id: str, material_id: str, request: Request):
        return pages(request).materials.read(page_id, material_id)

    @app.get("/api/pages/{page_id}/materials/{material_id}/download", response_class=Response,
             responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    def download_material(page_id: str, material_id: str, request: Request):
        service = file_service(request)
        result = service.materials.download(page_id, material_id)
        return _file_download(result["content"], result["material"]["name"], result["material"]["sha256"],
            headers={"X-Per-Pulse-World": service.runtime.world_id,
                     "X-Per-Pulse-Service-Instance": service.service_instance_id})

    @app.post("/api/pages/{page_id}/materials", status_code=201)
    async def upload_material(page_id: str, request: Request, name: str):
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/octet-stream":
            raise MaterialError("material_bytes_required", "Send the original file bytes.", 415)
        service = pages(request)
        service.execution._scope(page_id)
        if request.headers.get("x-per-pulse-world") != service.runtime.world_id:
            raise MaterialError("material_workspace_changed", "The workspace changed. Refresh before uploading.")
        # The first-party browser also fences uploads to the service instance it
        # observed. Retain compatibility with existing World-only native clients.
        if "x-per-pulse-service-instance" in request.headers:
            service.require_file_identity(request.headers.get("x-per-pulse-world"),
                                          request.headers.get("x-per-pulse-service-instance"))
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_FILE_BYTES:
                raise MaterialError("material_too_large", "Choose a text file of at most 1 MiB.", 413)
            data.extend(chunk)
        result = await asyncio.to_thread(service.materials.upload, page_id, name, bytes(data),
            upload_id=request.headers.get("x-per-pulse-upload-id"),
            world_id=request.headers.get("x-per-pulse-world"),
            expected_digest=request.headers.get("x-per-pulse-file-sha256"))
        return service.file_response(result)

    @app.post("/api/pages/{page_id}/materials/reference", status_code=201)
    def reference_material(page_id: str, body: ExistingMaterial, request: Request):
        service = file_service(request)
        return service.file_response(service.materials.reference_existing(
            page_id, body.path, body.sha256,
            world_id=request.headers.get("x-per-pulse-world"),
            service_instance_id=request.headers.get("x-per-pulse-service-instance"),
        ))

    @app.get("/api/pages/{page_id}/messages")
    def earlier_messages(page_id: str, request: Request, beforeMessageId: Identifier):
        return pages(request).history(page_id, before_message_id=beforeMessageId)

    @app.get("/api/pages/{page_id}/messages/search")
    def find_messages(page_id: str, request: Request, q: MessageQuery, beforeMessageId: Identifier | None = None):
        return pages(request).search_messages(page_id, q, before_message_id=beforeMessageId)

    @app.post("/api/pages/{page_id}/references", status_code=201)
    def reference(page_id: str, body: NewReference, request: Request):
        return pages(request).add_reference(page_id, body.targetPageId, body.sourceMessageId, body.label)

    @app.post("/api/pages/{page_id}/return", status_code=201)
    def return_home(page_id: str, body: ReturnHome, request: Request):
        return pages(request).return_home(page_id, body.content, body.messageId, include_materials=body.includeMaterials)

    @app.patch("/api/pages/{page_id}")
    def rename(page_id: str, body: Rename, request: Request):
        return pages(request).rename(page_id, body.title)

    @app.get("/api/pages/{page_id}/posture")
    def learning(page_id: str, request: Request):
        return pages(request).learning.view(page_id)

    @app.post("/api/pages/{page_id}/posture/experiences", status_code=201)
    def learn(page_id: str, body: NewExperience, request: Request):
        return pages(request).learning.create(page_id, body.model_dump())

    @app.post("/api/pages/{page_id}/posture/examples", status_code=201)
    def create_example(page_id: str, body: NewExample, request: Request):
        return pages(request).learning.create_example(page_id, body.model_dump())

    @app.patch("/api/pages/{page_id}/posture/experiences/{experience_id}")
    def revise_experience(page_id: str, experience_id: str, body: ReviseExperience, request: Request):
        return pages(request).learning.revise(page_id, experience_id, body.expectedRevision, body.changes, body.evidence)

    @app.post("/api/pages/{page_id}/posture/experiences/{experience_id}/feedback")
    def experience_feedback(page_id: str, experience_id: str, body: ExperienceFeedback, request: Request):
        return pages(request).learning.feedback(
            page_id, experience_id, body.expectedRevision, body.verdict, body.evidence,
            result_message_id=body.resultMessageId,
        )

    @app.get("/api/pages/{page_id}/execution")
    def execution(page_id: str, request: Request):
        return pages(request).execution.snapshot(page_id)

    @app.get("/api/pages/{page_id}/artifacts")
    def artifacts(page_id: str, request: Request):
        return pages(request).artifacts.snapshot(page_id)

    @app.get("/api/pages/{page_id}/artifacts/{artifact_id}")
    def artifact(page_id: str, artifact_id: str, request: Request):
        return pages(request).artifacts.read(page_id, artifact_id)

    @app.get("/api/pages/{page_id}/artifacts/{artifact_id}/download", response_class=Response,
             responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    def download_artifact(page_id: str, artifact_id: str, request: Request):
        result = pages(request).artifacts.download(page_id, artifact_id)
        # RFC 6266: offer only a basename, with UTF-8 filename* and an ASCII
        # fallback. The browser controls the destination; no file is served by
        # reopening a path after the version/content checks above.
        return _file_download(result["content"], result["artifact"]["path"], result["artifact"]["sha256"])

    @app.post("/api/pages/{page_id}/execution/stop")
    def stop(page_id: str, body: StopTurn, request: Request):
        svc = pages(request)
        return {**svc.execution.cancel(page_id, body.turnId), "view": svc.view(page_id)}

    @app.post("/api/pages/{page_id}/execution/actions/{action_id}")
    def decide(page_id: str, action_id: str, body: DecideAction, request: Request):
        svc = pages(request)
        return {**svc.execution.resolve_approval(page_id, action_id, body.decision), "view": svc.view(page_id)}

    # Mount only the compiled frontend. User data and workspace files are
    # never exposed as static content.
    built = Path(web_dir).resolve() if web_dir is not None else default_web_dir()
    if (built / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=built / "assets"), name="assets")

    @app.get("/{route:path}", include_in_schema=False)
    def frontend(route: str):
        if route == "api" or route.startswith("api/"):
            return _error("not_found", "This API endpoint does not exist.", 404)
        if not (built / "index.html").is_file():
            return _error(
                "frontend_not_built", "Build the web client with npm run build in web/, then restart Per Pulse.", 503,
            )
        candidate = (built / route).resolve()
        if candidate.is_relative_to(built) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(built / "index.html")

    return app
