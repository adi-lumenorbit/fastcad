"""Regression: camera aspect must track the canvas's CSS aspect, even
at devicePixelRatio == 1. The original resize() compared drawing-
buffer dimensions against CSS dimensions, which coincidentally match
when pixelRatio == 1 — so the resize was skipped and camera.aspect
stayed at its constructor default of 1, stretching the scene along
whichever axis the canvas was longer on."""
from __future__ import annotations


def test_camera_aspect_matches_canvas(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")
    # After one animation frame the resize() handler should have set
    # camera.aspect from the canvas CSS dimensions.
    page.wait_for_function(
        "(() => {"
        "  const c = window.fastcad.renderer.domElement;"
        "  return Math.abs(window.fastcad.camera.aspect -"
        "    (c.clientWidth / c.clientHeight)) < 0.001;"
        "})()"
    )


def test_camera_aspect_updates_on_resize(live_server: str, page) -> None:
    """Resizing the viewport must propagate to camera.aspect."""
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")
    page.set_viewport_size({"width": 1600, "height": 600})
    page.wait_for_function(
        "(() => {"
        "  const c = window.fastcad.renderer.domElement;"
        "  return Math.abs(window.fastcad.camera.aspect -"
        "    (c.clientWidth / c.clientHeight)) < 0.001"
        "    && c.clientWidth > 1000;"
        "})()"
    )
    page.set_viewport_size({"width": 800, "height": 1000})
    page.wait_for_function(
        "(() => {"
        "  const c = window.fastcad.renderer.domElement;"
        "  return Math.abs(window.fastcad.camera.aspect -"
        "    (c.clientWidth / c.clientHeight)) < 0.001"
        "    && c.clientHeight > 800;"
        "})()"
    )
