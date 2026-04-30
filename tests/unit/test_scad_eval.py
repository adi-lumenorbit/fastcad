"""Tests for the spec evaluator: .scad source → ModuleEval per top-level
geometry-producing statement."""
from __future__ import annotations

import math

import pytest

from fastcad.model.scad_eval import (
    EvalError,
    content_hash_for_top_level,
    evaluate_source,
    top_level_node_ids,
)
from fastcad.model.scad_parser import parse


def _eval(src: str) -> dict:
    return evaluate_source(parse(src))


# ---- primitives -----------------------------------------------------------


def test_top_level_cube_produces_one_node():
    out = _eval("cube([10, 10, 10]);")
    assert "cube" in out
    me = out["cube"]
    assert me.manifold is not None
    assert me.bbox.xmax == pytest.approx(10.0)


def test_top_level_sphere():
    out = _eval("sphere(r = 5);")
    me = out["sphere"]
    bb = me.bbox
    assert (bb.xmax - bb.xmin) == pytest.approx(10.0, rel=0.05)


def test_cylinder_height_param():
    out = _eval("cylinder(h = 20, r = 3);")
    me = out["cylinder"]
    bb = me.bbox
    assert (bb.zmax - bb.zmin) == pytest.approx(20.0, abs=1e-5)


# ---- transforms -----------------------------------------------------------


def test_translate_shifts_bbox():
    out = _eval("translate([10, 0, 0]) cube([1, 1, 1]);")
    me = out["translate"]
    assert me.bbox.xmin == pytest.approx(10.0)


def test_rotate_in_xy_plane():
    out = _eval("rotate([0, 0, 90]) cube([2, 1, 1]);")
    me = out["rotate"]
    bb = me.bbox
    assert (bb.ymax - bb.ymin) == pytest.approx(2.0, abs=1e-5)


def test_scale_doubles_extent():
    out = _eval("scale([2, 1, 1]) cube([1, 1, 1]);")
    me = out["scale"]
    bb = me.bbox
    assert (bb.xmax - bb.xmin) == pytest.approx(2.0, abs=1e-5)


# ---- CSG ------------------------------------------------------------------


def test_union_combines_volumes():
    src = """
union() {
  cube([2, 2, 2]);
  translate([1, 0, 0]) cube([2, 2, 2]);
}
"""
    out = _eval(src)
    me = out["union"]
    bb = me.bbox
    assert bb.xmax == pytest.approx(3.0, abs=1e-5)


def test_difference_subtracts():
    src = """
difference() {
  cube([10, 10, 10]);
  translate([3, 3, -1]) cube([4, 4, 12]);
}
"""
    out = _eval(src)
    me = out["difference"]
    from fastcad.model import kernel as k
    expected = 10 * 10 * 10 - 4 * 4 * 10
    assert k.volume(me.manifold) == pytest.approx(expected, abs=1e-3)


def test_intersection_overlap():
    src = """
intersection() {
  cube([2, 2, 2]);
  translate([1, 1, 1]) cube([2, 2, 2]);
}
"""
    out = _eval(src)
    me = out["intersection"]
    from fastcad.model import kernel as k
    assert k.volume(me.manifold) == pytest.approx(1.0, abs=1e-5)


# ---- 2D + extrudes --------------------------------------------------------


def test_circle_in_linear_extrude():
    out = _eval("$fn = 64; linear_extrude(height = 5) circle(r = 3);")
    me = out["linear_extrude"]
    from fastcad.model import kernel as k
    assert k.volume(me.manifold) == pytest.approx(math.pi * 9 * 5, rel=0.02)


def test_square_in_linear_extrude():
    out = _eval("linear_extrude(height = 2) square([3, 4]);")
    me = out["linear_extrude"]
    from fastcad.model import kernel as k
    assert k.volume(me.manifold) == pytest.approx(24.0, abs=1e-5)


