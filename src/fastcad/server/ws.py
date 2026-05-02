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
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect

from ..agent.client import AgentTurn, _reset_fake, run_turn
from ..model import kernel as k
from ..session import SessionState


# --- Input hardening ------------------------------------------------------

# Hard cap on a single inbound WS frame (bytes). Anything larger is
# certainly not a legitimate prompt; refuse before trying to parse JSON.
_MAX_FRAME_BYTES = 64 * 1024  # 64 KB

# Cap on the user-facing prompt text. Generous enough for verbose
# requirements; tight enough that a flood of huge prompts can't drain
# tokens in seconds.
_MAX_PROMPT_CHARS = 4096

# Whitelist of inbound message types. Anything else is rejected without
# being dispatched. This is the single place where new WS verbs land.
_ALLOWED_TYPES = frozenset({
    "prompt",
    "user_choice",
    "undo",
    "redo",
    "reset",
    "export_scad",
    "ws_log_request",
})

# Per-session prompt rate limits. Token-bucket with two windows: a burst
# allowance (10 prompts in 60 s) and a daily ceiling (200 prompts in
# 24 h). The daily ceiling is the real cost backstop — at typical
# token usage the API spend cap on the key still dominates, but this
# gives us cheap, in-process protection.
_RATE_BURST_WINDOW_S = 60.0
_RATE_BURST_MAX = 10
_RATE_DAILY_WINDOW_S = 86400.0
_RATE_DAILY_MAX = 200


