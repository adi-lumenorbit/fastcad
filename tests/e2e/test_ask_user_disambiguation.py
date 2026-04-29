"""With two cubes, the agent asks; clicking an option resumes."""
from __future__ import annotations


def test_two_cubes_then_sphere_triggers_ask(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    for prompt in ["Make a 20mm cube", "Make a 30mm cube"]:
        page.fill("[data-testid=chat-input]", prompt)
        page.click("[data-testid=chat-send-btn]")

    page.wait_for_function("window.fastcad.meshMap.size === 2")

    page.fill("[data-testid=chat-input]", "Add a sphere on top")
    page.click("[data-testid=chat-send-btn]")

    page.wait_for_selector("[data-testid=ask-user-area]:not([hidden])", timeout=5000)
    options = page.locator("[data-testid=ask-option]")
    options.first.wait_for()
    assert options.count() >= 2

    # No new mesh yet.
    n_before = page.evaluate("() => window.fastcad.meshMap.size")
    assert n_before == 2

    options.first.click()
    page.wait_for_function("window.fastcad.meshMap.size === 3", timeout=5000)
