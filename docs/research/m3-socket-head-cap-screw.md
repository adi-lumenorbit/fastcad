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
