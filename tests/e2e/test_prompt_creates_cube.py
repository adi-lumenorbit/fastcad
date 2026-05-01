"""Prompt -> cube appears in three.js scene as a single Mesh."""
from __future__ import annotations


def test_prompt_creates_one_mesh(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true", timeout=5000)

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")

    # Wait until the per-id mesh map has exactly one entry.
    page.wait_for_function("window.fastcad.meshMap.size === 1", timeout=5000)
    # Agent message confirms.
    page.wait_for_selector("[data-testid=chat-log] .msg.agent")

    bbox = page.evaluate(
        """() => {
        const m = [...window.fastcad.meshMap.values()][0];
        m.geometry.computeBoundingBox();
        const bb = m.geometry.boundingBox;
        return { min: bb.min.toArray(), max: bb.max.toArray() };
        }"""
    )
    assert bbox["min"] == [0, 0, 0]
    assert bbox["max"] == [20, 20, 20]


def test_second_prompt_only_adds_one_mesh(live_server: str, page) -> None:
    page.goto(live_server + "/")
    page.wait_for_function("window.fastcad && window.fastcad.ready === true")

    page.fill("[data-testid=chat-input]", "Make a 20mm cube")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 1")

    # Snapshot the existing mesh's UUID — it must NOT change.
    uuid_before = page.evaluate(
        "() => [...window.fastcad.meshMap.values()][0].uuid"
    )

    page.fill("[data-testid=chat-input]", "Add a 10mm sphere on top centered")
    page.click("[data-testid=chat-send-btn]")
    page.wait_for_function("window.fastcad.meshMap.size === 2", timeout=5000)

    uuid_after = page.evaluate(
        """() => {
            // Under the .scad-spec model the agent names modules
            // semantically; the fake mode emits cube_1 / sphere_1.
            const m = window.fastcad.meshMap.get('cube_1');
            return m ? m.uuid : null;
        }"""
    )
    # The cube mesh object identity is preserved across the second prompt —
    # only the new sphere mesh was added. This is the "incremental render"
    # guarantee the plan promises.
    assert uuid_before == uuid_after
