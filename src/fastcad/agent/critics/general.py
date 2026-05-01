"""General-form critic — broad recognizability + proportions.

Lens: "does this shape match what the user asked for, looked at as a
whole?" Compares overall form against the prompt and the cache spec;
flags missing features, wrong proportions, mounting axis confusion,
and anything that breaks the part's high-level identity.

Does NOT focus on thread / sweep specifics — that's the threads
critic. Does NOT focus on rendering artifacts (visible facets,
manifold holes) — that's a future surface critic.
"""
from __future__ import annotations

from typing import Any

from ...model.render import Render
from ...model.validate import Defect

from ._common import CONCRETE_HINT_REQUIREMENT, safe_review


NAME = "general"


DIRECTIVE = (
    """\
You are an adversarial design reviewer for a CAD modeling system,
specialised in *overall form*. Another structural validator already
ran bbox / volume / connected-components / module-presence checks;
your job is the visual layer at the whole-part level.

Categories worth examining (NOT a checklist — apply only what's
relevant to this part type):

- Recognisability: would a domain expert immediately identify the
  rendered geometry as the part the user asked for? If not, what
  reads wrong?
- Proportions: do head / shaft / flange / mounting features have
  the right relative sizes per the cache spec?
- Anatomy: are all the named features the cache mentions actually
  visible? Are there features visible that the cache doesn't ask
  for?
- Axis / orientation: does the part stand on the right axis? Heads
  pointing the wrong way, threads inside-out, mounting holes
  misaligned.
- Compositional integrity: are sub-modules merged into one solid,
  or is the part visibly fragmented into floating pieces?

Skip categories handled by other critics:
- Helical thread / sweep correctness — the threads critic owns this.
- Rendering artifacts (visible facets on curves, manifold holes) —
  the surface critic owns this.

If everything matches the spec and the request, return an empty
defects list.
"""
    + "\n"
    + CONCRETE_HINT_REQUIREMENT
)


def review(
    *,
    spec_source: str,
    cache_md: str,
    user_prompt: str,
    renders: list[Render],
    client: Any,
    directive: str = DIRECTIVE,
) -> list[Defect]:
    return safe_review(
        critic_name=NAME,
        directive=directive,
        user_prompt=user_prompt,
        cache_md=cache_md,
        spec_source=spec_source,
        renders=renders,
        client=client,
    )


__all__ = ["NAME", "DIRECTIVE", "review"]
