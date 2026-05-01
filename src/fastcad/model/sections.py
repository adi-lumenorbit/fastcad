"""2D cross-section extraction, rendering, and analysis.

Why this module exists: agents reason poorly about 3D geometry from
.scad source alone. They guess what the rendered solid will look like
and frequently get it wrong (helical bands of zero axial thickness,
inverted thread profiles, overcuts that disconnect components). 2D
cross-sections sidestep that blind spot — both vision critics and
programmatic checks can interpret a 2D outline unambiguously.

API surface:

- `extract_axis_section(manifold, plane, offset)`: axis-aligned
  cross-section ("XY"|"XZ"|"YZ"). Returns a `Section` with polygons
  in the canonical 2D coordinate frame for that plane.
- `extract_oblique_section(manifold, normal, point)`: arbitrary
  cross-section through `point` with the given `normal`. The 2D
  result lives in the section plane's local frame.
- `canonical_sections(manifold, *, count=...)`: a fixed inspection
  set for axisymmetric parts (XZ@y=0, YZ@x=0, XY at three z-levels).
  Higher-level "section planner" agents may bypass this and request
  oblique sections directly.
- `render_section_png(section, ...)`: rasterise a Section to PNG bytes
  via Pillow. Used by critics; also persistable for diagnostics.
- `axial_peak_metrics(section)`: programmatic measurements on an
  axial (XZ or YZ) section — peak count, mean axial extent, mean
  flank angle. Replaces brittle peak-azimuth checks.
- `radial_metrics(section)`: outer-protrusion count + radius range
  on a radial (XY) section.

manifold3d's API (verified): `rotate([dx,dy,dz])`, `transform(3x4)`,
`slice(z)`, `project()`.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import manifold3d as _m3
import numpy as np


# ---------------------------------------------------------------------------
# Section value type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """A 2D cross-section through a 3D manifold.

    `polygons` is a list of contour outlines, each a list of (u, v)
    pairs. For axis-aligned sections the (u, v) frame is the obvious
    one (XY → (x, y); XZ → (x, z); YZ → (y, z)). For oblique sections
    (u, v) is the section plane's local 2D frame.
    """

    plane_label: str
    """Human-readable label, e.g. "XZ@y=0" or "oblique(n=(1,1,0))"."""

    polygons: tuple[tuple[tuple[float, float], ...], ...]
    """Contour outlines, immutable."""

    u_axis: str
    """Label for the section's local U axis ("x", "y", "z", or "u")."""

    v_axis: str
    """Label for the section's local V axis."""

    plane: str = "oblique"
    """One of "XY", "XZ", "YZ", or "oblique" (informational)."""

    offset: float = 0.0
    """Plane offset along its normal (only meaningful for axis-aligned)."""

    @property
    def is_empty(self) -> bool:
        return not self.polygons or all(len(p) < 3 for p in self.polygons)

    @property
    def bbox_2d(self) -> tuple[float, float, float, float]:
        """(umin, vmin, umax, vmax). (0,0,0,0) when empty."""
        if self.is_empty:
            return (0.0, 0.0, 0.0, 0.0)
        us = [u for poly in self.polygons for u, _ in poly]
        vs = [v for poly in self.polygons for _, v in poly]
        return (min(us), min(vs), max(us), max(vs))

    def total_area(self) -> float:
        """Sum of signed polygon areas. For a section with one outer
        contour and inner holes, signed-area summation gives the net
        material area."""
        return sum(_polygon_signed_area(p) for p in self.polygons)


def _polygon_signed_area(poly: Sequence[tuple[float, float]]) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        u1, v1 = poly[i]
        u2, v2 = poly[(i + 1) % n]
        s += u1 * v2 - u2 * v1
    return 0.5 * s


# ---------------------------------------------------------------------------
# Extraction — axis-aligned and oblique
# ---------------------------------------------------------------------------


def _polys_from_cs(cs: Any) -> tuple[tuple[tuple[float, float], ...], ...]:
    polys: list[tuple[tuple[float, float], ...]] = []
    for poly in cs.to_polygons():
        polys.append(tuple((float(p[0]), float(p[1])) for p in poly))
    return tuple(polys)


