"""Op dataclasses + ChangeSet.

Ops are immutable. Applying an op to a SceneGraph returns a ChangeSet
describing which node ids were added/updated/removed; the server uses
this to send minimal scene_delta messages and only the listed meshes
are re-transferred to the browser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PrimitiveKind = Literal["cube", "sphere", "cylinder"]
BooleanKind = Literal["union", "difference"]
Anchor = Literal["origin", "top", "bottom", "center"]


@dataclass(frozen=True)
class AddPrimitive:
    """Add a primitive, optionally anchored to an existing node.

    If `anchor_to` is None the primitive lands at its natural origin.
    Otherwise we resolve the anchor on the target node's bbox and place
    the primitive's *center* there, then apply `offset`.
    """
    kind: PrimitiveKind
    params: dict
    node_id: str
    anchor_to: str | None = None
    anchor: Anchor = "origin"
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Boolean:
    """Replace `target_id`'s manifold with target OP with."""
    kind: BooleanKind
    target_id: str
    with_id: str
    # When True, the `with_id` node is consumed (removed) by the boolean.
    consume_with: bool = True


Op = AddPrimitive | Boolean


@dataclass
class ChangeSet:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def merge(self, other: "ChangeSet") -> None:
        for nid in other.added:
            if nid not in self.added:
                self.added.append(nid)
        for nid in other.updated:
            if nid not in self.updated and nid not in self.added:
                self.updated.append(nid)
        for nid in other.removed:
            if nid not in self.removed:
                self.removed.append(nid)
            if nid in self.added:
                self.added.remove(nid)
            if nid in self.updated:
                self.updated.remove(nid)
