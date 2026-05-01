"""Tests for the new kernel primitives backing the .scad-as-spec
evaluator: extrude_polygon, revolve_polygon, polyhedron_from_mesh,
apply_transform."""
from __future__ import annotations

import math

import pytest

from fastcad.model import kernel as k


# ---- extrude --------------------------------------------------------------


def test_extrude_unit_square():
    poly = [[(0, 0), (1, 0), (1, 1), (0, 1)]]
    body = k.extrude_polygon(poly, height=1.0)
    assert k.volume(body) == pytest.approx(1.0, abs=1e-6)


def test_extrude_taller():
    poly = [[(0, 0), (2, 0), (2, 1), (0, 1)]]  # 2x1 rect
    body = k.extrude_polygon(poly, height=5.0)
    assert k.volume(body) == pytest.approx(10.0, abs=1e-6)


def test_extrude_with_twist():
    """Twist preserves cross-sectional area, so volume stays same as
    untwisted extrude (within tessellation tolerance)."""
    poly = [[(-1, -1), (1, -1), (1, 1), (-1, 1)]]
    plain = k.extrude_polygon(poly, height=10.0)
    twisted = k.extrude_polygon(poly, height=10.0, twist_deg=180.0, n_divisions=32)
    assert k.volume(twisted) == pytest.approx(k.volume(plain), rel=0.05)


def test_extrude_with_holes():
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole = [(2, 2), (2, 4), (4, 4), (4, 2)]  # CW hole (rev of CCW)
    body = k.extrude_polygon([outer, hole], height=1.0)
    expected = (10 * 10 - 2 * 2) * 1
    assert k.volume(body) == pytest.approx(expected, rel=0.01)


def test_extrude_zero_height_rejected():
    poly = [[(0, 0), (1, 0), (1, 1), (0, 1)]]
    with pytest.raises(ValueError):
        k.extrude_polygon(poly, height=0.0)


def test_extrude_too_few_vertices_rejected():
    with pytest.raises(ValueError):
        k.extrude_polygon([[(0, 0), (1, 0)]], height=1.0)


# ---- revolve --------------------------------------------------------------


def test_revolve_annulus():
    """Revolving a unit-square at x ∈ [2, 3] gives an annular ring whose
    volume is the disk-area-difference times the square's height,
    scaled by the full revolution.

    For a square (2 ≤ x ≤ 3, 0 ≤ y ≤ 1), revolved 360°:
    volume ≈ π·(3² - 2²)·1 = 5π ≈ 15.71.
    """
    poly = [[(2, 0), (3, 0), (3, 1), (2, 1)]]
    body = k.revolve_polygon(poly, segments=128)
    expected = math.pi * (9 - 4) * 1.0
    assert k.volume(body) == pytest.approx(expected, rel=0.02)


def test_revolve_partial():
    poly = [[(2, 0), (3, 0), (3, 1), (2, 1)]]
    body = k.revolve_polygon(poly, segments=128, revolve_deg=180.0)
    expected = 0.5 * math.pi * (9 - 4) * 1.0
    assert k.volume(body) == pytest.approx(expected, rel=0.02)


# ---- polyhedron -----------------------------------------------------------


def test_polyhedron_tetrahedron():
    verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    # CCW-from-outside winding for each face
    faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    body = k.polyhedron_from_mesh(verts, faces)
    # Tetrahedron volume = 1/6 (corner of unit cube).
    assert k.volume(body) == pytest.approx(1.0 / 6.0, abs=1e-6)


def test_polyhedron_quad_face_fan_triangulates():
    verts = [
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
    ]
    # 6 quad faces of a cube; will be fan-triangulated by polyhedron_from_mesh.
    faces = [
        (0, 3, 2, 1),  # bottom
        (4, 5, 6, 7),  # top
        (0, 1, 5, 4),  # front
        (2, 3, 7, 6),  # back
        (1, 2, 6, 5),  # right
        (0, 4, 7, 3),  # left
    ]
    body = k.polyhedron_from_mesh(verts, faces)
    assert k.volume(body) == pytest.approx(1.0, abs=1e-6)


def test_polyhedron_empty_rejected():
    with pytest.raises(ValueError):
        k.polyhedron_from_mesh([], [])


# ---- apply_transform ------------------------------------------------------


def test_translate_only_shifts_bbox():
    body = k.cube([2, 2, 2])
    moved = k.apply_transform(body, translate_v=(5, 0, 0))
    bb = k.BBox.from_manifold(moved)
    assert bb.xmin == pytest.approx(5.0)
    assert bb.xmax == pytest.approx(7.0)


def test_rotate_swaps_axes():
    body = k.cube([2, 1, 1])  # x-major
    rotated = k.apply_transform(body, rotate_xyz_deg=(0, 0, 90))
    bb = k.BBox.from_manifold(rotated)
    # 90° about Z rotates x-major into y-major; bbox extents swap.
    assert (bb.xmax - bb.xmin) == pytest.approx(1.0, abs=1e-5)
    assert (bb.ymax - bb.ymin) == pytest.approx(2.0, abs=1e-5)


def test_scale_doubles_volume():
    body = k.cube([1, 1, 1])
    scaled = k.apply_transform(body, scale_v=(2, 1, 1))
    assert k.volume(scaled) == pytest.approx(2.0, abs=1e-6)


def test_mirror_axis_x_flips():
    body = k.cube([1, 1, 1])  # corner at origin
    mirrored = k.apply_transform(body, mirror_axis="x")
    bb = k.BBox.from_manifold(mirrored)
    assert bb.xmin == pytest.approx(-1.0)
    assert bb.xmax == pytest.approx(0.0)


def test_mirror_invalid_axis_rejected():
    body = k.cube([1, 1, 1])
    with pytest.raises(ValueError):
        k.apply_transform(body, mirror_axis="diagonal")


def test_compose_translate_and_rotate():
    body = k.cube([2, 1, 1])  # corner at origin: covers x∈[0,2], y∈[0,1]
    out = k.apply_transform(body, rotate_xyz_deg=(0, 0, 90), translate_v=(10, 0, 0))
    bb = k.BBox.from_manifold(out)
    # 90° about Z (world origin): the y=1 face moves to x=-1; +X face → +Y.
    # Then +10 in x: xmin=9, xmax=10, ymin=0, ymax=2.
    assert bb.xmin == pytest.approx(9.0, abs=1e-5)
    assert bb.xmax == pytest.approx(10.0, abs=1e-5)
    assert (bb.ymax - bb.ymin) == pytest.approx(2.0, abs=1e-5)


def test_apply_transform_no_args_returns_input():
    body = k.cube([1, 1, 1])
    out = k.apply_transform(body)
    assert k.volume(out) == pytest.approx(k.volume(body))


# ---- intersection ---------------------------------------------------------


def test_intersection_overlap():
    a = k.cube([2, 2, 2])  # corner at origin → covers [0,2]^3
    b = k.translate(k.cube([2, 2, 2]), [1, 1, 1])  # covers [1,3]^3
    inter = k.intersection(a, b)
    assert k.volume(inter) == pytest.approx(1.0, abs=1e-5)
