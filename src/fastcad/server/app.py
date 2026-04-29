"""FastAPI app: static `web/` + `/ws` + `/feedback` + `/healthz`.

Single-user local app; every connection gets its own session.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import ws as ws_handler
from .feedback import router as feedback_router


def _web_dir() -> Path:
    # repo_root/web — package lives at repo_root/src/fastcad/server/app.py
    return Path(__file__).resolve().parents[3] / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="fastcad")

    web = _web_dir()
    if not web.exists():
        raise RuntimeError(f"web/ dir missing at {web}")

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
