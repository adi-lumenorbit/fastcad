"""FastAPI app: static `web/` + `/ws` + `/feedback` + `/healthz`.

Single-user local app; every connection gets its own session.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import ws as ws_handler
from .feedback import router as feedback_router


class _NoStaticCacheMiddleware(BaseHTTPMiddleware):
    """Tell the browser to revalidate static assets on every load.

    Default StaticFiles ships ETag + Last-Modified, but the browser
    is free to serve a memory-cached copy without re-asking — which
    is how a refresh after a JS edit can keep showing the old code.
    `Cache-Control: no-cache` forces a conditional revalidation
    while still letting the 304 path work, so the network cost is
    one round-trip per reload instead of a re-download.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.endswith((".js", ".css", ".html")) or path == "/":
            response.headers["Cache-Control"] = "no-cache"
        return response


def _web_dir() -> Path:
    # repo_root/web — package lives at repo_root/src/fastcad/server/app.py
    return Path(__file__).resolve().parents[3] / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="fastcad")

    web = _web_dir()
    if not web.exists():
        raise RuntimeError(f"web/ dir missing at {web}")

    app.add_middleware(_NoStaticCacheMiddleware)
    app.include_router(feedback_router)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws_handler.handle(ws)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web / "index.html")

    app.mount("/", StaticFiles(directory=str(web)), name="web")
    return app


app = create_app()
