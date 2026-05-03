"""Anthropic Claude tool-use loop with a deterministic fake mode.

Real mode: each turn embeds `session.current_source` in the system
prompt (cached by Anthropic) and runs the tool-use loop. The agent's
primary tool is `set_source`; the typical turn calls it once.

Fake mode (ANTHROPIC_FAKE=1): pattern-matches a small set of canned
prompts to `set_source` calls. Maintains a per-session op list so
"add a sphere on top of the cube" can refer to the cube created on a
prior turn. Used by the unit + e2e suites for deterministic offline
testing.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from ..model.spec_diff import ChangeSet
from ..session import SessionState
from .system_prompt import SYSTEM_PROMPT_BASE, system_prompt
from .tools import TOOL_DEFINITIONS, ToolResult, dispatch


ProgressCallback = Callable[[dict], None]


@dataclass
class TurnStats:
    """Cost + timing for one user prompt → final assistant message.

    Token counts are *cumulative* across all iterations of the
    tool-use loop within the turn. `cost_usd` is computed at the
    rates in `_PRICING`; if the model isn't in the table we report
    0.0 and leave the raw tokens for the UI to display.
    """
    model: str = ""
    elapsed_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    cost_usd: float = 0.0
    iterations: int = 0

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "elapsed_s": round(self.elapsed_s, 3),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_create_tokens": self.cache_create_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "iterations": self.iterations,
        }


@dataclass
class AgentTurn:
    text: str | None = None
    ask_user: dict | None = None
    changes: ChangeSet = field(default_factory=ChangeSet)
    tool_log: list[dict] = field(default_factory=list)
    stats: TurnStats = field(default_factory=TurnStats)


# Anthropic per-1M-token prices (USD). Update as pricing changes.
# Cache-read: typically 0.10× input. Cache-create (5-min TTL): 1.25×
# input. Stored explicitly so the math is auditable.
_PRICING = {
    # claude-opus-4-7
    "claude-opus-4-7":            {"in": 15.0, "out": 75.0, "cache_read": 1.50,  "cache_create": 18.75},
    "claude-opus-4-7[1m]":        {"in": 15.0, "out": 75.0, "cache_read": 1.50,  "cache_create": 18.75},
    # claude-sonnet-4-6
    "claude-sonnet-4-6":          {"in":  3.0, "out": 15.0, "cache_read": 0.30,  "cache_create":  3.75},
    # claude-haiku-4-5
    "claude-haiku-4-5":           {"in":  1.0, "out":  5.0, "cache_read": 0.10,  "cache_create":  1.25},
    "claude-haiku-4-5-20251001":  {"in":  1.0, "out":  5.0, "cache_read": 0.10,  "cache_create":  1.25},
}


def _cost_usd(model: str, in_t: int, out_t: int, cache_r: int, cache_c: int) -> float:
    """Compute USD cost from a row in `_PRICING`. Returns 0.0 for an
    unknown model so the rest of the stats still flow to the UI."""
    p = _PRICING.get(model)
    if p is None:
        return 0.0
    M = 1_000_000
    return (
        in_t       * p["in"]           / M
        + out_t    * p["out"]          / M
        + cache_r  * p["cache_read"]   / M
        + cache_c  * p["cache_create"] / M
    )


def _accumulate_usage(stats: TurnStats, resp_usage) -> None:
    """Add an Anthropic response's usage block into the running
    TurnStats. Tolerates missing fields (older API responses, fake
    mode) by treating absent values as 0."""
    if resp_usage is None:
        return
    stats.input_tokens        += int(getattr(resp_usage, "input_tokens", 0) or 0)
    stats.output_tokens       += int(getattr(resp_usage, "output_tokens", 0) or 0)
    stats.cache_read_tokens   += int(getattr(resp_usage, "cache_read_input_tokens", 0) or 0)
    stats.cache_create_tokens += int(getattr(resp_usage, "cache_creation_input_tokens", 0) or 0)


def _is_fake() -> bool:
    return os.environ.get("ANTHROPIC_FAKE") == "1"


def run_turn(
    user_message: str,
    session: SessionState,
    *,
    transcript: list[dict] | None = None,
    pending_ask: dict | None = None,
    on_progress: ProgressCallback | None = None,
) -> AgentTurn:
    if transcript is None:
        transcript = []
    transcript.append({"role": "user", "content": user_message})
    started_at = time.monotonic()
    if _is_fake():
        turn = _fake_turn(user_message, session, transcript, pending_ask, on_progress)
    else:
        turn = _real_turn(user_message, session, transcript, on_progress)
    turn.stats.elapsed_s = time.monotonic() - started_at
    turn.stats.cost_usd = _cost_usd(
        turn.stats.model,
        turn.stats.input_tokens,
        turn.stats.output_tokens,
        turn.stats.cache_read_tokens,
        turn.stats.cache_create_tokens,
    )
    transcript.append({"role": "assistant", "content": turn.text or ""})
    return turn


# ---------------------------------------------------------------------------
# Fake-mode internal state. Keyed by id(session) so every connection has
# its own op list. Reset on session.reset() via _reset_fake_state hook.
# ---------------------------------------------------------------------------


_NUM = r"(\d+(?:\.\d+)?)"


# Per-session fake op list. Each op is a dict describing a primitive
# the user has asked for; the fake source generator turns the list
# into a .scad spec.
_FAKE_OPS: dict[int, list[dict]] = {}


def _fake_ops(session: SessionState) -> list[dict]:
    return _FAKE_OPS.setdefault(id(session), [])


def _reset_fake(session: SessionState) -> None:
    _FAKE_OPS.pop(id(session), None)


def _next_index(session: SessionState, prefix: str) -> int:
    ops = _fake_ops(session)
    used = {op["id"] for op in ops}
    i = 1
    while f"{prefix}_{i}" in used:
        i += 1
    return i


def _extract_mm(text: str, default: float) -> float:
    m = re.search(rf"{_NUM}\s*mm\b", text)
    return float(m.group(1)) if m else default


def _fmt(v: float) -> str:
    """Format a number as int when integral, else trimmed float (mirrors
    the OpenSCAD-tradition / `model/scad.py`'s old emitter)."""
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}"
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _ops_to_scad(ops: list[dict]) -> str:
    """Synthesize a .scad spec from the fake op list. Each op produces
    its own named module; later ops can transform earlier ones via the
    `subtract_through` op which wraps the target's body in a
    difference."""
    if not ops:
        return "// fastcad spec — empty scene\n"
    bodies: dict[str, str] = {}
    order: list[str] = []
    rendered: set[str] = set()  # ids that should be top-level rendered
    for op in ops:
        kind = op["kind"]
        if kind == "cube":
            n = _fmt(op["size"])
            bodies[op["id"]] = f"cube([{n}, {n}, {n}]);"
            order.append(op["id"])
            rendered.add(op["id"])
        elif kind == "sphere":
            r = _fmt(op["radius"])
            anchor_to = op.get("anchor_to")
            if anchor_to is not None and anchor_to in bodies:
                cx, cy, cz = _anchor_top_for_op(ops, anchor_to)
                bodies[op["id"]] = (
                    f"translate([{_fmt(cx)}, {_fmt(cy)}, {_fmt(cz)}]) "
                    f"sphere(r = {r}, $fn = 64);"
                )
            else:
                bodies[op["id"]] = f"sphere(r = {r}, $fn = 64);"
            order.append(op["id"])
            rendered.add(op["id"])
        elif kind == "cylinder":
            h = _fmt(op["height"])
            r = _fmt(op["radius"])
            bodies[op["id"]] = f"cylinder(h = {h}, r = {r}, $fn = 64);"
            order.append(op["id"])
            rendered.add(op["id"])
        elif kind == "subtract_through":
            target_id = op["target"]
            d = op["diameter"]
            h = _fmt(op["through_height"])
            existing = bodies.get(target_id, "")
            bodies[target_id] = (
                f"difference() {{\n    {existing}\n    "
                f"translate([0, 0, -2]) cylinder(h = {h}, r = {_fmt(d / 2)}, $fn = 64);\n  }}"
            )
        else:
            continue
    # Synthesize source.
    out: list[str] = ["// fastcad spec\n"]
    for nid in order:
        if nid not in rendered:
            continue
        body = bodies.get(nid, "// (empty)")
        out.append(f"module {nid}() {{\n  {body}\n}}\n")
    out.append("\n")
    for nid in order:
        if nid in rendered:
            out.append(f"{nid}();\n")
    return "".join(out)


