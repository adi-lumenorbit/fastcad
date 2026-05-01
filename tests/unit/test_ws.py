"""TestClient-based WS smoke test. No browser, no Playwright."""
import json
import os

import pytest

os.environ.setdefault("ANTHROPIC_FAKE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from fastcad.server.app import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _drain_until(ws, target_type: str, max_msgs: int = 16) -> dict:
    for _ in range(max_msgs):
        msg = json.loads(ws.receive_text())
        if msg["type"] == target_type:
            return msg
    raise AssertionError(f"never saw {target_type}")


def _collect(ws, until_type: str, max_msgs: int = 16) -> list[dict]:
    """Receive WS messages until a `until_type` message arrives;
    return everything received including the terminator."""
    out: list[dict] = []
    for _ in range(max_msgs):
        msg = json.loads(ws.receive_text())
        out.append(msg)
        if msg["type"] == until_type:
            return out
    raise AssertionError(f"never saw {until_type} within {max_msgs} messages")


def _drain_all(ws, max_msgs: int = 8) -> list[dict]:
    out: list[dict] = []
    for _ in range(max_msgs):
        try:
            ws.receive_text(timeout=0.2)  # type: ignore[arg-type]
        except Exception:
            break
    return out


def test_ws_initial_scene_is_empty(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "scene_init"
        assert msg["nodes"] == []


def test_ws_prompt_creates_cube_and_emits_delta(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        json.loads(ws.receive_text())  # scene_init
        ws.send_text(json.dumps({"type": "prompt", "text": "Make a 20mm cube"}))
        delta = _drain_until(ws, "scene_delta")
        assert len(delta["added"]) == 1
        # Under the .scad-spec model, the agent wraps the cube in a
        # named module; the node id is the module name.
        assert delta["added"][0]["id"] == "cube_1"
        # Mesh transport carries vertex+triangle counts.
        mesh = delta["added"][0]["mesh"]
        assert mesh["vertex_count"] == 8
        assert mesh["triangle_count"] == 12


def test_ws_undo_redo(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "prompt", "text": "Make a 20mm cube"}))
        _drain_until(ws, "scene_delta")
        ws.send_text(json.dumps({"type": "undo"}))
        snap = _drain_until(ws, "scene_init")
        assert snap["nodes"] == []
        ws.send_text(json.dumps({"type": "redo"}))
        snap = _drain_until(ws, "scene_init")
        assert len(snap["nodes"]) == 1


def test_ws_ask_user_when_two_cubes(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "prompt", "text": "Make a 20mm cube"}))
        _drain_until(ws, "scene_delta")
        ws.send_text(json.dumps({"type": "prompt", "text": "Make a 30mm cube"}))
        _drain_until(ws, "scene_delta")
        ws.send_text(json.dumps({"type": "prompt", "text": "Add a sphere on top"}))
        ask = _drain_until(ws, "ask_user")
        assert len(ask["options"]) >= 2
        # Answer with one of the options.
        chosen = ask["options"][0]
        ws.send_text(json.dumps({"type": "user_choice", "text": chosen}))
        _drain_until(ws, "scene_delta")


def test_ws_progress_events_during_prompt(client: TestClient):
    """A prompt that triggers a set_source emits at least one
    `progress` message bracketing the tool call."""
    with client.websocket_connect("/ws") as ws:
        json.loads(ws.receive_text())  # scene_init
        ws.send_text(json.dumps({"type": "prompt", "text": "Make a 20mm cube"}))
        msgs = _collect(ws, until_type="tool_log", max_msgs=32)
        types = [m["type"] for m in msgs]
        # We expect at least one progress event (around set_source).
        assert "progress" in types, f"no progress events seen: {types}"
        progress_msgs = [m for m in msgs if m["type"] == "progress"]
        # The very first progress event from a "make a cube" turn
        # should be a tool_call_started for set_source.
        first_started = next(
            (m for m in progress_msgs if m["event"]["type"] == "tool_call_started"),
            None,
        )
        assert first_started is not None
        assert first_started["event"]["tool"] == "set_source"
        # Each progress carries an id.
        for m in progress_msgs:
            assert m["id"].startswith("evt_")


def test_ws_export_scad(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "prompt", "text": "Make a 20mm cube"}))
        _drain_until(ws, "scene_delta")
        ws.send_text(json.dumps({"type": "export_scad"}))
        scad = _drain_until(ws, "scad")
        # The export IS the spec source — raw .scad with the agent's
        # module names — no translation layer.
        assert "module cube_1" in scad["source"]
        assert "cube([20, 20, 20])" in scad["source"]
        assert "cube_1();" in scad["source"]
