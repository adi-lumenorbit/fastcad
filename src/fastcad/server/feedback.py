"""Feedback bundle writer.

Receives multipart POST from web/feedback.js. Writes everything for one
report into `tmp/feedback/<ISO_TS>/` so the AI agent can read it later.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse


router = APIRouter()


def _root() -> Path:
    return Path(os.environ.get("FASTCAD_FEEDBACK_DIR", "tmp/feedback"))


def _new_dir() -> Path:
    ts = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    d = _root() / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/feedback")
async def feedback(
    description: str = Form(""),
    target: str = Form("{}"),
    rrweb_events: str = Form("[]"),
    camera: str = Form("{}"),
    oplog: str = Form("[]"),
    ws_log: str = Form("[]"),
    dom_png: UploadFile | None = File(None),
    viewer_png: UploadFile | None = File(None),
):
    try:
        target_obj = json.loads(target)
        rrweb_obj = json.loads(rrweb_events)
        camera_obj = json.loads(camera)
        oplog_obj = json.loads(oplog)
        ws_log_obj = json.loads(ws_log)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"invalid json field: {exc}")

    d = _new_dir()
    (d / "description.txt").write_text(description, encoding="utf-8")
    (d / "target.json").write_text(json.dumps(target_obj, indent=2), encoding="utf-8")
    (d / "rrweb.json").write_text(json.dumps(rrweb_obj), encoding="utf-8")
    (d / "camera.json").write_text(json.dumps(camera_obj, indent=2), encoding="utf-8")
    (d / "oplog.json").write_text(json.dumps(oplog_obj, indent=2), encoding="utf-8")
    (d / "ws_log.json").write_text(json.dumps(ws_log_obj, indent=2), encoding="utf-8")

    if dom_png is not None:
        (d / "dom.png").write_bytes(await dom_png.read())
    if viewer_png is not None:
        (d / "viewer.png").write_bytes(await viewer_png.read())

    return JSONResponse({"ok": True, "dir": str(d.relative_to(_root().parent))})
