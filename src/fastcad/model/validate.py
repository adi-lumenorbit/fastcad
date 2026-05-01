"""Channel 1 — symbolic / structural design validator.

After `set_source` succeeds (manifold built), this module verifies the
geometry against the user-referenced cache entry's `## Acceptance`
schema. Defects flow back to the agent as tool errors so it can self-
correct in the same turn.

The validator is **deterministic and free**: pure functions over the
manifold + AST, no API calls, no rendering. Trade-off: it can only
catch what the schema names. The vision-based critic (Channel 2) fills
the remaining blind spot.

The bug class this catches: silent semantic drifts where the system
builds geometry that nominally evaluates but doesn't match the spec.
The Stage-1 12-start-thread bug, the Stage-2 ribbon-thread bug, and
the "head looks like sphere" rendering bug would all have failed at
least one check here.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import manifold3d as _m3

from . import kernel as k
from .scad_eval import ModuleEval
from .scad_parser import ModuleDef, Source


# ---------------------------------------------------------------------------
# Defect type — what we surface back to the agent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Defect:
    severity: str  # "error" | "warning"
    where: str  # e.g. "bbox_z_extent" or "horizontal_slices_at_z[0]"
    expected: str
    actual: str
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "where": self.where,
            "expected": self.expected,
            "actual": self.actual,
            "hint": self.hint,
        }


# ---------------------------------------------------------------------------
# Schema parser — extract the `## Acceptance` JSON block from a cache file
# ---------------------------------------------------------------------------


class AcceptanceSchemaError(Exception):
    """Raised when the cache file's Acceptance section is missing or
    malformed. The validator treats this as 'no schema, no checks' and
    skips Channel 1; the cache file gets fixed on the next research."""


_ACCEPTANCE_HEADING = re.compile(r"^##\s+Acceptance\s*$", re.MULTILINE)


def parse_acceptance_schema(cache_md: str) -> dict | None:
    """Pull the JSON block from the `## Acceptance` section. Returns
    None if the section is absent (older cache entries pre-Stage 3).
    Raises `AcceptanceSchemaError` if the section exists but is
    malformed."""
    m = _ACCEPTANCE_HEADING.search(cache_md)
    if not m:
        return None
    tail = cache_md[m.end():]
    # Stop at the next ## heading (or end of file).
    next_h2 = re.search(r"^##\s+", tail, re.MULTILINE)
    section = tail[: next_h2.start()] if next_h2 else tail

    # Find the first fenced JSON block: ```json ... ```
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", section, re.DOTALL)
    if not fence:
        raise AcceptanceSchemaError(
            "Acceptance section present but no JSON code block found. "
            "Expected ```json ... ``` immediately under `## Acceptance`."
        )
    body = fence.group(1).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise AcceptanceSchemaError(f"Acceptance JSON malformed: {exc}") from None


# ---------------------------------------------------------------------------
# Top-level entry — validate cache schema + AST + manifolds
# ---------------------------------------------------------------------------


def validate_against_cache(
    spec_source_ast: Source,
    cache_md: str,
    eval_cache: dict[str, ModuleEval],
) -> list[Defect]:
    """Run every Channel-1 check against the (parsed) source + the
    eval cache (id → ModuleEval). Returns a list of Defect; empty list
    means the geometry passes."""
    schema = parse_acceptance_schema(cache_md)
    if schema is None:
        # No schema → no checks possible. Not a defect; treat the
        # cache entry as opting out of automatic validation.
        return []

    defects: list[Defect] = []
    primary = _select_primary_node(eval_cache)

    # bbox / volume / components / slices apply to the primary
    # manifold (the largest 3D node). expected_modules is an AST-only
    # check.
    if primary is not None:
        defects.extend(_check_bbox(primary, schema))
        defects.extend(_check_volume(primary, schema))
        defects.extend(_check_connected_components(primary, schema))
        defects.extend(_check_horizontal_slices(primary, schema))
        defects.extend(_check_axial_consistency(primary, schema))
        defects.extend(_check_axial_section(primary, schema))
    elif eval_cache:
        # cache had nodes but none were 3D — treat as "primary missing"
        defects.append(
            Defect(
                severity="error",
                where="primary_manifold",
                expected="at least one 3D node in the scene",
                actual="cache had only 2D / empty nodes",
                hint="The agent may have produced top-level 2D primitives. "
                     "Wrap geometry in a 3D module and call it at the bottom.",
            )
        )

    defects.extend(_check_expected_modules(spec_source_ast, schema))
    return defects


def _select_primary_node(eval_cache: dict[str, ModuleEval]) -> ModuleEval | None:
    """The 'main' geometry to validate against. Pick the largest by
    volume — works for the typical fastcad pattern where the agent
    wraps everything in one named module called at the bottom."""
    candidates = [me for me in eval_cache.values() if me.manifold is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda me: float(me.manifold.volume()))


# ---------------------------------------------------------------------------
# Per-check implementations
# ---------------------------------------------------------------------------


def _check_bbox(primary: ModuleEval, schema: dict) -> list[Defect]:
    out: list[Defect] = []
    bb = primary.bbox
    if bb is None:
        return out
    x_extent = bb.xmax - bb.xmin
    y_extent = bb.ymax - bb.ymin
    z_extent = bb.zmax - bb.zmin
    xy_max = max(x_extent, y_extent)

    if "bbox_z_extent" in schema:
        out.extend(_check_range("bbox_z_extent", z_extent, schema["bbox_z_extent"], "mm"))
    if "bbox_xy_max" in schema:
        out.extend(_check_range("bbox_xy_max", xy_max, schema["bbox_xy_max"], "mm"))

    # Rotational-symmetry check: x and y extents must be within 5% for
    # parts that are nominally axisymmetric around Z (fasteners,
    # cylindrical bosses). Catches accidental scale() in one axis,
    # non-circular head primitives, and similar.
    if schema.get("bbox_xy_symmetric"):
        smaller = max(min(x_extent, y_extent), 1e-9)
        ratio = max(x_extent, y_extent) / smaller
        if ratio > 1.05:
            out.append(
                Defect(
                    severity="error",
                    where="bbox_xy_symmetric",
                    expected="x_extent ≈ y_extent (within 5%)",
                    actual=f"x={x_extent:.3f} mm, y={y_extent:.3f} mm, ratio={ratio:.2f}",
                    hint=(
                        "The geometry is not rotationally symmetric in the XY "
                        "plane. For a fastener axisymmetric around Z, x and y "
                        "extents should be equal. Check for accidental "
                        "scale([sx, sy, ...]) with sx ≠ sy, an elliptical or "
                        "non-circular head primitive, or a translated copy "
                        "that breaks symmetry."
                    ),
                )
            )

    return out


def _check_volume(primary: ModuleEval, schema: dict) -> list[Defect]:
    if "volume_range" not in schema:
        return []
    vol = float(primary.manifold.volume())
    return _check_range("volume_range", vol, schema["volume_range"], "mm³")


def _check_range(where: str, actual: float, spec: Any, unit: str = "") -> list[Defect]:
    if not (isinstance(spec, (list, tuple)) and len(spec) == 2):
        return [
            Defect(
                severity="error",
                where=where,
                expected=f"[min, max] pair",
                actual=f"{spec!r}",
                hint="Fix the cache's Acceptance schema; this isn't a range.",
            )
        ]
    lo, hi = float(spec[0]), float(spec[1])
    if actual < lo or actual > hi:
        return [
            Defect(
                severity="error",
                where=where,
                expected=f"[{lo}, {hi}] {unit}".strip(),
                actual=f"{actual:.3f} {unit}".strip(),
                hint=_range_hint(where, actual, lo, hi),
            )
        ]
    return []


def _range_hint(where: str, actual: float, lo: float, hi: float) -> str:
    direction = "smaller" if actual > hi else "larger"
    return f"The geometry is {direction} than the spec allows. " \
           f"Adjust parameters so {where} lands in [{lo}, {hi}]."


def _check_connected_components(primary: ModuleEval, schema: dict) -> list[Defect]:
    if "connected_components" not in schema:
        return []
    expected = int(schema["connected_components"])
    components = primary.manifold.decompose()
    actual = len(components)
    if actual != expected:
        return [
            Defect(
                severity="error",
                where="connected_components",
                expected=str(expected),
                actual=str(actual),
                hint="The geometry is split into multiple disconnected pieces. "
                     "Make sure all sub-modules are union'd together in the "
                     "main module.",
            )
        ]
    return []


def _check_expected_modules(src: Source, schema: dict) -> list[Defect]:
    """Each expected_modules entry is a regex; at least one defined
    module's name must match. Catches 'agent forgot the head'."""
    fragments = schema.get("expected_modules") or []
    if not fragments:
        return []
    defined_names = [s.name for s in src.stmts if isinstance(s, ModuleDef)]
    out: list[Defect] = []
    for frag in fragments:
        try:
            pattern = re.compile(frag)
        except re.error:
            out.append(
                Defect(
                    severity="warning",
                    where=f"expected_modules: {frag}",
                    expected="valid regex",
                    actual=frag,
                    hint="Acceptance schema has a malformed regex.",
                )
            )
            continue
        if not any(pattern.search(n) for n in defined_names):
            out.append(
                Defect(
                    severity="error",
                    where=f"expected_modules: {frag}",
                    expected=f"at least one module name matching /{frag}/",
                    actual=f"defined modules: {defined_names}",
                    hint=f"Add a module whose name matches /{frag}/ — the "
                         f"agent may have skipped this feature.",
                )
            )
    return out


