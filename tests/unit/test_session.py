"""Tests for the rewritten SessionState (current_source + undo / redo
stacks + per-id cache, replacing the previous op log)."""
from __future__ import annotations

import pytest

from fastcad.model.scad_eval import EvalError
from fastcad.model.scad_parser import ScadParseError
from fastcad.session import INITIAL_SOURCE, SessionState


def test_initial_state():
    s = SessionState()
    assert s.current_source == INITIAL_SOURCE
    assert s.cache == {}
    assert not s.can_undo()
    assert not s.can_redo()


def test_set_source_creates_node():
    s = SessionState()
    cs = s.set_source("cube([10, 10, 10]);")
    assert cs.added == ["cube"]
    assert "cube" in s.cache
    assert s.can_undo()


def test_set_source_no_geometry_change_still_pushes_undo():
    """Even if the new source produces the same geometry, the source
    string changed (perhaps a comment was added), so undo should be
    enabled."""
    s = SessionState()
    s.set_source("cube([1, 1, 1]);")
    s.set_source("// comment\ncube([1, 1, 1]);")
    assert s.can_undo()


def test_undo_restores_prior_source():
    s = SessionState()
    s.set_source("cube([1, 1, 1]);")
    s.set_source("sphere(r = 1);")
    assert "sphere" in s.cache
    s.undo()
    assert "cube" in s.cache
    assert "sphere" not in s.cache
    assert s.can_redo()


def test_redo_restores_undone_source():
    s = SessionState()
    s.set_source("cube([1, 1, 1]);")
    s.set_source("sphere(r = 1);")
    s.undo()
    s.redo()
    assert "sphere" in s.cache


def test_set_source_after_undo_truncates_redo():
    s = SessionState()
    s.set_source("cube([1, 1, 1]);")
    s.set_source("sphere(r = 1);")
    s.undo()
    assert s.can_redo()
    s.set_source("cylinder(h = 2, r = 1);")
    assert not s.can_redo()
    assert "cylinder" in s.cache
    assert "sphere" not in s.cache


def test_undo_with_empty_stack_is_noop():
    s = SessionState()
    cs = s.undo()
    assert cs.added == []
    assert cs.updated == []
    assert cs.removed == []


def test_reset_clears_state():
    s = SessionState()
    s.set_source("cube([1, 1, 1]);")
    s.reset()
    assert s.current_source == INITIAL_SOURCE
    assert s.cache == {}
    assert not s.can_undo()
    assert not s.can_redo()


def test_set_source_parse_error_does_not_mutate():
    s = SessionState()
    s.set_source("cube([10, 10, 10]);")
    prev_src = s.current_source
    prev_cache = dict(s.cache)
    with pytest.raises(ScadParseError):
        s.set_source("cube(missing semicolon\n")
    assert s.current_source == prev_src
    assert s.cache == prev_cache


def test_set_source_eval_error_does_not_mutate():
    s = SessionState()
    s.set_source("cube([10, 10, 10]);")
    prev_src = s.current_source
    with pytest.raises(EvalError):
        s.set_source("nonexistent_module();")
    assert s.current_source == prev_src


def test_set_source_incremental_only_invalidates_dependents():
    s = SessionState()
    s.set_source("""
length = 10;
module a() { cube([length, 1, 1]); }
module b() { cube([1, 1, 5]); }
a(); b();
""")
    a_before = s.cache["a"]
    b_before = s.cache["b"]
    cs = s.set_source("""
length = 20;
module a() { cube([length, 1, 1]); }
module b() { cube([1, 1, 5]); }
a(); b();
""")
    # a was invalidated; b cache-hit.
    assert "a" in cs.updated
    assert "b" not in cs.updated
    assert s.cache["b"] is b_before  # same object
    assert s.cache["a"] is not a_before
