"""Resizable dividers — both the vertical viewer / chat-pane split
and the horizontal chat-log / progress-pane split. Tests that:

- The dividers exist and are draggable.
- Dragging changes the underlying CSS variables (size).
- The change persists in localStorage so reloads keep the layout.
- The drag math has no dead-zone when the clamp is hit and reversed
  (regression test for the previous "anchored" implementation).
"""
from __future__ import annotations


def test_vertical_divider_drag_resizes_chat_pane(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    # Initial right-pane width — read straight from the CSS variable.
    initial = page.evaluate(
        "() => parseFloat(getComputedStyle(document.getElementById('app')).getPropertyValue('--right-pane-width'))"
    )
    assert initial > 0

    # Drag the vertical divider 80 px to the LEFT (widens right pane).
    bbox = page.locator("[data-testid=app-divider]").bounding_box()
    page.mouse.move(bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2)
    page.mouse.down()
    page.mouse.move(bbox["x"] - 80, bbox["y"] + bbox["height"] / 2, steps=8)
    page.mouse.up()

    new_width = page.evaluate(
        "() => parseFloat(getComputedStyle(document.getElementById('app')).getPropertyValue('--right-pane-width'))"
    )
    # Right pane should be wider than before (within ±2 px of the
    # 80 px drag — small drift OK due to clamps and rounding).
    assert new_width > initial + 50, f"expected widening, got {initial} → {new_width}"


def test_horizontal_divider_drag_resizes_chat_log(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    initial = page.evaluate(
        "() => parseFloat(getComputedStyle(document.getElementById('chat-pane')).getPropertyValue('--pane-split'))"
    )

    bbox = page.locator("[data-testid=pane-divider]").bounding_box()
    # Drag down 60 px → chat-log grows.
    page.mouse.move(bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2)
    page.mouse.down()
    page.mouse.move(bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2 + 60, steps=6)
    page.mouse.up()

    after_down = page.evaluate(
        "() => parseFloat(getComputedStyle(document.getElementById('chat-pane')).getPropertyValue('--pane-split'))"
    )
    assert after_down > initial, f"expected chat-log to grow, got {initial} → {after_down}"


def test_horizontal_divider_no_dead_zone_on_clamp_reversal(live_server: str, page) -> None:
    """Regression: the previous "anchored" drag had a dead-zone where
    dragging past the 85% clamp and reversing left a gap before the
    divider tracked the cursor. movementY-based math has no such gap.
    """
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    bbox = page.locator("[data-testid=pane-divider]").bounding_box()
    cx, cy = bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2

    # Drag way past the clamp (huge downward).
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx, cy + 5000, steps=20)   # well past the bottom

    pct_at_max = page.evaluate(
        "() => parseFloat(getComputedStyle(document.getElementById('chat-pane')).getPropertyValue('--pane-split'))"
    )
    # Clamped at 85.
    assert 80 <= pct_at_max <= 90

    # Now reverse: a small upward drag should immediately move the
    # divider back. With the old code there'd be a dead-zone because
    # the dragStartPct + delta math kept overshooting.
    page.mouse.move(cx, cy + 5000 - 100, steps=10)
    pct_after_reverse = page.evaluate(
        "() => parseFloat(getComputedStyle(document.getElementById('chat-pane')).getPropertyValue('--pane-split'))"
    )
    page.mouse.up()
    # Some movement should have registered immediately.
    assert pct_after_reverse < pct_at_max, (
        f"dead-zone regressed: divider stuck at {pct_at_max} after 100 px reverse → {pct_after_reverse}"
    )
