"""Threads / swept-feature critic — focused on helical and rotated
features.

Lens: "if this part has threads, screw flutes, helical fins, twisted
beams, etc., are they constructed correctly?" Flags the bug class
fastcad keeps hitting: stacked rings instead of helix, multi-start
when single-start was specified, inverted profile (cylinder with
groove instead of core with ridge), zero-width tooth profiles, sharp
discontinuities.

If the part has no helical / swept features, this critic returns no
defects.
"""
from __future__ import annotations

from typing import Any

from ...model.render import Render
from ...model.validate import Defect

from ._common import CONCRETE_HINT_REQUIREMENT, SectionImage, safe_review


NAME = "threads"


DIRECTIVE = (
    """\
You are an adversarial design reviewer for a CAD modeling system,
specialised in *helical / swept features* — threads on screws,
flutes on drills, helical fins, twisted beams, etc.

If the part the user asked for has no helical or swept features,
return an empty defects list immediately.

If it does, examine the renders for these failure modes (apply only
the ones relevant to the construction the agent attempted):

1. **Stacked rings instead of a helix.** If the cross-section
   appears as a series of horizontal disks at the pitch interval,
   the agent likely used `for(i=...) translate([0,0,i*pitch])`
   stacked clones, OR `linear_extrude(twist=0)` on a non-symmetric
   profile, OR a series of `cylinder()` calls. The fix is one
   `linear_extrude(height=length, twist=N*360, slices=...)` call
   over the FULL length, where N is total turns.

2. **Multi-start where single-start was specified.** If the cross-
   section has N teeth around the circumference, the swept thread
   has N starts. Standard ISO/UNF threads are single-start. The
   fix is exactly ONE bump in the 2D cross-section polygon.

3. **Inverted profile** (the bug from this iteration). The thread
   should appear as raised triangular ridges separated by smooth
   cylinder, NOT as a smooth cylinder with thin spiral grooves cut
   into it. Inverted means the agent used `difference()` on a full-
   diameter cylinder when they should have used `union()` on a
   minor-diameter core plus a helical ridge. Look at the visible
   "thread" — is it the wider material that wraps the shaft (good),
   or are there narrow cuts on otherwise-full material (bad)?

4. **Zero-width or hairline thread.** If the visible thread is a
   single line rather than a triangular ridge with visible flank
   angles, the polygon defining the cross-section likely has zero
   thickness in the radial direction or near-zero pitch height.
   Standard ISO threads have ~60° included angle; the polygon
   should be a triangle with non-trivial flank slope.

5. **Pitch discontinuities.** Real helices are continuous; visible
   steps along the shaft suggest too-low slice count (slices param
   < |twist|/5 ° per slice).

6. **Smooth shaft, no thread at all.** The agent emitted a thread
   module but its body is degenerate / empty / not unioned into
   the main shaft.

Reference your hints to the agent's specific module names and
parameter values. If the source has a `module thread_xs()` or
similar, name it; if there's a `thread_pitch` parameter, name it.

**Use the cross-section views as ground truth.** The XZ@y=0 and
YZ@x=0 sections show the thread profile in axial cross-section: a
correct ISO/UNF thread has clearly-visible triangular ridges of
height ~0.5×pitch and axial extent ~0.4–0.85×pitch with consistent
~60° flank angle. Each section ships with computed `axial_peaks`
metrics — quote them in your hint.

Specifically: if the section view shows the thread as **paper-thin
horizontal whiskers** (mean_axial_extent ≪ pitch×0.4) when the spec
calls for a real thread, the cross-section the agent fed to
`linear_extrude(twist=)` has too-narrow azimuthal coverage. The
canonical (minor circle + small triangle) construction CANNOT
produce a real ISO thread no matter the slice count — the
relationship `tooth_axial_extent ≈ tooth_azimuth_coverage / 360 ×
pitch` makes a triangle of y-extent pitch/2 produce ~12° azimuthal
coverage and ~0.03 mm of axial coverage. The fix is a polyhedron-
based thread (helically-positioned vertices) or a much wider
azimuthal tooth in the cross-section — not more slices.

If the helical features in the renders + sections match the spec,
return an empty defects list.
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
    sections: list[SectionImage] | None = None,
) -> list[Defect]:
    return safe_review(
        critic_name=NAME,
        directive=directive,
        user_prompt=user_prompt,
        cache_md=cache_md,
        spec_source=spec_source,
        renders=renders,
        client=client,
        sections=sections,
    )


__all__ = ["NAME", "DIRECTIVE", "review"]
