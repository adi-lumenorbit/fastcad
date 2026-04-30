"""Channel 2 — adversarial vision-based critic.

After Channel 1's structural checks pass, this module renders the
agent's `.scad` to canonical-angle PNGs and ships them through a
single multi-modal Anthropic call along with the user's prompt and
the cache file. The critic is told to be skeptical: find what's
wrong, not confirm what's right. Defects come back as structured
JSON and merge with Channel 1's defect list before the result lands
in the agent's tool_result.

Design notes:
- The directive is **part-agnostic**. It lists "common things to
  look for" as broad categories (proportions, missing features,
  rotational symmetry) but never references a specific standard. A
  worked example for any one part type would steer the critic
  toward that example, defeating the point of the channel.
- Single SDK call (not Claude Code subprocess). Vision is one-shot,
  not agentic; the subprocess pattern is overkill here and would
  push setup cost up.
- Renders are persisted to `tmp/research/<slug>-<ts>/` for debug
  inspection when defects fire.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from ..model.render import Render, persist_renders, render_scad_source
from ..model.validate import Defect


CRITIC_DIRECTIVE = """\
You are an adversarial design reviewer for a CAD modeling system. The
agent on the other side wrote OpenSCAD source for a part the user
asked for; another validator already passed structural checks (bbox,
volume, slice topology) on the resulting geometry. Your job is the
visual layer: find defects the structural validator can't see.

You will see N renders of the part from canonical angles. Compare
the renders against (a) the user's prompt and (b) the reference spec
the agent was given.

Be skeptical and specific. Categories worth examining (NOT a
checklist — apply only the ones relevant to the part type):

- Proportions that don't match the spec the agent was given.
- Features described in the user prompt or the cache that aren't
  visible in the renders (drives, recesses, fillets, chamfers,
  threading).
- Features visible in the renders that the spec didn't ask for.
- Geometry that looks like a stack of disks or rings instead of the
  continuous helix / curve / sweep the part requires.
- Asymmetry where rotational, mirror, or translational symmetry is
  required by the part type.
- Orientation / mounting axis that doesn't match a standard's
  convention.
- Rendering artifacts that suggest evaluator bugs upstream of the
  agent (visible discrete steps where smoothness was intended,
  obvious z-fighting, holes in the surface) — flag these as
  warnings rather than errors since they may not be the agent's
  fault.

If everything matches the spec and the request, return an empty
defects list.

Output a single JSON object, no markdown, no commentary outside it:

{
  "defects": [
    {
      "severity": "error" | "warning",
      "where": "<short localizer, e.g. 'head — top view'>",
      "what": "<concrete defect>",
      "hint": "<one-line suggestion the agent could act on>"
    }
  ]
}
"""


def _user_message(user_prompt: str, cache_md: str, spec_source: str, n_renders: int) -> str:
    return (
        "User's request:\n"
        f"{user_prompt.strip()}\n"
        "\n"
        "Reference spec (the cache file the agent was supposed to "
        "follow):\n"
        "```\n"
        f"{cache_md.strip()}\n"
        "```\n"
        "\n"
        "Agent's OpenSCAD source:\n"
        "```scad\n"
        f"{spec_source.strip()}\n"
        "```\n"
        "\n"
        f"You will now see {n_renders} renders of the resulting "
        "geometry. Apply the directive: find specific defects."
    )


@dataclass
class CriticConfig:
    model: str = "claude-opus-4-7"
    max_tokens: int = 2048
    timeout_s: float = 120.0


def review(
    spec_source: str,
    cache_md: str,
    user_prompt: str,
    bbox: tuple[float, float, float, float, float, float],
    *,
    on_progress: Callable[[dict], None] | None = None,
    config: CriticConfig | None = None,
    client: Any | None = None,
    render_fn: Callable | None = None,
    persist_slug: str | None = None,
) -> list[Defect]:
    """Run the visual critic. Returns a list of Defect (possibly
    empty). On rendering failure, returns a single warning defect
    rather than blocking the turn — Channel 1 already passed, so the
    spec is committed regardless.
    """
    cfg = config or CriticConfig()
    if on_progress is not None:
        on_progress({"type": "critic_started"})

    renderer = render_fn or render_scad_source
    renders: list[Render] = renderer(spec_source, bbox)
    if not renders:
        if on_progress is not None:
            on_progress({"type": "critic_skipped", "reason": "no renders produced (openscad missing or render failed)"})
        return [
            Defect(
                severity="warning",
                where="critic_render",
                expected="3 renders from canonical angles",
                actual="0 renders (openscad missing or failed)",
                hint=(
                    "Channel 2 (visual critic) was skipped because "
                    "the renderer produced no images. Channel 1 "
                    "passed; the spec is committed. Install OpenSCAD "
                    "CLI to enable visual review."
                ),
            )
        ]

    if persist_slug:
        persist_renders(renders, persist_slug)

    if client is None:
        from anthropic import Anthropic  # local import keeps tests light
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        client = Anthropic(api_key=api_key) if api_key else Anthropic()

    content: list[dict] = [
        {
            "type": "text",
            "text": _user_message(user_prompt, cache_md, spec_source, len(renders)),
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

    try:
        resp = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=CRITIC_DIRECTIVE,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:  # noqa: BLE001
        if on_progress is not None:
            on_progress({"type": "critic_error", "error": str(exc)})
        return [
            Defect(
                severity="warning",
                where="critic_api",
                expected="critic responds",
                actual=f"{type(exc).__name__}: {exc}",
                hint="Vision-critic API call failed; structural checks already passed.",
            )
        ]

    defects = _parse_response(resp)
    if on_progress is not None:
        for d in defects:
            on_progress(
                {
                    "type": "validation_defect",
                    "severity": d.severity,
                    "where": d.where,
                    "expected": d.expected,
                    "actual": d.actual,
                    "hint": d.hint,
                    "channel": "vision",
                }
            )
        if not defects:
            on_progress({"type": "critic_pass"})
    return defects


def _parse_response(resp: Any) -> list[Defect]:
    """Extract the JSON defects list from a Claude vision response.
    Robust to: bare JSON, ```json fences, extra prose around the
    JSON. Falls back to a single-warning defect on parse failure.
    """
    text = ""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", "") == "text":
            text += getattr(block, "text", "")
    text = text.strip()
    if not text:
        return []

    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # Some responses preface with prose. Find the outermost { ... }.
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
                where="critic_response",
                expected="parseable JSON {defects: [...]}",
                actual=f"could not parse: {exc}",
                hint="Critic returned non-JSON; visual review skipped this turn.",
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
                where=f"vision: {d.get('where', 'unspecified')}",
                expected="visual review passes",
                actual=str(d.get("what", "")),
                hint=str(d.get("hint", "")),
            )
        )
    return out


__all__ = ["CRITIC_DIRECTIVE", "CriticConfig", "review"]
