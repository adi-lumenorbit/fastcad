$fn = 48;
difference() {
  cylinder(h = 8, r = 10);
  // 6 angled bores — same pattern as the wire twister, simplified.
  for (i = [0:5]) {
    rotate(i * 60, [0, 0, 1])
    rotate(8, [0, 1, 0])
    translate([6, 0, 0])
      cylinder(h = 30, r = 1.5, center = true);
  }
}
