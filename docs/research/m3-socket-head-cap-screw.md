# M3 hexagon socket head cap screw (ISO 4762)

researched: 2026-04-30
researcher: claude-opus-4-7 via Claude Code (subagent)
slug: m3-socket-head-cap-screw

## Canonical name
ISO 4762 — Hexagon socket head cap screws, M-series, coarse pitch
thread, product grade A. Dimensionally interchangeable with DIN 912
for the M3 size.

## Key dimensions

All dimensions in millimetres. Refer to ISO 4762 figure for parameter
identifiers (P, dk, k, s, t, da, ds, e, r, w, b).

Thread (per ISO 724 / ISO 68-1, M3 × 0.5 coarse):

- thread designation: M3 × 0.5
- pitch P: 0.5
- major diameter d (nominal): 3.000
- pitch diameter d2 (basic): 2.675
- minor diameter, internal thread d1 (basic): 2.459
- minor diameter, external thread root d3 (basic): 2.387

Head (per ISO 4762 table for M3):

- head diameter dk: 5.50 max / 5.32 min  (nominal 5.5)
- head height k: 3.00 max / 2.86 min     (nominal 3.0)
- under-head bearing thickness w: 1.15 min
- under-head fillet radius r: 0.1 min
- transition (fillet) diameter da: 3.6 max

Shank / body (between head fillet and thread runout):

- body diameter ds: 3.00 max / 2.86 min

Hex socket (drive recess):

- hex key nominal size: 2.5 mm
- across-flats s: 2.5 nominal, 2.58 max, 2.52 min
- across-corners e: 2.873 min
- key engagement depth t: 1.3 min

Thread length (reference):

- b (reference thread length used to define ls/lg breakpoints): 18

Standard nominal lengths l (typical commercial range): 5, 6, 8, 10,
12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70. Screws shorter
than approximately 2 × d are fully threaded; longer screws have an
unthreaded shank of length (l − b).

## Variants

- DIN 912 — older German standard, dimensionally identical to ISO 4762
  for M3. Many suppliers list parts as "DIN 912 / ISO 4762".
- Property classes (steel): 8.8, 10.9, 12.9 (most common for socket
  cap screws is 12.9 alloy steel).
- Stainless variants: A2-70, A4-70, A4-80.
- ISO 14579 — hexalobular (Torx) socket head cap screw, same head and
  shank envelope but different drive. Not interchangeable on the
  drive side.
- ISO 7380 — button head, and ISO 10642 — countersunk: different
  head profiles, not part of ISO 4762.
- Fine-pitch M3 socket caps are not standard under ISO 4762 (which is
  coarse-pitch only); fine-pitch M3 × 0.35 is covered by other
  standards and is uncommon as a stock item.

## Sources

- https://www.fasteners.eu/standards/iso/4762/
- https://fullerfasteners.com/tech/iso-4762-12474-specifications-hex-socket-cap-screws/
- https://www.engineersedge.com/iso_socket_head_screw.htm
- https://torqbolt.com/iso-4762-socket-head-cap-screws-dimensions-standards-specifications
- https://en.wikipedia.org/wiki/ISO_metric_screw_thread

## Acceptance

Schema for the structural validator. Tolerances are generous:
manifold tessellation noise + the agent's choice of how to model
the thread (linear_extrude with twist vs. swept polyhedron) shifts
the numbers a few percent. Sized for an M3 × 20 cap screw.

```json
{
  "bbox_z_extent": [22.0, 23.5],
  "bbox_xy_max": [5.30, 5.65],
  "bbox_xy_symmetric": true,
  "volume_range": [120, 230],
  "connected_components": 1,
  "axial_consistency": "helical",
  "expected_modules": [
    "shaft|thread",
    "head|cap"
  ],
  "horizontal_slices_at_z": [
    {"z": 5.0,  "outer_protrusions": 1, "radius_range": [1.10, 1.55]},
    {"z": 8.0,  "outer_protrusions": 1, "radius_range": [1.10, 1.55]},
    {"z": 11.0, "outer_protrusions": 1, "radius_range": [1.10, 1.55]},
    {"z": 14.0, "outer_protrusions": 1, "radius_range": [1.10, 1.55]},
    {"z": 17.0, "outer_protrusions": 1, "radius_range": [1.10, 1.55]}
  ]
}
```