def test_polygon_in_linear_extrude():
    out = _eval("linear_extrude(height = 1) polygon([[0,0],[1,0],[0,1]]);")
    me = out["linear_extrude"]
    from fastcad.model import kernel as k
    assert k.volume(me.manifold) == pytest.approx(0.5, abs=1e-5)


def test_rotate_extrude_creates_torus_like():
    src = """
$fn = 96;
rotate_extrude() translate([4, 0, 0]) circle(r = 1);
"""
    out = _eval(src)
    me = out["rotate_extrude"]
    from fastcad.model import kernel as k
    expected = 2 * math.pi * 4 * math.pi * 1
    assert k.volume(me.manifold) == pytest.approx(expected, rel=0.05)


# ---- modules --------------------------------------------------------------


def test_user_module_with_params():
    src = """
module pillar(h = 5, r = 1) {
  cylinder(h = h, r = r, $fn = 32);
}
pillar(h = 10, r = 2);
"""
    out = _eval(src)
    me = out["pillar"]
    bb = me.bbox
    assert (bb.zmax - bb.zmin) == pytest.approx(10.0, abs=1e-5)
    assert (bb.xmax - bb.xmin) == pytest.approx(4.0, rel=0.02)


def test_user_module_implicit_union_of_body():
    src = """
module two_cubes() {
  cube([1, 1, 1]);
  translate([2, 0, 0]) cube([1, 1, 1]);
}
two_cubes();
"""
    out = _eval(src)
    me = out["two_cubes"]
    bb = me.bbox
    assert bb.xmin == pytest.approx(0.0)
    assert bb.xmax == pytest.approx(3.0)


def test_recursive_module_rejected():
    src = """
module r(n) { r(n - 1); }
r(3);
"""
    with pytest.raises(EvalError, match="recursive"):
        _eval(src)


def test_unknown_module_errors():
    with pytest.raises(EvalError, match="unknown module"):
        _eval("doesnotexist();")


def test_too_few_args_errors():
    src = """
module needs_one(x) { cube([x, x, x]); }
needs_one();
"""
    with pytest.raises(EvalError, match="missing required parameter"):
        _eval(src)


# ---- control flow ---------------------------------------------------------


def test_for_loop_unions_iterations():
    src = """
for (k = [0:2]) translate([k * 2, 0, 0]) cube([1, 1, 1]);
"""
    out = _eval(src)
    me = out["for"]
    bb = me.bbox
    assert bb.xmin == pytest.approx(0.0)
    assert bb.xmax == pytest.approx(5.0)


def test_if_branch_taken():
    src = """
n = 5;
if (n > 0) cube([1, 1, 1]); else cube([10, 10, 10]);
"""
    out = _eval(src)
    me = out["if"]
    assert me.bbox.xmax == pytest.approx(1.0)


def test_let_local_binding():
    src = """
let (a = 7) cube([a, 1, 1]);
"""
    out = _eval(src)
    me = out["let"]
    assert me.bbox.xmax == pytest.approx(7.0)


# ---- expressions --------------------------------------------------------


def test_arithmetic_in_args():
    src = """
length = 10;
cube([length * 2, length / 5, length - 8]);
"""
    out = _eval(src)
    me = out["cube"]
    bb = me.bbox
    assert bb.xmax == pytest.approx(20.0)
    assert bb.ymax == pytest.approx(2.0)
    assert bb.zmax == pytest.approx(2.0)


def test_function_call_in_arg():
    src = """
$fn = 64;
cylinder(h = sqrt(100), r = 1);
"""
    out = _eval(src)
    me = out["cylinder"]
    assert (me.bbox.zmax - me.bbox.zmin) == pytest.approx(10.0, abs=1e-5)


def test_ternary_in_arg():
    src = """
big = true;
cube([big ? 10 : 1, 1, 1]);
"""
    out = _eval(src)
    assert out["cube"].bbox.xmax == pytest.approx(10.0)


# ---- threaded-extrude fixture: parser + evaluator end-to-end --------------


