# 02 — Stage 1: `.scad`-as-spec

GitHub issue: https://github.com/adi-lumenorbit/fastcad/issues/2

## Problem

Today the agent calls `add_primitive` / `boolean` and the backend
maintains an op log that must round-trip into `.scad` for export.
Two formats. Edits like "make it 25 mm long" regenerate from scratch.
Anchors are bbox heuristics. The agent has to call `list_scene` every
turn to recover state.

The fix isn't more primitives — it's collapsing the spec and the
export into one format the agent already speaks fluently: OpenSCAD.

## Context — three tries, what changed

**Try 1 (rejected): more primitives.** Add `extrude_polygon`,
`revolve_polygon`, `polyhedron`, `transform`. Optimised the symptom
("M3 screw") not the system. Op log + bbox anchors stay; same
ceiling.

**Try 2 (rejected as too verbose): JSON spec + patches.**
Replace the op log with a JSON `Spec` — params + named features +
`@ref`s — that the agent edits via `apply_patch`. The model becomes
declarative, edits become first-class, identity becomes semantic.
The core insight is right but the *encoding* is wrong: JSON is a
wire format, not a thinking format. Six features in M3-screw → ~30
lines of `{"name", "kind", "args", "@ref"}` ceremony for what
should be three lines of CAD code.

**Try 3 (this plan): the spec is `.scad` source.** Same insight as
try 2 — declarative model, agent rewrites it, system re-derives —
but the encoding *is the export format*. There is exactly one
representation of the model: a `.scad` source string. It is what the
agent reads, what the agent writes, what the export returns, and
what reads back when the user opens the file in real OpenSCAD. The
only translation in the system is `text → AST → manifold`, which is
just evaluation.

The current `CLAUDE.md` invariant says ".scad is export-only — never
used as input." That exists because there is no parser. Building one
inverts the invariant; the new invariant becomes "spec source IS the
.scad export." Single source of truth.

## What this looks like for the agent and the user

The system prompt (cached) embeds the current `.scad` source verbatim
each turn. The user prompts; the agent responds with `set_source(<new
.scad>)`. The backend parses, AST-diffs against the previous source,
re-evaluates only modules whose definitions or transitive dependencies
changed, sends a `scene_delta` for affected nodes.

For "Design an M3 screw, 20 mm long":

```scad
// fastcad spec — one file, no external libraries.
diameter = 3;
length   = 20;
pitch    = 0.5;
$fn      = 64;

module thread_section(major, minor) {
  difference() {
    circle(d = major);
    for (k = [0:11])
      rotate([0, 0, k * 30])
        translate([minor / 2, 0, 0])
          polygon([[0, -0.15], [0.4, 0], [0, 0.15]]);
  }
}

module shaft() {
  linear_extrude(height = length, twist = 360 * length / pitch)
    thread_section(major = diameter, minor = diameter * 0.85);
}

module head() {
  translate([0, 0, length])
    linear_extrude(height = 2)
      circle(d = diameter * 1.6);
}

module screw() {
  union() { shaft(); head(); }
}

screw();
```

For "Make it 25 mm long" the agent rewrites `length = 25;` and
resends. AST diff sees only that literal changed; `shaft` and `screw`
invalidate, `head` cache-hits.

For "Add a sphere on top of the head" the agent extends the source
with a `head_sphere` module + updated `screw` union. No JSON, no
patches.

## Metadata strategy — "stay a `.scad`"

The temptation is to bolt sidecar metadata onto the spec. Resist it.
Two principles:

1. **Anything the system needs is derived from `.scad` structure**:
   - **Param table**: top-level scalar assignments preceding the
     first `module` definition.
   - **Module call graph**: built by the parser.
   - **Named faces per module**: derived from body kind
     (`linear_extrude` → `+Z`/`-Z`/`lateral`).
   - **Per-id node names**: the module call name at the top level.
   - **The "main scene"**: convention — last top-level statement.
