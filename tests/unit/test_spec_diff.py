"""Tests for the incremental-rebuild diff layer."""
from __future__ import annotations

import pytest

from fastcad.model.spec_diff import diff_and_evaluate, hashes_match


def test_first_eval_all_added():
    src = "cube([1,1,1]); sphere(r=2);"
    cs, cache = diff_and_evaluate(src, prev_cache=None)
    assert sorted(cs.added) == sorted(cache.keys())
    assert "cube" in cache
    assert "sphere" in cache
    assert cs.updated == []
    assert cs.removed == []


def test_no_change_produces_empty_diff():
    src = "cube([1,1,1]);"
    _, cache = diff_and_evaluate(src, prev_cache=None)
    cs, cache2 = diff_and_evaluate(src, prev_cache=cache)
    assert cs.added == []
    assert cs.updated == []
    assert cs.removed == []
    assert cache2["cube"] is cache["cube"]   # cache hit (same object)


def test_used_var_change_invalidates_only_dependents():
    src1 = """
length = 20;
module shaft() { cube([length, 1, 1]); }
module head() { cube([1, 1, 5]); }
shaft(); head();
"""
    src2 = """
length = 25;
module shaft() { cube([length, 1, 1]); }
module head() { cube([1, 1, 5]); }
shaft(); head();
"""
    _, cache = diff_and_evaluate(src1, None)
    cs, cache2 = diff_and_evaluate(src2, cache)
    assert "shaft" in cs.updated
    assert "head" not in cs.updated
    # head must remain the same cached object.
    assert cache2["head"] is cache["head"]


def test_unrelated_var_change_no_diff():
    src1 = """
length = 20;
unused = 5;
cube([length, 1, 1]);
"""
    src2 = """
length = 20;
unused = 999;
cube([length, 1, 1]);
"""
    _, cache = diff_and_evaluate(src1, None)
    cs, cache2 = diff_and_evaluate(src2, cache)
    assert cs.added == []
    assert cs.updated == []
    assert cs.removed == []
    assert cache2["cube"] is cache["cube"]


def test_added_top_level_call_emits_added():
    src1 = "cube([1,1,1]);"
    src2 = "cube([1,1,1]); sphere(r=1);"
    _, cache = diff_and_evaluate(src1, None)
    cs, cache2 = diff_and_evaluate(src2, cache)
    assert cs.added == ["sphere"]
    assert cs.updated == []
    assert cs.removed == []


def test_removed_top_level_call_emits_removed():
    src1 = "cube([1,1,1]); sphere(r=1);"
    src2 = "cube([1,1,1]);"
    _, cache = diff_and_evaluate(src1, None)
    cs, cache2 = diff_and_evaluate(src2, cache)
    assert cs.removed == ["sphere"]
    assert "sphere" not in cache2


def test_renamed_module_emits_remove_plus_add():
    src1 = """
module old_name() { cube([1,1,1]); }
old_name();
"""
    src2 = """
module new_name() { cube([1,1,1]); }
new_name();
"""
    _, cache = diff_and_evaluate(src1, None)
    cs, _ = diff_and_evaluate(src2, cache)
    assert "old_name" in cs.removed
    assert "new_name" in cs.added


def test_called_module_body_change_cascades():
    """Changing the body of a module that's called by another module
    invalidates the caller too."""
    src1 = """
module inner() { cube([1, 1, 1]); }
module outer() { union() { inner(); translate([2,0,0]) inner(); } }
outer();
"""
    src2 = """
module inner() { cube([2, 2, 2]); }
module outer() { union() { inner(); translate([2,0,0]) inner(); } }
outer();
"""
    _, cache = diff_and_evaluate(src1, None)
    cs, _ = diff_and_evaluate(src2, cache)
    assert "outer" in cs.updated


def test_hashes_match_helper():
    src = "cube([1,1,1]);"
    _, cache = diff_and_evaluate(src, None)
    assert hashes_match(cache, src) is True
    assert hashes_match(cache, "cube([2,2,2]);") is False
    assert hashes_match(cache, "cube([1,1,1]); sphere(r=1);") is False


def test_hash_determinism_across_parses():
    """Same source string → same hash twice."""
    src = "cube([1,1,1]);"
    _, cache1 = diff_and_evaluate(src, None)
    _, cache2 = diff_and_evaluate(src, None)
    assert cache1["cube"].content_hash == cache2["cube"].content_hash


def test_top_level_with_only_2d_silently_skipped():
    """A top-level circle() produces 2D output which we don't render
    in 3D — should not appear in cache and should not error."""
    src = "circle(r=5);"
    cs, cache = diff_and_evaluate(src, None)
    assert cache == {}
    assert cs.added == []
    assert cs.updated == []
    assert cs.removed == []


def test_2d_to_3d_transition():
    """If a top-level id was 2D-only (skipped) and now becomes 3D
    (e.g. via linear_extrude), it should appear as added."""
    src1 = "circle(r=5);"
    src2 = "linear_extrude(height=2) circle(r=5);"
    _, cache = diff_and_evaluate(src1, None)
    cs, cache2 = diff_and_evaluate(src2, cache)
    # New src has node id "linear_extrude"
    assert "linear_extrude" in cs.added
    assert "linear_extrude" in cache2
