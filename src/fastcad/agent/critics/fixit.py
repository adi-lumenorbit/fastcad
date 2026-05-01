"""Fix-it critic (P8).

Triggers ONLY when the agent has been thrashing on the same class
of defects across multiple set_source attempts. Instead of
producing more abstract hints, this critic outputs a complete
replacement OpenSCAD module body that addresses the persistent
defects. The hint is the patch.

Activation: the orchestrator passes `prior_defects: list[list[dict]]`
when persistence is detected (≥N iterations with overlapping
`where` fields). The fix-it critic refuses to fire when no
persistence is detected — its directive is calibrated for the
escalated case.

Output shape is intentionally identical to other critics
(`{defects: [...]}`) so the orchestrator + UI need no special
casing. The defects' `hint` fields contain the OpenSCAD code; the
agent splices it into a `set_source` call.
"""
from __future__ import annotations

import textwrap
from typing import Any

from ...model.render import Render
from ...model.validate import Defect

from ._common import SectionImage, safe_review


NAME = "fixit"


_BASE_DIRECTIVE = """\
You are an escalated CAD-modeling assistant. The agent has tried
multiple `set_source` attempts to fix the same class of defects and
has not converged. Your job is to break the deadlock by writing
COMPLETE replacement code, not abstract advice.

Inputs you receive:
- The cache file (the part's spec + canonical construction
  guidance).
- The agent's CURRENT OpenSCAD source.
- Renders of the current geometry from three angles.
- The PRIOR-ITERATION defects below — each one shows the agent
  has tried and failed to address the issue.

Output format (single JSON object, no markdown, no commentary):

{
  "defects": [
    {
      "severity": "error",
      "where": "<which module / feature you're patching>",
      "what": "<one-line summary of the persistent issue>",
      "hint": "<COMPLETE replacement OpenSCAD code for the failing module>"
    }
  ]
}

Hint requirements:
- The hint must be runnable OpenSCAD that drops in to replace the
  failing portion. Use the same parameter names the agent's source
  already uses; preserve the rest of the source structure.
- For helical-thread issues: write the full thread_xs() and
  shaft() (or equivalently named) modules using
  `linear_extrude(twist=N*360, slices=...)` over a `union()` of a
  minor-diameter circle plus ONE radial tooth polygon.
- For chamfer / fillet issues: prefer `rotate_extrude` of a
  triangular profile or `cylinder(d1=, d2=, h=)` truncated cones,
  not `difference()` with thin discs.
- For multi-component / disconnected geometry: identify which
  modules need explicit `union(){...}` wrapping.
- Do NOT rewrite the entire file. Replace only the parts that the
  prior-iteration defects flagged.
- Reference identifiers (module names, params) that already exist
  in the agent's source — don't introduce new names unless
  strictly necessary.

If you don't see any persistent issue you can fix concretely,
return `{"defects": []}` — better an empty response than a wrong
patch.
"""


def DIRECTIVE_with_prior(prior_defects_summary: str) -> str:
    return _BASE_DIRECTIVE + (
        "\n\nPrior-iteration defects (each line is one defect that "
        "the agent has already been told and not fixed):\n"
        f"{prior_defects_summary}\n"
    )


# Module-level DIRECTIVE for tests / introspection. The real
# orchestrator builds a per-call directive that includes the prior-
# defects summary; this constant just shows the shape.
DIRECTIVE = _BASE_DIRECTIVE


def review(
    *,
    spec_source: str,
    cache_md: str,
    user_prompt: str,
    renders: list[Render],
    client: Any,
    directive: str | None = None,
    prior_defects: list[list[dict]] | None = None,
    sections: list[SectionImage] | None = None,
) -> list[Defect]:
    """Run the fix-it critic. `prior_defects` is a list-of-lists
    where each inner list is the defects produced by one prior
    set_source attempt (oldest first). The directive is built
    dynamically to include a summary of those prior defects."""
    if not prior_defects:
        return []   # not in escalation territory

    summary = _summarize_prior_defects(prior_defects)
    full_directive = directive or DIRECTIVE_with_prior(summary)

    return safe_review(
        critic_name=NAME,
        directive=full_directive,
        user_prompt=user_prompt,
        cache_md=cache_md,
        spec_source=spec_source,
        renders=renders,
        client=client,
        sections=sections,
    )


def _summarize_prior_defects(history: list[list[dict]]) -> str:
    """Compact rendering of the defect history for the directive
    prompt. Each iteration block is dated; each defect is one line
    `[where] what`."""
    if not history:
        return "(none)"
    lines: list[str] = []
    for i, defects in enumerate(history, 1):
        lines.append(f"--- iteration {i} ---")
        if not defects:
            lines.append("(no defects)")
            continue
        for d in defects:
            where = d.get("where") or "?"
            what = d.get("actual") or d.get("what") or ""
            lines.append(f"  [{where}] {textwrap.shorten(str(what), 200)}")
    return "\n".join(lines)


__all__ = ["NAME", "DIRECTIVE", "DIRECTIVE_with_prior", "review"]
