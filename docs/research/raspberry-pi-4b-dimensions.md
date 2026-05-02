# Raspberry Pi 4 Model B — board dimensions and connector layout

researched: 2026-05-02
researcher: claude-opus-4-7 via Claude Code (subagent)
slug: raspberry-pi-4b-dimensions

## Canonical name

Raspberry Pi 4 Model B (RPi 4B). Single-board computer manufactured
by Raspberry Pi Ltd. Mechanical envelope is shared with the
"Raspberry Pi HAT" footprint family (the Pi 2 B / 3 B / 3 B+ / 4 B
all use the same 85 × 56 mm outline and the same 4-hole pattern).

## Key dimensions

All dimensions in millimetres. Origin convention used below: the
board's bottom-left corner when viewing the **component side** with
the 40-pin GPIO header along the **top** edge and the USB / Ethernet
stack on the **right** edge. +X runs along the 85 mm edge toward the
USB stack; +Y runs along the 56 mm edge toward the GPIO header; +Z
is up out of the component side.

PCB outline:

- length (X): 85.00
- width  (Y): 56.00
- thickness: 1.40 (per Raspberry Pi mechanical spec; some third-
  party sources quote 1.6 — use 1.4 unless the design needs the
  worst-case stack height)
- corner radius: 3.0 (all four corners rounded, identical radius)
- mass: ~46 g (informational, not a design dimension)

Mounting holes (4 ×, M2.5 clearance):

- hole diameter: 2.75 (often quoted as 2.7; nominal M2.5 clearance)
- pad / annular keep-out diameter: 6.2 (copper ground pad on
  component side; do not place tall components within this radius)
- pattern: 58.0 (X) × 49.0 (Y) rectangle
- hole centre coordinates (origin = bottom-left of PCB):
  - H1: (3.5, 3.5)
  - H2: (61.5, 3.5)
  - H3: (3.5, 52.5)
  - H4: (61.5, 52.5)
- offsets from edges: 3.5 from left, bottom, and top edges; 23.5
  from right edge (the asymmetry exists because the USB / Ethernet
  stack consumes the right side of the board)

40-pin GPIO header (J8, 2 × 20, 2.54 mm pitch):

- pin 1 centre: (7.11, 52.07) — square pad, nearest the USB-C end
- header footprint extent: X = 50.80 mm (20 pins × 2.54), Y = 5.08
  mm (2 rows × 2.54)
- pin pitch: 2.54 in both directions
- pin orientation: pin row runs along +X; pin 2 is at
  (7.11, 54.61), pin 39 is at (55.37, 52.07), pin 40 at
  (55.37, 54.61)
- header body height above PCB: ~8.5 (standard 0.64 mm² square
  posts in a black plastic shroud)
- pin length above shroud: ~6.0 (pin tip Z ≈ 14.5)

Bottom-edge connectors (Y ≈ 0; bodies overhang beyond Y = 0 toward
−Y by 1–2 mm). Centres given as X coordinates along the bottom
edge:

- USB-C power input  (J2): X centre 11.2, body 8.94 × 7.35,
  height above PCB 3.2, overhangs ~1.4 mm past Y = 0
- micro-HDMI 0       (J5): X centre 26.0, body 6.4 × 8.4,
  height above PCB 3.0, overhangs ~1.5 mm
- micro-HDMI 1       (J6): X centre 39.5, body 6.4 × 8.4,
  height above PCB 3.0, overhangs ~1.5 mm
- 3.5 mm AV jack     (J4): X centre 54.0, body 6.0 × 7.5,
  height above PCB 5.6, overhangs ~3.0 mm

microSD card slot (J1) is on the **solder** (back) side of the PCB,
opposite the bottom edge:

- centred at X ≈ 22.0, Y ≈ 2.5
- protrudes ~2.0 mm below the PCB (−Z direction)
- card itself (when inserted) protrudes ~1.5 mm past Y = 0

Right-edge connectors (X ≈ 85; bodies overhang beyond X = 85 toward
+X). Centres given as Y coordinates along the right edge:

