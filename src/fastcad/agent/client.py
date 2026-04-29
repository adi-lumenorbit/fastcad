"""Anthropic Claude tool-use loop with a deterministic fake mode.

Real mode talks to the Anthropic API using the `anthropic` SDK and the
tool-use loop. Fake mode (ANTHROPIC_FAKE=1) interprets the user prompt
with simple regex heuristics so e2e tests are reproducible without
network access. The fake calls the same `dispatch` function the real
loop uses, so tool semantics stay unified.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from ..session import SessionState
from ..model.ops import ChangeSet
from .system_prompt import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, ToolResult, dispatch


@dataclass
class AgentTurn:
    text: str | None = None
    ask_user: dict | None = None
    changes: ChangeSet = field(default_factory=ChangeSet)
    tool_log: list[dict] = field(default_factory=list)  # for transparency in feedback bundles


def _is_fake() -> bool:
    return os.environ.get("ANTHROPIC_FAKE") == "1"


def run_turn(
    user_message: str,
    session: SessionState,
    *,
    transcript: list[dict] | None = None,
    pending_ask: dict | None = None,
) -> AgentTurn:
    """Run one user-turn end-to-end. Mutates `session` and `transcript`.

    `pending_ask` is set when the previous turn ended with ask_user; the
    user_message is then interpreted as the chosen option.
    """
    if transcript is None:
        transcript = []
    transcript.append({"role": "user", "content": user_message})
    if _is_fake():
        turn = _fake_turn(user_message, session, transcript, pending_ask)
    else:
        turn = _real_turn(user_message, session, transcript)
    transcript.append({"role": "assistant", "content": turn.text or ""})
    return turn


# ---------------------------------------------------------------------------
# Fake mode — deterministic, regex-driven, no network.
# ---------------------------------------------------------------------------


_NUM = r"(\d+(?:\.\d+)?)"


def _extract_mm(text: str, default: float) -> float:
    m = re.search(rf"{_NUM}\s*mm\b", text)
    return float(m.group(1)) if m else default


def _call(name: str, args: dict, session: SessionState, turn: AgentTurn) -> ToolResult:
    res = dispatch(name, args, session)
    turn.tool_log.append({"name": name, "args": args, "content": res.content})
    if res.changes is not None:
        turn.changes.merge(res.changes)
    return res


def _fake_turn(
    user_message: str,
    session: SessionState,
    transcript: list[dict],
    pending_ask: dict | None,
) -> AgentTurn:
    text = user_message.strip()
    low = text.lower()
    turn = AgentTurn()

    # Resume after ask_user: user picked a node id.
    if pending_ask is not None:
        chosen_id = text.strip()
        if chosen_id in session.scene.nodes:
            stored = pending_ask.get("payload", {})
            kind = stored.get("kind", "sphere")
            params = stored.get("params", {"radius": 5})
            anchor = stored.get("anchor", "top")
            _call(
                "add_primitive",
                {"kind": kind, "params": params, "anchor_to": chosen_id, "anchor": anchor},
                session,
                turn,
            )
            turn.text = f"placed {kind} on {chosen_id}."
            return turn
        turn.text = f"could not find node {chosen_id!r}."
        return turn

    # Patterns ----------------------------------------------------------------

    # "subtract" / "drill" / "hole through" -> cylinder + difference
    if re.search(r"\b(subtract|drill|hole|cut)\b", low) or "through" in low:
        size = _extract_mm(low, 5.0)
        scene = session.scene.describe_for_agent()
        # Pick the largest existing solid as the target.
        if not scene:
            turn.text = "nothing to subtract from yet."
            return turn
        target = max(scene, key=lambda n: max(n["size"]))
        height = max(target["size"]) + 4.0
        _call("list_scene", {}, session, turn)
        cyl = _call(
            "add_primitive",
            {
                "kind": "cylinder",
                "params": {"height": height, "radius": size / 2.0, "segments": 64},
                "anchor_to": target["id"],
                "anchor": "bottom",
                "offset": [0, 0, -2],
            },
            session,
            turn,
        )
        # Recover new id from result content
        import json as _json
        cyl_id = _json.loads(cyl.content)["node_id"]
        _call(
            "boolean",
            {"kind": "difference", "target_id": target["id"], "with_id": cyl_id},
            session,
            turn,
        )
        turn.text = f"subtracted a {int(size)}mm hole through {target['id']}."
        return turn

    # "sphere ... on top" / "ball ... above"
    if "sphere" in low and ("on top" in low or "above" in low or "atop" in low):
        radius = _extract_mm(low, 10.0) / 2.0
        scene = session.scene.describe_for_agent()
        _call("list_scene", {}, session, turn)
        candidates = [n for n in scene if n["kind"] in ("cube", "sphere", "cylinder")
                      or n["kind"].startswith("boolean:")]
        if not candidates:
            # No target — place at origin.
            _call(
                "add_primitive",
                {"kind": "sphere", "params": {"radius": radius, "segments": 64}},
                session,
                turn,
            )
            turn.text = f"added a sphere (no target to anchor to)."
            return turn
        if len(candidates) == 1:
            _call(
                "add_primitive",
                {
                    "kind": "sphere",
                    "params": {"radius": radius, "segments": 64},
                    "anchor_to": candidates[0]["id"],
                    "anchor": "top",
                },
                session,
                turn,
            )
            turn.text = f"placed sphere on top of {candidates[0]['id']}."
            return turn
        # Multiple — ask_user.
        ask = _call(
            "ask_user",
            {
                "question": "Which object should the sphere sit on top of?",
                "options": [c["id"] for c in candidates],
            },
            session,
            turn,
        )
        turn.ask_user = {
            "question": "Which object should the sphere sit on top of?",
            "options": [c["id"] for c in candidates],
            "payload": {
                "kind": "sphere",
                "params": {"radius": radius, "segments": 64},
                "anchor": "top",
            },
        }
        turn.text = None
        return turn

    # "cylinder ... mm tall ... mm radius"
    if "cylinder" in low and not ("subtract" in low or "through" in low):
        h = _extract_mm(low, 10.0)
        r_match = re.search(rf"radius\s*{_NUM}", low) or re.search(rf"{_NUM}\s*mm radius", low)
        r = float(r_match.group(1)) if r_match else 5.0
        _call(
            "add_primitive",
            {"kind": "cylinder", "params": {"height": h, "radius": r, "segments": 64}},
            session,
            turn,
        )
        turn.text = f"added a {int(h)}mm cylinder."
        return turn

    # "sphere" alone
    if "sphere" in low:
        radius = _extract_mm(low, 10.0) / 2.0
        _call(
            "add_primitive",
            {"kind": "sphere", "params": {"radius": radius, "segments": 64}},
            session,
            turn,
        )
        turn.text = f"added a sphere."
        return turn

    # "cube" — default
    if "cube" in low or "box" in low:
        size = _extract_mm(low, 20.0)
        _call(
            "add_primitive",
            {"kind": "cube", "params": {"size": [size, size, size]}},
            session,
            turn,
        )
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
) -> AgentTurn:
    from anthropic import Anthropic  # local import keeps fake-mode lighter

    client = Anthropic()
    model = os.environ.get("FASTCAD_MODEL", "claude-sonnet-4-6")
    max_tool_iterations = int(os.environ.get("FASTCAD_MAX_TOOL_ITER", "8"))

    # Build messages. The transcript stores plain {role,content} strings so
    # we re-emit them as Anthropic messages.
    messages: list[dict] = []
    for entry in transcript:
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry["content"]})
        else:
            messages.append({"role": "assistant", "content": entry["content"]})

    turn = AgentTurn()

    for _ in range(max_tool_iterations):
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        # Collect tool_use blocks and any text.
        text_parts: list[str] = []
        tool_uses: list[dict] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(
                    {"id": block.id, "name": block.name, "input": block.input}
                )

        # Append the assistant turn (raw content) so subsequent calls see it.
        messages.append({"role": "assistant", "content": resp.content})

        if not tool_uses:
            turn.text = "\n".join(text_parts).strip() or None
            return turn

        # Dispatch tools and feed results back.
        tool_results: list[dict] = []
        ask: dict | None = None
        for tu in tool_uses:
            result = dispatch(tu["name"], tu["input"], session)
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
