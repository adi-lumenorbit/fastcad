# M6 hexagon head bolt / screw (ISO 4014 / ISO 4017)

researched: 2026-04-30
researcher: claude-opus-4-7 via Claude Code (subagent)
slug: m6-hex-head-bolt

## Canonical name
ISO 4014 — Hexagon head bolts, M-series, partially threaded, product
grades A and B. ISO 4017 — Hexagon head screws, M-series, fully
threaded, product grades A and B. The two standards share head and
thread geometry; they differ only in whether the screw has an
unthreaded shank section. For the M6 size both are dimensionally
interchangeable with DIN 931 (partially threaded) and DIN 933 (fully
threaded).

## Key dimensions

All dimensions in millimetres. Refer to ISO 4014 / ISO 4017 figure
for parameter identifiers (P, k, s, e, dw, da, ds, b, c).

Thread (per ISO 724 / ISO 68-1, M6 × 1 coarse):

- thread designation: M6 × 1
- pitch P: 1.000
- major diameter d (nominal): 6.000
- pitch diameter d2 (basic): 5.350
- minor diameter, internal thread D1 (basic): 4.917
- minor diameter, external thread root d3 (basic): 4.773

Head (per ISO 4014 / ISO 4017 table for M6, current revision):

- width across flats s: 10.00 nominal, 10.00 max, 9.78 min (product grade A)
- width across corners e: 11.05 min
- head height k: 4.00 nominal, 4.00 max, 3.74 min (product grade A)
- wrenching height k' min (k_w): 2.8
- washer-face / bearing-surface diameter dw min: 8.74
- washer-face thickness c: 0.15 min / 0.6 max
- transition (fillet) diameter da max: 6.8

Note: an older revision of ISO 4014 / 4017 used s = 10 mm for M6;
some legacy DIN 931 / DIN 933 parts and machinery references list
s = 10 mm. The current ISO and current DIN both standardize on 10.

Shank / body (between under-head fillet and thread runout, ISO 4014 only):

- body diameter ds: 6.00 max / 5.82 min (≈ nominal d)

Drive: external hex (the head itself); no separate drive recess.

Thread length b (ISO 4014 reference values, function of nominal length l):

- l ≤ 125: b = 18  (b = 2d + 6)
- 125 < l ≤ 200: b = 24  (b = 2d + 12)
- l > 200: b = 37  (b = 2d + 25)

ISO 4017 has no unthreaded shank: thread runs from under the head
fillet (da) to the chamfered tip, less the standard incomplete-thread
allowance at each end.

Standard nominal lengths l (typical commercial range): 10, 12, 16, 20,
25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120, 130,
140, 150. ISO 4014 below ~25 mm is rarely stocked; ISO 4017 covers
the short end. End chamfer at 45° to a diameter ≈ d3 (chamfer length
≈ pitch).

## Variants

- DIN 931 — older German standard for partially threaded hex head
  bolts, dimensionally identical to ISO 4014 for M6. Suppliers list
  parts as "DIN 931 / ISO 4014".
- DIN 933 — older German standard for fully threaded hex head screws,
  dimensionally identical to ISO 4017 for M6. Listed as "DIN 933 /
  ISO 4017".
- Property classes (steel): 4.6, 5.6, 8.8, 10.9, 12.9. 8.8 is the
  default stock item; 10.9 and 12.9 for high-strength applications.
- Stainless variants: A2-70, A4-70 (most common); A2-80, A4-80 for
  higher tensile.
- Flange variants: ISO 4162 / DIN 6921 (hex head with integrated
  flanged washer face) — different head profile, not interchangeable.
- ISO 8676 / DIN 961 — fine-pitch M6 × 0.75 hex head; same head
  envelope, different thread pitch. Not part of ISO 4014 / 4017 (which
  are coarse-pitch only).
- Grade B: relaxed dimensional tolerances vs. grade A. Head and
  thread nominals are identical; tolerance bands widen.

## Sources

- https://www.fasteners.eu/standards/iso/4014/
- https://www.fasteners.eu/standards/iso/4017/
- https://fullerfasteners.com/tech/iso-4014-specifications-hex-bolts/
- https://fullerfasteners.com/tech/iso-4017-specifications-hex-screws/
- https://www.engineersedge.com/iso_hex_bolts.htm
- https://en.wikipedia.org/wiki/ISO_metric_screw_thread

## Acceptance

Schema for the structural validator. Tolerances are generous:
manifold tessellation noise + the agent's choice of how to model
the thread (linear_extrude with twist vs. swept polyhedron) shifts
the numbers a few percent. Sized for an M6 × 30 hex head bolt
(ISO 4014: 18 mm threaded, 12 mm unthreaded shank) — but the
acceptance ranges are wide enough to also cover an ISO 4017 M6 × 30
fully threaded screw of the same overall length.

```json
{
  "bbox_z_extent": [32.5, 35.0],
  "bbox_xy_max": [10.4, 12.2],
  "volume_range": [800, 1600],
  "connected_components": 1,
  "axial_consistency": "helical",
  "expected_modules": [
    "shaft|thread|shank",
    "head|hex"
  ],
  "horizontal_slices_at_z": [
    {"z": 4.0,  "outer_protrusions": 1, "radius_range": [2.20, 3.15]},
    {"z": 8.0,  "outer_protrusions": 1, "radius_range": [2.20, 3.15]},
    {"z": 12.0, "outer_protrusions": 1, "radius_range": [2.20, 3.15]},
    {"z": 16.0, "outer_protrusions": 1, "radius_range": [2.20, 3.15]}
  ]
}
```

