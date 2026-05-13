"""Section plane viewer feature — toggle on/off, axis cycling, hotkeys.

Smoke-level coverage. The full UX (TransformControls drag against an
animated 3D gizmo) is hard to drive deterministically from Playwright;
we instead verify the state contract `window.fastcad.sectionState()`
exposes: which axis is active, how many clipping planes the material
holds, whether the visualization is shown."""
from __future__ import annotations


def _make_a_cube(page) -> None:
    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")


def test_section_toggle_on_and_off(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")
    _make_a_cube(page)

    # Initially: no section, no clipping plane, viz hidden.
    state = page.evaluate("window.fastcad.sectionState()")
    assert state["axis"] is None
    assert state["clippingPlaneCount"] == 0
    assert state["vizVisible"] is False

    # Click Cut Z → section active on z, one clipping plane, viz shown.
    page.click("[data-testid=section-z-btn]")
    page.wait_for_function("window.fastcad.sectionState().axis === 'z'")
    state = page.evaluate("window.fastcad.sectionState()")
    assert state["clippingPlaneCount"] == 1
    assert state["vizVisible"] is True
    # Plane normal points along -Z (the convention: meshes on the +Z
    # side of the cut are kept).
    assert state["planeNormal"] == [0, 0, -1]

    # Click Off → no clipping, viz hidden, axis null.
    page.click("[data-testid=section-off-btn]")
    page.wait_for_function("window.fastcad.sectionState().axis === null")
    state = page.evaluate("window.fastcad.sectionState()")
    assert state["clippingPlaneCount"] == 0
    assert state["vizVisible"] is False


def test_section_cycles_between_axes(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")
    _make_a_cube(page)

    page.click("[data-testid=section-x-btn]")
    page.wait_for_function("window.fastcad.sectionState().axis === 'x'")
    assert page.evaluate("window.fastcad.sectionState().planeNormal") == [-1, 0, 0]

    # Switching axis directly without going off in between.
    page.click("[data-testid=section-y-btn]")
    page.wait_for_function("window.fastcad.sectionState().axis === 'y'")
    assert page.evaluate("window.fastcad.sectionState().planeNormal") == [0, -1, 0]

    # Re-click active axis acts like Off.
    page.click("[data-testid=section-y-btn]")
    page.wait_for_function("window.fastcad.sectionState().axis === null")


def test_section_hotkeys(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")
    _make_a_cube(page)

    # Focus the canvas so the keydown listener on window picks us up
    # (the keydown handler guards against keystrokes targeting an
    # <input>, so we explicitly click the viewer surface).
    page.click("[data-testid=viewer]")

    page.keyboard.press("1")
    page.wait_for_function("window.fastcad.sectionState().axis === 'x'")
    page.keyboard.press("2")
    page.wait_for_function("window.fastcad.sectionState().axis === 'y'")
    page.keyboard.press("3")
    page.wait_for_function("window.fastcad.sectionState().axis === 'z'")
    page.keyboard.press("0")
    page.wait_for_function("window.fastcad.sectionState().axis === null")


def test_active_button_styled(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.click("[data-testid=section-z-btn]")
    page.wait_for_function(
        "document.querySelector('[data-testid=section-z-btn]')"
        ".classList.contains('section-active')"
    )
    page.click("[data-testid=section-off-btn]")
    page.wait_for_function(
        "!document.querySelector('[data-testid=section-z-btn]')"
        ".classList.contains('section-active')"
    )
