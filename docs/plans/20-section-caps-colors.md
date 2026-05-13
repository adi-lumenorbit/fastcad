# 20 — Section caps + per-object colors

GitHub issue: https://github.com/adi-lumenorbit/fastcad/issues/20

## Problem

Two visual deficiencies in the current viewer:

1. **Section cuts look hollow.** The section plane (#17) is implemented
   via `WebGLRenderer.localClippingEnabled` + `material.clippingPlanes`.
   That clips triangles whose vertices lie past the plane, but it
   doesn't fill the resulting cross-section. A closed solid (the
   housing, a roller) cut by the plane appears as an empty shell — the
   user sees through to the back of the same mesh.

2. **Every mesh is the same color.** All meshes share one
   `MeshStandardMaterial` with color `0xc9c1a8`, so the bearing's
   housing, disks, rollers, rods, and cable all render in the same
   tan. Distinguishing parts at a glance is hard.

## Investigation — section capping options

| Approach | What the cap is | Render-loop change | Long-term cost |
|----------|------------------|--------------------|-----------------|
| **A. Stencil capping (this PR)** | A flat region painted in the framebuffer, masked by stencil. Pixel-only — doesn't exist outside rendering. | Multi-pass: for each mesh, back-face stencil increment + front-face decrement + cap-quad masked draw + clearStencil between meshes. ~150 lines of state plumbing. | Every future render-pipeline change (RenderBundles, postprocessing) has to know about it. |
| **B. Real-mesh capping** | An actual triangulated polygon mesh, computed by intersecting the solid with the half-space at the plane. | None — caps are just regular meshes. | Caps are first-class: exportable, pickable, measurable, usable by `inspect_section`. |
| **C. DoubleSide rendering** | Inside surface of each shell visible at the cut. | 1-line material change. | Doesn't actually look capped; lighting is wrong on reversed normals. |
| **D. CPU clipping over the WebSocket** | Real geometry, but server-computed per drag tick. | None client-side; backend round-trip per move. | Latency-bound; not interactive. |

**Picked:** A (stencil capping). It satisfies the immediate visual
need without pulling manifold3d-wasm into the browser bundle. Filed a
follow-up issue (see Push/merge below) to replace it with B when caps
need to become real geometry (export, measurement, picking).

## Affected Components

| Component | Status | Notes |
|-----------|--------|-------|
| `web/main.js` — material setup | MISS | Replace shared `meshMaterial` with per-mesh material in `applyNodeUpdate`; dispose in `removeNode`. |
| `web/main.js` — `frame()` loop | MISS | When `sectionAxis !== null`, multi-pass renderer pipeline: per mesh, stencil increment back + decrement front + cap pass + clearStencil; final color pass. |
| `web/main.js` — section state | MISS | Lazy-built cap quad + stencil materials; reused across frames. |
| `web/style.css` | — | No change. |
| `tests/e2e/test_per_object_colors.py` | MISS | Assert each mesh has a unique color and the colors are stable across runs (deterministic hash). |
| `tests/e2e/test_section_caps.py` | MISS | Assert that with section active, the renderer has run multi-pass (proxy: stencil cap quad mesh is present in scene). |

## Fix

### 1. Per-mesh colors

`applyNodeUpdate` creates a new `MeshStandardMaterial` per mesh:

```js
function colorForId(id) {
  // FNV-1a hash → hue. Muted CAD palette (S=0.4, L=0.55) keeps
  // adjacent hues readable without screaming.
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  const hue = ((h >>> 0) % 360) / 360;
  return new THREE.Color().setHSL(hue, 0.4, 0.55);
}
```

The shared `meshMaterial` constant disappears. `removeNode` disposes
the per-mesh material along with the geometry.

### 2. Stencil capping

State, created once at module load:

