"""Agent tool schemas + dispatch.

Stage 1 collapses the previous flat-CSG tool set into a single primary
tool: `set_source(text)`. The agent rewrites the entire `.scad` spec
each turn; the system handles incremental rendering by AST-diff against
the previous source.

Stage 2 adds three tools that let the agent look up standardized parts
before modeling them: `list_research`, `read_research`, and
`research`. The cache lives in `docs/research/` (text-based,
git-tracked, human-editable). See `docs/research/README.md`.

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
- `list_research` — enumerate cached research entries.
- `read_research(slug)` — return a cached entry's full markdown.
- `research(topic, slug?)` — spawn a Claude Code subagent to deeply
  research a standardized part; writes a new cache entry. Idempotent
  on slug.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from ..model.scad_eval import EvalError
from ..model.scad_parser import ScadParseError
from ..model.spec_diff import ChangeSet, diff_and_evaluate
from ..session import SessionState
from . import research as _research


ProgressCallback = Callable[[dict], None]


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
    {
        "name": "list_research",
        "description": (
            "List cached research entries for standardized parts. "
            "Returns [{slug, title, researched, path}, ...]. Cheap; "
            "call this before modeling any standardized component to "
            "see if a spec is already cached."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "read_research",
        "description": (
            "Return the full markdown content of a cached research "
            "entry. Apply the dimensions verbatim when modeling — do "
            "not approximate. The cache is the authority."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "kebab-case slug of the entry"},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "research",
        "description": (
            "Spawn a Claude Code subagent to research a standardized "
            "part and write a new cache entry to "
            "`docs/research/<slug>.md`. Use this when `list_research` "
            "shows no relevant entry. Idempotent on slug — if the file "
            "already exists, returns it without re-running. Long-"
            "running (typically ~30s); progress streams to the UI panel."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Free-text description of the part to research, e.g. \"M3 socket head cap screw\".",
                },
                "slug": {
                    "type": "string",
                    "description": "Optional kebab-case slug. If omitted, the subagent picks one.",
                },
            },
            "required": ["topic"],
        },
    },
]


@dataclass
class ToolResult:
    content: str
    changes: ChangeSet | None = None
    ask_user: dict | None = None


def dispatch(
    name: str,
    args: dict,
    session: SessionState,
    on_progress: ProgressCallback | None = None,
) -> ToolResult:
    """Dispatch a tool call. `on_progress` is forwarded to long-running
    tools (currently `research`); fast tools may emit a single
    started/done pair around themselves so the UI sees activity even
    for quick operations."""
    if on_progress is not None:
        on_progress({"type": "tool_call_started", "tool": name, "args": _safe_args(name, args)})

    try:
        result = _dispatch_inner(name, args, session, on_progress)
    except Exception as exc:  # noqa: BLE001
        if on_progress is not None:
            on_progress({"type": "tool_call_error", "tool": name, "error": str(exc)})
        raise

    if on_progress is not None:
        on_progress({"type": "tool_call_done", "tool": name, "summary": _summarize_result(name, result)})
    return result


def _dispatch_inner(
    name: str,
    args: dict,
    session: SessionState,
    on_progress: ProgressCallback | None,
) -> ToolResult:
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

    if name == "list_research":
        entries = _research.list_research()
        return ToolResult(content=json.dumps({
            "entries": [
                {
                    "slug": e.slug,
                    "title": e.title,
                    "researched": e.researched,
                    "path": e.path,
                }
                for e in entries
            ],
        }))

    if name == "read_research":
        slug = str(args.get("slug", ""))
        content = _research.read_research(slug)
        if content is None:
            return ToolResult(content=json.dumps({
                "ok": False,
                "error": f"unknown slug: {slug!r}",
            }))
        return ToolResult(content=json.dumps({
            "ok": True,
            "slug": slug,
            "content": content,
        }))

    if name == "research":
        topic = str(args.get("topic", ""))
        slug = args.get("slug")
        slug = str(slug) if slug else None
        result = _research.run_research(
            topic, slug=slug, on_progress=on_progress
        )
        return ToolResult(content=json.dumps(result.to_dict()))

    return ToolResult(content=json.dumps({"error": f"unknown tool: {name}"}))


def _safe_args(name: str, args: dict) -> dict:
    """Trim potentially-huge args (set_source text, validate text) for
    progress events so the WS doesn't ship megabytes."""
    if name in ("set_source", "validate"):
        text = args.get("text", "")
        if isinstance(text, str) and len(text) > 200:
            return {"text": f"<{len(text)} chars>"}
    return dict(args)


def _summarize_result(name: str, result: ToolResult) -> dict:
    """One-line result summary for progress events."""
    try:
        payload = json.loads(result.content)
    except (json.JSONDecodeError, TypeError):
        return {}
    if name == "set_source":
        if payload.get("ok"):
            return {
                "ok": True,
                "added": payload.get("added", []),
                "updated": payload.get("updated", []),
                "removed": payload.get("removed", []),
            }
        return {"ok": False, "error": str(payload.get("error", ""))[:200]}
    if name == "research":
        return {
            "slug": payload.get("slug"),
            "cached_hit": payload.get("cached_hit"),
            "cache_path": payload.get("cache_path"),
        }
    if name == "list_research":
        return {"count": len(payload.get("entries", []))}
    if name == "read_research":
        return {"ok": payload.get("ok"), "slug": payload.get("slug")}
    if name == "validate":
        return {"ok": payload.get("ok")}
    if name == "select_face":
        return {"ok": payload.get("ok")}
    return {}


__all__ = ["TOOL_DEFINITIONS", "ToolResult", "dispatch", "ProgressCallback"]
