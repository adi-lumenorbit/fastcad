"""Each mesh renders in its own deterministic color (FNV-1a hash of
the node id mapped to an HSL hue). Two distinct meshes → two distinct
colors. The hash is deterministic, so the same node id always gets the
same color across reloads."""
from __future__ import annotations


def test_each_mesh_has_unique_color(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")

    page.fill("[data-testid=chat-input]", "Make a 30mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 2")

    colors = page.evaluate(
        "(() => [...window.fastcad.meshMap.values()]"
        ".map(m => m.material.color.getHex()))()"
    )
    assert len(colors) == 2
    assert colors[0] != colors[1]


def test_color_is_deterministic_for_id(live_server: str, page) -> None:
    """Same id → same color, regardless of when the mesh was created.
    Tested directly through the exposed helper."""
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")
    a = page.evaluate("window.fastcad.colorForId('housing_1').getHex()")
    b = page.evaluate("window.fastcad.colorForId('housing_1').getHex()")
    c = page.evaluate("window.fastcad.colorForId('housing_2').getHex()")
    assert a == b
    assert a != c
