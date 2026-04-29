"""WebSocket session loop.

Single in-memory session per connection — fastcad is a single-user local
app. The protocol is documented in the plan; in summary the browser sends
{type: prompt|undo|redo|export_scad|reset|user_choice} and receives
{type: scene_init|scene_delta|agent_message|ask_user|scad|tool_log}.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..agent.client import AgentTurn, run_turn
from ..model import kernel as k
from ..model.scad import render as render_scad
from ..session import SessionState


@dataclass
class WSContext:
    session: SessionState = field(default_factory=SessionState)
    transcript: list[dict] = field(default_factory=list)
    pending_ask: dict | None = None
    ws_log: list[dict] = field(default_factory=list)  # outbound + inbound for feedback bundles


def _node_payload(ctx: WSContext, node_id: str) -> dict:
    node = ctx.session.scene.nodes[node_id]
    bb = k.BBox.from_manifold(node.manifold)
    mesh = k.to_mesh_dict(node.manifold)
    return {
        "id": node_id,
        "kind": node.kind,
        "bbox": {"min": [bb.xmin, bb.ymin, bb.zmin], "max": [bb.xmax, bb.ymax, bb.zmax]},
        "mesh": mesh,
    }


def _scene_init(ctx: WSContext) -> dict:
    return {
        "type": "scene_init",
        "nodes": [_node_payload(ctx, nid) for nid in ctx.session.scene.order],
    }


def _scene_delta(ctx: WSContext, added: list[str], updated: list[str], removed: list[str]) -> dict:
    return {
        "type": "scene_delta",
        "added": [_node_payload(ctx, nid) for nid in added if nid in ctx.session.scene.nodes],
        "updated": [_node_payload(ctx, nid) for nid in updated if nid in ctx.session.scene.nodes],
        "removed": removed,
    }


async def _send(ws: WebSocket, ctx: WSContext, payload: dict) -> None:
    ctx.ws_log.append({"dir": "out", "t": time.time(), "type": payload.get("type"),
                       "summary": _summary(payload)})
    await ws.send_text(json.dumps(payload))


def _summary(payload: dict) -> dict:
    """Compact, mesh-free copy used for the ws_log feedback bundle."""
    typ = payload.get("type")
    if typ in {"scene_init", "scene_delta"}:
        return {
            "type": typ,
            "added_ids": [n["id"] for n in payload.get("added", payload.get("nodes", []))],
            "updated_ids": [n["id"] for n in payload.get("updated", [])],
            "removed_ids": list(payload.get("removed", [])),
        }
    if typ == "scad":
        src = payload.get("source", "")
        return {"type": typ, "len": len(src)}
    return {k: v for k, v in payload.items() if k != "mesh"}


async def _emit_turn(ws: WebSocket, ctx: WSContext, turn: AgentTurn) -> None:
    cs = turn.changes
    if cs.added or cs.updated or cs.removed:
        await _send(ws, ctx, _scene_delta(ctx, cs.added, cs.updated, cs.removed))
    if turn.text:
        await _send(ws, ctx, {"type": "agent_message", "text": turn.text})
    if turn.ask_user is not None:
        ctx.pending_ask = turn.ask_user
        await _send(
            ws,
            ctx,
            {"type": "ask_user", "question": turn.ask_user["question"], "options": turn.ask_user["options"]},
        )
    else:
        ctx.pending_ask = None
    if turn.tool_log:
        await _send(ws, ctx, {"type": "tool_log", "calls": turn.tool_log})


async def handle(ws: WebSocket) -> None:
    ctx = WSContext()
    await ws.accept()
    await _send(ws, ctx, _scene_init(ctx))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, ctx, {"type": "error", "message": "invalid json"})
                continue
            ctx.ws_log.append({"dir": "in", "t": time.time(), "type": msg.get("type"), "msg": msg})
            await _dispatch(ws, ctx, msg)
    except WebSocketDisconnect:
        return


async def _dispatch(ws: WebSocket, ctx: WSContext, msg: dict[str, Any]) -> None:
    typ = msg.get("type")
    if typ == "prompt":
        text = str(msg.get("text", ""))
        try:
            turn = run_turn(text, ctx.session, transcript=ctx.transcript, pending_ask=ctx.pending_ask)
        except Exception as exc:  # noqa: BLE001
            await _send(ws, ctx, {"type": "error", "message": f"agent error: {exc}"})
            return
        await _emit_turn(ws, ctx, turn)
        return
    if typ == "user_choice":
        # User picked an option from the previous ask_user.
        text = str(msg.get("text", ""))
        try:
            turn = run_turn(text, ctx.session, transcript=ctx.transcript, pending_ask=ctx.pending_ask)
        except Exception as exc:  # noqa: BLE001
            await _send(ws, ctx, {"type": "error", "message": f"agent error: {exc}"})
            return
        await _emit_turn(ws, ctx, turn)
        return
    if typ == "undo":
        cs = ctx.session.undo()
        await _send(ws, ctx, _scene_init(ctx))
        await _send(ws, ctx, {"type": "agent_message", "text": "undone."})
        return
    if typ == "redo":
        cs = ctx.session.redo()
        await _send(ws, ctx, _scene_init(ctx))
        await _send(ws, ctx, {"type": "agent_message", "text": "redone."})
        return
    if typ == "reset":
        ctx.session.reset()
        ctx.transcript.clear()
        ctx.pending_ask = None
        await _send(ws, ctx, _scene_init(ctx))
        return
    if typ == "export_scad":
        src = render_scad(ctx.session.log[: ctx.session.head])
        await _send(ws, ctx, {"type": "scad", "source": src})
        return
    if typ == "ws_log_request":
        await _send(ws, ctx, {"type": "ws_log", "log": list(ctx.ws_log[-200:])})
        return
    await _send(ws, ctx, {"type": "error", "message": f"unknown type: {typ}"})