def extract_axis_section(manifold: Any, plane: str, offset: float) -> Section:
    """Axis-aligned cross-section.

    plane="XY" slices at z=offset (returned 2D is (x, y)).
    plane="XZ" slices at y=offset (returned 2D is (x, z)).
    plane="YZ" slices at x=offset (returned 2D is (y, z)).
    """
    p = plane.upper()
    if p == "XY":
        cs = _safe_slice(manifold, offset)
        return Section(
            plane_label=f"XY@z={offset:g}",
            polygons=_polys_from_cs(cs),
            u_axis="x",
            v_axis="y",
            plane="XY",
            offset=float(offset),
        )

    if p == "XZ":
        # Rotate by -90° around X axis: (x,y,z) → (x, z, -y).
        # The original y=offset plane is now at z'=-offset.
        # Slicing the rotated manifold at z'=-offset returns 2D points
        # (x_rot, y_rot) which equal (x_orig, z_orig).
        rotated = manifold.rotate([-90.0, 0.0, 0.0])
        cs = _safe_slice(rotated, -float(offset))
        return Section(
            plane_label=f"XZ@y={offset:g}",
            polygons=_polys_from_cs(cs),
            u_axis="x",
            v_axis="z",
            plane="XZ",
            offset=float(offset),
        )

    if p == "YZ":
        # Rotate by 90° around Y axis: (x,y,z) → (z, y, -x).
        # The original x=offset plane is now at z'=-offset.
        # Slicing returns (x_rot, y_rot) = (z_orig, y_orig). We swap
        # to present (y, z) as the canonical YZ section.
        rotated = manifold.rotate([0.0, 90.0, 0.0])
        cs = _safe_slice(rotated, -float(offset))
        polys = _polys_from_cs(cs)
        polys = tuple(tuple((v, u) for u, v in poly) for poly in polys)
        return Section(
            plane_label=f"YZ@x={offset:g}",
            polygons=polys,
            u_axis="y",
            v_axis="z",
            plane="YZ",
            offset=float(offset),
        )

    raise ValueError(f"unknown plane {plane!r}; expected one of XY/XZ/YZ")


def extract_oblique_section(
    manifold: Any,
    normal: Sequence[float],
    point: Sequence[float] = (0.0, 0.0, 0.0),
    *,
    label: str | None = None,
) -> Section:
    """Cross-section through `point` with the given `normal`.

    Useful for parts whose interesting features don't align with the
    world axes (skewed mounts, oblique threads, mating faces). A
    higher-level section planner can call this with normals it
    derives from the part type.

    The returned Section's (u, v) is the local frame of the section
    plane: the v-axis is the projection of world +Z onto the plane
    (so for a plane that contains Z, v matches z-up); the u-axis
    completes a right-handed 2D frame.
    """
    n = np.asarray(normal, dtype=float)
    n_norm = np.linalg.norm(n)
    if n_norm < 1e-12:
        raise ValueError("normal must be non-zero")
    n = n / n_norm
    p0 = np.asarray(point, dtype=float)

    # Build an orthonormal frame (u, v, n) on the plane. v is the
    # projection of world +Z onto the plane, normalised. If n is
    # parallel to +Z, fall back to world +Y for v.
    z_world = np.array([0.0, 0.0, 1.0])
    v = z_world - np.dot(z_world, n) * n
    if np.linalg.norm(v) < 1e-9:
        v = np.array([0.0, 1.0, 0.0])
        v = v - np.dot(v, n) * n
    v = v / np.linalg.norm(v)
    u = np.cross(v, n)  # right-handed: u × v = n

    # Build a 3x4 affine that sends (u, v, n) basis to (x, y, z) and
    # the plane point to origin. After applying T, the section plane
    # becomes z=0 in the rotated frame.
    R = np.column_stack([u, v, n])  # columns = (u, v, n) in world coords
    R_inv = R.T  # orthonormal — inverse is transpose
    t = -R_inv @ p0
    T = np.column_stack([R_inv, t])  # 3x4

    rotated = manifold.transform(T)
    cs = _safe_slice(rotated, 0.0)
    polys = _polys_from_cs(cs)

    plane_label = label or _format_oblique_label(n, p0)
    return Section(
        plane_label=plane_label,
        polygons=polys,
        u_axis="u",
        v_axis="v",
        plane="oblique",
        offset=0.0,
    )


