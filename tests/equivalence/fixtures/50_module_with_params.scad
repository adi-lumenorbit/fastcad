module slot(len, dia, h) {
  $fn = 32;
  hull() {
    cylinder(h = h, r = dia / 2);
    translate([len, 0, 0]) cylinder(h = h, r = dia / 2);
  }
}

slot(8, 3, 2);
