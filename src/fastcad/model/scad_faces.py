"""Semantic-name face publisher.

Each top-level module call publishes named faces (`+Z`, `-Z`, `+X`, …)
derived from its construction kind, not from manifold3d face_id
tracking. The point/normal returned for each face is computed from the
final manifold's bbox — good enough for anchoring follow-up parts in v1.

A future Stage-2 sharpening will replace this with manifold3d's
`face_id` so faces survive booleans without disappearing.
"""
from __future__ import annotations

from typing import Any

from . import kernel as k
from .scad_parser import ModuleCall, Stmt


def faces_for(stmt: Stmt, bbox: k.BBox, env: Any) -> dict[str, "FacePoint"]:
    """Return the named-face dict for a top-level statement.

    The face dict maps face name → (point, normal). The point is on the
    face; the normal points outward.
    """
    from .scad_eval import FacePoint  # late import (cycle)

    if not isinstance(stmt, ModuleCall):
        return _bbox_faces(bbox, FacePoint)

    name = stmt.callable
    # User-defined modules: walk into their body to figure out the
    # primary geometric kind. For simplicity in v1, we always emit
    # bbox-based ±X/±Y/±Z faces for any 3D result. The +Z / -Z faces
    # match what an extruded module would publish. The lateral face is
    # represented by +X (a point on the +X face of the bbox).
    return _bbox_faces(bbox, FacePoint)


def _bbox_faces(bb: k.BBox, FacePoint) -> dict[str, "FacePoint"]:
    cx, cy, cz = bb.center
    return {
        "+X": FacePoint(point=(bb.xmax, cy, cz), normal=(1.0, 0.0, 0.0)),
        "-X": FacePoint(point=(bb.xmin, cy, cz), normal=(-1.0, 0.0, 0.0)),
        "+Y": FacePoint(point=(cx, bb.ymax, cz), normal=(0.0, 1.0, 0.0)),
        "-Y": FacePoint(point=(cx, bb.ymin, cz), normal=(0.0, -1.0, 0.0)),
        "+Z": FacePoint(point=(cx, cy, bb.zmax), normal=(0.0, 0.0, 1.0)),
        "-Z": FacePoint(point=(cx, cy, bb.zmin), normal=(0.0, 0.0, -1.0)),
    }


__all__ = ["faces_for"]