def _format_oblique_label(n: np.ndarray, p0: np.ndarray) -> str:
    nx, ny, nz = (round(float(c), 3) for c in n)
    px, py, pz = (round(float(c), 3) for c in p0)
    return f"oblique(n=({nx:g},{ny:g},{nz:g}), p=({px:g},{py:g},{pz:g}))"


def _safe_slice(manifold: Any, z: float) -> Any:
    """Manifold.slice with a fallback for older manifold3d versions."""
    try:
        return manifold.slice(float(z))
    except (AttributeError, TypeError):
        slab = _m3.Manifold.cube([1e6, 1e6, 1e-3], center=True).translate([0.0, 0.0, float(z)])
        inter = manifold ^ slab
        return inter.project()


# ---------------------------------------------------------------------------
# Canonical inspection set — for axisymmetric parts
# ---------------------------------------------------------------------------


def canonical_sections(
    manifold: Any,
    *,
    radial_fractions: Sequence[float] = (0.25, 0.50, 0.75),
) -> list[Section]:
    """Fixed canonical inspection set:

    - One axial XZ section through y=0
    - One axial YZ section through x=0
    - Three radial XY sections at z = bbox_zmin + frac × bbox_zheight,
      for each fraction in `radial_fractions`.

    Suitable for axisymmetric parts (fasteners, bosses, simple
    rotational solids). For more complex parts a section planner
    should generate sections via `extract_oblique_section`.
    """
    bb = _bbox(manifold)
    if bb is None:
        return []
    xmin, ymin, zmin, xmax, ymax, zmax = bb
    z_height = zmax - zmin

    out: list[Section] = []
    out.append(extract_axis_section(manifold, "XZ", 0.0))
    out.append(extract_axis_section(manifold, "YZ", 0.0))
    for frac in radial_fractions:
        z = zmin + frac * z_height
        out.append(extract_axis_section(manifold, "XY", z))
    return out


def _bbox(manifold: Any) -> tuple[float, float, float, float, float, float] | None:
    try:
        bb = manifold.bounding_box()
    except AttributeError:
        return None
    if bb is None:
        return None
    # manifold3d returns a numpy array shape (2, 3) or similar; be tolerant.
    arr = np.asarray(bb).reshape(-1)
    if len(arr) < 6:
        return None
    return (float(arr[0]), float(arr[1]), float(arr[2]),
            float(arr[3]), float(arr[4]), float(arr[5]))


# ---------------------------------------------------------------------------
# Rendering — Section → PNG bytes via Pillow
# ---------------------------------------------------------------------------


