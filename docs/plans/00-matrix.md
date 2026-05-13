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
| 04 | [research subsystem](./04-stage2-research.md) | [#4](https://github.com/adi-lumenorbit/fastcad/issues/4) | OK | OK | OK | OK | OK | OK |

## Stage 3 — adversarial design validation

Independent verifier in the loop after `set_source`: cache files gain
an `## Acceptance` schema for symbolic / structural checks (bbox,
volume, slice topology, single-start verification), a separate Claude
Code subagent reviews renders adversarially, and defects flow back as
tool errors so the agent self-corrects in the same turn.

| #  | Item | Issue | validate | render | critic | cache schema | tools | tests |
|----|------|-------|----------|--------|--------|--------------|-------|-------|
| 06 | [adversarial validation](./06-stage3-validation.md) | [#6](https://github.com/adi-lumenorbit/fastcad/issues/6) | OK | OK | OK | OK | OK | OK |

Channels 1 (structural) and 2 (render + vision critic) shipped in
PR #7. Vision rendering uses the OpenSCAD CLI (`openscad -o out.png
--camera ...`) — no `pyrender` / EGL dependency. The vision pass
runs after Channel 1 passes, when `FASTCAD_AUTO_VALIDATE=structural+vision`.

## Stage 4 — open-from-disk + conversation comments

Adds the inverse of `Export .scad`: load an existing `.scad` from a
server-side path into the session as the new spec. Pairs with a
conventions update so the agent emits design history inline as
`fc-meta` / `fc-prompt` / `fc-decision` / `fc-note` comments, making
an opened `.scad` self-describing.

| #  | Item | Issue | ws | web | agent | spec | tests |
|----|------|-------|----|-----|-------|------|-------|
| 15 | [Open .scad button + conversation comments per spec](./15-open-scad.md) | [#15](https://github.com/adi-lumenorbit/fastcad/issues/15) | OK | OK | OK | OK | OK |

## Stage 4.1 — interactive section plane

Viewer-only sectioning along the X / Y / Z axis with a draggable
clipping-plane gizmo. `Cut X` / `Cut Y` / `Cut Z` toolbar buttons +
hotkeys 1/2/3/0. `TransformControls` translates the visualisation
plane along the active axis; `OrbitControls` is paused during the
drag.

| #  | Item | Issue | web | vendor | tests |
|----|------|-------|-----|--------|-------|
| 17 | [Optional X/Y/Z section plane in the viewer](./17-section-plane.md) | [#17](https://github.com/adi-lumenorbit/fastcad/issues/17) | OK | OK | OK |

## Stage 4.2 — section caps + per-object colors

Each cut solid gets its own stencil-based cap quad (per-mesh section
bundle: back-face + front-face stencil shadows sharing the user
mesh's geometry, plus a cap quad masked by `stencil != 0` with
`ReplaceStencilOp` resetting the stencil). Cross-sections render as
solid axis-tinted faces instead of hollow shells. Per-mesh
`MeshStandardMaterial` instances with HSL-hash colours derived from
the node id make multi-module models visually distinct. Three real
bugs were stacked here (no `stencil: true` on the WebGLRenderer
config; `renderer.clearStencil()` being a setter and not a clear;
sectionViz translucent plane painting over the caps in the
transparent pass) — `tests/e2e/test_section_caps.py` locks them
down.

| #  | Item | Issue | web | tests |
|----|------|-------|-----|-------|
| 20 | [Section caps + per-object colors](./20-section-caps-colors.md) | [#20](https://github.com/adi-lumenorbit/fastcad/issues/20) | OK | OK |

## Stage 5 — language subset extensions + equivalence regression net

First slice of the Stage 1.5 roadmap (`hull / minkowski / offset /
function`). Adds `hull()` as a manifold3d-backed builtin, excludes
reserved keywords (`for / if / else / let / module / function / true
/ false / undef`) from module-call names so bare-for-after-transform
parses, and seeds `$preview = false` / `$t = 0.0` so real-world
files that branch on those special vars load.

Companion: `tests/equivalence/` — a fixture-driven suite that runs
each `tests/equivalence/fixtures/*.scad` through both OpenSCAD's
CLI and fastcad's evaluator and asserts the resulting solids agree
on volume + bbox within tolerance. Surfaced four evaluator bugs in
the process (all fixed in the same PR):

1. `rotate(angle, [axis])` axis-angle form was being silently dropped
   to `rotate(angle)` (Z only).
2. `cylinder(h, r1, r2)` ignored `r2` — frustums rendered as full
   cylinders.
3. `translate([x, y])` (2-vector) rejected on 2D children.
4. `linear_extrude(twist=N)` twisted opposite to OpenSCAD (manifold3d
   sign convention).

| #  | Item | Issue | parser | eval | tests |
|----|------|-------|--------|------|-------|
| 22 | [hull() + reserved-keyword + $preview](./22-hull-builtin.md) | [#22](https://github.com/adi-lumenorbit/fastcad/issues/22) | OK | OK | OK |
| 24 | [OpenSCAD equivalence test suite (+ 4 evaluator bug fixes)](./24-equivalence-suite.md) | [#24](https://github.com/adi-lumenorbit/fastcad/issues/24) | OK | OK | OK |

## Roadmap (filed as issues when scoped)

| Title | Notes |
|-------|-------|
| manifold3d face_id tracking through booleans | Replace semantic-name faces with real face-ids that survive CSG. Stage 2 sharpening of Stage 1's face publisher. |
| `minkowski` / `offset` / `function` | Remainder of Stage 1.5 — `hull` shipped in #22. The other three need design work that hull didn't (offset's join styles, minkowski's domain restrictions, function syntax). |
| Real-mesh section caps via manifold3d-wasm | Replace the stencil-based section caps with real triangulated cap polygons computed from a manifold3d-wasm worker. First-class scene nodes that can be exported / picked / measured. Trigger: when `inspect_section` / measurement / export need a real cap mesh. |
| Snapshot-based undo | If the per-feature cache outgrows memory, persist module-level snapshots. |
| Multi-user / save+load | Serialize current_source to disk on connect; per-session storage. |
