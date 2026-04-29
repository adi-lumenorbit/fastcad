"""Tests for the tool dispatcher + the fake-mode agent loop.

Real-mode (Anthropic) is exercised via integration in e2e (and skipped if
no key); fake mode is exhaustively tested here so behavior is locked in.
"""
import os

import pytest

os.environ.setdefault("ANTHROPIC_FAKE", "1")

from fastcad.agent.client import run_turn  # noqa: E402
from fastcad.agent.tools import dispatch  # noqa: E402
from fastcad.session import SessionState  # noqa: E402
from fastcad.model.kernel import volume  # noqa: E402


def test_dispatch_add_primitive():
    s = SessionState()
    result = dispatch("add_primitive", {"kind": "cube", "params": {"size": [5, 5, 5]}}, s)
    assert result.changes is not None
    assert len(s.scene.nodes) == 1
    assert result.changes.added


def test_dispatch_unknown_tool():
    s = SessionState()
    res = dispatch("not_a_tool", {}, s)
    assert "error" in res.content


def test_fake_make_cube():
    s = SessionState()
    turn = run_turn("Make a 20mm cube", s)
    assert turn.text and "cube" in turn.text.lower()
    assert len(s.scene.nodes) == 1
    nid = next(iter(s.scene.nodes))
    assert volume(s.scene.nodes[nid].manifold) == pytest.approx(8000.0)


def test_fake_sphere_on_top_anchors_to_only_solid():
    s = SessionState()
    run_turn("Make a 20mm cube", s)
    turn = run_turn("Add a 10mm sphere on top centered", s)
    assert turn.ask_user is None
    assert len(s.scene.nodes) == 2
    sphere_node = [n for n in s.scene.nodes.values() if n.kind == "sphere"][0]
    bb = sphere_node.manifold.bounding_box()
    # cube is 20mm, sphere center should be at z=20
    assert bb[2] == pytest.approx(15.0, abs=0.5)  # zmin
    assert bb[5] == pytest.approx(25.0, abs=0.5)  # zmax


def test_fake_sphere_on_top_with_two_cubes_asks_user():
    s = SessionState()
    run_turn("Make a 20mm cube", s)
    run_turn("Make a 30mm cube", s)
    turn = run_turn("Add a sphere on top", s)
    assert turn.ask_user is not None
    assert len(turn.ask_user["options"]) >= 2
    # No new sphere yet.
    assert sum(1 for n in s.scene.nodes.values() if n.kind == "sphere") == 0


def test_fake_resume_after_ask_user():
    s = SessionState()
    run_turn("Make a 20mm cube", s)
    run_turn("Make a 30mm cube", s)
    turn = run_turn("Add a sphere on top", s)
    assert turn.ask_user is not None
    chosen = turn.ask_user["options"][1]
    follow = run_turn(chosen, s, pending_ask=turn.ask_user)
    assert follow.text and "placed" in follow.text.lower()
    assert sum(1 for n in s.scene.nodes.values() if n.kind == "sphere") == 1


def test_fake_subtract_creates_difference():
    s = SessionState()
    run_turn("Make a 20mm cube", s)
    vol_before = volume(next(iter(s.scene.nodes.values())).manifold)
    turn = run_turn("Subtract a 5mm cylinder through it", s)
    assert turn.text and "subtract" in turn.text.lower()
    # cube remains, cylinder consumed
    assert all(n.kind != "cylinder" for n in s.scene.nodes.values())
    cube_like = next(iter(s.scene.nodes.values()))
    assert volume(cube_like.manifold) < vol_before


def test_fake_unknown_prompt_returns_help_text():
    s = SessionState()
    turn = run_turn("teach me python", s)
    assert turn.text is not None
    assert len(s.scene.nodes) == 0
