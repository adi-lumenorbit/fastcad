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
  "pitch": 0.5,
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
  ],
  "axial_section": {
    "plane": "XZ",
    "offset": 0.0,
    "peak_count": [30, 45],
    "pitch": 0.5,
    "peak_axial_extent_pct_of_pitch": [0.30, 0.95],
    "flank_angle_deg": [40, 80]
  }
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
wrong).**

The trap most agents fall into: cross-section = (minor circle + tiny
triangle bump). This LOOKS correct in 2D — one bump, single-start —
but `linear_extrude(twist=)` maps the cross-section's **azimuthal
coverage** to the **axial extent** of each thread tooth in the final
3D shape. A small triangle of y-extent `pitch/2` covers only ~12° of
azimuth at minor radius, which produces a thread whose teeth are
~0.03 mm tall axially — a "helical band of zero thickness" that fails
any axial-section inspection. **No `slices` count fixes this.**

**The correct approach:** the cross-section's outer envelope must
sweep through `r_minor → r_major → r_minor` over a full 360° of
azimuth. When extruded with `twist = 360°·length/pitch` (one full
rotation per pitch), this generates a real sawtooth thread profile
in any axial section. Build with `union()` of triangular wedges
since the parser doesn't support list comprehensions:

```
N = 96;
module thread_xs() {
  union() {
    for (i = [0 : N - 1]) {
      let (
        a0 = 360 * i / N,
        a1 = 360 * (i + 1) / N,
        t0 = 1 - abs(1 - a0 / 180),    // 0 → 1 → 0 across [0, 360]
        t1 = 1 - abs(1 - a1 / 180),
        r0 = minor / 2 + (major - minor) / 2 * t0,
        r1 = minor / 2 + (major - minor) / 2 * t1
      )
        polygon([
          [0,            0],
          [r0 * cos(a0), r0 * sin(a0)],
          [r1 * cos(a1), r1 * sin(a1)],
        ]);
    }
  }
}

module shaft() {
  linear_extrude(
    height = length,
    twist  = 360 * length / pitch,   // RH thread; negate for LH
    slices = max(64, floor(abs(360 * length / pitch) / 5))
  )
    thread_xs();
}
```

`slices = |twist| / 5` (≥ 64) gives ~5° of rotation per slice, which
keeps the helix smooth.

**Verifying the construction.** After committing the source, call
`inspect_section(plane="XZ", offset=0)` and read
`metrics.axial_peaks.mean_axial_extent`. A correct thread shows
0.30–0.95 × pitch. If you see < 0.05 mm, the cross-section's
azimuthal coverage is too narrow — go back and use the lobed
approach above, not a "minor circle + tiny triangle."

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
- **Paper-thin / zero-axial-extent thread.** A "minor circle + small
  triangle" cross-section produces threads whose visible teeth have
  ~0.03 mm of axial extent, no matter how many slices you use. The
  fix is the lobed cross-section in **Helical thread construction**
  above — the tooth must sweep azimuthally enough to give the
  desired axial tooth height.
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
