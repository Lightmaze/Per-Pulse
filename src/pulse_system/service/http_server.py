"""Shared HTTP serving policy for the product and its rendered integration test."""
from __future__ import annotations

HTTP_CONNECTION_DRAIN_SECONDS = 3


def run_http_server(app, *, host: str, port: int, verbose: bool = False) -> None:
    """Bound live-connection drain so SSE cannot block the runtime's lifespan stop.

    This timeout only governs HTTP requests after termination is requested. The
    lifespan still owns runtime cancellation, worker joining and lease release;
    a disconnected observer never grants permission to replay or abandon work.
    """
    import uvicorn

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="debug" if verbose else "warning",
        timeout_graceful_shutdown=HTTP_CONNECTION_DRAIN_SECONDS,
    )
