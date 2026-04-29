"""Thin manifold3d wrapper.

The only place in the codebase that imports manifold3d. Everything else
treats `Manifold` as an opaque token. Swapping kernels later is local to
this file.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Sequence

import manifold3d as _m
import numpy as np


Manifold = _m.Manifold


def cube(size: Sequence[float], center: bool = False) -> Manifold:
    sx, sy, sz = float(size[0]), float(size[1]), float(size[2])
    if sx <= 0 or sy <= 0 or sz <= 0:
        raise ValueError(f"cube size must be positive, got {size!r}")
    return _m.Manifold.cube([sx, sy, sz], center)


def sphere(radius: float, segments: int = 32) -> Manifold:
    if radius <= 0:
        raise ValueError(f"sphere radius must be positive, got {radius!r}")
    return _m.Manifold.sphere(float(radius), int(segments))


def cylinder(height: float, radius: float, segments: int = 32, center: bool = False) -> Manifold:
    if height <= 0 or radius <= 0:
        raise ValueError(f"cylinder needs positive height and radius, got {height!r}, {radius!r}")
    return _m.Manifold.cylinder(float(height), float(radius), -1.0, int(segments), center)


def translate(m: Manifold, offset: Sequence[float]) -> Manifold:
    return m.translate([float(offset[0]), float(offset[1]), float(offset[2])])


def union(a: Manifold, b: Manifold) -> Manifold:
    return a + b


def difference(a: Manifold, b: Manifold) -> Manifold:
    return a - b


@dataclass(frozen=True)
class BBox:
    """Axis-aligned bounding box. Coords in millimeters."""
    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    @classmethod
    def from_manifold(cls, m: Manifold) -> "BBox":
        xmn, ymn, zmn, xmx, ymx, zmx = m.bounding_box()
        return cls(xmn, ymn, zmn, xmx, ymx, zmx)

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            0.5 * (self.xmin + self.xmax),
            0.5 * (self.ymin + self.ymax),
            0.5 * (self.zmin + self.zmax),
        )

    @property
    def size(self) -> tuple[float, float, float]:
        return (self.xmax - self.xmin, self.ymax - self.ymin, self.zmax - self.zmin)


def volume(m: Manifold) -> float:
    return float(m.volume())


def to_mesh_dict(m: Manifold) -> dict:
    """Serialize a Manifold to a transport-friendly dict.

    positions: base64 of float32 [N*3] (xyz interleaved)
    indices:   base64 of uint32 [M*3]
    """
    mesh = m.to_mesh()
    verts = np.asarray(mesh.vert_properties, dtype=np.float32)
    # vert_properties may carry extra channels (normals, colors); we only want xyz.
    if verts.ndim != 2 or verts.shape[1] < 3:
        raise RuntimeError(f"unexpected vert_properties shape: {verts.shape}")
    pos = np.ascontiguousarray(verts[:, :3], dtype=np.float32)
    tris = np.ascontiguousarray(np.asarray(mesh.tri_verts), dtype=np.uint32)
    return {
        "positions_b64": base64.b64encode(pos.tobytes()).decode("ascii"),
        "indices_b64": base64.b64encode(tris.tobytes()).decode("ascii"),
        "vertex_count": int(pos.shape[0]),
        "triangle_count": int(tris.shape[0]),
    }
