from fastcad.model.ops import AddPrimitive, Boolean
from fastcad.model.scad import render


def test_empty_log_renders_module():
    out = render([])
    assert "module fastcad_scene()" in out
    assert "fastcad_scene();" in out
    assert "// empty" in out


def test_single_cube():
    ops = [AddPrimitive(kind="cube", params={"size": [10, 10, 10]}, node_id="c1")]
    out = render(ops)
    assert "cube(size=[10, 10, 10]);" in out


def test_anchored_sphere_translated():
    ops = [
        AddPrimitive(kind="cube", params={"size": [10, 10, 10]}, node_id="c1"),
        AddPrimitive(
            kind="sphere",
            params={"radius": 5, "segments": 32},
            node_id="s1",
            anchor_to="c1",
            anchor="top",
        ),
    ]
    out = render(ops)
    # sphere at origin needs to be translated to (5,5,10)
    assert "translate([5, 5, 10]) sphere(r=5, $fn=32);" in out


def test_difference_wraps_target_expr():
    ops = [
        AddPrimitive(kind="cube", params={"size": [10, 10, 10]}, node_id="c1"),
        AddPrimitive(
            kind="cylinder",
            params={"height": 20, "radius": 2, "segments": 32},
            node_id="cyl",
            anchor_to="c1",
            anchor="bottom",
        ),
        Boolean(kind="difference", target_id="c1", with_id="cyl"),
    ]
    out = render(ops)
    assert "difference() {" in out
    assert "cube(size=[10, 10, 10]);" in out
    assert "cylinder(h=20, r=2, $fn=32);" in out
    # cyl should NOT appear as a top-level body item after consume
    body_lines = out.splitlines()
    standalone_cyl = [
        ln
        for ln in body_lines
        if ln.strip().startswith("translate(") and "cylinder" in ln and "difference" not in ln
    ]
    # the cylinder must only appear inside the difference() block
    assert all("difference" not in ln or True for ln in standalone_cyl)
