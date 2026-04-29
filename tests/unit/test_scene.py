import pytest

from fastcad.model import kernel as k
from fastcad.model.ops import AddPrimitive, Boolean
from fastcad.model.scene import SceneGraph, resolve_anchor


def test_add_primitive_creates_node():
    sg = SceneGraph()
    cs = sg.apply(AddPrimitive(kind="cube", params={"size": [10, 10, 10]}, node_id="c1"))
    assert cs.added == ["c1"]
    assert "c1" in sg.nodes
    assert k.volume(sg.nodes["c1"].manifold) == pytest.approx(1000.0)


def test_duplicate_id_rejected():
    sg = SceneGraph()
    sg.apply(AddPrimitive(kind="cube", params={"size": [1, 1, 1]}, node_id="c1"))
    with pytest.raises(ValueError):
        sg.apply(AddPrimitive(kind="cube", params={"size": [1, 1, 1]}, node_id="c1"))


def test_anchor_top_places_sphere_centered_above_cube():
    sg = SceneGraph()
    sg.apply(AddPrimitive(kind="cube", params={"size": [10, 10, 10]}, node_id="c1"))
    sg.apply(
        AddPrimitive(
            kind="sphere",
            params={"radius": 5, "segments": 64},
            node_id="s1",
            anchor_to="c1",
            anchor="top",
        )
    )
    bb = k.BBox.from_manifold(sg.nodes["s1"].manifold)
    cx, cy, cz = bb.center
    assert cx == pytest.approx(5.0)
    assert cy == pytest.approx(5.0)
    assert cz == pytest.approx(10.0)  # sphere center sits on top face of cube


def test_anchor_unknown_target_errors():
    sg = SceneGraph()
    with pytest.raises(ValueError):
        sg.apply(
            AddPrimitive(
                kind="cube",
                params={"size": [1, 1, 1]},
                node_id="x",
                anchor_to="nope",
                anchor="top",
            )
        )


def test_resolve_anchor_named_points():
    sg = SceneGraph()
    sg.apply(AddPrimitive(kind="cube", params={"size": [10, 20, 30]}, node_id="c1"))
    n = sg.nodes["c1"]
    assert resolve_anchor(n, "center") == pytest.approx((5.0, 10.0, 15.0))
    assert resolve_anchor(n, "top") == pytest.approx((5.0, 10.0, 30.0))
    assert resolve_anchor(n, "bottom") == pytest.approx((5.0, 10.0, 0.0))


def test_boolean_difference_replaces_target_and_consumes_with():
    sg = SceneGraph()
    sg.apply(AddPrimitive(kind="cube", params={"size": [10, 10, 10]}, node_id="c1"))
    sg.apply(
        AddPrimitive(
            kind="cylinder",
            params={"height": 20, "radius": 2, "segments": 64},
            node_id="cyl",
            anchor_to="c1",
            anchor="bottom",
        )
    )
    vol_before = k.volume(sg.nodes["c1"].manifold)
    cs = sg.apply(Boolean(kind="difference", target_id="c1", with_id="cyl"))
    assert "c1" in cs.updated
    assert "cyl" in cs.removed
    assert "cyl" not in sg.nodes
    assert k.volume(sg.nodes["c1"].manifold) < vol_before


def test_describe_for_agent_includes_bbox_and_center():
    sg = SceneGraph()
    sg.apply(AddPrimitive(kind="cube", params={"size": [10, 10, 10]}, node_id="c1"))
    desc = sg.describe_for_agent()
    assert len(desc) == 1
    assert desc[0]["id"] == "c1"
    assert desc[0]["center"] == pytest.approx([5.0, 5.0, 5.0])
    assert desc[0]["bbox"]["max"] == pytest.approx([10.0, 10.0, 10.0])
