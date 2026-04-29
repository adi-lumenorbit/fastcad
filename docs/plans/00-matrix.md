# 00 — Convergence matrix

Source of truth for outstanding work. Each row links a plan file
(`docs/plans/NN-slug.md`) and the GitHub issue.

Status legend: **OK** = implemented + tests pass; **PARTIAL** = some
pieces done; **MISS** = not started; **BUG** = known broken; **—** = N/A.

## Bootstrap

| #  | Item | Issue | kernel | scene | session | scad | agent | server | web | feedback | tests |
|----|------|-------|--------|-------|---------|------|-------|--------|-----|----------|-------|
| 01 | [Bootstrap fastcad vertical slice](./01-bootstrap.md) | — | OK | OK | OK | OK | OK | OK | OK | OK | OK |

## Roadmap (filed as issues when scoped)

| Title | Notes |
|-------|-------|
| Face / edge / plane anchors | Replace bbox-only anchors in `scene.resolve_anchor` with named faces from manifold3d. New tools in `agent/tools.py`: `pick_face`, `pick_edge`. |
| Parametric constraints | Persist parametric expressions per node so dimensions can be edited after the fact. |
| Snapshot-based undo | Replace full-replay rebuild in `session._rebuild` with periodic Manifold snapshots for large scenes. |
| Multi-user / save+load | Serialize op log JSON; load from disk on connect; per-session storage. |
| Real-OpenSCAD round-trip import | Parse a subset of `.scad` back into ops for editing imported models. |