2. **The two minimal annotations we *do* embrace are pre-existing
   OpenSCAD conventions**:
   - One-line `//` docstring above each `module` (agent emits, system
     ignores; pure readability).
   - OpenSCAD Customizer comment syntax for parameters (`// [min:max]`
     etc.) — round-trips through real OpenSCAD untouched.

What we **do not** add: `/* @fastcad-meta { … } */` JSON frontmatter,
`// @main` / `// @anchor` tags, dependency-graph comments, or
author/timestamp metadata in the source. The export file is byte-
equivalent to the spec source.

## Affected Components

| Component | Status | Notes |
|---|---|---|
| `model/scad_parser.py` | NEW | lark grammar + AST dataclasses + transformer |
| `model/scad_eval.py` | NEW | AST → ModuleEval (manifold + bbox + faces + content_hash) |
| `model/scad_faces.py` | NEW | semantic-name face publisher per module call |
| `model/spec_diff.py` | NEW | (prev_src, new_src) → ChangeSet |
| `model/kernel.py` | EXTEND | + extrude_polygon, revolve_polygon, polyhedron_from_mesh, apply_transform |
| `model/ops.py` | DELETE | op-log dataclasses obsolete |
| `model/scene.py` | DELETE | SceneGraph obsolete; eval owns derivation |
| `model/scad.py` | DELETE | emitter obsolete; spec IS the export |
| `session.py` | REWRITE | current_source + undo/redo stacks + cache |
| `server/ws.py` | EDIT | _node_payload reads from session.cache |
| `agent/tools.py` | REWRITE | set_source, validate, read_source, select_face, ask_user |
| `agent/system_prompt.py` | REWRITE | embed source, document subset, worked example |
| `agent/client.py` | EDIT | fake-mode emits set_source; real mode renders source into prompt |
| `pyproject.toml` | EDIT | add lark dep |
| `tests/unit/` | REWRITE | new files for parser/eval/faces/diff/session/tools |
| `tests/e2e/` | EDIT | rewrite existing 5 + add test_spec_edit + test_export_round_trip |
| `web/*` | NO CHANGE | wire format unchanged |

## Subset of OpenSCAD supported in v1

**Top-level**: variable assignment, module definition, module call,
`$fn`, line / block comments.

**Expressions**: numeric literals, vectors, arithmetic
(`+ - * / %` + unary), comparisons, boolean, conditionals (`?:`),
function calls (`sin cos tan asin acos atan atan2 sqrt pow abs min
max floor ceil round len concat`, `PI`), variable references, vector
indexing.

**Statements**: `for (var = [start:end])`, range with step,
list-form `for (var = [list])`, `if/else`, `let`.

**Built-ins delegated to `kernel.py`**:
- 3D: `cube`, `sphere`, `cylinder`, `polyhedron`
- 2D: `circle`, `square`, `polygon`
- Extrude: `linear_extrude(height=, twist=, scale=)`,
  `rotate_extrude(angle=)`
- Transform: `translate`, `rotate`, `scale`, `mirror`
- CSG: `union`, `difference`, `intersection`

**Out of v1** (parser rejects with hint): `function` defs,
`include`/`use`, `import`, `hull`/`minkowski`/`offset`/`projection`,
`text`/`surface`/`color`, `assert`/`echo`, `each`, recursion in
modules.

## Fix

### What stays
`model/kernel.py` (extended), wire format / WS message types,
`web/*`, `server/feedback.py`, `server/app.py`. Single-source-of-
manifold3d invariant preserved.

### What goes
`model/ops.py`, `model/scene.py`, `model/scad.py` deleted; their
`AddPrimitive`/`Boolean`/`SceneGraph`/render-emitter responsibilities
dissolve into the parser + evaluator + diff trio.

### What's new

- **`model/scad_parser.py`** — `lark` grammar (~150 lines) + AST node
  dataclasses (~200 LOC) + transformer. Returns a frozen AST.
- **`model/scad_eval.py`** — walks AST, resolves variables, dispatches
  to kernel, composes for transforms / CSG. Returns
  `dict[node_id → ModuleEval]` where `ModuleEval` carries
  `{manifold, bbox, face_dict, content_hash}`.
