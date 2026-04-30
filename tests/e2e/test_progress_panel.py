"""Progress panel: a prompt that runs a tool emits live progress
events that render as entries in the panel below chat-log."""
from __future__ import annotations


def test_progress_panel_populates_on_prompt(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    # Panel visible from the start.
    panel = page.locator("[data-testid=progress-panel]")
    panel.wait_for()
    assert page.evaluate("window.fastcad.progressEntryCount()") == 0

    # Send a prompt that triggers a set_source.
    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")

    # At least one progress entry appears (tool_call_started → done for
    # set_source). Wait for the entry to land + flip to done.
    page.wait_for_function(
        "() => window.fastcad.progressEntryCount() >= 1",
        timeout=5000,
    )
    page.wait_for_function(
        "() => document.querySelectorAll('.progress-entry.done').length >= 1",
        timeout=5000,
    )

    # The set_source entry should mention "set_source" with a checkmark.
    text = page.evaluate(
        """() => {
            const dones = document.querySelectorAll('.progress-entry.done');
            return [...dones].map(e => e.textContent).join('\\n');
        }"""
    )
    assert "set_source" in text


def test_progress_panel_clear_button(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function(
        "() => window.fastcad.progressEntryCount() >= 1",
        timeout=5000,
    )

    page.click("[data-testid=progress-clear-btn]")
    page.wait_for_function(
        "() => window.fastcad.progressEntryCount() === 0",
        timeout=2000,
    )