def render_section_png(
    section: Section,
    *,
    size: tuple[int, int] = (512, 512),
    margin_pct: float = 0.10,
    fill: tuple[int, int, int] = (200, 200, 220),
    outline: tuple[int, int, int] = (20, 20, 30),
    background: tuple[int, int, int] = (255, 255, 255),
    grid: bool = True,
) -> bytes:
    """Rasterise the Section to a PNG. Vision critics consume this.

    Layout: a square (or rectangular) image with the section centred
    and scaled to fill `1 - 2×margin_pct` of the smaller dimension.
    Polygon interiors are filled in `fill`; outlines are drawn in
    `outline`. A faint grid + axes annotate the (u, v) frame so the
    viewer can read coordinates off the rendered image.
    """
    from PIL import Image, ImageDraw, ImageFont  # imported lazily

    w, h = size
    img = Image.new("RGB", (w, h), background)
    draw = ImageDraw.Draw(img)

    if section.is_empty:
        draw.text((10, 10), f"{section.plane_label}\n(empty section)", fill=outline)
        return _pil_to_png_bytes(img)

    umin, vmin, umax, vmax = section.bbox_2d
    u_extent = max(umax - umin, 1e-6)
    v_extent = max(vmax - vmin, 1e-6)

    margin = int(min(w, h) * margin_pct)
    avail_w = w - 2 * margin
    avail_h = h - 2 * margin
    scale = min(avail_w / u_extent, avail_h / v_extent)

    cu = (umin + umax) / 2
    cv = (vmin + vmax) / 2

    def to_px(u: float, v: float) -> tuple[float, float]:
        # Map (u, v) in world to (x, y) in pixels. v inverts because
        # image y grows downward; the user's intuition is v-up.
        x = w / 2 + (u - cu) * scale
        y = h / 2 - (v - cv) * scale
        return (x, y)

    # Faint grid lines at world unit increments (1mm by default).
    if grid:
        unit = _choose_grid_unit(max(u_extent, v_extent))
        grid_color = (235, 235, 240)
        u_start = math.floor(umin / unit) * unit
        u_end = math.ceil(umax / unit) * unit
        v_start = math.floor(vmin / unit) * unit
        v_end = math.ceil(vmax / unit) * unit
        u = u_start
        while u <= u_end + 1e-9:
            x0, y0 = to_px(u, vmin)
            x1, y1 = to_px(u, vmax)
            draw.line([(x0, y0), (x1, y1)], fill=grid_color, width=1)
            u += unit
        v = v_start
        while v <= v_end + 1e-9:
            x0, y0 = to_px(umin, v)
            x1, y1 = to_px(umax, v)
            draw.line([(x0, y0), (x1, y1)], fill=grid_color, width=1)
            v += unit

    # Axes through (0, 0) if visible.
    axes_color = (180, 180, 200)
    if umin <= 0 <= umax:
        x0, y0 = to_px(0.0, vmin)
        x1, y1 = to_px(0.0, vmax)
        draw.line([(x0, y0), (x1, y1)], fill=axes_color, width=1)
    if vmin <= 0 <= vmax:
        x0, y0 = to_px(umin, 0.0)
        x1, y1 = to_px(umax, 0.0)
        draw.line([(x0, y0), (x1, y1)], fill=axes_color, width=1)

    # Polygons. Sort by absolute signed area (largest first) so outer
    # contours render before holes — and use even-odd winding so nested
    # contours auto-cancel when Pillow draws multiple polygons.
    sorted_polys = sorted(
        section.polygons,
        key=lambda p: abs(_polygon_signed_area(p)),
        reverse=True,
    )
    for poly in sorted_polys:
        if len(poly) < 3:
            continue
        pts = [to_px(u, v) for u, v in poly]
        # Use signed area to decide fill: positive = outer (fill); negative = hole (white).
        area = _polygon_signed_area(poly)
        polygon_fill = fill if area >= 0 else background
        draw.polygon(pts, fill=polygon_fill, outline=outline)

    # Header annotation.
    label = (
        f"{section.plane_label}   "
        f"u={section.u_axis} ∈[{umin:.2f},{umax:.2f}]   "
        f"v={section.v_axis} ∈[{vmin:.2f},{vmax:.2f}]"
    )
    draw.text((6, 4), label, fill=outline)

    return _pil_to_png_bytes(img)


