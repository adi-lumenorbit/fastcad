"""Agent tool schemas + dispatch.

Stage 1 collapses the previous flat-CSG tool set into a single primary
tool: `set_source(text)`. The agent rewrites the entire `.scad` spec
each turn; the system handles incremental rendering by AST-diff against
the previous source.

Tools:

- `read_source` — return the current `.scad`. Rarely needed; the spec
  is in the system prompt every turn.
- `set_source(text)` — replace the spec. Parses + evaluates; on parse
  or eval error, returns the error message verbatim so the agent can
  self-correct on the next turn (no mutation occurs).
- `validate(text)` — dry-run a candidate source. Same parse + eval
  pipeline, but no mutation regardless of outcome. Used by the agent
  to self-check a tricky rewrite before committing.
- `select_face(node_id, face_name)` — return `{point, normal}` for a
  named face on a top-level module call. Helps the agent place a
  follow-up part on a face semantically rather than via bbox guessing.
- `ask_user(question, options)` — pause for clarification.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..model.ops import ChangeSet
from ..model.scad_eval import EvalError
from ..model.scad_parser import ScadParseError
from ..model.spec_diff import diff_and_evaluate
from ..session import SessionState


TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "read_source",
        "description": (
            "Return the current `.scad` spec source. The spec is also "
            "embedded in the system prompt every turn, so calling this "
            "is rarely needed."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "set_source",
        "description": (
            "Replace the entire `.scad` spec with the given text. The "
            "system parses, evaluates, and renders only the modules "
            "whose dependencies actually changed. On parse / eval error "
            "the spec is unchanged and the error is returned to you — "
            "fix the source and call again. This is the primary edit "
            "tool: any change to the model goes through it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Full new `.scad` spec source. Replaces the current spec wholesale.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "validate",
        "description": (
            "Dry-run a candidate `.scad` source through the parser + "
            "evaluator without mutating the spec. Use when you're "
            "unsure whether a tricky rewrite will parse or evaluate "
            "cleanly. Returns ok/true or an error message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "select_face",
        "description": (
            "Get the `{point, normal}` of a named face on a top-level "
            "module call. Useful for placing a follow-up part on a "
            "specific face. Face names: +X, -X, +Y, -Y, +Z, -Z."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "face_name": {"type": "string", "enum": ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]},
            },
            "required": ["node_id", "face_name"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Pause and ask the user to disambiguate. Use only when "
            "there are multiple plausible interpretations and you "
            "cannot pick deterministically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                },
            },
            "required": ["question", "options"],
        },
    },
]


@dataclass
class ToolResult:
    content: str
    changes: ChangeSet | None = None
    ask_user: dict | None = None


def dispatch(name: str, args: dict, session: SessionState) -> ToolResult:
    if name == "read_source":
        return ToolResult(content=json.dumps({"source": session.current_source}))

    if name == "set_source":
        text = str(args.get("text", ""))
        try:
            cs = session.set_source(text)
        except (ScadParseError, EvalError) as exc:
            return ToolResult(content=json.dumps({"ok": False, "error": str(exc)}))
        return ToolResult(
            content=json.dumps({
                "ok": True,
                "added": list(cs.added),
                "updated": list(cs.updated),
                "removed": list(cs.removed),
            }),
            changes=cs,
        )

    if name == "validate":
        text = str(args.get("text", ""))
        try:
            diff_and_evaluate(text, session.cache)
        except (ScadParseError, EvalError) as exc:
            return ToolResult(content=json.dumps({"ok": False, "error": str(exc)}))
        return ToolResult(content=json.dumps({"ok": True}))

    if name == "select_face":
        node_id = str(args.get("node_id", ""))
        face_name = str(args.get("face_name", ""))
        me = session.cache.get(node_id)
        if me is None:
            return ToolResult(content=json.dumps({
                "ok": False,
                "error": f"unknown node id: {node_id!r}. "
                         f"Known: {sorted(session.cache.keys())}",
            }))
        face = me.faces.get(face_name)
        if face is None:
            return ToolResult(content=json.dumps({
                "ok": False,
                "error": f"unknown face name: {face_name!r}. Available: {sorted(me.faces.keys())}",
            }))
        return ToolResult(content=json.dumps({
            "ok": True,
            "point": list(face.point),
            "normal": list(face.normal),
        }))

    if name == "ask_user":
        return ToolResult(
            content=json.dumps({"asked": True}),
            ask_user={"question": args["question"], "options": list(args["options"])},
        )

    return ToolResult(content=json.dumps({"error": f"unknown tool: {name}"}))


__all__ = ["TOOL_DEFINITIONS", "ToolResult", "dispatch"]
