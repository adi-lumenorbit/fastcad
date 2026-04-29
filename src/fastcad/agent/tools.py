"""Tool schemas + dispatch.

Each tool returns a `ToolResult` with `content` (string surfaced to the LLM
as the tool_result) and an optional `ChangeSet` produced by mutating the
session. `ask_user` is special: it pauses the agent loop and surfaces a
clarification request to the UI; the next user turn resumes the loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..model.ops import AddPrimitive, Boolean, ChangeSet
from ..session import SessionState


TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "list_scene",
        "description": (
            "Return all current nodes with id, kind, bbox, center, size. "
            "Call this whenever the user's prompt references existing geometry."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "add_primitive",
        "description": (
            "Add a primitive to the scene. Returns the new node's id. "
            "Anchor places the primitive's *center* at the target's anchor point."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["cube", "sphere", "cylinder"]},
                "params": {
                    "type": "object",
                    "description": (
                        "cube: {size: [x,y,z]}; "
                        "sphere: {radius, segments?}; "
                        "cylinder: {height, radius, segments?}"
                    ),
                },
                "anchor_to": {
                    "type": ["string", "null"],
                    "description": "Existing node id, or null to place at world origin.",
                },
                "anchor": {
                    "type": "string",
                    "enum": ["origin", "top", "bottom", "center"],
                    "default": "origin",
                },
                "offset": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "default": [0, 0, 0],
                },
            },
            "required": ["kind", "params"],
        },
    },
    {
        "name": "boolean",
        "description": (
            "Apply a CSG boolean. target_id is mutated; with_id is consumed by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["union", "difference"]},
                "target_id": {"type": "string"},
                "with_id": {"type": "string"},
                "consume_with": {"type": "boolean", "default": True},
            },
            "required": ["kind", "target_id", "with_id"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Pause and ask the user to disambiguate. Use only when there are "
            "multiple plausible targets and you cannot pick deterministically."
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
    if name == "list_scene":
        return ToolResult(content=json.dumps({"nodes": session.scene.describe_for_agent()}))

    if name == "add_primitive":
        kind = args["kind"]
        params = args["params"]
        anchor_to = args.get("anchor_to")
        anchor = args.get("anchor", "origin")
        offset_raw = args.get("offset") or [0, 0, 0]
        offset = (float(offset_raw[0]), float(offset_raw[1]), float(offset_raw[2]))
        nid = session.fresh_id(kind)
        op = AddPrimitive(
            kind=kind,
            params=params,
            node_id=nid,
            anchor_to=anchor_to,
            anchor=anchor,
            offset=offset,
        )
        cs = session.append(op)
        return ToolResult(
            content=json.dumps({"node_id": nid, "ok": True}),
            changes=cs,
        )

    if name == "boolean":
        op = Boolean(
            kind=args["kind"],
            target_id=args["target_id"],
            with_id=args["with_id"],
            consume_with=bool(args.get("consume_with", True)),
        )
        cs = session.append(op)
        return ToolResult(
            content=json.dumps({"target_id": op.target_id, "ok": True}),
            changes=cs,
        )

    if name == "ask_user":
        return ToolResult(
            content=json.dumps({"asked": True}),
            ask_user={"question": args["question"], "options": list(args["options"])},
        )

    return ToolResult(content=json.dumps({"error": f"unknown tool: {name}"}))
