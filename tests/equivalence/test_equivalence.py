"""Functional equivalence: every fixture under `fixtures/` is rendered
by both engines and the resulting solid must agree on volume + bbox
within a tight tolerance.

Skips if the `openscad` CLI isn't on PATH so a fresh checkout without
the package still gives a clean `pytest` run.

Some fixtures are known to differ — those are listed in
`KNOWN_DIVERGENCES` with a short reason. They're marked `xfail` so the
suite is green by default while still surfacing the gap. Move out of
the dict when fixed."""
from __future__ import annotations

from pathlib import Path

import pytest

from .helpers import (
    Geom,
    Tolerance,
    compare,
    openscad_path,
    render_to_stl,
)


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# Fixtures where fastcad and OpenSCAD legitimately differ (or where
# we know there's an open bug). Map: fixture stem -> short reason.
# Marked `xfail` so they don't fail the suite but get re-checked on
# every run; remove from this dict the moment fastcad catches up.
KNOWN_DIVERGENCES: dict[str, str] = {}


# Per-fixture tolerance overrides. Anything not listed uses Tolerance()
# defaults (0.5% volume, 0.01 mm bbox). Twist / rotate_extrude need a
# looser volume bound because the two engines pick slightly different
# slice/segment counts when `slices` isn't pinned.
_TOLERANCES: dict[str, Tolerance] = {
    # Twist + slice count interpolation is engine-dependent: with the
    # same nominal `slices` value, manifold3d undershoots OpenSCAD's
    # piecewise-linear approximation by a few percent on the volume
    # (bbox is identical because corners line up at each slice).
    "13_linear_extrude_twist": Tolerance(volume_rel=0.06),
    "14_rotate_extrude": Tolerance(volume_rel=0.01),
}


def _fixture_paths() -> list[Path]:
    return sorted(_FIXTURES_DIR.glob("*.scad"))


def _stem(p: Path) -> str:
    return p.stem


pytestmark = pytest.mark.skipif(
    openscad_path() is None,
    reason="`openscad` CLI not on PATH; install OpenSCAD to run the equivalence suite",
)


@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=_stem,
)
def test_fixture_matches_openscad(fixture_path: Path, tmp_path: Path) -> None:
    name = _stem(fixture_path)
    if name in KNOWN_DIVERGENCES:
        pytest.xfail(KNOWN_DIVERGENCES[name])

    stl_path = tmp_path / f"{name}.stl"
    render_to_stl(fixture_path, stl_path)
    os_geom = Geom.from_stl(stl_path)
    fc_geom = Geom.from_fastcad(fixture_path.read_text())

    tol = _TOLERANCES.get(name, Tolerance())
    diffs = compare(fc_geom, os_geom, tol)
    assert not diffs, (
        f"{name} diverges:\n  " + "\n  ".join(diffs) +
        f"\n  fastcad: vol={fc_geom.volume:.4f}  bbox={fc_geom.bbox_min} -> {fc_geom.bbox_max}"
        f"\n  openscad: vol={os_geom.volume:.4f}  bbox={os_geom.bbox_min} -> {os_geom.bbox_max}"
    )