- USB 2.0 dual stack (J3): Y centre 9.0, body 17.25 × 13.0,
  height above PCB 15.6, overhangs ~3.0 mm past X = 85
- USB 3.0 dual stack (J7): Y centre 27.0, body 17.25 × 13.0,
  height above PCB 15.6, overhangs ~3.0 mm
- Ethernet RJ45      (J8 / Eth): Y centre 45.5, body 21.3 × 16.0,
  height above PCB 13.5, overhangs ~3.0 mm

Top-side FPC / FFC connectors (lying flat on the component side,
not protruding past board edges):

- DSI display connector (J5_DISP): centred at (3.7, 11.5),
  15-pin 1.0 mm-pitch FFC, oriented with contacts facing −Y,
  height ~2.7
- CSI camera connector  (J6_CAM):  centred at (45.0, 11.5),
  15-pin 1.0 mm-pitch FFC, oriented with contacts facing −Y,
  height ~2.7

PoE header (J14): 4-pin 0.1″ header located near the upper-right
quadrant. Centre approximately (58.0, 48.5), pins arranged in a
2 × 2 block; height above PCB ~7.5.

Tallest features above PCB (component side):

- USB ports: ~15.6 mm (15.0 socket body + small lip)
- Ethernet:  ~13.5 mm
- GPIO pin tips: ~14.5 mm
- HDMI / USB-C / AV: ≤ ~6 mm

Tallest feature below PCB (solder side):

- microSD slot housing: ~2.0 mm

Recommended overall keep-out volume for a snug enclosure:

- X: −3.0 (USB / Ethernet overhang) to 88.0 (USB / Ethernet
  overhang) → 91.0 mm long
- Y: −3.5 (AV jack overhang) to 56.0 → ~60 mm wide
- Z: −2.5 (SD slot) to +18.0 (USB / GPIO clearance) → ~21 mm tall

## Variants

- **Raspberry Pi 4 Model B** — this entry. 2 GB / 4 GB / 8 GB RAM
  variants are mechanically identical.
- **Raspberry Pi 3 Model B / B+** — same 85 × 56 outline, same 4-
  hole pattern, same GPIO position. Differences: full-size HDMI on
  the bottom edge instead of two micro-HDMI; full-size USB-A power
  inlet instead of USB-C; AV jack location is shifted slightly. A
  single CAD model parameterised by `variant` can cover them.
- **Raspberry Pi 2 Model B** — same outline and holes; different
  port arrangement (4 × USB on the right but no Ethernet stack).
- **Raspberry Pi 5** — DIFFERENT board layout. Connector positions,
  CSI/DSI socket type (now 22-pin 0.5 mm), and PoE pinout differ.
  Do not reuse this entry for the Pi 5; create a separate file.
- **Raspberry Pi Zero / Zero 2 W** — entirely different (65 × 30
  outline, 2 holes); not interchangeable.
- **HAT mechanical specification** — defines an add-on board with
  the same 65 × 56 outline that aligns to the Pi's mounting holes
  and GPIO. A HAT model and a Pi 4B model share the same hole
  pattern.

## Sources

- https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-mechanical-drawing.pdf
- https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-product-brief.pdf
- https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-datasheet.pdf
- https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#raspberry-pi-4-model-b
- https://github.com/raspberrypi/documentation/blob/develop/documentation/asciidoc/computers/raspberry-pi/mechanical-specifications.adoc
- https://en.wikipedia.org/wiki/Raspberry_Pi_4

## Acceptance

Schema for the structural validator. The Pi 4B is not a helical /
threaded part, so the thread-specific fields (`axial_consistency`,
`pitch`, `axial_section`, helical-thread `horizontal_slices_at_z`
checks) are omitted — a board model is mostly a rectangular
extrusion plus mounting holes plus a few rectangular blocks for the
larger connectors. Tolerances are ±5 % on dimensions and ±15 % on
volume; XY allows up to +5 mm on each side for connector overhang
since the agent may or may not include the connectors in the
modelled bounding box.

