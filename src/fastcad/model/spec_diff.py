"""Spec diff layer: compute incremental rebuild deltas from
(new_source, prev_cache) → (ChangeSet, new_cache).

The cache is keyed by top-level node id; each entry carries the
content hash that produced it. On a new source we recompute hashes per
new id, compare against cached hashes, and only re-evaluate the
mismatched (or new) ones. Removed ids fall out by absence.

This is what makes "make it 25 mm" cheap: a one-literal change
invalidates only the dependents that actually reference that literal.
"""
from __future__ import annotations

from typing import Iterable

from .ops import ChangeSet
from .scad_eval import (
    ModuleEval,
    content_hash_for_top_level,
    evaluate_top_level,
    top_level_node_ids,
)
from .scad_parser import Source, parse


def diff_and_evaluate(
    new_source: str | Source,
    prev_cache: dict[str, ModuleEval] | None = None,
) -> tuple[ChangeSet, dict[str, ModuleEval]]:
    """Reconcile a new spec source against the previous cache.

    Returns:
        change_set: lists of added / updated / removed node ids,
            describing what changed for the wire layer.
        new_cache: the post-update cache, with hits copied from
            prev_cache and misses freshly evaluated.
    """
    new_ast: Source = parse(new_source) if isinstance(new_source, str) else new_source
    prev: dict[str, ModuleEval] = prev_cache or {}

    new_ids = top_level_node_ids(new_ast)
    new_id_set = set(new_ids)

    cs = ChangeSet()
    new_cache: dict[str, ModuleEval] = {}
    needs_eval: set[str] = set()

    for nid in new_ids:
        new_hash = content_hash_for_top_level(new_ast, nid)
        prev_eval = prev.get(nid)
        if prev_eval is not None and prev_eval.content_hash == new_hash:
            new_cache[nid] = prev_eval
            continue
        if prev_eval is not None:
            cs.updated.append(nid)
        else:
            cs.added.append(nid)
        needs_eval.add(nid)

    for old_nid in prev:
        if old_nid not in new_id_set:
            cs.removed.append(old_nid)

    if needs_eval:
        fresh = evaluate_top_level(new_ast, needs_eval)
        # If a previously-cached id newly evaluates to "no geometry"
        # (e.g. only 2D), drop it from the cache and treat as removed.
        for nid in needs_eval:
            if nid in fresh:
                new_cache[nid] = fresh[nid]
            else:
                if nid in prev:
                    if nid not in cs.removed:
                        cs.removed.append(nid)
                    if nid in cs.updated:
                        cs.updated.remove(nid)
                else:
                    if nid in cs.added:
                        cs.added.remove(nid)

    return cs, new_cache


def hashes_match(prev_cache: dict[str, ModuleEval], new_source: str | Source) -> bool:
    """Convenience predicate: returns True if every node id in the new
    source has a matching cached content hash and no ids are added or
    removed. Useful for fast-path no-op detection."""
    new_ast = parse(new_source) if isinstance(new_source, str) else new_source
    new_ids = top_level_node_ids(new_ast)
    if set(new_ids) != set(prev_cache):
        return False
    for nid in new_ids:
        if content_hash_for_top_level(new_ast, nid) != prev_cache[nid].content_hash:
            return False
    return True


__all__ = ["diff_and_evaluate", "hashes_match"]