def _allowed_origins() -> set[str]:
    """Origins permitted to open a WebSocket connection. Defaults to
    same-origin (empty set means "no Origin header check"). Set
    `FASTCAD_ALLOWED_ORIGINS` to a comma-separated list of full
    origins (e.g. "https://fastcad.example.com") to lock the WS to
    your deployed hostname only."""
    raw = os.environ.get("FASTCAD_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return set()
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _origin_ok(ws: WebSocket) -> bool:
    """Return True if the WS handshake's Origin header is permitted.
    Empty allow-list ⇒ always allow (single-user local mode).
    Origin missing ⇒ allow only when allow-list is empty."""
    allowed = _allowed_origins()
    if not allowed:
        return True
    origin = (ws.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return False
    # Tolerate `https://host:443` vs `https://host` by parsing.
    try:
        parsed = urlsplit(origin)
        canonical = f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        return False
    return canonical in allowed


@dataclass
class WSContext:
    session: SessionState = field(default_factory=SessionState)
    transcript: list[dict] = field(default_factory=list)
    pending_ask: dict | None = None
    ws_log: list[dict] = field(default_factory=list)  # outbound + inbound for feedback bundles
    progress_seq: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Per-session timestamps of accepted prompts (oldest first). Used
    # by the rate limiter; bounded by `_RATE_DAILY_MAX` entries.
    prompt_times: deque[float] = field(default_factory=deque)


def _check_rate_limit(ctx: WSContext) -> str | None:
    """Return None when a new prompt is allowed, or a human-readable
    reason string when blocked. On allow, the function records the
    timestamp."""
    now = time.monotonic()
    # Drop entries outside the daily window.
    while ctx.prompt_times and now - ctx.prompt_times[0] > _RATE_DAILY_WINDOW_S:
        ctx.prompt_times.popleft()
    # Burst window: count entries within the last RATE_BURST_WINDOW_S.
    burst_count = sum(1 for t in ctx.prompt_times
                      if now - t <= _RATE_BURST_WINDOW_S)
    if burst_count >= _RATE_BURST_MAX:
        return (
            f"rate limit: max {_RATE_BURST_MAX} prompts per "
            f"{int(_RATE_BURST_WINDOW_S)} s on this connection"
        )
    if len(ctx.prompt_times) >= _RATE_DAILY_MAX:
        return (
            f"rate limit: daily cap of {_RATE_DAILY_MAX} prompts "
            f"reached on this connection"
        )
    ctx.prompt_times.append(now)
    return None


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
        await _send(ws, ctx, {
            "type": "agent_message",
            "text": turn.text,
            "stats": turn.stats.to_dict(),
        })
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
    # Origin check: reject cross-site WebSocket hijacking before we
    # even accept the handshake. Local single-user mode is preserved
    # by leaving the allow-list empty (FASTCAD_ALLOWED_ORIGINS unset).
    if not _origin_ok(ws):
        await ws.close(code=1008, reason="origin not allowed")
        return
    await ws.accept()
    await _send(ws, ctx, _scene_init(ctx))
    try:
        while True:
            raw = await ws.receive_text()
            # Reject oversized frames before paying the JSON-parse cost.
            if len(raw) > _MAX_FRAME_BYTES:
                await _send(ws, ctx, {
                    "type": "error",
                    "message": f"message too large ({len(raw)} > {_MAX_FRAME_BYTES} bytes)",
                })
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, ctx, {"type": "error", "message": "invalid json"})
                continue
            if not isinstance(msg, dict):
                await _send(ws, ctx, {
                    "type": "error",
                    "message": "message must be a JSON object",
                })
                continue
            typ = msg.get("type")
            if typ not in _ALLOWED_TYPES:
                await _send(ws, ctx, {
                    "type": "error",
                    "message": f"unknown or disallowed type: {typ!r}",
                })
                continue
            ctx.ws_log.append({"dir": "in", "t": time.time(), "type": typ, "msg": msg})
            await _dispatch(ws, ctx, msg)
    except WebSocketDisconnect:
        return


async def _dispatch(ws: WebSocket, ctx: WSContext, msg: dict[str, Any]) -> None:
    typ = msg.get("type")
    if typ == "prompt":
        text = _clean_prompt_text(msg.get("text"))
        if text is None:
            await _send(ws, ctx, {
                "type": "error",
                "message": f"prompt rejected: must be 1..{_MAX_PROMPT_CHARS} chars after sanitization",
            })
            return
        blocked = _check_rate_limit(ctx)
        if blocked is not None:
            await _send(ws, ctx, {"type": "error", "message": blocked})
            return
        turn = await _run_turn_streaming(ws, ctx, text)
        if turn is not None:
            await _emit_turn(ws, ctx, turn)
        return
    if typ == "user_choice":
        # User picked an option from the previous ask_user. We only
        # accept a choice when there's a pending ask AND the choice
        # text matches one of the offered options — prevents arbitrary
        # text from sneaking in via a stale or forged user_choice.
        text = str(msg.get("text", ""))
        if ctx.pending_ask is None:
            await _send(ws, ctx, {
                "type": "error",
                "message": "user_choice without a pending ask_user",
            })
            return
        options = ctx.pending_ask.get("options") or []
        if text not in options:
            await _send(ws, ctx, {
                "type": "error",
                "message": "user_choice must match one of the offered options",
            })
            return
        blocked = _check_rate_limit(ctx)
        if blocked is not None:
            await _send(ws, ctx, {"type": "error", "message": blocked})
            return
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


# --- Prompt sanitization ---------------------------------------------------

# Strip ASCII control characters except whitespace (LF / CR / TAB).
# Anything else in 0x00-0x1f / 0x7f is most likely an injection attempt
# (ANSI escapes, NUL bytes for log poisoning, etc.) and never appears in
# legitimate user prose.
import re as _re  # imported here to keep the module-top imports tidy
_PROMPT_CONTROL_RE = _re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_prompt_text(raw: Any) -> str | None:
    """Sanitize a user-supplied prompt string. Returns None when the
    payload is the wrong type, is empty after stripping, or exceeds
    the max-prompt cap. Caller surfaces None as a protocol error."""
    if not isinstance(raw, str):
        return None
    cleaned = _PROMPT_CONTROL_RE.sub("", raw).strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_PROMPT_CHARS:
        return None
    return cleaned
