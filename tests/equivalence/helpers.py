"""Helpers for the OpenSCAD-vs-fastcad equivalence suite.

The suite walks `tests/equivalence/fixtures/*.scad`, renders each
through both engines, and asserts the resulting solids are
geometrically equivalent within a tight tolerance.

- OpenSCAD side: shell out to the `openscad` CLI, ask for an STL.
  Parse the STL into a triangle array and compute volume + bbox in
  plain numpy.
- fastcad side: feed the source through `evaluate_source` and read
  `manifold.volume()` + `bounding_box()` via `kernel`.

If the `openscad` binary isn't on PATH (CI without the package, fresh
WSL, etc.) the suite skips itself cleanly — no hard dependency."""
from __future__ import annotations

import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fastcad.model import kernel as k
from fastcad.session import SessionState


# ---- OpenSCAD CLI -------------------------------------------------------


def openscad_path() -> str | None:
    """Resolve the OpenSCAD CLI, or None if absent."""
    return shutil.which("openscad")


def render_to_stl(scad_path: Path, out_path: Path, *, timeout_s: float = 60) -> None:
    """Run `openscad -o out.stl in.scad`. Raises CalledProcessError on
    a non-zero exit or RuntimeError when the binary is missing. We
    don't pass `--render` — it's flaky on 2021.01 and the default
    pipeline already CSG-renders for STL output."""
    binary = openscad_path()
    if not binary:
        raise RuntimeError("openscad binary not on PATH")
    proc = subprocess.run(
        [binary, "-o", str(out_path), str(scad_path)],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"openscad failed for {scad_path.name}:\n"
            f"stdout: {proc.stdout[-400:]}\n"
            f"stderr: {proc.stderr[-400:]}"
        )


# ---- STL parsing --------------------------------------------------------


def stl_triangles(path: Path) -> np.ndarray:
    """Return `Nx3x3` array of (triangle, vertex, xyz) from `path`.
    Handles both ASCII and binary STL — OpenSCAD emits ASCII by
    default for small files and binary for larger ones."""
    data = path.read_bytes()
    if data[:5] == b"solid" and b"facet normal" in data[:512]:
        verts = []
        for line in data.decode(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("vertex"):
                _, x, y, z = line.split()
                verts.append((float(x), float(y), float(z)))
        return np.array(verts, dtype=np.float64).reshape(-1, 3, 3)
    # Binary: 80-byte header, uint32 count, then n records of size 50.
    n_tri = struct.unpack("<I", data[80:84])[0]
    tris = np.empty((n_tri, 3, 3), dtype=np.float64)
    rec = 50
    for i in range(n_tri):
        off = 84 + i * rec + 12  # skip the per-triangle normal
        vs = struct.unpack("<9f", data[off : off + 36])
        tris[i, 0] = vs[0:3]
        tris[i, 1] = vs[3:6]
        tris[i, 2] = vs[6:9]
    return tris


def stl_volume(tris: np.ndarray) -> float:
    """Signed-tetra volume sum, absolute value. Works on any closed
    mesh regardless of winding."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    return float(abs(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0))


def stl_bbox(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Axis-aligned bounding box as (min[3], max[3])."""
    flat = tris.reshape(-1, 3)
    return flat.min(axis=0), flat.max(axis=0)


# ---- fastcad side -------------------------------------------------------


@dataclass(frozen=True)
class Geom:
    """Single solid: volume + axis-aligned bbox. Comparable between
    fastcad and OpenSCAD via element-wise tolerances."""
    volume: float
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]

    @classmethod
    def from_stl(cls, path: Path) -> "Geom":
        tris = stl_triangles(path)
        lo, hi = stl_bbox(tris)
        return cls(
            volume=stl_volume(tris),
            bbox_min=(float(lo[0]), float(lo[1]), float(lo[2])),
            bbox_max=(float(hi[0]), float(hi[1]), float(hi[2])),
        )

    @classmethod
    def from_fastcad(cls, src: str) -> "Geom":
        """Evaluate `src` through fastcad and reduce all top-level
        nodes to a single bbox + summed volume. For most fixtures
        there's exactly one top-level node; for multi-node fixtures
        we union the metrics so the comparison still works."""
        session = SessionState()
        session.set_source(src)
        if not session.cache:
            raise ValueError("fastcad produced no geometry")
        total_vol = 0.0
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for me in session.cache.values():
            if me.manifold is None or me.bbox is None:
                continue
            total_vol += k.volume(me.manifold)
            lo[0] = min(lo[0], me.bbox.xmin)
            lo[1] = min(lo[1], me.bbox.ymin)
            lo[2] = min(lo[2], me.bbox.zmin)
            hi[0] = max(hi[0], me.bbox.xmax)
            hi[1] = max(hi[1], me.bbox.ymax)
            hi[2] = max(hi[2], me.bbox.zmax)
        if total_vol == 0.0 and lo[0] == float("inf"):
            raise ValueError("fastcad produced no geometry with manifolds")
        return cls(
            volume=total_vol,
            bbox_min=(lo[0], lo[1], lo[2]),
            bbox_max=(hi[0], hi[1], hi[2]),
        )


# ---- comparison ---------------------------------------------------------


@dataclass(frozen=True)
class Tolerance:
    """Per-fixture tolerance knobs. Defaults pass for everything
    that's a clean primitive-and-CSG composition; curved surfaces
    discretised at $fn=64 also stay within the volume bound."""
    volume_rel: float = 0.005   # 0.5% relative
    volume_abs: float = 1e-3    # absolute floor for very small parts
    bbox_abs: float = 0.01      # 0.01 mm per coordinate


def compare(fc: Geom, os_: Geom, tol: Tolerance) -> list[str]:
    """Return a list of human-readable mismatches; empty list = OK."""
    out: list[str] = []
    denom = max(abs(os_.volume), tol.volume_abs)
    vrel = abs(fc.volume - os_.volume) / denom
    if vrel > tol.volume_rel:
        out.append(
            f"volume differs: fastcad={fc.volume:.4f} "
            f"openscad={os_.volume:.4f} relative={vrel:.4%}"
        )
    for axis, i in (("x", 0), ("y", 1), ("z", 2)):
        if abs(fc.bbox_min[i] - os_.bbox_min[i]) > tol.bbox_abs:
            out.append(
                f"bbox.{axis}.min differs: "
                f"fastcad={fc.bbox_min[i]:.4f} "
                f"openscad={os_.bbox_min[i]:.4f}"
            )
        if abs(fc.bbox_max[i] - os_.bbox_max[i]) > tol.bbox_abs:
            out.append(
                f"bbox.{axis}.max differs: "
                f"fastcad={fc.bbox_max[i]:.4f} "
                f"openscad={os_.bbox_max[i]:.4f}"
            )
    return out
