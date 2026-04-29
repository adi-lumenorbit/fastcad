"""Undo and redo via the toolbar buttons."""
from __future__ import annotations


def test_undo_removes_last_mesh(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")

    page.click("[data-testid=undo-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 0", timeout=5000)


def test_redo_restores_mesh(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")

    page.click("[data-testid=undo-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 0")

    page.click("[data-testid=redo-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")
