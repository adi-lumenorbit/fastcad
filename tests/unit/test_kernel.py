import base64
import math

import numpy as np
import pytest

from fastcad.model import kernel as k


def test_cube_volume():
    c = k.cube([10, 10, 10])
    assert k.volume(c) == pytest.approx(1000.0)


def test_cube_rejects_zero():
    with pytest.raises(ValueError):
        k.cube([0, 1, 1])
    with pytest.raises(ValueError):
        k.cube([-1, 1, 1])


def test_sphere_volume_within_tolerance():
    s = k.sphere(5, segments=128)
    expected = (4.0 / 3.0) * math.pi * 5**3
    assert k.volume(s) == pytest.approx(expected, rel=0.02)


def test_cylinder_volume_within_tolerance():
    c = k.cylinder(20, 5, segments=128)
    expected = math.pi * 5**2 * 20
    assert k.volume(c) == pytest.approx(expected, rel=0.02)


def test_translate_shifts_bbox():
    c = k.cube([10, 10, 10])
    moved = k.translate(c, [5, 0, 0])
    bb = k.BBox.from_manifold(moved)
    assert bb.xmin == pytest.approx(5.0)
    assert bb.xmax == pytest.approx(15.0)
    assert bb.ymin == pytest.approx(0.0)


def test_union_volume_increases_then_caps():
    c = k.cube([10, 10, 10])
    s = k.sphere(5, segments=64)  # at origin, half overlaps the cube corner
    u = k.union(c, s)
    assert k.volume(u) > k.volume(c)
    assert k.volume(u) < k.volume(c) + k.volume(s) + 1e-6


def test_difference_subtracts_volume():
    c = k.cube([10, 10, 10])
    s = k.sphere(5, segments=64)
    d = k.difference(c, s)
    assert k.volume(d) < k.volume(c)


def test_bbox_center_and_size():
    c = k.cube([10, 20, 30])
    bb = k.BBox.from_manifold(c)
    assert bb.center == pytest.approx((5.0, 10.0, 15.0))
    assert bb.size == pytest.approx((10.0, 20.0, 30.0))


def test_to_mesh_dict_roundtrip_shape():
    c = k.cube([10, 10, 10])
    d = k.to_mesh_dict(c)
    pos = np.frombuffer(base64.b64decode(d["positions_b64"]), dtype=np.float32).reshape(-1, 3)
    idx = np.frombuffer(base64.b64decode(d["indices_b64"]), dtype=np.uint32).reshape(-1, 3)
    assert pos.shape[0] == d["vertex_count"]
    assert idx.shape[0] == d["triangle_count"]
    # cube has 8 verts and 12 tris (manifold3d uses welded verts)
    assert pos.shape[0] == 8
    assert idx.shape[0] == 12
    # all indices in range
    assert idx.max() < pos.shape[0]
