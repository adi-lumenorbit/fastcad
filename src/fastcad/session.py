"""SessionState: append-only op log + head pointer.

Undo/redo move the head; appending while head < len(log) truncates the
tail (standard editor semantics). Reapplying always rebuilds the scene
from op 0 to head — full replay, simple, correct for v0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model.ops import AddPrimitive, Boolean, ChangeSet, Op
from .model.scene import SceneGraph


@dataclass
class SessionState:
    log: list[Op] = field(default_factory=list)
    head: int = 0  # ops 0..head-1 are applied; head points to next.
    scene: SceneGraph = field(default_factory=SceneGraph)
    next_id: int = 1

    def reset(self) -> None:
        self.log.clear()
        self.head = 0
        self.scene.reset()
        self.next_id = 1

    def fresh_id(self, prefix: str) -> str:
        nid = f"{prefix}_{self.next_id}"
        self.next_id += 1
        return nid

    def can_undo(self) -> bool:
        return self.head > 0

    def can_redo(self) -> bool:
        return self.head < len(self.log)

    def append(self, op: Op) -> ChangeSet:
        """Append-and-apply. Truncates any tail past head first."""
        if self.head < len(self.log):
            self.log = self.log[: self.head]
        cs = self.scene.apply(op)
        self.log.append(op)
        self.head += 1
        return cs

    def undo(self) -> ChangeSet:
        if not self.can_undo():
            return ChangeSet()
        self.head -= 1
        return self._rebuild()

    def redo(self) -> ChangeSet:
        if not self.can_redo():
            return ChangeSet()
        self.head += 1
        return self._rebuild()

    def _rebuild(self) -> ChangeSet:
        """Replay ops 0..head from a clean scene; return a delta vs the
        previous frontend state expressed as a wholesale reset.

        For the browser, the simplest contract is: send a `scene_init`
        snapshot listing every current node. This is what the WS layer
        does for undo/redo.
        """
        self.scene.reset()
        # next_id is *not* reset because outgoing ids should stay stable
        # across undo/redo; the new ops we append later get fresh suffixes.
        for op in self.log[: self.head]:
            self.scene.apply(op)
        return ChangeSet(added=list(self.scene.order))


__all__ = ["SessionState", "AddPrimitive", "Boolean", "ChangeSet"]
