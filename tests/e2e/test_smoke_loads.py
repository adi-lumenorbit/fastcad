"""Page boots, viewer canvas appears, WS connects."""
from __future__ import annotations


def test_page_loads_and_ws_opens(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_selector("[data-testid=viewer]")
    page.wait_for_function("document.body.dataset.wsState === 'open'", timeout=5000)
    # Importmap-loaded modules: window.fastcad.ready becomes true once main.js runs.
    page.wait_for_function("window.fastcad && window.fastcad.ready === true", timeout=5000)
    # Send button is wired up.
    assert page.locator("[data-testid=chat-send-btn]").is_enabled()
    assert page.locator("[data-testid=feedback-send-btn]").is_enabled()
