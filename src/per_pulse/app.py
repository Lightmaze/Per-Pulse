"""Small local HTTP adapter for Per Pulse's bilingual session-page client.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pulse_system.service.runtime import ServiceError

from . import __version__
from .language import ResponseLanguage
from .service import PageError, PageService, _ENV_PROVIDER

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Content = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32_000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


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
    content: Content
    references: list[ReferenceInput] = Field(default_factory=list, max_length=20)


class NewReference(ReferenceInput):
    sourceMessageId: Identifier | None = None


class ReturnHome(Input):
    content: Content
    messageId: Identifier | None = None


class Rename(Input):
    title: Title


class SettingsInput(Input):
    responseLanguage: ResponseLanguage


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


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
    web_dir: str | Path | None = None,
    model_factory: Any = _ENV_PROVIDER,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pages = await asyncio.to_thread(
            PageService, data_dir, example=example, model_factory=model_factory,
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
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                host = request.headers.get("host", "")
                same_origin = parsed.netloc == host and parsed.scheme in {"http", "https"}
                dev_origin = origin in {"http://localhost:5173", "http://127.0.0.1:5173"}
                if not same_origin and not dev_origin:
                    return _error("origin_not_allowed", "Open Per Pulse from its local address.", 403)
            if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
                if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                    return _error("json_required", "Send this request as JSON.", 415)
                if len(await request.body()) > 128 * 1024:
                    return _error("request_too_large", "This request is too large.", 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PageError)
    async def page_error(_request: Request, error: PageError):
        return _error(error.code, error.message, error.status)

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, error: ServiceError):
        return _error(error.error, error.detail, error.status)

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

    @app.patch("/api/settings")
    def update_settings(body: SettingsInput, request: Request):
        return pages(request).update_settings(body.responseLanguage)

    @app.get("/api/pages/{page_id}")
    def page(page_id: str, request: Request):
        return pages(request).view(page_id)

    @app.post("/api/pages", status_code=201)
    def create(body: NewPage, request: Request):
        return pages(request).create_page(
            body.title, body.instruction, body.sourcePageId, body.sourceMessageId,
        )

    @app.post("/api/pages/{page_id}/messages", status_code=202)
    def message(page_id: str, body: NewMessage, request: Request):
        return pages(request).send(page_id, body.content, [r.model_dump() for r in body.references])

    @app.post("/api/pages/{page_id}/references", status_code=201)
    def reference(page_id: str, body: NewReference, request: Request):
        return pages(request).add_reference(page_id, body.targetPageId, body.sourceMessageId, body.label)

    @app.post("/api/pages/{page_id}/return", status_code=201)
    def return_home(page_id: str, body: ReturnHome, request: Request):
        return pages(request).return_home(page_id, body.content, body.messageId)

    @app.patch("/api/pages/{page_id}")
    def rename(page_id: str, body: Rename, request: Request):
        return pages(request).rename(page_id, body.title)

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