THREADED_EXTRUDE_SRC = """
diameter = 3;
length   = 20;
pitch    = 0.5;
$fn      = 64;

module thread_section(major, minor) {
  difference() {
    circle(d = major);
    for (k = [0:11])
      rotate([0, 0, k * 30])
        translate([minor / 2, 0, 0])
          polygon([[0, -0.15], [0.4, 0], [0, 0.15]]);
  }
}

module shaft() {
  linear_extrude(height = length, twist = 360 * length / pitch)
    thread_section(major = diameter, minor = diameter * 0.85);
}

module head() {
  translate([0, 0, length])
    linear_extrude(height = 2)
      circle(d = diameter * 1.6);
}

module screw() {
  union() { shaft(); head(); }
}

screw();
"""


def test_threaded_extrude_evaluates_to_one_node():
    """A non-trivial extrude+twist composition evaluates to a single
    top-level node whose total Z-extent is shaft length + head height."""
    out = _eval(THREADED_EXTRUDE_SRC)
    assert list(out.keys()) == ["screw"]
    me = out["screw"]
    bb = me.bbox
    # Total height = shaft length 20 + head height 2 = 22.
    assert (bb.zmax - bb.zmin) == pytest.approx(22.0, abs=0.5)


# ---- faces ----------------------------------------------------------------


def test_top_level_call_publishes_six_faces():
    out = _eval("cube([10, 10, 10]);")
    me = out["cube"]
    assert set(me.faces.keys()) == {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}
    assert me.faces["+Z"].point == pytest.approx((5.0, 5.0, 10.0))
    assert me.faces["+Z"].normal == pytest.approx((0.0, 0.0, 1.0))


# ---- node ids -------------------------------------------------------------


def test_node_ids_include_user_module_name():
    src = """
module gizmo() { cube([1,1,1]); }
gizmo();
"""
    assert top_level_node_ids(parse(src)) == ["gizmo"]


def test_node_ids_anonymous_top_level_get_synth():
    """A `for` at the top level (not inside a module) is anonymous."""
    src = "for (k = [0:2]) translate([k, 0, 0]) cube([1, 1, 1]);"
    out = _eval(src)
    # The id is the statement-kind name "for".
    assert "for" in out


# ---- content hashing ------------------------------------------------------


def test_content_hash_changes_when_used_var_changes():
    src1 = """
length = 20;
cube([length, 1, 1]);
"""
    src2 = """
length = 25;
cube([length, 1, 1]);
"""
    h1 = content_hash_for_top_level(parse(src1), "cube")
    h2 = content_hash_for_top_level(parse(src2), "cube")
    assert h1 != h2


def test_content_hash_unchanged_when_unrelated_var_changes():
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
    h1 = content_hash_for_top_level(parse(src1), "cube")
    h2 = content_hash_for_top_level(parse(src2), "cube")
    assert h1 == h2


def test_content_hash_changes_when_called_module_body_changes():
    src1 = """
module box() { cube([1, 1, 1]); }
box();
"""
    src2 = """
module box() { cube([2, 2, 2]); }
box();
"""
    h1 = content_hash_for_top_level(parse(src1), "box")
    h2 = content_hash_for_top_level(parse(src2), "box")
    assert h1 != h2


def test_content_hash_unchanged_for_unaffected_call():
    """Two top-level calls; changing a var only used by the second
    should leave the first call's hash untouched."""
    src1 = """
a = 10;
b = 5;
module first() { cube([a, 1, 1]); }
module second() { cube([b, 1, 1]); }
first();
second();
"""
    src2 = """
a = 10;
b = 99;
module first() { cube([a, 1, 1]); }
module second() { cube([b, 1, 1]); }
first();
second();
"""
    p1, p2 = parse(src1), parse(src2)
    assert content_hash_for_top_level(p1, "first") == content_hash_for_top_level(p2, "first")
    assert content_hash_for_top_level(p1, "second") != content_hash_for_top_level(p2, "second")