def _pil_to_png_bytes(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _choose_grid_unit(extent: float) -> float:
    """Pick a grid spacing so 5–25 lines span the bbox."""
    if extent <= 0:
        return 1.0
    # Aim for 10 grid lines.
    target = extent / 10.0
    pow10 = 10 ** math.floor(math.log10(target))
    for m in (1, 2, 5, 10):
        unit = m * pow10
        if extent / unit <= 25:
            return unit
    return pow10 * 10


# ---------------------------------------------------------------------------
# Programmatic metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AxialPeakMetrics:
    """Measurements taken on an axial (XZ or YZ) section of a
    rotationally-symmetric or thread-bearing part. Catches paper-thin
    threads deterministically (mean_axial_extent ≪ pitch×0.5)."""

    peak_count: int
    mean_axial_extent: float
    min_axial_extent: float
    max_axial_extent: float
    mean_flank_angle_deg: float
    radii_range: tuple[float, float]


def axial_peak_metrics(
    section: Section,
    *,
    axis: str = "v",
    side: str = "right",
) -> AxialPeakMetrics:
    """For an axial section, count peaks of the *outer envelope* along
    the axial direction (default: v-axis, side: right of u=axis_offset).

    Algorithm: project each polygon vertex into (axial, radial) where
    axial = v (the long axis) and radial = u relative to the section's
    radial centroid. Take the max-radius profile binned along axial,
    then count local maxima above (median + tol) of the profile.

    Returns 0 peaks gracefully on degenerate input rather than
    raising — caller decides how to surface that.
    """
    if section.is_empty:
        return AxialPeakMetrics(0, 0.0, 0.0, 0.0, 0.0, (0.0, 0.0))

    pts = [(u, v) for poly in section.polygons for u, v in poly]
    if not pts:
        return AxialPeakMetrics(0, 0.0, 0.0, 0.0, 0.0, (0.0, 0.0))

    cu = sum(u for u, _ in pts) / len(pts)
    if side == "right":
        right = [(u - cu, v) for u, v in pts if u >= cu]
    else:
        right = [(cu - u, v) for u, v in pts if u <= cu]
    if not right:
        return AxialPeakMetrics(0, 0.0, 0.0, 0.0, 0.0, (0.0, 0.0))
    # With sparse data (low-poly inputs) we still report a sensible
    # radii_range, just no peak counts.
    rs_simple = [r for r, _ in right]
    if len(right) < 4:
        return AxialPeakMetrics(0, 0.0, 0.0, 0.0, 0.0, (min(rs_simple), max(rs_simple)))

    vmin = min(v for _, v in right)
    vmax = max(v for _, v in right)
    v_extent = max(vmax - vmin, 1e-9)
    # 50 bins per unit (mm) so we can resolve peaks down to ~0.02 mm
    # axial extent — needed to flag paper-thin threads (~0.03 mm).
    bins = max(128, int(v_extent * 50))
    bin_max = [0.0] * bins
    for r, v in right:
        idx = min(bins - 1, max(0, int((v - vmin) / v_extent * bins)))
        if r > bin_max[idx]:
            bin_max[idx] = r

    populated = [r for r in bin_max if r > 0]
    if not populated:
        return AxialPeakMetrics(0, 0.0, 0.0, 0.0, 0.0, (0.0, 0.0))

    sorted_r = sorted(populated)
    median = sorted_r[len(sorted_r) // 2]
    threshold = median * 1.03

    # Find peak runs — contiguous runs of bins where bin_max > threshold.
    peaks: list[tuple[int, int]] = []  # list of (start_idx, end_idx_exclusive)
    in_peak = False
    start = 0
    for i, r in enumerate(bin_max):
        if r > threshold and not in_peak:
            in_peak = True
            start = i
        elif r <= threshold and in_peak:
            in_peak = False
            peaks.append((start, i))
    if in_peak:
        peaks.append((start, bins))

    if not peaks:
        return AxialPeakMetrics(0, 0.0, 0.0, 0.0, 0.0, (min(populated), max(populated)))

    bin_w = v_extent / bins
    extents = [(end - start) * bin_w for start, end in peaks]
    flank_angles_deg: list[float] = []
    for start, end in peaks:
        # Approximate flank angle: rise/run from base of peak (bin
        # outside the peak) to the tip (max bin within the peak).
        if start <= 0 or end >= bins:
            continue
        base_r = max(bin_max[start - 1], bin_max[end] if end < bins else 0.0)
        tip_r = max(bin_max[i] for i in range(start, end))
        rise = tip_r - base_r
        # Run = half the peak's axial extent (one flank).
        run = max((end - start) * bin_w / 2.0, 1e-9)
        if rise > 0 and run > 0:
            flank_angles_deg.append(math.degrees(math.atan2(rise, run)))

    return AxialPeakMetrics(
        peak_count=len(peaks),
        mean_axial_extent=sum(extents) / len(extents),
        min_axial_extent=min(extents),
        max_axial_extent=max(extents),
        mean_flank_angle_deg=(sum(flank_angles_deg) / len(flank_angles_deg)) if flank_angles_deg else 0.0,
        radii_range=(min(populated), max(populated)),
    )


@dataclass(frozen=True)
class RadialMetrics:
    outer_protrusions: int
    radius_range: tuple[float, float]
    polygon_count: int


def radial_metrics(section: Section) -> RadialMetrics:
    """Same protrusion-count algorithm as the existing
    `_count_outer_protrusions`, applied to a radial section. Reused
    here so the validator and tool agree on the count."""
    if section.is_empty:
        return RadialMetrics(0, (0.0, 0.0), 0)
    pts = [(u, v) for poly in section.polygons for u, v in poly]
    if len(pts) < 6:
        return RadialMetrics(0, (0.0, 0.0), len(section.polygons))
    cu = sum(u for u, _ in pts) / len(pts)
    cv = sum(v for _, v in pts) / len(pts)
    BINS = 72
    bin_max = [0.0] * BINS
    for u, v in pts:
        du = u - cu
        dv = v - cv
        r = math.sqrt(du * du + dv * dv)
        theta = math.atan2(dv, du)
        idx = int((theta + math.pi) / (2 * math.pi) * BINS) % BINS
        if r > bin_max[idx]:
            bin_max[idx] = r
    populated = [r for r in bin_max if r > 0]
    if not populated:
        return RadialMetrics(0, (0.0, 0.0), len(section.polygons))
    sorted_r = sorted(populated)
    median = sorted_r[len(sorted_r) // 2]
    threshold = median * 1.05
    peaks = 0
    for i in range(BINS):
        cur = bin_max[i]
        if cur <= threshold:
            continue
        prev_i = (i - 1) % BINS
        next_i = (i + 1) % BINS
        if cur > bin_max[prev_i] and cur >= bin_max[next_i]:
            peaks += 1
    return RadialMetrics(
        outer_protrusions=peaks,
        radius_range=(min(populated), max(populated)),
        polygon_count=len(section.polygons),
    )


# ---------------------------------------------------------------------------
# Convenience: per-section metrics dict for tool output
# ---------------------------------------------------------------------------


def section_metrics_dict(section: Section) -> dict:
    """JSON-friendly metrics summary for the agent's tool reply.
    Picks axial vs radial measurements based on the section's plane."""
    bb = section.bbox_2d
    out: dict = {
        "plane_label": section.plane_label,
        "u_axis": section.u_axis,
        "v_axis": section.v_axis,
        "polygon_count": len(section.polygons),
        "is_empty": section.is_empty,
        "bbox_2d": {"umin": bb[0], "vmin": bb[1], "umax": bb[2], "vmax": bb[3]},
        "total_area": section.total_area(),
    }
    if section.plane in ("XZ", "YZ"):
        m = axial_peak_metrics(section)
        out["axial_peaks"] = {
            "count": m.peak_count,
            "mean_axial_extent": m.mean_axial_extent,
            "min_axial_extent": m.min_axial_extent,
            "max_axial_extent": m.max_axial_extent,
            "mean_flank_angle_deg": m.mean_flank_angle_deg,
            "radii_range": list(m.radii_range),
        }
    if section.plane == "XY":
        rm = radial_metrics(section)
        out["radial"] = {
            "outer_protrusions": rm.outer_protrusions,
            "radius_range": list(rm.radius_range),
        }
    return out


__all__ = [
    "Section",
    "AxialPeakMetrics",
    "RadialMetrics",
    "extract_axis_section",
    "extract_oblique_section",
    "canonical_sections",
    "render_section_png",
    "axial_peak_metrics",
    "radial_metrics",
    "section_metrics_dict",
]