```json
{
  "bbox_z_extent": [1.3, 20.0],
  "bbox_xy_max": [85.0, 92.0],
  "volume_range": [7000, 28000],
  "connected_components": 1,
  "expected_modules": [
    "pcb|board",
    "mount(ing)?_hole|hole|standoff",
    "port|connector|usb|ethernet|hdmi|gpio|header"
  ],
  "horizontal_slices_at_z": [
    {"z": 0.7,  "outer_protrusions": 0, "radius_range": [40.0, 55.0]}
  ]
}
```

Field notes:

- `bbox_z_extent` lower bound 1.3 covers a "PCB only" model; upper
  bound 20.0 covers a model that includes the USB stack and GPIO
  pin tips. Either is acceptable.
- `bbox_xy_max` upper bound 92 leaves room for connectors that
  overhang past X = 85 by ~3 mm on each side (≈3 + 85 + 3 = 91, +
  slack).
- `volume_range` lower bound is the bare PCB
  (85 × 56 × 1.4 ≈ 6664 mm³, minus four 2.75 mm holes ≈ -33 mm³,
  rounded down for slack); upper bound covers a model with
  Ethernet, USB stacks, GPIO header, and HDMI / USB-C bodies.
- `connected_components: 1` — the PCB and any modelled connectors
  must be `union()`-ed into a single solid. Floating connectors
  indicate a missing union.
- `expected_modules` — at least one of each row's regex fragments
  must match a defined module. The board itself, the mounting
  holes, and at least one port / connector / header are required.
- `horizontal_slices_at_z` slice at z = 0.7 sits in the middle of
  the PCB. The cross-section is rectangular (no radial
  protrusions), and the radius range bounds the diagonal half-
  extent of an 85 × 56 rectangle (~51 mm) with slack for either
  PCB-only or PCB-plus-connectors models.

## Implementation guidance

A Raspberry Pi 4B model decomposes naturally into a small set of
independent sub-modules. The board itself is the dominant feature;
connectors are simplified to rectangular blocks for collision /
keep-out purposes (full connector geometry is rarely worth
modelling and bloats the mesh).

Suggested module decomposition:

- `module pcb()` — the 85 × 56 × 1.4 PCB body with 3 mm rounded
  corners and four mounting holes drilled through.
- `module mounting_hole(x, y)` — a single through-hole cylinder of
  diameter `mount_hole_d` at `(x, y)`, centred at z = pcb_t / 2.
  Called four times by `pcb()` inside a `difference()`.
- `module connector(w, d, h)` — a generic rectangular block, used
  to stamp out USB, Ethernet, HDMI, USB-C, AV, and the GPIO header
  shroud. Centred on its footprint so callers can `translate()`
  to the connector's PCB-side centre.
- `module gpio_header()` — a 50.80 × 5.08 × 8.5 shroud block plus
  optional 0.64 mm² × 6 mm pin posts above. The pins are usually
  the visually distinctive feature; even a single block is
  acceptable for keep-out modelling.
- `module pi4b()` — top-level `union()` of `pcb()` and every
  connector / header, each `translate()`-d into place using the
  coordinates from the **Key dimensions** section.

**Rounded-rectangle PCB.** The agent's OpenSCAD subset typically
lacks `offset()` for arbitrary 2D shapes; build the rounded
rectangle as the `hull()` of four corner cylinders:

```
module pcb_outline_2d() {
  hull() {
    translate([corner_r,         corner_r        ]) circle(r = corner_r);
    translate([pcb_l - corner_r, corner_r        ]) circle(r = corner_r);
    translate([corner_r,         pcb_w - corner_r]) circle(r = corner_r);
    translate([pcb_l - corner_r, pcb_w - corner_r]) circle(r = corner_r);
  }
}

module pcb() {
  difference() {
    linear_extrude(height = pcb_t) pcb_outline_2d();
    // mounting holes — drill through with a small +z epsilon to
    // avoid coplanar top/bottom faces.
    for (p = [[3.5, 3.5], [61.5, 3.5], [3.5, 52.5], [61.5, 52.5]])
      translate([p[0], p[1], -0.01])
        cylinder(d = mount_hole_d, h = pcb_t + 0.02, $fn = 32);
  }
}
```

