"""Unit tests for the open_scad WS handler and its helpers.

Two layers:
- pure validators (`_validate_open_path`, `_extract_fc_meta_title`)
  tested directly — they need no WS context.
- the dispatch path via `TestClient`, mirroring the other WS tests
  in `test_ws.py`.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("ANTHROPIC_FAKE", "1")

from fastapi.testclient import TestClient  # noqa: E402

from fastcad.server import ws  # noqa: E402
from fastcad.server.app import app  # noqa: E402


# --- helpers -------------------------------------------------------------

_CUBE_SCAD = """\
/* fc-meta
title: tiny test cube
created: 2026-05-12
tool: pytest
*/

module cube_1() {
  cube([10, 10, 10]);
}
cube_1();
"""


@pytest.fixture
def fixture_scad(tmp_path, monkeypatch):
    """Write a small valid .scad into tmp_path and point the
    open_scad allow-list at that directory."""
    p = tmp_path / "cube.scad"
    p.write_text(_CUBE_SCAD)
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(tmp_path))
    return p


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _drain_until(socket, target_type: str, max_msgs: int = 16) -> dict:
    for _ in range(max_msgs):
        msg = json.loads(socket.receive_text())
        if msg["type"] == target_type:
            return msg
    raise AssertionError(f"never saw {target_type}")


# --- pure validator tests ------------------------------------------------

def test_validate_rejects_non_string():
    resolved, err = ws._validate_open_path(123)
    assert resolved is None
    assert err == "path must be a string"


def test_validate_rejects_empty():
    resolved, err = ws._validate_open_path("")
    assert resolved is None
    assert err == "path must be non-empty"


def test_validate_rejects_non_scad_extension(tmp_path, monkeypatch):
    p = tmp_path / "notes.txt"
    p.write_text("hi")
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(tmp_path))
    resolved, err = ws._validate_open_path(str(p))
    assert resolved is None
    assert err == "path must end in .scad"


def test_validate_rejects_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(tmp_path))
    missing = tmp_path / "ghost.scad"
    resolved, err = ws._validate_open_path(str(missing))
    assert resolved is None
    assert err is not None and "not found" in err


def test_validate_rejects_outside_allowlist(tmp_path, monkeypatch):
    # tmp_path holds the file, but allow-list points elsewhere.
    p = tmp_path / "x.scad"
    p.write_text("cube([1,1,1]);")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(other))
    resolved, err = ws._validate_open_path(str(p))
    assert resolved is None
    assert err == "path is not inside any allow-listed directory"


def test_validate_rejects_oversize_file(tmp_path, monkeypatch):
    p = tmp_path / "big.scad"
    # Slightly over the 1 MiB cap.
    p.write_bytes(b"// pad\n" * (ws._OPEN_MAX_BYTES // 7 + 1))
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(tmp_path))
    resolved, err = ws._validate_open_path(str(p))
    assert resolved is None
    assert err is not None and err.startswith("file too large")


def test_validate_symlink_outside_allowlist_is_rejected(tmp_path, monkeypatch):
    """A symlink inside the allow-list pointing at a file outside the
    allow-list must be rejected (post-resolve check)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "real.scad"
    target.write_text("cube([1,1,1]);")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "link.scad"
    link.symlink_to(target)
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(allowed))
    resolved, err = ws._validate_open_path(str(link))
    assert resolved is None
    assert err == "path is not inside any allow-listed directory"


def test_validate_accepts_valid_path(fixture_scad):
    resolved, err = ws._validate_open_path(str(fixture_scad))
    assert err is None
    assert resolved is not None
    assert resolved.suffix == ".scad"
    assert resolved.is_file()


def test_extract_fc_meta_title_returns_value():
    text = "/* fc-meta\ntitle: hello world\ntool: x\n*/\ncube(1);"
    assert ws._extract_fc_meta_title(text) == "hello world"


def test_extract_fc_meta_title_returns_none_when_no_block():
    assert ws._extract_fc_meta_title("cube(1);") is None


def test_extract_fc_meta_title_returns_none_when_block_has_no_title():
    text = "/* fc-meta\ntool: x\ncreated: 2026-05-12\n*/\ncube(1);"
    assert ws._extract_fc_meta_title(text) is None


# --- WS dispatch tests ---------------------------------------------------

def test_open_scad_loads_file_and_updates_scene(client, fixture_scad):
    with client.websocket_connect("/ws") as sock:
        _drain_until(sock, "scene_init")  # initial empty scene
        sock.send_text(json.dumps({"type": "open_scad", "path": str(fixture_scad)}))
        scene = _drain_until(sock, "scene_init")
        assert len(scene["nodes"]) == 1
        assert scene["nodes"][0]["id"] == "cube_1"
        msg = _drain_until(sock, "agent_message")
        assert "cube.scad" in msg["text"]
        assert "tiny test cube" in msg["text"]


def test_open_scad_rejects_path_outside_allowlist(client, tmp_path, monkeypatch):
    # Put the file in tmp_path but DON'T add tmp_path to the allow-list.
    p = tmp_path / "x.scad"
    p.write_text("cube([1,1,1]);")
    # Allow-list points elsewhere.
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(other))
    with client.websocket_connect("/ws") as sock:
        _drain_until(sock, "scene_init")
        sock.send_text(json.dumps({"type": "open_scad", "path": str(p)}))
        err = _drain_until(sock, "error")
        assert "allow-listed" in err["message"]


def test_open_scad_rejects_parse_failure_without_mutating(client, tmp_path, monkeypatch):
    p = tmp_path / "broken.scad"
    p.write_text("cube([1,1,1  // missing close\n")
    monkeypatch.setenv("FASTCAD_OPEN_ALLOWED_DIRS", str(tmp_path))
    with client.websocket_connect("/ws") as sock:
        _drain_until(sock, "scene_init")
        sock.send_text(json.dumps({"type": "open_scad", "path": str(p)}))
        err = _drain_until(sock, "error")
        assert "parse/eval failed" in err["message"]
        # Confirm state is still empty by exporting and checking the
        # spec source — a parse failure must not have mutated.
        sock.send_text(json.dumps({"type": "export_scad"}))
        scad = _drain_until(sock, "scad")
        from fastcad.session import INITIAL_SOURCE
        assert scad["source"] == INITIAL_SOURCE


def test_open_scad_then_undo_restores_previous(client, fixture_scad):
    with client.websocket_connect("/ws") as sock:
        _drain_until(sock, "scene_init")
        sock.send_text(json.dumps({"type": "open_scad", "path": str(fixture_scad)}))
        _drain_until(sock, "scene_init")
        _drain_until(sock, "agent_message")
        sock.send_text(json.dumps({"type": "undo"}))
        scene = _drain_until(sock, "scene_init")
        assert scene["nodes"] == []


def test_open_scad_rejects_missing_path_field(client):
    with client.websocket_connect("/ws") as sock:
        _drain_until(sock, "scene_init")
        sock.send_text(json.dumps({"type": "open_scad"}))
        err = _drain_until(sock, "error")
        assert "path must be a string" in err["message"]
