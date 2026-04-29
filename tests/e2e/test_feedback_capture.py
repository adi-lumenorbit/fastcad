"""Send Feedback bundles everything to tmp/feedback/<ts>/."""
from __future__ import annotations

import json
from pathlib import Path


def test_feedback_capture_writes_bundle(live_server: str, page, feedback_dir: Path) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    # Get something into the scene so the bundle has interesting content.
    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")

    # Pre-fill a target as if the user clicked Point + an element. We do this
    # programmatically so the test doesn't have to mock window.prompt twice.
    page.evaluate(
        """() => window.fastcad.setPointed({
            selector: '[data-testid=undo-btn]',
            rect: {x: 12, y: 12, w: 60, h: 28},
            text: 'Undo',
            tag: 'button'
        })"""
    )

    # Auto-accept the prompt() with a known description.
    description = "the undo button label is unclear"
    page.once("dialog", lambda d: d.accept(description))

    before = set(p.name for p in feedback_dir.iterdir())
    page.click("[data-testid=feedback-send-btn]")
    page.wait_for_function(
        "() => /saved:/.test(document.querySelector('[data-testid=feedback-status]').textContent)",
        timeout=10000,
    )

    new = [p for p in feedback_dir.iterdir() if p.name not in before]
    assert len(new) == 1
    d = new[0]
    assert (d / "description.txt").read_text() == description
    target = json.loads((d / "target.json").read_text())
    assert target["selector"] == "[data-testid=undo-btn]"
    assert (d / "rrweb.json").exists()
    # Camera state captured.
    cam = json.loads((d / "camera.json").read_text())
    assert "position" in cam and len(cam["position"]) == 3
    # WS log carries the prompt + scene_delta.
    ws_log = json.loads((d / "ws_log.json").read_text())
    types = {entry.get("type") for entry in ws_log}
    assert "prompt" in types
    assert "scene_delta" in types
