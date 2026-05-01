# M20 hexagon socket head cap screw (ISO 4762)

researched: 2026-04-30
researcher: claude-opus-4-7 via Claude Code (subagent)
slug: m20-socket-head-cap-screw

## Canonical name
ISO 4762 — Hexagon socket head cap screws, M-series, coarse pitch
thread, product grade A. Dimensionally interchangeable with DIN 912
for the M20 size.

## Key dimensions

All dimensions in millimetres. Refer to ISO 4762 figure for parameter
identifiers (P, dk, k, s, t, da, ds, e, r, w, b).

Thread (per ISO 724 / ISO 68-1, M20 × 2.5 coarse):

- thread designation: M20 × 2.5
- pitch P: 2.5
- major diameter d (nominal): 20.000
- pitch diameter d2 (basic): 18.376
- minor diameter, internal thread D1 (basic): 17.294
- minor diameter, external thread root d3 (basic): 16.933

Head (per ISO 4762 table for M20):

- head diameter dk: 30.00 max / 29.67 min  (nominal 30.0)
- head height k: 20.00 max / 19.48 min     (nominal 20.0)
- under-head bearing thickness w: 8.6 min
- under-head fillet radius r: 0.8 min
- transition (fillet) diameter da: 22.4 max

Shank / body (between head fillet and thread runout):

- body diameter ds: 20.00 max / 19.67 min

Hex socket (drive recess):

- hex key nominal size: 17 mm
- across-flats s: 17 nominal, 17.23 max, 17.05 min
- across-corners e: 19.437 min
- key engagement depth t: 10 min

Thread length (reference):

- b (reference thread length used to define ls/lg breakpoints): 52

Standard nominal lengths l (typical commercial range): 30, 35, 40,
45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160,
180, 200. Screws with l ≤ b are fully threaded; longer screws have
an unthreaded shank of length (l − b).

## Variants

- DIN 912 — older German standard, dimensionally identical to ISO 4762
  for M20. Many suppliers list parts as "DIN 912 / ISO 4762".
- Property classes (steel): 8.8, 10.9, 12.9 (most common for socket
  cap screws is 12.9 alloy steel).
- Stainless variants: A2-70, A4-70, A4-80.
- ISO 14579 — hexalobular (Torx) socket head cap screw, same head and
  shank envelope but different drive. Not interchangeable on the
  drive side.
- ISO 7380 — button head, and ISO 10642 — countersunk: different
  head profiles, not part of ISO 4762.
- Fine-pitch M20 socket caps with M20 × 1.5 are covered by ISO 4762
  fine-pitch tables in some references but coarse M20 × 2.5 is the
  default stock item.

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
the numbers a few percent. Sized for an M20 × 50 cap screw.

```json
{
  "bbox_z_extent": [68.5, 73.0],
  "bbox_xy_max": [28.5, 31.5],
  "volume_range": [16000, 33000],
  "connected_components": 1,
  "expected_modules": [
    "shaft|thread",
    "head|cap"
  ],
  "horizontal_slices_at_z": [
    {"z": 10.0, "outer_protrusions": 1, "radius_range": [8.0, 10.5]},
    {"z": 25.0, "outer_protrusions": 1, "radius_range": [8.0, 10.5]},
    {"z": 40.0, "outer_protrusions": 1, "radius_range": [8.0, 10.5]}
  ]
}
```

Notes:
- **bbox_z_extent** = 50mm shaft + 20mm head ± slack for placement
  (some agents put the screw tip at z = -1, others at z = 0).
- **bbox_xy_max** = 30mm head Ø, ±5%.
- **volume_range** is wide because thread-tooth profile choice
  changes total volume by ~25%; the M20 × 50 cap-screw volume should
  land in [16000, 33000] mm³ regardless. Nominal smooth-cylinder
  estimate is ~27,000 mm³ (head π·15²·20 + shaft π·10²·50 minus the
  hex socket recess); a minor-diameter shaft drops it toward
  ~23,000 mm³.
- **outer_protrusions: 1** is the **single-start thread** check.
  A multi-start thread (the original M3 bug) would fail here on
  every slice.
- The slice z values (10, 25, 40) all sit on the threaded portion
  of an M20 × 50 (l = 50 ≤ b = 52, so the screw is fully threaded
  along the entire shaft).
