"""Feedback POST endpoint test using FastAPI TestClient.

Verifies a multipart bundle lands in tmp/feedback/<ts>/ with each piece in
the right file.
"""
import json
import os
from pathlib import Path

import pytest

from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTCAD_FEEDBACK_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_FAKE", "1")
    # Re-import to pick up env.
    import importlib
    import fastcad.server.app as appmod
    importlib.reload(appmod)
    return appmod.app, Path(tmp_path)


def test_feedback_writes_full_bundle(app):
    fastapi_app, root = app
    client = TestClient(fastapi_app)
    files = {
        "dom_png": ("dom.png", b"\x89PNG\r\n\x1a\n_dom_", "image/png"),
        "viewer_png": ("viewer.png", b"\x89PNG\r\n\x1a\n_view_", "image/png"),
    }
    data = {
        "description": "the undo button is misaligned on hover",
        "target": json.dumps({"selector": "#undo-btn", "rect": {"x": 12, "y": 12, "w": 60, "h": 28}}),
        "rrweb_events": json.dumps([{"type": 1, "timestamp": 0}]),
        "camera": json.dumps({"position": [80, -80, 60]}),
        "oplog": json.dumps([{"kind": "AddPrimitive", "node_id": "cube_1"}]),
        "ws_log": json.dumps([{"dir": "out", "type": "prompt"}]),
    }
    r = client.post("/feedback", data=data, files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    # Find the directory created.
    dirs = list(root.iterdir())
    assert len(dirs) == 1
    d = dirs[0]
    assert (d / "description.txt").read_text() == data["description"]
    assert json.loads((d / "target.json").read_text())["selector"] == "#undo-btn"
    assert json.loads((d / "camera.json").read_text())["position"] == [80, -80, 60]
    assert (d / "dom.png").read_bytes().endswith(b"_dom_")
    assert (d / "viewer.png").read_bytes().endswith(b"_view_")
    assert json.loads((d / "rrweb.json").read_text())[0]["type"] == 1
    assert json.loads((d / "oplog.json").read_text())[0]["node_id"] == "cube_1"
    assert json.loads((d / "ws_log.json").read_text())[0]["type"] == "prompt"


def test_feedback_minimal_post(app):
    fastapi_app, root = app
    client = TestClient(fastapi_app)
    r = client.post("/feedback", data={"description": "hi"})
    assert r.status_code == 200
    dirs = list(root.iterdir())
    assert len(dirs) == 1
    assert (dirs[0] / "description.txt").read_text() == "hi"
    assert not (dirs[0] / "dom.png").exists()


def test_feedback_invalid_json_rejected(app):
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    r = client.post("/feedback", data={"target": "not-json"})
    assert r.status_code == 400
