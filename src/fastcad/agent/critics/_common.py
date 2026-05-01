"""Shared critic plumbing — JSON parsing, multimodal call assembly,
defect normalization. Each specific critic is a thin layer that
provides a directive + name + maps the parsed response to Defect
objects in its own way (or uses `parse_defects` directly)."""
from __future__ import annotations

import json
import re
from typing import Any

from ...model.render import Render
from ...model.validate import Defect


CONCRETE_HINT_REQUIREMENT = """\
HINT REQUIREMENT (strict):
- Every defect's `hint` field must reference at least one identifier
  from the OpenSCAD source above — a `module` name, a top-level
  parameter name, or a polygon vertex literal that you can see in
  the source.
- Propose the precise change in OpenSCAD code form. Do NOT give
  abstract advice ("use linear_extrude with twist", "make the head
  rounder"). Refer to specific names and write the replacement
  fragment.
- Two-line maximum per hint.

Output a single JSON object, no markdown, no commentary outside it:

{
  "defects": [
    {
      "severity": "error" | "warning",
      "where": "<short localizer, e.g. 'head — top view'>",
      "what": "<concrete defect>",
      "hint": "<code-level fix referencing source identifiers>"
    }
  ]
}
"""


def build_user_message(
    user_prompt: str,
    cache_md: str,
    spec_source: str,
    n_renders: int,
) -> str:
    return (
        "User's request:\n"
        f"{(user_prompt or '(no prompt)').strip()}\n"
        "\n"
        "Reference spec (cache file the agent was supposed to follow):\n"
        "```\n"
        f"{cache_md.strip()}\n"
        "```\n"
        "\n"
        "Agent's OpenSCAD source — your hints MUST reference identifiers from this source:\n"
        "```scad\n"
        f"{spec_source.strip()}\n"
        "```\n"
        "\n"
        f"You will now see {n_renders} renders of the resulting "
        "geometry. Apply the directive above; find specific defects."
    )


def call_vision(
    client: Any,
    directive: str,
    user_prompt: str,
    cache_md: str,
    spec_source: str,
    renders: list[Render],
    *,
    model: str = "claude-opus-4-7",
    max_tokens: int = 2048,
) -> str:
    """Single multimodal Anthropic call. Returns the text body of the
    first text block in the response (caller parses)."""
    content: list[dict] = [
        {
            "type": "text",
            "text": build_user_message(user_prompt, cache_md, spec_source, len(renders)),
        }
    ]
    for r in renders:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": r.b64(),
                },
            }
        )
        content.append({"type": "text", "text": f"^ render angle: {r.angle}"})

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=directive,
        messages=[{"role": "user", "content": content}],
    )

    text = ""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", "") == "text":
            text += getattr(block, "text", "")
    return text.strip()


def parse_defects(text: str, critic_name: str) -> list[Defect]:
    """Extract a `{defects: [...]}` JSON object from a critic's
    response text. Robust to ```json fences and prose around the
    JSON. Falls back to a single warning-defect on parse failure so
    the agent's turn isn't blocked."""
    if not text:
        return []

    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [
            Defect(
                severity="warning",
                where=f"critic_response:{critic_name}",
                expected="parseable JSON {defects: [...]}",
                actual=f"could not parse: {exc}",
                hint=f"The {critic_name} critic returned non-JSON; visual review skipped this turn.",
            )
        ]

    raw = data.get("defects") or []
    out: list[Defect] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        sev = str(d.get("severity", "warning"))
        if sev not in ("error", "warning"):
            sev = "warning"
        out.append(
            Defect(
                severity=sev,
                where=f"vision/{critic_name}: {d.get('where', 'unspecified')}",
                expected="visual review passes",
                actual=str(d.get("what", "")),
                hint=str(d.get("hint", "")),
            )
        )
    return out


def safe_review(
    critic_name: str,
    directive: str,
    user_prompt: str,
    cache_md: str,
    spec_source: str,
    renders: list[Render],
    client: Any,
) -> list[Defect]:
    """Standard wrapper used by every critic: calls vision, parses
    JSON, returns Defect list. On API failure surfaces a warning
    Defect rather than raising — the agent's turn must continue."""
    try:
        text = call_vision(
            client,
            directive,
            user_prompt,
            cache_md,
            spec_source,
            renders,
        )
    except Exception as exc:  # noqa: BLE001
        return [
            Defect(
                severity="warning",
                where=f"critic_api:{critic_name}",
                expected="critic responds",
                actual=f"{type(exc).__name__}: {exc}",
                hint=f"The {critic_name} critic API call failed; structural checks already ran.",
            )
        ]
    return parse_defects(text, critic_name)


__all__ = [
    "CONCRETE_HINT_REQUIREMENT",
    "build_user_message",
    "call_vision",
    "parse_defects",
    "safe_review",
]