```js
// Stencil materials: shared across all meshes, swapped onto the
// mesh.geometry temporarily during stencil passes.
const stencilBackMat = makeStencilMat({ side: BackSide, op: IncrementWrap });
const stencilFrontMat = makeStencilMat({ side: FrontSide, op: DecrementWrap });
// Cap quad: a single full-bbox quad, positioned and oriented per
// frame based on the section plane. Per-axis color.
const capMat = new THREE.MeshBasicMaterial({
  transparent: true, opacity: 0.85,
  stencilWrite: true,
  stencilFunc: NotEqualStencilFunc,
  stencilRef: 0,
});
const capQuad = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), capMat);
```

Render pass per frame, when `sectionAxis !== null`:

```js
renderer.autoClear = false;
renderer.clear();        // color + depth + stencil

// 1. Stencil + cap, per mesh, sequentially.
for (const mesh of meshMap.values()) {
  renderer.clearStencil();
  // Back faces: stencil increment.
  mesh.material = stencilBackMat;
  renderer.render(scene, camera);
  // Front faces: stencil decrement.
  mesh.material = stencilFrontMat;
  renderer.render(scene, camera);
  // Restore the real material so subsequent meshes render themselves
  // correctly during the final pass.
  mesh.material = mesh.userData.material;
  // Cap quad: masked by stencil != 0.
  positionCapQuad(capQuad, sectionPlane, sceneBbox);
  capScene.children = [capQuad];
  renderer.render(capScene, camera);
}

// 2. Final color pass: full scene, normal clipping.
renderer.clearStencil();
renderer.render(scene, camera);

renderer.autoClear = true;
```

(Implementation will hide the material swap by stashing
`mesh.userData.material` and toggling references.)

**Cost:** ~3 draw calls per mesh + 1 final pass. Bearing has 12
top-level meshes → 37 draw calls/frame when section is active. Three.js
hits 60 fps easily.

### 3. Cap color

Cap material color = axis tint (matches visualization plane):
- X → `0xee5555`
- Y → `0x55cc55`
- Z → `0x6699ee`

Cap is the same color across all meshes for a given axis. This was
the user's pick when offered the choice between "axis color" and
"object color" — the axis cue stays useful, and per-object
distinction is preserved by the geometry's own per-object color
visible on uncut surfaces.

## Tests

`tests/e2e/test_per_object_colors.py`:

- Make a cube + sphere. Assert
  `mesh1.material.color !== mesh2.material.color`.
- Reload page, assert each mesh's color is the same as before
  (hash is deterministic).

`tests/e2e/test_section_caps.py`:

- Load a 20 mm cube. Click `Cut Z`. Assert that after a frame, the
  scene contains a cap quad mesh (`name = "section-cap-quad"` or
  similar). Toggle Off, assert it's gone.

The render-correctness of the cap (does it look filled) is a visual
property; we test the structural setup, not the framebuffer pixels.

## Acceptance Criteria

- [ ] Each mesh has a unique color derived from its id.
- [ ] Section cap fills the cross-section of each cut solid as a
      solid-colored face matching the axis tint.
- [ ] Caps update live as the user drags the section gizmo.
- [ ] Per-solid caps: each cut solid gets its own cap region; cap of
      one mesh doesn't bleed into another even when they overlap in
      world space.
- [ ] OrbitControls + TransformControls behavior unchanged.
- [ ] Unit/e2e suite green.

## Push / merge

- Branch: `feat/section-caps-colors` off `main`.
- PR title: `20 — Section caps + per-object colors`.
- Squash-merge to `main`.
- After merge: file follow-up issue `Replace stencil-based section
  capping with real-mesh capping via manifold3d-wasm`. Roadmap row:
  same.

## Verification

After merge:

1. `git pull && bash scripts/dev.sh`.
2. Open `~/src/3d-models/tightening_bearing.scad` via Open .scad.
3. Each part (housing, disks, rollers, rods, cable) renders in a
   distinct color.
4. `Cut Z` → cross-section appears as solid blue-tinted faces over
   each cut solid; drag the gizmo → caps follow.
5. Try X, Y as well.

## Follow-up (filed after merge)

> Replace stencil-based section capping with real-mesh capping via
> manifold3d-wasm worker. Cap meshes become first-class scene nodes,
> the multi-pass render helper deletes, caps become pickable and
> exportable.