def _anchor_top_for_op(ops: list[dict], target_id: str) -> tuple[float, float, float]:
    """Resolve the +Z face point of an op for use as anchor-to-top."""
    for op in ops:
        if op["id"] != target_id:
            continue
        if op["kind"] == "cube":
            n = op["size"]
            return (n / 2.0, n / 2.0, n)
        if op["kind"] == "cylinder":
            return (0.0, 0.0, op["height"])
        if op["kind"] == "sphere":
            r = op["radius"]
            return (0.0, 0.0, r)
    return (0.0, 0.0, 0.0)


def _largest_op_id(ops: list[dict]) -> str | None:
    """Return the id of the largest 'solid' op (cube / cylinder)."""
    candidates = [op for op in ops if op["kind"] in ("cube", "cylinder")]
    if not candidates:
        return None
    def size_of(op):
        if op["kind"] == "cube":
            return op["size"]
        return max(op["height"], op["radius"] * 2)
    return max(candidates, key=size_of)["id"]


def _solid_op_ids(ops: list[dict]) -> list[str]:
    return [op["id"] for op in ops if op["kind"] in ("cube", "sphere", "cylinder")]


def _fake_call_set_source(
    session: SessionState,
    source: str,
    turn: AgentTurn,
    on_progress: ProgressCallback | None = None,
) -> None:
    res = dispatch("set_source", {"text": source}, session, on_progress=on_progress)
    turn.tool_log.append({"name": "set_source", "args": {"text": source}, "content": res.content})
    if res.changes is not None:
        turn.changes.merge(res.changes)


