// Bug surfaced by the wire twister: `rotate(angle, [axis])` was
// silently dropped to `rotate(angle)` (Z-only). This pins the
// rotation around X to 90 degrees.
rotate(90, [1, 0, 0]) translate([0, 0, 5]) cube([2, 3, 4]);
