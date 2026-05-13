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


def cylinder(
    height: float,
    radius: float,
    segments: int = 32,
    center: bool = False,
    radius_top: float | None = None,
) -> Manifold:
    """Cylinder or truncated cone. `radius` is the bottom radius;
    `radius_top` overrides the top radius for cone / frustum shapes
    (OpenSCAD's `r1` / `r2`). Either radius may be zero (apex), but
    not both."""
    if height <= 0:
        raise ValueError(f"cylinder needs positive height, got {height!r}")
    top = radius if radius_top is None else float(radius_top)
    if radius < 0 or top < 0:
        raise ValueError(f"cylinder radii must be non-negative, got {radius!r}, {top!r}")
    if radius == 0 and top == 0:
        raise ValueError("cylinder needs at least one positive radius")
    return _m.Manifold.cylinder(float(height), float(radius), float(top), int(segments), center)


def translate(m: Manifold, offset: Sequence[float]) -> Manifold:
    return m.translate([float(offset[0]), float(offset[1]), float(offset[2])])


def union(a: Manifold, b: Manifold) -> Manifold:
    return a + b


def difference(a: Manifold, b: Manifold) -> Manifold:
    return a - b


def intersection(a: Manifold, b: Manifold) -> Manifold:
    return a ^ b


# ---------------------------------------------------------------------------
# Sketches → manifolds. Thin wrappers around manifold3d.CrossSection and
# Manifold.extrude / revolve. The "polygons" argument is a list of contours
# (each contour is a list of (x, y) tuples). The first contour is the outer
# boundary (CCW); subsequent contours are holes (CW).
# ---------------------------------------------------------------------------


def _to_cross_section(polygons: Sequence[Sequence[Sequence[float]]]) -> "_m.CrossSection":
    if not polygons:
        raise ValueError("at least one polygon contour required")
    contours = [
        [(float(p[0]), float(p[1])) for p in contour]
        for contour in polygons
    ]
    for i, c in enumerate(contours):
        if len(c) < 3:
            raise ValueError(f"polygon contour {i} has fewer than 3 vertices")
    return _m.CrossSection(contours)


def extrude_polygon(
    polygons: Sequence[Sequence[Sequence[float]]],
    height: float,
    twist_deg: float = 0.0,
    n_divisions: int = 0,
    scale_top: Sequence[float] = (1.0, 1.0),
) -> Manifold:
    """Linear extrude a 2D polygon (or polygon-with-holes) into 3D.

    `n_divisions` is the number of intermediate cross-sections used when
    `twist_deg` is non-zero (manifold3d wires this through). With twist=0
    a single section is enough.
    """
    if height <= 0:
        raise ValueError(f"extrude height must be positive, got {height!r}")
    cs = _to_cross_section(polygons)
    # OpenSCAD twists clockwise looking from below toward +Z (positive
    # twist rotates the bottom edge to the right). manifold3d's
    # extrude rotates the other way — empirically the X/Y bboxes of
    # a non-symmetric twisted section come out mirrored. Negate the
    # angle here so the .scad-spec semantics match what the user sees
    # in OpenSCAD's viewer.
    return _m.Manifold.extrude(
        cs,
        float(height),
        int(n_divisions),
        -float(twist_deg),
        (float(scale_top[0]), float(scale_top[1])),
    )


def revolve_polygon(
    polygons: Sequence[Sequence[Sequence[float]]],
    segments: int = 64,
    revolve_deg: float = 360.0,
) -> Manifold:
    """Revolve a 2D polygon around the Y axis (manifold3d convention).

    Profiles must lie at x ≥ 0 (negative x produces undefined geometry,
    matching OpenSCAD's `rotate_extrude`).
    """
    cs = _to_cross_section(polygons)
    return _m.Manifold.revolve(cs, int(segments), float(revolve_deg))


def polyhedron_from_mesh(
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
) -> Manifold:
    """Construct a Manifold directly from raw vertex + face data.

    Faces of more than 3 vertices are fan-triangulated. Triangles are
    expected to be CCW when viewed from outside the solid; manifold3d
    will surface a `status` warning if the result is not closed.
    """
    if not vertices:
        raise ValueError("polyhedron requires at least one vertex")
    if not faces:
        raise ValueError("polyhedron requires at least one face")
    verts = np.asarray(vertices, dtype=np.float32)
    if verts.ndim != 2 or verts.shape[1] != 3:
        raise ValueError(f"polyhedron vertices must be (N, 3), got {verts.shape}")
    tris: list[tuple[int, int, int]] = []
    for face in faces:
        if len(face) < 3:
            raise ValueError(f"polyhedron face has fewer than 3 vertices: {face!r}")
        if len(face) == 3:
            tris.append((int(face[0]), int(face[1]), int(face[2])))
        else:
            # Fan triangulation around the first vertex.
            first = int(face[0])
            for i in range(1, len(face) - 1):
                tris.append((first, int(face[i]), int(face[i + 1])))
    tri_arr = np.asarray(tris, dtype=np.uint32)
    mesh = _m.Mesh(vert_properties=verts, tri_verts=tri_arr)
    return _m.Manifold(mesh)


def apply_transform(
    m: Manifold,
    translate_v: Sequence[float] | None = None,
    rotate_xyz_deg: Sequence[float] | None = None,
    scale_v: Sequence[float] | None = None,
    mirror_axis: str | None = None,
) -> Manifold:
    """Compose transforms in the standard CAD order:
    scale → mirror → rotate → translate.

    Each argument is optional; if all are None the manifold is
    returned unchanged.
    """
    out = m
    if scale_v is not None:
        out = out.scale([float(scale_v[0]), float(scale_v[1]), float(scale_v[2])])
    if mirror_axis is not None:
        normal = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}.get(mirror_axis.lower())
        if normal is None:
            raise ValueError(f"mirror_axis must be one of 'x', 'y', 'z'; got {mirror_axis!r}")
        out = out.mirror(normal)
    if rotate_xyz_deg is not None:
        out = out.rotate(
            [float(rotate_xyz_deg[0]), float(rotate_xyz_deg[1]), float(rotate_xyz_deg[2])]
        )
    if translate_v is not None:
        out = out.translate(
            [float(translate_v[0]), float(translate_v[1]), float(translate_v[2])]
        )
    return out


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