- **`model/scad_faces.py`** — per-feature-kind named faces (`+Z`/`-Z`/
  `lateral` for extrude, `±X/±Y/±Z` for cube/sphere, etc.). Semantic
  only — no manifold3d face_id wiring in v1.
- **`model/spec_diff.py`** — parse both sources, walk top-level
  invocation tree, compare per-id content hashes; emit
  added/updated/removed.

### What's changed

```python
# session.py
@dataclass
class SessionState:
    current_source: str
    undo_stack: list[str]
    redo_stack: list[str]
    cache: dict[str, ModuleEval]

    def set_source(self, text: str) -> ChangeSet: ...
    def undo(self) -> ChangeSet: ...
    def redo(self) -> ChangeSet: ...
    def reset(self) -> None: ...
```

`server/ws.py`: `_node_payload(ctx, node_id)` pulls
`session.cache[node_id].manifold` instead of `scene.nodes[node_id].manifold`.
Wire format unchanged.

`agent/tools.py` exports `read_source`, `set_source`, `validate`,
`select_face`, `ask_user`. `add_primitive` / `boolean` / `list_scene`
removed.

`agent/system_prompt.py` rewritten: documents the subset, includes a
worked M3-screw example, embeds the current source verbatim every
turn. Editing rule: "rewrite the whole `.scad` with your change
applied; the system handles incremental rendering."

`agent/client.py`: real mode renders source into the system prompt
before each `messages.create`; fake mode's regex matchers emit
`set_source(text)` instead of `add_primitive` chains.

### Naming convention for transport

- Top-level module call → node id == module name (`screw(); → "screw"`).
- Anonymous top-level expression → synthetic id `_top_<n>`.
- Agent best practice (per system prompt): always wrap visible
  geometry in a named module and call it at the bottom.

### How incremental rebuild works

1. Agent calls `set_source(new_text)`.
2. `scad_parser.parse(new_text)` → new AST.
3. `spec_diff.diff(old_ast, new_ast)`:
   - Per top-level invocation, compute content hash recursively over
     (kind + args + transitive dep hashes).
   - Hash same as cached → cache-hit.
   - Hash differs → invalidate, mark `updated`.
   - New id → `added`. Vanished id → `removed`.
4. For invalidated/new ids: re-evaluate via `scad_eval.evaluate`,
   update cache.
5. ChangeSet → `ws.py`, builds `_node_payload(ctx, nid)` from cache,
   emits `scene_delta`.

For the screw → 25 mm edit: ~10 ms total; `head` cache-hits.

## Existing functions to reuse

- `kernel.cube` / `sphere` / `cylinder` / `translate` / `union` /
  `difference` — eval dispatches.
- `kernel.BBox.from_manifold`, `kernel.to_mesh_dict` — payload.
- `server/ws.py` framing helpers (`_send`, `_summary`, `_emit_turn`)
  — unchanged shape.
- Fake-mode helpers `_extract_mm`, `_NUM`, `_call` — kept; matchers
  rewrite.

## Tests

### Unit (`.venv/bin/pytest tests/unit -q`)

- `test_scad_parser.py` — round-trip on 6 fixtures (M3 screw, hex
  bracket, vase via rotate_extrude, polyhedron tetra, nested
  booleans, `for/if/let`). Error cases: missing semicolons,
  unsupported keyword, unknown function.
- `test_scad_eval.py` — fixture sources → expected manifold volumes
  within tolerance; named faces correct; cycle detection rejects
  recursive modules.
- `test_kernel_new.py` — `extrude_polygon` (unit square, 1 mm³),
  `revolve_polygon` (annulus, known volume), `polyhedron_from_mesh`
  (tetra), `apply_transform(translate)` shifts bbox.
- `test_spec_diff.py` — change literal → only dependents invalidate;
  rename → remove + add; cascade through callers; hash
  determinism.
