"""Open .scad dialog — load a fixture, undo, error path."""
from __future__ import annotations

from pathlib import Path


_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_open_scad_loads_fixture_and_undo_restores(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    # Open the dialog.
    page.click("[data-testid=open-btn]")
    page.wait_for_function(
        "document.querySelector('[data-testid=open-dialog]').open === true"
    )

    # Fill the fixture path + confirm.
    cube_path = str(_FIXTURES / "cube.scad")
    page.fill("[data-testid=open-path-input]", cube_path)
    page.click("[data-testid=open-confirm-btn]")

    # The dialog closes once scene_init arrives; mesh shows up.
    page.wait_for_function("window.fastcad.meshMap.size === 1", timeout=5000)
    page.wait_for_function(
        "document.querySelector('[data-testid=open-dialog]').open === false"
    )

    # The chat log should mention the loaded file + the fc-meta title.
    page.wait_for_function(
        "document.querySelector('[data-testid=chat-log]')"
        ".innerText.includes('cube.scad')"
    )
    page.wait_for_function(
        "document.querySelector('[data-testid=chat-log]')"
        ".innerText.includes('e2e fixture cube')"
    )

    # Undo returns the scene to empty.
    page.click("[data-testid=undo-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 0", timeout=5000)


def test_open_scad_handles_path_with_spaces(live_server: str, page) -> None:
    """Regression: a fixture path with spaces in the filename.
    Reported as not working from the UI even though the server
    handled it correctly when probed directly — flags a client-side
    bug in how the path is captured / sent / awaited."""
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.click("[data-testid=open-btn]")
    page.wait_for_function(
        "document.querySelector('[data-testid=open-dialog]').open === true"
    )

    spaced_path = str(_FIXTURES / "cube with spaces.scad")
    page.fill("[data-testid=open-path-input]", spaced_path)
    page.click("[data-testid=open-confirm-btn]")

    page.wait_for_function("window.fastcad.meshMap.size === 1", timeout=5000)
    page.wait_for_function(
        "document.querySelector('[data-testid=open-dialog]').open === false"
    )


def test_open_scad_invalid_path_shows_inline_error(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.click("[data-testid=open-btn]")
    page.wait_for_function(
        "document.querySelector('[data-testid=open-dialog]').open === true"
    )

    # /etc/passwd is outside the allow-list AND lacks the .scad extension —
    # the validator should reject before reading anything.
    page.fill("[data-testid=open-path-input]", "/etc/passwd")
    page.click("[data-testid=open-confirm-btn]")

    # Error shown in the dialog, dialog stays open.
    page.wait_for_selector("[data-testid=open-error]:not([hidden])")
    err_text = page.inner_text("[data-testid=open-error]")
    assert "open_scad" in err_text
    # Dialog still open.
    assert page.evaluate(
        "document.querySelector('[data-testid=open-dialog]').open"
    ) is True
    # Scene still empty.
    assert page.evaluate("window.fastcad.meshMap.size") == 0

    # Cancel closes the dialog without mutating state.
    page.click("[data-testid=open-cancel-btn]")
    page.wait_for_function(
        "document.querySelector('[data-testid=open-dialog]').open === false"
    )