def _check_horizontal_slices(primary: ModuleEval, schema: dict) -> list[Defect]:
    """For each slice spec, intersect the manifold with a thin
    horizontal slab and analyse the resulting 2D cross-section. The
    main check is `outer_protrusions` — counts radial peaks, which
    detects multi-start thread bugs (where N > 1 means N starts when
    only 1 was wanted)."""
    slices = schema.get("horizontal_slices_at_z") or []
    if not slices:
        return []
    bb = primary.bbox
    out: list[Defect] = []
    for i, spec in enumerate(slices):
        if not isinstance(spec, dict):
            out.append(
                Defect(
                    severity="warning",
                    where=f"horizontal_slices_at_z[{i}]",
                    expected="object with 'z' and check fields",
                    actual=str(spec),
                    hint="Each slice spec must be a JSON object.",
                )
            )
            continue
        z = float(spec["z"])
        if not (bb.zmin - 1e-3 <= z <= bb.zmax + 1e-3):
            out.append(
                Defect(
                    severity="warning",
                    where=f"horizontal_slices_at_z[{i}]",
                    expected=f"z within bbox z=[{bb.zmin:.2f}, {bb.zmax:.2f}]",
                    actual=f"z={z}",
                    hint="The slice is outside the model's z-extent; either "
                         "the model is wrong or the schema z value is.",
                )
            )
            continue

        cs = _slice_at_z(primary.manifold, z)
        if cs is None or cs.is_empty():
            out.append(
                Defect(
                    severity="error",
                    where=f"horizontal_slices_at_z[{i}]",
                    expected=f"non-empty cross-section at z={z}",
                    actual="empty",
                    hint="The model has a hole at this z. Likely a gap "
                         "between sub-modules along the axis.",
                )
            )
            continue

        if "outer_protrusions" in spec:
            expected_n = int(spec["outer_protrusions"])
            actual_n = _count_outer_protrusions(cs)
            if actual_n != expected_n:
                out.append(
                    Defect(
                        severity="error",
                        where=f"horizontal_slices_at_z[{i}].outer_protrusions",
                        expected=str(expected_n),
                        actual=str(actual_n),
                        hint=_protrusion_hint(expected_n, actual_n),
                    )
                )

        if "radius_range" in spec:
            r_min, r_max = _cross_section_radius_range(cs)
            range_spec = spec["radius_range"]
            if (isinstance(range_spec, (list, tuple)) and len(range_spec) == 2):
                lo, hi = float(range_spec[0]), float(range_spec[1])
                # The actual radius range should overlap the spec range.
                if r_max < lo or r_min > hi:
                    out.append(
                        Defect(
                            severity="error",
                            where=f"horizontal_slices_at_z[{i}].radius_range",
                            expected=f"[{lo}, {hi}] mm",
                            actual=f"[{r_min:.3f}, {r_max:.3f}] mm",
                            hint="The cross-section's radial range falls "
                                 "outside the spec at this z.",
                        )
                    )
    return out


