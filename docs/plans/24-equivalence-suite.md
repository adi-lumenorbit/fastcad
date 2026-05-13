# 24 — OpenSCAD equivalence test suite (+ 4 evaluator bug fixes)

GitHub issue: https://github.com/adi-lumenorbit/fastcad/issues/24

## Problem

Up to this point fastcad's evaluator had no regression net against
silent geometric divergence from OpenSCAD's reference implementation.
A user opening a real-world `.scad` (the wire twister at
`~/src/3d-models/wire twister 2/wire twister 2.04-thinner-4mm-hole.scad`)
got geometry that looked wrong — the body was the right rough
shape but the angled bores were missing, the central frustum was a
straight cylinder, and twisted extrudes mirrored the OpenSCAD
result.

A fixture-driven equivalence suite catches this class of bug
deterministically.

## Affected Components

| Component | Status | Notes |
|-----------|--------|-------|
| `tests/equivalence/fixtures/*.scad` | OK | 34 fixtures covering primitives (cube/sphere/cylinder/polyhedron), 2D + extrudes (linear with twist + scale, rotate_extrude), all three `rotate` forms, scale, mirror, all CSG (union/difference/intersection/hull), `for` (range + step), `if/else`, `let`, modules with parameters, real-world combinations. |
| `tests/equivalence/helpers.py` | OK | `openscad` CLI shell-out + STL parser (binary + ASCII) + manifold3d volume/bbox helpers + tolerance comparator. |
| `tests/equivalence/test_equivalence.py` | OK | pytest parametrized over the fixture directory; skips cleanly when the `openscad` binary isn't on PATH. |
| `src/fastcad/model/scad_eval.py` | OK | Four evaluator fixes (see below). |
| `src/fastcad/model/kernel.py` | OK | `cylinder(height, radius, ..., radius_top)` for frustums; `extrude_polygon` negates twist to match OpenSCAD's sign convention. |

## Bugs the suite caught (all fixed in this PR)

1. **`rotate(angle, [axis])` axis-angle form** was being silently
   dropped to `rotate(angle)` (Z only). The axis argument never
   reached the geometry pipeline. Implemented via Rodrigues' rotation
   matrix → `manifold.transform()`.
   - Fixtures: `23_rotate_axis_angle_x`, `24_rotate_axis_angle_arbitrary`.
2. **`cylinder(h, r1, r2)`** (frustum / cone) ignored `r2`. Volume
   of `cylinder(h=12, r1=5, r2=2)` came out at the full-cylinder 942
   instead of the frustum 489 — 92 % error. Plumbed `radius_top`
   through `kernel.cylinder` to manifold3d's
   `Manifold.cylinder(h, r_low, r_high, ...)`.
   - Fixture: `06_cylinder_cone`.
3. **`translate([x, y])`** on 2D children raised
   `EvalError: translate requires a 3-vector`. The 2-vector form is
   natural for 2D pipelines (`translate([x,y]) circle(r)`). Accept
   both shapes; pad z=0 for 2D.
   - Fixture: `14_rotate_extrude`.
4. **`linear_extrude(twist=N)`** twisted opposite to OpenSCAD —
   manifold3d uses the reverse sign convention. The XY bbox of a
   non-symmetric twisted section came out mirrored. Negate the twist
   value at the kernel boundary.
   - Fixture: `13_linear_extrude_twist`.

The wire-twister volume (the file that triggered this work) now
matches OpenSCAD's STL volume to 5 decimal places: 9549.305 vs
9549.312.

## Out of scope

- Deeper geometric diff (Hausdorff distance, cross-section topology).
  Volume + bbox catches a surprising amount; deeper checks are a
  follow-up.
- Negative cases (constructs that should differ — `$preview` branches,
  things outside the supported subset).
