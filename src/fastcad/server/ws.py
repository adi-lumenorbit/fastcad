"""WebSocket session loop.

Single in-memory session per connection — fastcad is a single-user local
app. The protocol is documented in the plan; in summary the browser sends
{type: prompt|undo|redo|export_scad|reset|user_choice} and receives
{type: scene_init|scene_delta|agent_message|ask_user|scad|tool_log|progress}.

`progress` events stream live during long-running agent turns (research
subagents, tool calls). They flow from a sync `on_progress` callback the
agent passes around: the WS layer wraps it with run_coroutine_threadsafe
to bridge the executor thread back to the asyncio loop, and an
`asyncio.Lock` on the WSContext serialises every send so concurrent
emissions can't mangle frames.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..agent.client import AgentTurn, _reset_fake, run_turn
from ..model import kernel as k
from ..session import SessionState


@dataclass
class WSContext:
    session: SessionState = field(default_factory=SessionState)
    transcript: list[dict] = field(default_factory=list)
    pending_ask: dict | None = None
    ws_log: list[dict] = field(default_factory=list)  # outbound + inbound for feedback bundles
    progress_seq: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _node_payload(ctx: WSContext, node_id: str) -> dict:
    me = ctx.session.cache[node_id]
    bb = me.bbox
    mesh = k.to_mesh_dict(me.manifold)
    return {
        "id": node_id,
        # `kind` retained for the frontend's mesh.userData.kind property.
        # In the spec model the kind is conventionally the module name.
        "kind": node_id,
        "bbox": {"min": [bb.xmin, bb.ymin, bb.zmin], "max": [bb.xmax, bb.ymax, bb.zmax]},
        "mesh": mesh,
    }


def _scene_init(ctx: WSContext) -> dict:
    return {
        "type": "scene_init",
        "nodes": [_node_payload(ctx, nid) for nid in ctx.session.cache.keys()],
    }


def _scene_delta(ctx: WSContext, added: list[str], updated: list[str], removed: list[str]) -> dict:
    return {
        "type": "scene_delta",
        "added": [_node_payload(ctx, nid) for nid in added if nid in ctx.session.cache],
        "updated": [_node_payload(ctx, nid) for nid in updated if nid in ctx.session.cache],
        "removed": removed,
    }


async def _send(ws: WebSocket, ctx: WSContext, payload: dict) -> None:
    """All outbound WS sends go through here. The lock serialises
    concurrent emissions (agent turn body + progress events scheduled
    from worker threads) so frames stay coherent.

    Send-after-close: a long-running agent turn may still be flushing
    progress events when the client disconnects (page close, network
    drop, fresh empirical run). Swallow those errors — the alternative
    is that one stale send aborts the whole turn server-side and
    poisons the next iteration's state. The ws_log entry still goes
    in so the feedback bundle records what would have been sent."""
    async with ctx.send_lock:
        ctx.ws_log.append({"dir": "out", "t": time.time(), "type": payload.get("type"),
                           "summary": _summary(payload)})
        try:
            await ws.send_text(json.dumps(payload))
        except RuntimeError:
            # Starlette raises RuntimeError after a close has been
            # sent. Treat as benign; the client is gone.
            pass
        except WebSocketDisconnect:
            pass


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
    if typ == "progress":
        # Strip noisy nested event bodies for the feedback bundle.
        ev = payload.get("event") or {}
        return {
            "type": typ,
            "id": payload.get("id"),
            "event_type": ev.get("type"),
            "tool": ev.get("tool"),
        }
    return {k: v for k, v in payload.items() if k != "mesh"}


async def _emit_progress(ws: WebSocket, ctx: WSContext, raw_event: dict) -> None:
    """Wrap a raw on_progress event in a `progress` WS message and
    send it. ID is monotonic per session so the frontend can update
    a previously-seen entry by id."""
    ctx.progress_seq += 1
    payload = {
        "type": "progress",
        "id": f"evt_{ctx.progress_seq}",
        "event": raw_event,
        "ts": time.time(),
    }
    await _send(ws, ctx, payload)


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


def _make_progress_bridge(loop: asyncio.AbstractEventLoop, ws: WebSocket, ctx: WSContext):
    """Returns a sync callable suitable for `run_turn(on_progress=…)`.
    The callable is invoked from the executor thread; it schedules an
    `_emit_progress` coroutine on the asyncio loop. Errors during
    scheduling (e.g. loop closed because the WS disconnected) are
    swallowed silently; we don't want a stale callback to crash the
    research subagent."""
    def on_progress(event: dict) -> None:
        try:
            asyncio.run_coroutine_threadsafe(_emit_progress(ws, ctx, event), loop)
        except RuntimeError:
            pass
    return on_progress


async def _run_turn_streaming(
    ws: WebSocket,
    ctx: WSContext,
    text: str,
) -> AgentTurn | None:
    """Run a turn in an executor so the WS event loop stays free to
    flush progress events. Returns None on agent error (already
    surfaced as an `error` WS message)."""
    loop = asyncio.get_running_loop()
    on_progress = _make_progress_bridge(loop, ws, ctx)
    try:
        return await loop.run_in_executor(
            None,
            lambda: run_turn(
                text,
                ctx.session,
                transcript=ctx.transcript,
                pending_ask=ctx.pending_ask,
                on_progress=on_progress,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        await _send(ws, ctx, {"type": "error", "message": f"agent error: {exc}"})
        return None


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
        turn = await _run_turn_streaming(ws, ctx, text)
        if turn is not None:
            await _emit_turn(ws, ctx, turn)
        return
    if typ == "user_choice":
        # User picked an option from the previous ask_user.
        text = str(msg.get("text", ""))
        turn = await _run_turn_streaming(ws, ctx, text)
        if turn is not None:
            await _emit_turn(ws, ctx, turn)
        return
    if typ == "undo":
        ctx.session.undo()
        await _send(ws, ctx, _scene_init(ctx))
        await _send(ws, ctx, {"type": "agent_message", "text": "undone."})
        return
    if typ == "redo":
        ctx.session.redo()
        await _send(ws, ctx, _scene_init(ctx))
        await _send(ws, ctx, {"type": "agent_message", "text": "redone."})
        return
    if typ == "reset":
        ctx.session.reset()
        _reset_fake(ctx.session)
        ctx.transcript.clear()
        ctx.pending_ask = None
        await _send(ws, ctx, _scene_init(ctx))
        return
    if typ == "export_scad":
        # The spec source IS the export — no translation layer.
        await _send(ws, ctx, {"type": "scad", "source": ctx.session.current_source})
        return
    if typ == "ws_log_request":
        await _send(ws, ctx, {"type": "ws_log", "log": list(ctx.ws_log[-200:])})
        return
    await _send(ws, ctx, {"type": "error", "message": f"unknown type: {typ}"})
