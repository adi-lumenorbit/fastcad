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
from dataclasses import dataclass, field
from typing import Callable

from ..model.spec_diff import ChangeSet
from ..session import SessionState
from .system_prompt import SYSTEM_PROMPT_BASE, system_prompt
from .tools import TOOL_DEFINITIONS, ToolResult, dispatch


ProgressCallback = Callable[[dict], None]


@dataclass
class AgentTurn:
    text: str | None = None
    ask_user: dict | None = None
    changes: ChangeSet = field(default_factory=ChangeSet)
    tool_log: list[dict] = field(default_factory=list)


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
    if _is_fake():
        turn = _fake_turn(user_message, session, transcript, pending_ask, on_progress)
    else:
        turn = _real_turn(user_message, session, transcript, on_progress)
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

    for _ in range(max_tool_iterations):
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

    turn.text = "(agent stopped: max tool iterations exceeded)"
    return turn


__all__ = ["AgentTurn", "run_turn", "_reset_fake"]
