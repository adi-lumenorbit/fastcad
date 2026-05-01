"""Tests for model/sections.py — 2D cross-section extraction, PNG
rendering, and programmatic metrics. The metrics tests are written
against synthetic geometry so they assert deterministic numbers, not
"this looks roughly right.\""""
from __future__ import annotations

import math

import manifold3d as m3
import pytest

from fastcad.model.sections import (
    Section,
    extract_axis_section,
    extract_oblique_section,
    canonical_sections,
    render_section_png,
    axial_peak_metrics,
    radial_metrics,
    section_metrics_dict,
)


# ---------------------------------------------------------------------------
# Section dataclass
# ---------------------------------------------------------------------------


def test_section_is_empty_on_no_polygons():
    sec = Section(plane_label="XY", polygons=tuple(), u_axis="x", v_axis="y")
    assert sec.is_empty
    assert sec.bbox_2d == (0.0, 0.0, 0.0, 0.0)
    assert sec.total_area() == 0.0


def test_section_bbox_uses_extreme_points():
    sec = Section(
        plane_label="XY",
        polygons=(((0.0, 0.0), (3.0, 0.0), (3.0, 4.0), (0.0, 4.0)),),
        u_axis="x",
        v_axis="y",
    )
    assert sec.bbox_2d == (0.0, 0.0, 3.0, 4.0)
    # Area = 3 * 4 = 12
    assert sec.total_area() == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# Axis-aligned extraction on simple primitives
# ---------------------------------------------------------------------------


def _cube(size: float = 10.0):
    return m3.Manifold.cube([size, size, size], center=True)


def _cylinder(r: float = 3.0, h: float = 10.0, fn: int = 64):
    return m3.Manifold.cylinder(h, r, r, fn).translate([0, 0, -h / 2])


def test_xy_section_through_cube_returns_square():
    cube = _cube(10)
    sec = extract_axis_section(cube, "XY", 0.0)
    assert not sec.is_empty
    assert sec.plane == "XY"
    umin, vmin, umax, vmax = sec.bbox_2d
    assert umin == pytest.approx(-5.0)
    assert umax == pytest.approx(5.0)
    assert vmin == pytest.approx(-5.0)
    assert vmax == pytest.approx(5.0)
    assert sec.u_axis == "x"
    assert sec.v_axis == "y"


def test_xz_section_through_cube_returns_square():
    cube = _cube(10)
    sec = extract_axis_section(cube, "XZ", 0.0)
    assert not sec.is_empty
    assert sec.plane == "XZ"
    assert sec.u_axis == "x"
    assert sec.v_axis == "z"
    umin, vmin, umax, vmax = sec.bbox_2d
    assert umin == pytest.approx(-5.0)
    assert umax == pytest.approx(5.0)
    assert vmin == pytest.approx(-5.0)
    assert vmax == pytest.approx(5.0)


def test_yz_section_through_cube_returns_square():
    cube = _cube(10)
    sec = extract_axis_section(cube, "YZ", 0.0)
    assert not sec.is_empty
    assert sec.plane == "YZ"
    assert sec.u_axis == "y"
    assert sec.v_axis == "z"


def test_xz_section_through_cylinder_returns_rectangle():
    cyl = _cylinder(r=3.0, h=10.0, fn=64)
    sec = extract_axis_section(cyl, "XZ", 0.0)
    assert not sec.is_empty
    umin, vmin, umax, vmax = sec.bbox_2d
    # Cross-section is roughly 6 mm wide × 10 mm tall.
    assert umin == pytest.approx(-3.0, abs=0.01)
    assert umax == pytest.approx(3.0, abs=0.01)
    assert vmin == pytest.approx(-5.0, abs=0.01)
    assert vmax == pytest.approx(5.0, abs=0.01)


def test_xy_section_at_offset_outside_cube_is_empty():
    cube = _cube(10)
    sec = extract_axis_section(cube, "XY", 100.0)
    assert sec.is_empty


def test_invalid_plane_raises():
    cube = _cube(10)
    with pytest.raises(ValueError):
        extract_axis_section(cube, "ZZZ", 0.0)


# ---------------------------------------------------------------------------
# Oblique extraction
# ---------------------------------------------------------------------------


def test_oblique_section_with_z_normal_matches_xy():
    cube = _cube(10)
    oblique = extract_oblique_section(cube, normal=(0, 0, 1), point=(0, 0, 0))
    assert not oblique.is_empty
    bb = oblique.bbox_2d
    # The cube is 10x10x10 centered, so a horizontal slice is a 10x10 square.
    assert bb[2] - bb[0] == pytest.approx(10.0, abs=0.01)
    assert bb[3] - bb[1] == pytest.approx(10.0, abs=0.01)


def test_oblique_section_diagonal_through_cube():
    cube = _cube(10)
    sec = extract_oblique_section(cube, normal=(1, 0, 1), point=(0, 0, 0))
    assert not sec.is_empty
    # Cross-section through cube on a 45° plane: width 10, height 10*sqrt(2).
    bb = sec.bbox_2d
    width = bb[2] - bb[0]
    height = bb[3] - bb[1]
    assert width == pytest.approx(10.0, abs=0.5)
    assert height == pytest.approx(10.0 * math.sqrt(2), abs=0.5)


def test_oblique_zero_normal_raises():
    cube = _cube(10)
    with pytest.raises(ValueError):
        extract_oblique_section(cube, normal=(0, 0, 0))


# ---------------------------------------------------------------------------
# canonical_sections
# ---------------------------------------------------------------------------