**Connector blocks.** Each side-mounted connector is a rectangular
prism that overhangs the PCB edge. Place it by its PCB-side
footprint centre, not its connector body centre, so the overhang
falls outside the PCB outline naturally:

```
module connector(w, d, h) {
  translate([-w / 2, -d / 2, 0]) cube([w, d, h]);
}

module usb3_pair() {
  // overhangs ~3 mm past X = 85
  translate([85.0 - (17.25 - 3.0) / 2, 27.0, pcb_t])
    connector(17.25, 13.0, 15.6);
}
```

The `(85.0 - (17.25 - 3.0) / 2)` X-offset places the connector so
3 mm of its 17.25 mm length sits past the board edge. Equivalent
formulas apply to USB 2.0 (Y = 9.0), Ethernet (Y = 45.5, body
21.3 × 16.0 × 13.5), and the bottom-edge USB-C / HDMI / AV.

**GPIO header.** Build as a single shroud block; pins can be
omitted for keep-out work or added as a flat array of 0.64 mm²
posts:

```
module gpio_header() {
  translate([7.11 - 1.27, 52.07 - 1.27, pcb_t]) {
    cube([50.80, 5.08, gpio_shroud_h]);   // shroud body
    // optional pin posts:
    // for (i = [0:19]) for (j = [0:1])
    //   translate([1.27 + i*2.54 - 0.32, 1.27 + j*2.54 - 0.32, gpio_shroud_h])
    //     cube([0.64, 0.64, 6.0]);
  }
}
```

**Coordinate convention.** Place the board with its bottom-left
PCB corner at the world origin and the component side facing +Z.
This keeps every coordinate in this file usable verbatim. If the
caller wants the board centred at the origin instead, wrap the top-
level `pi4b()` in a single `translate([-pcb_l/2, -pcb_w/2, 0])`
rather than re-deriving every connector position.

**Pitfalls to AVOID.**

- **Wrong corner radius.** The Pi 4B uses 3 mm radius, not 2 mm
  and not 5 mm. A 2 mm radius will fit inside cases designed to a
  3 mm radius and rattle; a 5 mm radius cuts into the mounting-
  hole keep-outs.
- **Symmetric mounting holes.** The hole pattern is **NOT
  symmetric in X.** Holes are 3.5 mm from the left edge but 23.5
  mm from the right edge. Modelling them at `(85 - 3.5, …)`
  produces a part that physically does not fit on a real Pi 4B
  carrier board. Use the four absolute coordinates from the
  dimension table above; do not derive them by mirroring.
- **Mounting-hole keep-out pads.** The 6.2 mm copper pad around
  each hole is a soldered ground pad, not a clearance for tall
  components. Standoffs sit on it; nothing else should touch it.
  If your model places components or labels near a hole, check
  the 6.2 mm radius is clear.
- **Connector overhang direction.** Bottom-edge connectors (USB-C,
  HDMI ×2, AV) overhang in **−Y**, past Y = 0. Right-edge
  connectors (USB ×2, Ethernet) overhang in **+X**, past X = 85.
  microSD overhangs in **−Y** AND **−Z** (it sits on the solder
  side and protrudes past the bottom edge). Mixing these up makes
  cases that don't accept the cables.
- **PCB thickness.** Use 1.4 mm. Some references say 1.6 — that's
  a generic FR-4 default, not the Pi-specific spec. The
  difference matters for press-fit standoffs.
- **Pi 4 vs. Pi 5 / Pi 3.** The connector layouts differ enough
  between Pi 3 / 4 / 5 that a single parameterised model is
  worthwhile, but the **default** must match the requested
  variant. Do not silently model a Pi 3 layout when the user asks
  for a Pi 4B.

**Parameter names** (use these to keep cross-references obvious):
`pcb_l = 85`, `pcb_w = 56`, `pcb_t = 1.4`, `corner_r = 3`,
`mount_hole_d = 2.75`, `mount_hole_pad_d = 6.2`,
`gpio_shroud_h = 8.5`, `gpio_pin_h = 6.0`. Set `$fn = 64` at the
top.
