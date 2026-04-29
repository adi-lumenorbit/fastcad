"""SceneGraph: stable-id nodes, anchor resolution, op application.

Each Node owns its cached Manifold + a record of how it was built. Anchors
in v0 are derived from the node's axis-aligned bounding box (top/bottom/
center). The richer face/edge anchor model fits the same surface — add new
anchor names here and tools.py without changing the rest of the system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import kernel as k
from .ops import (
    AddPrimitive,
    Anchor,
    Boolean,
    ChangeSet,
    Op,
)


@dataclass
class Node:
    id: str
    kind: str               # "cube" | "sphere" | "cylinder" | "boolean:union" | "boolean:difference"
    params: dict            # primitive params; for booleans: {"target": id, "with": id}
    manifold: k.Manifold
    # For UI/debugging — last reason it changed.
    history: list[str] = field(default_factory=list)


def _primitive_manifold(kind: str, params: dict) -> k.Manifold:
    if kind == "cube":
        return k.cube(params["size"])
    if kind == "sphere":
        return k.sphere(params["radius"], params.get("segments", 32))
    if kind == "cylinder":
        return k.cylinder(params["height"], params["radius"], params.get("segments", 32))
    raise ValueError(f"unknown primitive kind: {kind!r}")


def resolve_anchor(node: Node, anchor: Anchor) -> tuple[float, float, float]:
    """Return the world-space point on `node` named by `anchor`."""
    bb = k.BBox.from_manifold(node.manifold)
    cx, cy, cz = bb.center
    if anchor == "origin":
        return (0.0, 0.0, 0.0)
    if anchor == "center":
        return (cx, cy, cz)
    if anchor == "top":
        return (cx, cy, bb.zmax)
    if anchor == "bottom":
        return (cx, cy, bb.zmin)
    raise ValueError(f"unknown anchor: {anchor!r}")


def _primitive_self_center(kind: str, params: dict) -> tuple[float, float, float]:
    """Where the primitive's center lands when built at its natural origin."""
    if kind == "cube":
        sx, sy, sz = params["size"]
        return (sx / 2.0, sy / 2.0, sz / 2.0)
    if kind == "sphere":
        return (0.0, 0.0, 0.0)
    if kind == "cylinder":
        return (0.0, 0.0, params["height"] / 2.0)
    raise ValueError(f"unknown primitive kind: {kind!r}")


@dataclass
class SceneGraph:
    nodes: dict[str, Node] = field(default_factory=dict)
    # Insertion order for deterministic .scad emission.
    order: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.nodes)

    def ids(self) -> Iterable[str]:
        return iter(self.order)

    def reset(self) -> None:
        self.nodes.clear()
        self.order.clear()

    def apply(self, op: Op) -> ChangeSet:
        if isinstance(op, AddPrimitive):
            return self._apply_add(op)
        if isinstance(op, Boolean):
            return self._apply_boolean(op)
        raise TypeError(f"unknown op: {type(op).__name__}")

    def _apply_add(self, op: AddPrimitive) -> ChangeSet:
        if op.node_id in self.nodes:
            raise ValueError(f"node id already exists: {op.node_id!r}")
        m = _primitive_manifold(op.kind, op.params)
        if op.anchor_to is not None:
            target = self.nodes.get(op.anchor_to)
            if target is None:
                raise ValueError(f"anchor_to refers to unknown node: {op.anchor_to!r}")
            anchor_pt = resolve_anchor(target, op.anchor)
            self_center = _primitive_self_center(op.kind, op.params)
            offset = (
                anchor_pt[0] - self_center[0] + op.offset[0],
                anchor_pt[1] - self_center[1] + op.offset[1],
                anchor_pt[2] - self_center[2] + op.offset[2],
            )
            m = k.translate(m, offset)
        elif op.offset != (0.0, 0.0, 0.0):
            m = k.translate(m, op.offset)
        node = Node(
            id=op.node_id,
            kind=op.kind,
            params={
                **op.params,
                "_anchor_to": op.anchor_to,
                "_anchor": op.anchor,
                "_offset": list(op.offset),
            },
            manifold=m,
            history=[f"add {op.kind} anchor={op.anchor} on={op.anchor_to}"],
        )
        self.nodes[op.node_id] = node
        self.order.append(op.node_id)
        return ChangeSet(added=[op.node_id])

    def _apply_boolean(self, op: Boolean) -> ChangeSet:
        target = self.nodes.get(op.target_id)
        with_node = self.nodes.get(op.with_id)
        if target is None:
            raise ValueError(f"boolean target unknown: {op.target_id!r}")
        if with_node is None:
            raise ValueError(f"boolean with unknown: {op.with_id!r}")
        if op.kind == "union":
            target.manifold = k.union(target.manifold, with_node.manifold)
        elif op.kind == "difference":
            target.manifold = k.difference(target.manifold, with_node.manifold)
        else:
            raise ValueError(f"unknown boolean kind: {op.kind!r}")
        target.kind = f"boolean:{op.kind}"
        target.params = {**target.params, "_last_with": op.with_id}
        target.history.append(f"{op.kind} with {op.with_id}")
        cs = ChangeSet(updated=[op.target_id])
        if op.consume_with:
            del self.nodes[op.with_id]
            self.order.remove(op.with_id)
            cs.removed.append(op.with_id)
        return cs

    def describe_for_agent(self) -> list[dict]:
        """Lightweight summary the LLM can use to reason about references."""
        out: list[dict] = []
        for nid in self.order:
            node = self.nodes[nid]
            bb = k.BBox.from_manifold(node.manifold)
            out.append(
                {
                    "id": nid,
                    "kind": node.kind,
                    "bbox": {
                        "min": [bb.xmin, bb.ymin, bb.zmin],
                        "max": [bb.xmax, bb.ymax, bb.zmax],
                    },
                    "center": list(bb.center),
                    "size": list(bb.size),
                }
            )
        return out