Notes:
- **bbox_z_extent** = 30 mm shank + 4 mm head ± slack for placement
  (some agents put the bolt tip at z = -1, others at z = 0).
- **bbox_xy_max** = the head is a hex prism — its max XY extent is
  the across-corners distance e ≈ 11.55 mm (= s / cos(30°) for
  s = 10 mm). The shank Ø 6 mm is smaller and does not drive the
  bounding box. Range allows ±5%.
- **volume_range** is wide because thread-tooth profile choice
  changes shaft volume by ~25%, and a fully threaded ISO 4017
  variant has slightly less volume than a partially threaded
  ISO 4014 variant. Smooth-cylinder estimate: head hex prism
  ((3√3/8)·e²·k) ≈ 347 mm³ + shank π·3²·30 ≈ 848 mm³ ≈ 1195 mm³
  total. Threaded shaft drops shaft volume toward ~700 mm³, giving
  ~1050 mm³.
- **outer_protrusions: 1** is the **single-start thread** check.
  A multi-start thread (the original M3 bug) would fail here on
  every threaded slice.
- The slice z values (4, 8, 12, 16) sit on the threaded portion of
  an M6 × 30 ISO 4014 (shank from z = 0 to z = 30, with the lower
  18 mm threaded; head sits above z = 30). For an ISO 4017 fully
  threaded screw the entire shank is threaded so all four slices
  still hit thread.

## Implementation guidance

A hex head bolt decomposes naturally into three sub-modules plus an
assembling top-level module. The key structural difference from a
socket head cap screw is that the **head is the drive feature** —
the hex is the OUTER profile of the head, not a recess inside it.
Do NOT attempt to subtract a hex socket from a cylindrical head;
that produces the wrong part (a socket cap screw, not a hex bolt).

- `module thread_xs()` — the 2D thread cross-section, swept later by
  `linear_extrude(twist=…)`.
- `module shaft()` — the threaded shank (ISO 4017) or the threaded
  portion plus an unthreaded shank cylinder (ISO 4014). For
  ISO 4014, build as `union()` of a smooth `cylinder(d=major)` for
  the unthreaded length and the helical thread for the lower
  portion.
- `module hex_head()` — a hex prism (height k, across-corners e),
  optionally with a washer-face chamfer on the underside.
- `module bolt()` — `union()` of `shaft()` and a translated
  `hex_head()`. This is the only top-level call.

**Hex head construction.** OpenSCAD's `cylinder(d=…, $fn=6)`
produces a hex prism whose `d` is the **across-corners** diameter,
not the across-flats (s) value the standard quotes. Convert:

```
across_corners = s / cos(30);   // for M6: 10 / cos(30°) ≈ 11.547
module hex_head() {
  cylinder(d = across_corners, h = head_h, $fn = 6);
}
```

The head sits on top of the shank: `translate([0, 0, length])
hex_head();`. If you want the washer-face chamfer (the small
conical relief on the underside that defines `dw`), apply a
`difference()` with a thin truncated cone whose top diameter is
`dw_min` and bottom diameter is `across_corners`.

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
keeps the helix smooth. With pitch = 1.0 and length = 30 that's
10800°/5 = 2160 slices.

**ISO 4014 (partially threaded) variant.** When the requested length
exceeds b (18 mm for M6 with l ≤ 125), the upper portion of the
shank is a smooth cylinder of diameter d (major) and the lower
portion is the helical thread of length b. Build as:

```
union() {
  // Threaded portion at the bottom (tip at z = 0).
  shaft();                                            // length = b
  // Unthreaded shank above the thread.
  translate([0, 0, b])
    cylinder(d = major, h = length - b);
}
```

For ISO 4017 (fully threaded) just call `shaft()` with `length = l`.

**Pitfalls to AVOID.**

- **Hex SOCKET instead of hex HEAD.** The single most common error
  for this part class: copy-pasting a socket head cap screw template
  and ending up with a cylindrical head with a hex hole subtracted
  from the top. The hex bolt has the hex on the OUTSIDE — the head
  itself is a hex prism. Use `cylinder(d=…, $fn=6)` for the head,
  not `difference() { cylinder(...); cylinder($fn=6); }`.
- **Across-flats vs. across-corners.** Same conversion issue as the
  hex socket on a cap screw, but applied to the OUTER head this
  time. `cylinder(d=10, $fn=6)` produces a hex prism that is too
  small — its across-flats is `10·cos(30°) ≈ 8.66 mm`, not the 10 mm
  the standard requires. Always pass `d = s / cos(30°)`.
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
- **Forgetting the unthreaded shank on ISO 4014.** A long ISO 4014
  bolt (l > b) has a smooth cylindrical section between the head
  fillet and the thread runout. If the entire shank is threaded
  the part is an ISO 4017 bolt, not an ISO 4014 bolt — be explicit
  about which variant you're modeling.

**Parameter names** — use these (matching the dimension table):
`major`, `minor`, `pitch`, `length`, `head_s` (across-flats),
`head_e` (across-corners, derived as `head_s / cos(30)`), `head_h`
(= k), `b` (thread length for ISO 4014). Set `$fn = 64` at the top
for the round shank; the head's hex is its own `$fn = 6` and is not
affected.
