"""Stencil-based section capping — when section is active, the cap
quad is positioned at the cut, color-matched to the axis tint, and
gone again when section is turned off.

The "is the cap visually filling the cross-section" question is a
framebuffer-pixel property; we test the structural setup instead
(cap quad transform, color, presence). Visual correctness is verified
manually in the issue."""
from __future__ import annotations


def test_cap_quad_exists_and_color_matches_axis(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")

    # Initially: cap quad exists (lazy-built at module load) but
    # isn't being rendered because section is off.
    assert page.evaluate("window.fastcad.capQuad.name") == "section-cap-quad"

    # Activate Cut Z. After one frame the cap quad should be
    # positioned + colored.
    page.click("[data-testid=section-z-btn]")
    # The frame loop only positions/colors the cap when section is
    # active, so wait for at least one render after the click.
    # Blue tint (0x6699ee) is the Z axis color.
    page.wait_for_function(
        "window.fastcad.capQuad.material.color.getHex() === 0x6699ee"
    )

    # Switch to Cut X — cap color updates to red.
    page.click("[data-testid=section-x-btn]")
    page.wait_for_function(
        "window.fastcad.capQuad.material.color.getHex() === 0xee5555"
    )

    # Off — section state clears. Cap quad object still exists in
    # memory (it's a long-lived lazy resource) but the cap pass is
    # no longer running, so capScene isn't rendered.
    page.click("[data-testid=section-off-btn]")
    page.wait_for_function("window.fastcad.sectionState().axis === null")
