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
    z_extent = bb.zmax - bb.zmin
    xy_max = max(bb.xmax - bb.xmin, bb.ymax - bb.ymin)

    if "bbox_z_extent" in schema:
        out.extend(_check_range("bbox_z_extent", z_extent, schema["bbox_z_extent"], "mm"))
    if "bbox_xy_max" in schema:
        out.extend(_check_range("bbox_xy_max", xy_max, schema["bbox_xy_max"], "mm"))
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