def _protrusion_hint(expected: int, actual: int) -> str:
    if expected == 1 and actual > 1:
        return (
            f"The cross-section has {actual} radial peaks; this is an "
            f"{actual}-start thread (or N-fold symmetric protrusion) "
            f"when {expected}-start was expected. For ISO threads use "
            f"a single tooth in the cross-section."
        )
    if expected == 1 and actual == 0:
        return (
            "The cross-section is smooth — no thread protrusion at all. "
            "The thread sweep may be misconfigured (e.g. linear_extrude "
            "twist resolution too low, or the tooth polygon is degenerate)."
        )
    return f"Expected {expected} radial protrusions; observed {actual}."


def _slice_at_z(manifold: Any, z: float) -> Any:
    """Return a CrossSection at the given z, using manifold3d's
    `slice` method. We intersect with a thin slab and project to be
    robust against versions of manifold3d that lack a single-z slice."""
    try:
        return manifold.slice(z)
    except (AttributeError, TypeError):
        # Fallback: intersect with a thin slab and project.
        try:
            slab = _m3.Manifold.cube([1e6, 1e6, 1e-3], center=True).translate([0, 0, z])
            inter = manifold ^ slab
            return inter.project()
        except Exception:  # noqa: BLE001
            return None


def _count_outer_protrusions(cs: Any, threshold_factor: float = 1.05) -> int:
    """Count radial peaks on the cross-section. We pool ALL vertices
    from every contour into one (theta, r) cloud (relative to the
    cloud's centroid), bin into 72 angular bins (5° each), take max
    r per bin, and count peaks above `median * threshold_factor`.

    Pooling matters: manifold3d sometimes returns the body and a
    bump as separate contours that touch but aren't merged (the
    slice tessellation didn't unify them). If we picked the largest
    polygon we'd miss the bump entirely — exactly the bug the unit
    tests caught."""
    polys = list(cs.to_polygons())
    if not polys:
        return 0

    pts: list[tuple[float, float]] = []
    for poly in polys:
        for p in poly:
            pts.append((float(p[0]), float(p[1])))
    if len(pts) < 6:
        return 0

    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)

    BINS = 72
    bin_max = [0.0] * BINS
    for x, y in pts:
        dx = x - cx
        dy = y - cy
        r = math.sqrt(dx * dx + dy * dy)
        theta = math.atan2(dy, dx)  # [-pi, pi]
        idx = int((theta + math.pi) / (2 * math.pi) * BINS) % BINS
        if r > bin_max[idx]:
            bin_max[idx] = r

    populated = [r for r in bin_max if r > 0]
    if not populated:
        return 0

    sorted_r = sorted(populated)
    median = sorted_r[len(sorted_r) // 2]
    threshold = median * threshold_factor

    # Count cyclic peaks: bin_max[i] strictly greater than both
    # cyclic neighbours and above threshold.
    peaks = 0
    for i in range(BINS):
        cur = bin_max[i]
        if cur <= threshold:
            continue
        prev_i = (i - 1) % BINS
        next_i = (i + 1) % BINS
        prev_r = bin_max[prev_i]
        next_r = bin_max[next_i]
        if cur > prev_r and cur >= next_r:
            peaks += 1
    return peaks


def _peak_azimuth(cs: Any) -> float | None:
    """Azimuth (radians, [-π, π]) of the largest radial peak across
    all contour vertices in the cross-section. Used by axial-
    consistency to detect helix-vs-stacked-rings."""
    polys = list(cs.to_polygons())
    if not polys:
        return None
    pts: list[tuple[float, float]] = []
    for poly in polys:
        for p in poly:
            pts.append((float(p[0]), float(p[1])))
    if not pts:
        return None
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    max_r = -1.0
    max_theta = 0.0
    for x, y in pts:
        dx = x - cx
        dy = y - cy
        r = math.sqrt(dx * dx + dy * dy)
        if r > max_r:
            max_r = r
            max_theta = math.atan2(dy, dx)
    return max_theta


def _check_axial_consistency(primary: ModuleEval, schema: dict) -> list[Defect]:
    """Catches the bug class Channel-1's per-slice protrusion check
    can't see: geometry that looks single-start at every individual
    slice, but where the protrusion is at the SAME azimuth at every
    z. That's stacked rings, not a helix.

    Trigger when `axial_consistency: "helical"` is set in the schema.
    Take the peak azimuth at each `horizontal_slices_at_z` slice;
    if a strong majority of pairs are within ±10° of each other,
    flag stacked-rings.

    **Slice-z trap fix**: when `pitch` is provided in the schema, this
    function perturbs each slice z by a *different* irrational fraction
    of pitch so consecutive sample points don't land at the same
    azimuth. Without this, slice z's at uniform spacing always give
    the same peak azimuth (Δz integer-multiple of pitch ⇒ azimuth
    advances by a full turn back to the start) — false-positive
    flagging valid threads.
    """
    mode = schema.get("axial_consistency")
    if mode != "helical":
        return []
    slices = schema.get("horizontal_slices_at_z") or []
    if len(slices) < 2:
        return []
    pitch = schema.get("pitch")
    # Per-slice offsets, fractions of pitch. Each is chosen so that
    # the resulting Δz between any two slices is NOT an integer
    # multiple of pitch — so the resulting azimuths spread instead
    # of stacking. Cycles through this list if there are more slices
    # than entries.
    offset_fractions = (0.27, 0.61, 0.83, 0.13, 0.41, 0.73)
    azimuths: list[float] = []
    for i, spec in enumerate(slices):
        if not isinstance(spec, dict) or "z" not in spec:
            continue
        offset = (offset_fractions[i % len(offset_fractions)] * float(pitch)
                  if pitch is not None else 0.0)
        z = float(spec["z"]) + offset
        cs = _slice_at_z(primary.manifold, z)
        if cs is None or cs.is_empty():
            continue
        az = _peak_azimuth(cs)
        if az is not None:
            azimuths.append(az)
    if len(azimuths) < 2:
        return []

    near_match_pairs = 0
    total_pairs = 0
    for i in range(len(azimuths)):
        for j in range(i + 1, len(azimuths)):
            total_pairs += 1
            diff = abs(azimuths[j] - azimuths[i])
            diff = min(diff, 2 * math.pi - diff)  # cyclic distance
            if diff < math.radians(10):
                near_match_pairs += 1

    # If >70% of pairs are at near-identical azimuths, every slice's
    # peak is in the same place — stacked rings, not a helix.
    if total_pairs > 0 and near_match_pairs / total_pairs > 0.7:
        sample = ", ".join(f"{math.degrees(a):.0f}°" for a in azimuths)
        return [
            Defect(
                severity="error",
                where="axial_consistency",
                expected="helical sweep (peak azimuths rotate across slices)",
                actual=f"stacked-ring pattern (peak azimuths: {sample})",
                hint=(
                    "Every horizontal slice has its outer protrusion at the "
                    "same azimuth. That's the signature of stacked translate+"
                    "rotate clones, OR linear_extrude(twist=0) on a non-"
                    "symmetric cross-section, NOT a true helical thread. "
                    "Use a single linear_extrude with non-zero `twist` and "
                    "enough `slices` (≥ |twist|/5) to produce a continuous "
                    "helix."
                ),
            )
        ]
    return []


def _check_axial_section(primary: ModuleEval, schema: dict) -> list[Defect]:
    """Programmatic checks on an axial cross-section (XZ or YZ).

    Schema fragment:

        "axial_section": {
          "plane": "XZ"|"YZ",            # default "XZ"
          "offset": 0.0,                  # default 0.0
          "peak_count": [min, max],
          "peak_axial_extent": [min, max],         # absolute mm range
          "peak_axial_extent_pct_of_pitch": [...], # alt — multiplied by `pitch`
          "pitch": 1.0,                   # required if using *_pct_of_pitch
          "flank_angle_deg": [min, max]
        }

    This is the *deterministic killer of paper-thin thread bugs*:
    `peak_axial_extent` ≪ `pitch×0.4` triggers regardless of how the
    thread was constructed. Replaces `axial_consistency`'s brittle
    peak-azimuth-rotation check (which fails for valid threads
    sampled at integer-pitch z values) with a measurement that
    actually quantifies the thread profile.
    """
    spec = schema.get("axial_section")
    if not isinstance(spec, dict):
        return []
    # Lazy import to avoid a circular dep — sections.py imports manifold3d
    # but not validate.py.
    from . import sections as _sections

    plane = str(spec.get("plane", "XZ")).upper()
    if plane not in ("XZ", "YZ"):
        return [
            Defect(
                severity="warning",
                where="axial_section.plane",
                expected="XZ or YZ",
                actual=str(plane),
                hint="Schema's axial_section.plane must be XZ or YZ.",
            )
        ]
    offset = float(spec.get("offset", 0.0))

    try:
        section = _sections.extract_axis_section(primary.manifold, plane, offset)
    except Exception as exc:  # noqa: BLE001
        return [
            Defect(
                severity="error",
                where="axial_section",
                expected="non-empty section",
                actual=f"extraction failed: {type(exc).__name__}: {exc}",
                hint="Internal: section extraction crashed. Check the manifold is valid.",
            )
        ]
    if section.is_empty:
        return [
            Defect(
                severity="error",
                where="axial_section",
                expected=f"non-empty section through {plane}@offset={offset}",
                actual="empty",
                hint="The section plane misses the geometry — check that the offset is inside the bbox.",
            )
        ]

    metrics = _sections.axial_peak_metrics(section)
    out: list[Defect] = []

    if "peak_count" in spec:
        out.extend(_check_range(
            "axial_section.peak_count",
            float(metrics.peak_count),
            spec["peak_count"],
            "peaks",
        ))

    extent_spec = None
    if "peak_axial_extent" in spec:
        extent_spec = spec["peak_axial_extent"]
        unit = "mm"
    elif "peak_axial_extent_pct_of_pitch" in spec:
        if "pitch" not in spec:
            out.append(
                Defect(
                    severity="warning",
                    where="axial_section.peak_axial_extent_pct_of_pitch",
                    expected="`pitch` field present alongside",
                    actual="missing `pitch`",
                    hint="Schema must include `pitch` when using peak_axial_extent_pct_of_pitch.",
                )
            )
        else:
            pitch = float(spec["pitch"])
            raw = spec["peak_axial_extent_pct_of_pitch"]
            if isinstance(raw, (list, tuple)) and len(raw) == 2:
                extent_spec = [float(raw[0]) * pitch, float(raw[1]) * pitch]
                unit = "mm"
    if extent_spec is not None:
        out.extend(_check_range(
            "axial_section.peak_axial_extent",
            metrics.mean_axial_extent,
            extent_spec,
            unit,
        ))
        # Add a tailored hint when the failure is the paper-thin signature.
        if (
            isinstance(extent_spec, (list, tuple))
            and len(extent_spec) == 2
            and metrics.mean_axial_extent < float(extent_spec[0])
        ):
            out.append(
                Defect(
                    severity="error",
                    where="axial_section.thread_profile",
                    expected="thread teeth with non-trivial axial extent",
                    actual=(
                        f"mean_axial_extent={metrics.mean_axial_extent:.3f} mm "
                        f"(peak_count={metrics.peak_count}, max_extent={metrics.max_axial_extent:.3f} mm)"
                    ),
                    hint=(
                        "The thread teeth are paper-thin in axial section. "
                        "linear_extrude(twist=) maps the cross-section's "
                        "azimuthal coverage to axial tooth thickness — a "
                        "narrow triangle (y-extent ≈ pitch/2) gives ~12° "
                        "azimuthal coverage and ~0.03 mm axial extent. Use a "
                        "polyhedron-based thread (helically-positioned "
                        "vertices) or a sector cross-section spanning enough "
                        "azimuth to give the desired axial tooth height. "
                        "Adding more `slices` will NOT fix this."
                    ),
                )
            )

    if "flank_angle_deg" in spec:
        out.extend(_check_range(
            "axial_section.flank_angle_deg",
            metrics.mean_flank_angle_deg,
            spec["flank_angle_deg"],
            "°",
        ))

    return out


def _cross_section_radius_range(cs: Any) -> tuple[float, float]:
    """(r_min, r_max) of the cross-section relative to its centroid."""
    polys = list(cs.to_polygons())
    if not polys:
        return (0.0, 0.0)
    pts: list[tuple[float, float]] = []
    for poly in polys:
        for p in poly:
            pts.append((float(p[0]), float(p[1])))
    if not pts:
        return (0.0, 0.0)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    rs = [math.sqrt((x - cx) ** 2 + (y - cy) ** 2) for x, y in pts]
    return (min(rs), max(rs))


__all__ = [
    "Defect",
    "AcceptanceSchemaError",
    "parse_acceptance_schema",
    "validate_against_cache",
]
