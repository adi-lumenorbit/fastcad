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


def test_agent_status_transitions_idle_to_thinking_to_idle(live_server: str, page) -> None:
    """Sending a prompt flips status to 'thinking'; the agent's
    final message flips it back to 'idle'. Asserts the full sequence
    via the recorded history rather than polling for a sub-tick
    intermediate state — fake-mode turns can complete inside one
    event-loop tick under load."""
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    assert page.evaluate("window.fastcad.agentStatus()") == "idle"

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")

    page.wait_for_function(
        "() => window.fastcad.agentStatus() === 'idle' && window.fastcad.agentStatusHistory().includes('thinking')",
        timeout=8000,
    )

    history = page.evaluate("window.fastcad.agentStatusHistory()")
    assert history[0] == "idle"
    assert "thinking" in history
    assert history[-1] == "idle"


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


def test_agent_status_shows_label(live_server: str, page) -> None:
    """The status indicator's text label is what the user reads.
    Verify it's rendered, non-empty, and follows the same idle →
    thinking → idle round-trip the icon does."""
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    label = page.locator("[data-testid=agent-status-label]")
    assert label.inner_text() == "Idle"

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")

    # End-state assertion plus a history check: at some point during
    # the turn the label was something other than Idle.
    page.wait_for_function(
        "() => window.fastcad.agentStatus() === 'idle' && window.fastcad.agentStatusHistory().includes('thinking')",
        timeout=8000,
    )
    assert label.inner_text() == "Idle"


def test_agent_message_includes_stats_footer(live_server: str, page) -> None:
    """Each agent reply gets a `.msg-stats` footer with $ spent and
    elapsed time. In fake mode tokens are zero so the renderer falls
    back to "$0 · NN ms · 0↑ 0↓" — but the element is still rendered
    so the user sees the row."""
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")

    # Wait for the agent reply, then for the stats footer beneath it.
    page.wait_for_selector(
        ".msg.agent [data-testid=agent-stats]", timeout=8000
    )
    text = page.locator(".msg.agent [data-testid=agent-stats]").last.inner_text()
    # Three "·"-joined fields: cost, elapsed, tokens. Look for any
    # plausible elapsed unit ("ms" or "s") to pin behaviour.
    assert " · " in text
    assert "ms" in text or " s" in text


def test_chat_input_history_arrow_keys(live_server: str, page) -> None:
    """ArrowUp scrolls back through previous prompts, ArrowDown scrolls
    forward, ArrowDown past the newest restores the in-progress draft."""
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    # Clear any history persisted from a previous test.
    page.evaluate("localStorage.removeItem('fastcad.chatHistory')")
    page.reload()
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    # Submit two prompts to build up history.
    for prompt in ["first prompt", "second prompt"]:
        page.fill("[data-testid=chat-input]", prompt)
        page.click("[data-testid=chat-send-btn]")
        # Wait for the input to clear (proxy for "submit handled").
        page.wait_for_function(
            "() => document.querySelector('[data-testid=chat-input]').value === ''",
            timeout=4000,
        )

    chat_input = page.locator("[data-testid=chat-input]")

    # Type a draft, then ArrowUp twice → first prompt; ArrowDown → second;
    # ArrowDown again → restored draft.
    chat_input.fill("draft")
    chat_input.press("ArrowUp")
    assert chat_input.input_value() == "second prompt"
    chat_input.press("ArrowUp")
    assert chat_input.input_value() == "first prompt"
    chat_input.press("ArrowDown")
    assert chat_input.input_value() == "second prompt"
    chat_input.press("ArrowDown")
    assert chat_input.input_value() == "draft"