def _fake_turn(
    user_message: str,
    session: SessionState,
    transcript: list[dict],
    pending_ask: dict | None,
    on_progress: ProgressCallback | None = None,
) -> AgentTurn:
    text = user_message.strip()
    low = text.lower()
    turn = AgentTurn()
    ops = _fake_ops(session)

    # Resume after ask_user: user picked a target id.
    if pending_ask is not None:
        chosen_id = text.strip()
        if chosen_id not in {op["id"] for op in ops}:
            turn.text = f"could not find node {chosen_id!r}."
            return turn
        payload = pending_ask.get("payload", {})
        kind = payload.get("kind", "sphere")
        radius = payload.get("radius", 5.0)
        anchor = payload.get("anchor", "top")
        if kind == "sphere":
            n = _next_index(session, "sphere")
            new_id = f"sphere_{n}"
            ops.append({
                "kind": "sphere",
                "id": new_id,
                "radius": radius,
                "anchor_to": chosen_id if anchor == "top" else None,
            })
            _fake_call_set_source(session, _ops_to_scad(ops), turn, on_progress)
            turn.text = f"placed sphere on {chosen_id}."
            return turn
        turn.text = f"unknown follow-up kind {kind!r}."
        return turn

    # "subtract / drill / hole / cut / through" → cylinder + difference
    if re.search(r"\b(subtract|drill|hole|cut)\b", low) or "through" in low:
        d = _extract_mm(low, 5.0)
        target = _largest_op_id(ops)
        if target is None:
            turn.text = "nothing to subtract from yet."
            return turn
        # Through-height: target dim + slack.
        target_op = next(op for op in ops if op["id"] == target)
        if target_op["kind"] == "cube":
            through_h = target_op["size"] + 4.0
        elif target_op["kind"] == "cylinder":
            through_h = target_op["height"] + 4.0
        else:
            through_h = 20.0
        ops.append({
            "kind": "subtract_through",
            "target": target,
            "diameter": d,
            "through_height": through_h,
        })
        _fake_call_set_source(session, _ops_to_scad(ops), turn, on_progress)
        turn.text = f"subtracted a {int(d)}mm hole through {target}."
        return turn

    # "sphere ... on top / above / atop"
    if "sphere" in low and ("on top" in low or "above" in low or "atop" in low):
        radius = _extract_mm(low, 10.0) / 2.0
        candidates = _solid_op_ids(ops)
        if not candidates:
            n = _next_index(session, "sphere")
            ops.append({"kind": "sphere", "id": f"sphere_{n}", "radius": radius})
            _fake_call_set_source(session, _ops_to_scad(ops), turn, on_progress)
            turn.text = "added a sphere (no target to anchor to)."
            return turn
        if len(candidates) == 1:
            n = _next_index(session, "sphere")
            ops.append({
                "kind": "sphere",
                "id": f"sphere_{n}",
                "radius": radius,
                "anchor_to": candidates[0],
            })
            _fake_call_set_source(session, _ops_to_scad(ops), turn, on_progress)
            turn.text = f"placed sphere on top of {candidates[0]}."
            return turn
        # Multiple candidates → ask_user.
        turn.ask_user = {
            "question": "Which object should the sphere sit on top of?",
            "options": list(candidates),
            "payload": {"kind": "sphere", "radius": radius, "anchor": "top"},
        }
        turn.text = None
        return turn

    # "cylinder ... mm tall ... mm radius"
    if "cylinder" in low and not ("subtract" in low or "through" in low):
        h = _extract_mm(low, 10.0)
        r_match = re.search(rf"radius\s*{_NUM}", low) or re.search(rf"{_NUM}\s*mm radius", low)
        r = float(r_match.group(1)) if r_match else 5.0
        n = _next_index(session, "cylinder")
        ops.append({"kind": "cylinder", "id": f"cylinder_{n}", "height": h, "radius": r})
        _fake_call_set_source(session, _ops_to_scad(ops), turn, on_progress)
        turn.text = f"added a {int(h)}mm cylinder."
        return turn

    # "sphere" alone
    if "sphere" in low:
        radius = _extract_mm(low, 10.0) / 2.0
        n = _next_index(session, "sphere")
        ops.append({"kind": "sphere", "id": f"sphere_{n}", "radius": radius})
        _fake_call_set_source(session, _ops_to_scad(ops), turn, on_progress)
        turn.text = "added a sphere."
        return turn

    # "cube" / "box" → default 20mm
    if "cube" in low or "box" in low:
        size = _extract_mm(low, 20.0)
        n = _next_index(session, "cube")
        ops.append({"kind": "cube", "id": f"cube_{n}", "size": size})
        _fake_call_set_source(session, _ops_to_scad(ops), turn, on_progress)
        turn.text = f"added a {int(size)}mm cube."
        return turn

    turn.text = "I'm not sure what to do with that. Try: \"make a 20mm cube\"."
    return turn