def test_canonical_sections_returns_5_for_axisymmetric():
    cyl = _cylinder(r=3.0, h=10.0, fn=64)
    sections = canonical_sections(cyl)
    # Default fractions are 25%, 50%, 75% → 2 axial + 3 radial = 5
    assert len(sections) == 5
    planes = [s.plane for s in sections]
    assert "XZ" in planes
    assert "YZ" in planes
    assert planes.count("XY") == 3


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_radial_metrics_on_smooth_cylinder_zero_protrusions():
    cyl = _cylinder(r=3.0, h=10.0, fn=64)
    sec = extract_axis_section(cyl, "XY", 0.0)
    rm = radial_metrics(sec)
    assert rm.outer_protrusions == 0
    assert rm.radius_range[1] == pytest.approx(3.0, abs=0.05)


def test_axial_peak_metrics_on_cylinder_no_peaks():
    cyl = _cylinder(r=3.0, h=10.0, fn=64)
    sec = extract_axis_section(cyl, "XZ", 0.0)
    apm = axial_peak_metrics(sec)
    assert apm.peak_count == 0


def test_axial_peak_metrics_catches_paper_thin_thread():
    """Reproduce the M6 paper-thin construction: minor circle plus
    a tiny triangle, twisted. The metric MUST flag the result with
    mean_axial_extent < 0.10 mm."""
    minor, major, pitch, b = 4.77, 6.0, 1.0, 6.0  # short b for fast test
    twist_deg = 360.0 * b / pitch
    slices = max(64, int(twist_deg / 5))
    N = 32
    circle = [
        (minor / 2 * math.cos(2 * math.pi * i / N),
         minor / 2 * math.sin(2 * math.pi * i / N))
        for i in range(N)
    ]
    triangle = [(minor / 2, -pitch / 4), (major / 2, 0.0), (minor / 2, pitch / 4)]
    cs = m3.CrossSection([circle]) + m3.CrossSection([triangle])
    thread = m3.Manifold.extrude(cs, b, slices, twist_deg, (1.0, 1.0))

    sec = extract_axis_section(thread, "XZ", 0.0)
    apm = axial_peak_metrics(sec)
    # Number of peaks: ~b/pitch.
    assert apm.peak_count >= b - 1
    # Paper-thin signature: mean_axial_extent ≪ pitch * 0.4.
    assert apm.mean_axial_extent < 0.10, (
        f"expected paper-thin (mean_extent < 0.10), got {apm.mean_axial_extent:.4f}"
    )


def test_axial_peak_metrics_accepts_correct_thread_construction():
    """A *correct* thread construction (lobed cross-section, where the
    azimuthal r-profile sweeps through the major-minor range) yields
    a thread with axial peak extent in the 0.3–0.95×pitch range — the
    validator's golden case. Synthesised here directly via manifold3d
    to avoid shipping a huge .scad file."""
    minor, major, pitch, b = 4.77, 6.0, 1.0, 4.0
    r_min = minor / 2
    r_max = major / 2
    # Triangle wave azimuth profile: r = r_min at θ=0, r_max at θ=180,
    # r_min at θ=360. When extruded with twist=360°·b/pitch this
    # produces a sawtooth thread with proper axial extent.
    N = 64
    pts: list[tuple[float, float]] = []
    for i in range(N):
        theta_deg = 360.0 * i / N
        t = 1.0 - abs(1.0 - theta_deg / 180.0)  # 0 → 1 → 0
        r = r_min + (r_max - r_min) * t
        pts.append((r * math.cos(math.radians(theta_deg)), r * math.sin(math.radians(theta_deg))))
    cs = m3.CrossSection([pts])
    twist_deg = 360.0 * b / pitch
    slices = max(64, int(twist_deg / 3))   # finer than default for clean profile
    thread = m3.Manifold.extrude(cs, b, slices, twist_deg, (1.0, 1.0))

    sec = extract_axis_section(thread, "XZ", 0.0)
    apm = axial_peak_metrics(sec)
    # ~b/pitch peaks expected.
    assert apm.peak_count >= b - 1
    # Should NOT be paper-thin.
    assert apm.mean_axial_extent > 0.30, (
        f"expected real thread (mean_extent > 0.30 × pitch), "
        f"got {apm.mean_axial_extent:.4f}"
    )
    assert apm.mean_axial_extent < 0.95, (
        f"expected mean_extent < 0.95 × pitch, got {apm.mean_axial_extent:.4f}"
    )


# ---------------------------------------------------------------------------
# PNG rendering
# ---------------------------------------------------------------------------


def test_render_section_png_returns_png_bytes():
    cube = _cube(10)
    sec = extract_axis_section(cube, "XY", 0.0)
    png = render_section_png(sec, size=(256, 256))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # Non-trivial size — actually drew something.
    assert len(png) > 200


def test_render_empty_section_does_not_crash():
    cube = _cube(10)
    sec = extract_axis_section(cube, "XY", 100.0)  # outside bbox
    png = render_section_png(sec, size=(128, 128))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# section_metrics_dict shape
# ---------------------------------------------------------------------------


def test_section_metrics_dict_contains_axial_for_xz():
    cyl = _cylinder(r=3.0, h=10.0, fn=32)
    sec = extract_axis_section(cyl, "XZ", 0.0)
    d = section_metrics_dict(sec)
    assert "axial_peaks" in d
    assert "polygon_count" in d
    assert "bbox_2d" in d


def test_section_metrics_dict_contains_radial_for_xy():
    cyl = _cylinder(r=3.0, h=10.0, fn=32)
    sec = extract_axis_section(cyl, "XY", 0.0)
    d = section_metrics_dict(sec)
    assert "radial" in d
    assert d["radial"]["outer_protrusions"] == 0
