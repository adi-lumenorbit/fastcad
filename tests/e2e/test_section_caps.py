"""Stencil-based section capping — when a section is active, each
visible user mesh gets its own cap bundle (back+front stencil
shadows + a cap quad). The bundle's cap material carries the axis
tint and the stencil ops that fill only the cross-section.

The "is the cap visually filling the cross-section" question is a
framebuffer-pixel property; we test the structural setup instead
(bundle count, cap color matches axis). Visual correctness is
verified manually."""
from __future__ import annotations


def test_section_bundles_exist_and_recolor_per_axis(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")

    # No section yet → no bundles.
    state = page.evaluate("window.fastcad.sectionState()")
    assert state["bundleCount"] == 0
    assert state["capColor"] is None

    # Activate Cut Z. One bundle per user mesh; cap color = blue.
    page.click("[data-testid=section-z-btn]")
    page.wait_for_function(
        "window.fastcad.sectionState().bundleCount === 1"
        " && window.fastcad.sectionState().capColor === 0x6699ee"
    )

    # Switch to Cut X — cap recolors to red.
    page.click("[data-testid=section-x-btn]")
    page.wait_for_function(
        "window.fastcad.sectionState().capColor === 0xee5555"
    )

    # Off — bundles torn down.
    page.click("[data-testid=section-off-btn]")
    page.wait_for_function("window.fastcad.sectionState().bundleCount === 0")
