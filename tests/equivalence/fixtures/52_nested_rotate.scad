// Three rotations stacked — the order matters; rotating the same
// brick with axis-angle then Euler then scalar exercises all three
// rotate code paths in sequence.
rotate(90, [0, 0, 1])
  rotate([15, 0, 0])
    rotate(20)
      translate([2, 0, 0])
        cube([2, 3, 4]);
