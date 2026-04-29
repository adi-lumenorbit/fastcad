"""SessionState: the .scad spec source + undo/redo stacks + per-id eval cache.

The single source of truth is `current_source`, a string of OpenSCAD
that is the agent's spec, the system's input to evaluation, and the
file the user exports.

Each `set_source(text)` parses, diffs against the cache, evaluates only
the invalidated/new top-level statements, and returns a ChangeSet for
the wire layer. Undo/redo restore prior sources from the stack and
re-derive the cache the same way (cheap thanks to dependency-aware
caching).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model.scad_eval import ModuleEval
from .model.spec_diff import ChangeSet, diff_and_evaluate


INITIAL_SOURCE = "// fastcad spec — empty scene\n"


@dataclass
class SessionState:
    current_source: str = INITIAL_SOURCE
    undo_stack: list[str] = field(default_factory=list)
    redo_stack: list[str] = field(default_factory=list)
    cache: dict[str, ModuleEval] = field(default_factory=dict)

    def reset(self) -> None:
        self.current_source = INITIAL_SOURCE
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.cache.clear()

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def set_source(self, text: str) -> ChangeSet:
        """Apply a new source. Validates by parsing + evaluating; on
        any error, state is unchanged and the exception propagates to
        the caller (the agent layer surfaces it as a tool-result so
        the agent can self-correct)."""
        cs, new_cache = diff_and_evaluate(text, self.cache)
        # Only mutate after a successful diff_and_evaluate.
        self.undo_stack.append(self.current_source)
        self.redo_stack.clear()
        self.current_source = text
        self.cache = new_cache
        return cs

    def undo(self) -> ChangeSet:
        if not self.undo_stack:
            return ChangeSet()
        prev = self.undo_stack.pop()
        cs, new_cache = diff_and_evaluate(prev, self.cache)
        self.redo_stack.append(self.current_source)
        self.current_source = prev
        self.cache = new_cache
        return cs

    def redo(self) -> ChangeSet:
        if not self.redo_stack:
            return ChangeSet()
        nxt = self.redo_stack.pop()
        cs, new_cache = diff_and_evaluate(nxt, self.cache)
        self.undo_stack.append(self.current_source)
        self.current_source = nxt
        self.cache = new_cache
        return cs


__all__ = ["SessionState", "ChangeSet", "INITIAL_SOURCE"]
