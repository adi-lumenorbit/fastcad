"""Tests for the rewritten agent tool dispatcher and the deterministic
fake-mode loop. Real-mode (Anthropic) is exercised in e2e."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("ANTHROPIC_FAKE", "1")

from fastcad.agent.client import _reset_fake, run_turn  # noqa: E402
from fastcad.agent.tools import dispatch  # noqa: E402
from fastcad.model.kernel import volume  # noqa: E402
from fastcad.session import SessionState  # noqa: E402


def _fresh() -> SessionState:
    s = SessionState()
    _reset_fake(s)
    return s


# ---- direct tool dispatch ---------------------------------------------------


def test_dispatch_set_source_cube():
    s = _fresh()
    res = dispatch("set_source", {"text": "cube([5, 5, 5]);"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert "cube" in payload["added"]
    assert "cube" in s.cache
    assert res.changes is not None
    assert res.changes.added


def test_dispatch_set_source_parse_error():
    s = _fresh()
    res = dispatch("set_source", {"text": "this is not scad"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "error" in payload


def test_dispatch_validate_does_not_mutate():
    s = _fresh()
    s.set_source("cube([5, 5, 5]);")
    prev = s.current_source
    res = dispatch("validate", {"text": "cube([10, 10, 10]);"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert s.current_source == prev


def test_dispatch_validate_reports_error():
    s = _fresh()
    res = dispatch("validate", {"text": "function f(x) = x+1;"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "function" in payload["error"]


def test_dispatch_read_source():
    s = _fresh()
    s.set_source("cube([1, 1, 1]);")
    res = dispatch("read_source", {}, s)
    payload = json.loads(res.content)
    assert payload["source"] == s.current_source


def test_dispatch_select_face_unknown_node():
    s = _fresh()
    res = dispatch("select_face", {"node_id": "doesnotexist", "face_name": "+Z"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False


def test_dispatch_select_face_returns_point_normal():
    s = _fresh()
    s.set_source("cube([10, 10, 10]);")
    res = dispatch("select_face", {"node_id": "cube", "face_name": "+Z"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert payload["point"] == [5.0, 5.0, 10.0]
    assert payload["normal"] == [0.0, 0.0, 1.0]


def test_dispatch_unknown_tool():
    s = _fresh()
    res = dispatch("not_a_tool", {}, s)
    assert "error" in res.content


# ---- fake-mode patterns ---------------------------------------------------


def test_fake_make_cube():
    s = _fresh()
    turn = run_turn("Make a 20mm cube", s)
    assert turn.text and "cube" in turn.text.lower()
    assert "cube_1" in s.cache
    assert volume(s.cache["cube_1"].manifold) == pytest.approx(8000.0)


def test_fake_sphere_on_top_anchors_to_only_solid():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    turn = run_turn("Add a 10mm sphere on top centered", s)
    assert turn.ask_user is None
    assert "sphere_1" in s.cache
    bb = s.cache["sphere_1"].bbox
    # Sphere center should land at z=20 (top face of cube).
    assert bb.zmin == pytest.approx(15.0, abs=0.5)
    assert bb.zmax == pytest.approx(25.0, abs=0.5)


def test_fake_sphere_on_top_with_two_cubes_asks_user():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    run_turn("Make a 30mm cube", s)
    turn = run_turn("Add a sphere on top", s)
    assert turn.ask_user is not None
    assert len(turn.ask_user["options"]) >= 2
    # No new sphere yet.
    assert not any(k.startswith("sphere_") for k in s.cache)


def test_fake_resume_after_ask_user():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    run_turn("Make a 30mm cube", s)
    turn = run_turn("Add a sphere on top", s)
    assert turn.ask_user is not None
    chosen = turn.ask_user["options"][1]
    follow = run_turn(chosen, s, pending_ask=turn.ask_user)
    assert follow.text and "placed" in follow.text.lower()
    assert any(k.startswith("sphere_") for k in s.cache)


def test_fake_subtract_creates_difference():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    vol_before = volume(s.cache["cube_1"].manifold)
    turn = run_turn("Subtract a 5mm cylinder through it", s)
    assert turn.text and "subtract" in turn.text.lower()
    # cube_1 is still the only top-level visible node — its body changed.
    assert set(s.cache.keys()) == {"cube_1"}
    assert volume(s.cache["cube_1"].manifold) < vol_before


def test_fake_unknown_prompt_returns_help_text():
    s = _fresh()
    turn = run_turn("teach me python", s)
    assert turn.text is not None
    assert s.cache == {}