# ---------------------------------------------------------------------------
# Real mode — Anthropic SDK tool-use loop.
# ---------------------------------------------------------------------------


def _real_turn(
    user_message: str,
    session: SessionState,
    transcript: list[dict],
    on_progress: ProgressCallback | None = None,
) -> AgentTurn:
    from anthropic import Anthropic  # local import keeps fake-mode lighter

    client = Anthropic()
    model = os.environ.get("FASTCAD_MODEL", "claude-sonnet-4-6")
    max_tool_iterations = int(os.environ.get("FASTCAD_MAX_TOOL_ITER", "16"))

    messages: list[dict] = []
    for entry in transcript:
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry["content"]})
        else:
            messages.append({"role": "assistant", "content": entry["content"]})

    turn = AgentTurn()
    turn.stats.model = model

    for _ in range(max_tool_iterations):
        turn.stats.iterations += 1
        # Re-render the system prompt each iteration so the agent sees
        # the *current* spec after any set_source it has already issued
        # this turn.
        sys_prompt = system_prompt(session.current_source)
        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=sys_prompt,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        _accumulate_usage(turn.stats, getattr(resp, "usage", None))
        text_parts: list[str] = []
        tool_uses: list[dict] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(
                    {"id": block.id, "name": block.name, "input": block.input}
                )

        messages.append({"role": "assistant", "content": resp.content})

        if not tool_uses:
            turn.text = "\n".join(text_parts).strip() or None
            return turn

        tool_results: list[dict] = []
        ask: dict | None = None
        for tu in tool_uses:
            result = dispatch(tu["name"], tu["input"], session, on_progress=on_progress)
            turn.tool_log.append(
                {"name": tu["name"], "args": tu["input"], "content": result.content}
            )
            if result.changes is not None:
                turn.changes.merge(result.changes)
            if result.ask_user is not None:
                ask = result.ask_user
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": result.content,
                }
            )

        messages.append({"role": "user", "content": tool_results})

        if ask is not None:
            turn.ask_user = {
                "question": ask["question"],
                "options": ask["options"],
                "payload": {},
            }
            turn.text = "\n".join(text_parts).strip() or None
            return turn

        if resp.stop_reason == "end_turn":
            turn.text = "\n".join(text_parts).strip() or None
            return turn

        # Smart exit: if the structural validator has reported the same
        # defect class on each of the last four iterations, the agent is
        # stuck. The fix-it critic (P8) fires at the threshold of 3, so
        # by 4 iterations of the same class we know it didn't help. End
        # the turn with a clear explanation rather than running to the
        # max_tool_iterations cap and dumping the generic message.
        persistent = _detect_persistent_defects_inline(session.defect_history, 4)
        if persistent:
            turn.text = (
                f"(agent stopped: defect class {persistent} has persisted "
                f"across the last 4 iterations and the fix-it critic has "
                f"already fired. The cache schema or guidance for this "
                f"part may need adjustment, or the user can rephrase. "
                f"Review the progress panel for the recurring defect.)"
            )
            return turn

    turn.text = "(agent stopped: max tool iterations exceeded — see the progress panel for the loop the agent was stuck in)"
    return turn


def _detect_persistent_defects_inline(history: list[list[dict]], threshold: int) -> list[str]:
    """Same shape as `agent.critics.detect_persistent_defects`, inlined
    here to avoid pulling the critics module on the hot path. Returns
    the sorted list of `where`-prefix keys that appear in each of the
    last `threshold` defect-list entries; empty when not persistent."""
    if len(history) < threshold:
        return []
    recent = history[-threshold:]
    if any(not it for it in recent):
        return []
    keys_per_iter: list[set[str]] = []
    for defects in recent:
        keys = set()
        for d in defects:
            where = str(d.get("where", ""))
            key = where.split("[")[0] if "[" in where else where
            keys.add(key)
        keys_per_iter.append(keys)
    intersection = keys_per_iter[0]
    for k in keys_per_iter[1:]:
        intersection &= k
    return sorted(intersection)


__all__ = ["AgentTurn", "run_turn", "_reset_fake"]