## Implementation guidance

A socket head cap screw decomposes naturally into three sub-modules
plus an assembling top-level module:

- `module thread_xs()` — the 2D thread cross-section, swept later by
  `linear_extrude(twist=…)`.
- `module shaft()` — the threaded shank, built by extruding
  `thread_xs()` over the full thread length.
- `module head()` — the cylindrical socket head with the hex socket
  recess subtracted from the top.
- `module screw()` — `union()` of `shaft()` and a translated
  `head()`. This is the only top-level call.

**Helical thread construction (the part agents most often get
wrong).** A correct ISO single-start thread is built by extruding a
2D cross-section that is **the minor-diameter circle PLUS one
triangular tooth on the +X side**, then twisting that profile around
Z as Z rises. Concretely:

```
module thread_xs() {
  // Minor-diameter core PLUS a single radial tooth at azimuth 0.
  union() {
    circle(d = minor);
    translate([minor / 2, 0])
      polygon([
        [0,                 -pitch / 4],
        [(major - minor)/2,  0       ],
        [0,                  pitch / 4]
      ]);
  }
}

module shaft() {
  linear_extrude(
    height = length,
    twist  = 360 * length / pitch,   // RH thread; negate for LH
    slices = max(64, abs(360 * length / pitch) / 5)
  )
    thread_xs();
}
```

The cross-section is `union()` of (small circle + one triangle), NOT
`difference()` of (big circle − one triangle). The latter produces
inverted geometry — a smooth shaft with a thin spiral *groove* — and
is wrong.

`slices = |twist| / 5` (≥ 64) gives ~5° of rotation per slice, which
keeps the helix smooth. With pitch = 0.5 and length = 20 that's
14400°/5 ≈ 2880 slices.

**Hex socket.** OpenSCAD's `cylinder(d=…, $fn=6)` produces a hex
prism whose `d` is the **across-corners** diameter, not the
across-flats (s) value the standard quotes. Convert:
`across_corners = s / cos(30°)`. Place the socket via `difference()`
on the head with `translate([0, 0, head_h - socket_depth])
cylinder(d = across_corners, h = socket_depth + 0.01, $fn = 6)`. The
`+0.01` epsilon prevents z-fighting at the top face.

**Pitfalls to AVOID.**

- **Stacked rings.** Building the thread with `for (i = [0:N])
  translate([0, 0, i*pitch]) rotate([0, 0, i*step]) ...` produces
  visible discrete rings, not a continuous helix. Always use a
  single `linear_extrude(twist=...)` over the full length.
- **Multi-start thread.** A cross-section with N teeth around the
  circle gives an N-start thread. Standard ISO threads are single-
  start. Use exactly ONE tooth (one `translate([minor/2, 0])
  polygon(...)`) in `thread_xs()`.
- **Hairline thread.** If the polygon points are colinear (e.g.
  `[[0,0],[major-minor,0],[0,0]]`) the tooth has zero area and the
  thread renders as a paper-thin fin. The polygon must form a real
  triangle: three non-colinear points.
- **Inverted thread (cylinder − groove).** As above, `union()` of
  a minor-diameter core + ridge, NOT `difference()` from a major
  cylinder.
- **Hex-socket sizing.** As above, distinguish across-flats from
  across-corners; OpenSCAD's `cylinder($fn=6)` uses across-corners.

**Parameter names** — use these (matching the dimension table):
`major`, `minor`, `pitch`, `length`, `head_d`, `head_h`, `socket_af`,
`socket_depth`. Set `$fn = 64` at the top.

Notes:
- **bbox_z_extent** = 20mm shaft + 3mm head ± slack for placement
  (some agents put the screw tip at z = -1, others at z = 0).
- **bbox_xy_max** = 5.5mm head Ø, ±5%.
- **volume_range** is wide because thread-tooth profile choice
  changes total volume by ~25%; the M3 cap-screw volume should
  land in [120, 230] mm³ regardless.
- **outer_protrusions: 1** is the **single-start thread** check.
  A multi-start thread (the original M3 bug) would fail here on
  every slice.
- The slice z values (5, 10, 15) all sit on the threaded portion
  of an M3 × 20.
