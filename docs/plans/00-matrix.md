# 00 — Convergence matrix

Source of truth for outstanding work. Each row links a plan file
(`docs/plans/NN-slug.md`) and the GitHub issue.

Status legend: **OK** = implemented + tests pass; **PARTIAL** = some
pieces done; **MISS** = not started; **BUG** = known broken; **—** = N/A.

## Bootstrap

| #  | Item | Issue | kernel | scene | session | scad | agent | server | web | feedback | tests |
|----|------|-------|--------|-------|---------|------|-------|--------|-----|----------|-------|
| 01 | [Bootstrap fastcad vertical slice](./01-bootstrap.md) | — | OK | OK | OK | OK | OK | OK | OK | OK | OK |

## Stage 1 — `.scad`-as-spec

Replaces the flat-CSG op log with a `.scad`-source-as-spec model:
single representation end-to-end (the agent reads, rewrites, and
exports the same `.scad` string), backed by a Python parser +
manifold3d evaluator with dependency-aware caching.

| #  | Item | Issue | parser | eval | faces | diff | kernel | session | ws | agent | tests |
|----|------|-------|--------|------|-------|------|--------|---------|----|-------|-------|
| 02 | [.scad-as-spec](./02-stage1-scad-spec.md) | [#2](https://github.com/adi-lumenorbit/fastcad/issues/2) | OK | OK | OK | OK | OK | OK | OK | OK | OK |

## Stage 2 — research subsystem + cache + progress UI

Lets the agent invoke a Claude Code subagent for deep research on
standardized parts, caches results as text files in `docs/research/`
(human-editable, git-tracked), and surfaces real-time progress in a
new chat-pane panel below the chat log.

| #  | Item | Issue | research | tools | ws | web | cache | tests |
|----|------|-------|----------|-------|----|-----|-------|-------|
| 04 | [research subsystem](./04-stage2-research.md) | [#4](https://github.com/adi-lumenorbit/fastcad/issues/4) | MISS | MISS | MISS | MISS | MISS | MISS |

## Roadmap (filed as issues when scoped)

| Title | Notes |
|-------|-------|
| manifold3d face_id tracking through booleans | Replace semantic-name faces with real face-ids that survive CSG. Stage 2 sharpening of Stage 1's face publisher. |
| `function` / `hull` / `minkowski` / `offset` | Stage 1.5 — extend the parser+evaluator with the OpenSCAD subset deferred from Stage 1. |
| `.scad` import (round-trip from disk) | Upload UI; load arbitrary `.scad` files into a fastcad session for editing. |
| Snapshot-based undo | If the per-feature cache outgrows memory, persist module-level snapshots. |
| Multi-user / save+load | Serialize current_source to disk on connect; per-session storage. |