- `test_session.py` — `set_source` happy path; parse error returns
  error and does NOT mutate; undo/redo restore prior source + cache;
  reset clears.
- `test_agent_tools_v2.py` — `set_source` end-to-end (fake);
  `validate` doesn't mutate; `select_face` returns correct
  point/normal for `linear_extrude` `+Z`.

### E2E (`bash scripts/e2e.sh`)

Existing 5 tests rewritten under new pipeline. Plus:

- `test_spec_edit.py` — "make a 20mm cube" → mesh appears → "make it
  30mm" → same per-id mesh updated, bbox 30³.
- `test_export_round_trip.py` — Export returns
  `session.current_source` byte-for-byte.

### Manual real-Claude verification

```
ANTHROPIC_API_KEY=… bash scripts/dev.sh   # http://localhost:8765/
```

1. "Design an M3 screw, 20 mm long. Do not use any external libraries." → visible threads; valid `.scad` export.
2. "Make it 25 mm long." → single `set_source`; `scene_delta` updates only `shaft` + `screw`; `head` cache-hits.
3. "Add a sphere on top of the head." → `select_face("head", "+Z")` → updated source with `head_sphere` module; sphere on the head's top face.
4. "Make the head hexagonal." → agent rewrites `head` body; only `head` + `screw` invalidate.

Test 4 is the most important — it's the edit no flat-CSG model
handles cleanly.

## Push / merge instructions

1. Issue filed at https://github.com/adi-lumenorbit/fastcad/issues/2
   (this plan).
2. This plan file lives at
   `docs/plans/02-stage1-scad-spec.md`.
3. Add a row to `docs/plans/00-matrix.md` under a new `## Stage 1`
   section.
4. Branch: `stage1-scad-spec` off `bootstrap-fastcad` (current
   integration branch; bootstrap not yet merged to main).
5. Commits, in order (one per phase):
   1. parser + tests + `lark` dep
   2. kernel extensions + tests
   3. evaluator + faces + tests
   4. spec_diff + tests
   5. session rewrite + tests
   6. ws.py adapter
   7. tools rewrite (`set_source`, `validate`, `select_face`,
      `read_source`)
   8. system prompt rewrite
   9. fake-mode rewrite + e2e rewrites + new e2e tests
   10. delete `ops.py` / `scene.py` / `scad.py`; update matrix
6. PR: `gh pr create -R adi-lumenorbit/fastcad --base
   bootstrap-fastcad --title "02 — Stage 1: .scad-as-spec"
   --body-file tmp/pr-body.txt`.
7. Do NOT merge before manual real-Claude tests 1–4 pass; results
   posted as a PR comment.

## Cost / descope

Estimate ~14–18 hours focused work. Descope choices:

- **A. Constrained-syntax minimum.** Drop `for / if / let / ?:` and
  function-calls in v1. ~10–12 hrs. Not recommended (M3 screw needs
  computed thread vertices).
- **B. Stage 1a — parser + evaluator only, no caching.** Always
  full-re-evaluate on `set_source`. ~10–12 hrs. Spec / export / agent
  UX still right. Reasonable if shipping fast matters.
- **C. Full Stage 1 as written.** ~14–18 hrs. Recommended.

User-approved scope: **C (full)**.

## Acceptance Criteria

- [ ] `model/scad_parser.py`, `model/scad_eval.py`,
      `model/scad_faces.py`, `model/spec_diff.py` exist.
- [ ] `model/ops.py`, `model/scene.py`, `model/scad.py` deleted.
- [ ] All new unit tests pass.
- [ ] All e2e tests pass (or skip cleanly without Chromium).
- [ ] `Export` returns `session.current_source` byte-for-byte.
- [ ] Manual real-Claude tests 1–4 pass; results posted as PR
      comment.
- [ ] `tool_log` shows ≤ 1 `set_source` per typical turn (verified
      for tests 1–4).
- [ ] System prompt embedding the spec source verified by reading
      `agent/client.py` real-mode path.
- [ ] `docs/plans/00-matrix.md` row added; PR opened, linked to
      issue.
