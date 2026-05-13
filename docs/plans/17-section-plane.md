# 17 — Optional X/Y/Z section plane in the viewer

GitHub issue: https://github.com/adi-lumenorbit/fastcad/issues/17

## Problem

The viewer renders the outside of the geometry. For nested designs
(housings, bearings, anything with internal cavities) you can't see
inside without re-rendering with a different camera or modifying the
spec — both heavyweight relative to the act of just *peeking*. A
viewer-side section plane gives a frictionless, non-destructive way to
inspect interior geometry.

## Affected Components

| Component | Status | Notes |
|-----------|--------|-------|
| `web/index.html` | MISS | Four buttons (`Cut X` / `Cut Y` / `Cut Z` / `Off`) in `#viewer-overlay`. |
| `web/main.js` | MISS | Renderer flag, clipping plane state, plane visualization, TransformControls, scene_init/delta hookup. |
| `web/style.css` | MISS | Small "active" highlight on the chosen axis button. |
| `web/vendor/` | MISS | Vendor `TransformControls.js` (and its dep `Box.js` / pointer events helpers) via `scripts/fetch-vendor.sh`. |
| `tests/e2e/test_section_plane.py` | MISS | Smoke: load a cube, click Cut Z, assert renderer has 1 clipping plane + visualization mesh in scene; click Off, both cleared. |

No server changes — the spec / op model / render pipeline are
untouched. This is purely a viewer affordance.

## Fix

### 1. Renderer / state

In `web/main.js`:

- `renderer.localClippingEnabled = true` at construction time.
- Module-scope state: `let sectionAxis = null;`,
  `const sectionPlane = new THREE.Plane(new THREE.Vector3(0,0,-1), 0);`,
  one visualization mesh + one TransformControls instance, both
  lazily created.
- A helper `applyClippingTo(mesh)` sets
  `mesh.material.clippingPlanes = sectionAxis ? [sectionPlane] : []`
  and `mesh.material.clipShadows = true`. Called from the existing
  per-mesh code in `applySceneInit` / `applySceneDelta` so new meshes
  inherit the current section.
- When the user clicks an axis button:
  - If that axis is already active, treat as Off.
  - Otherwise set `sectionAxis = "x"|"y"|"z"`, point `sectionPlane.normal`
    at `-x̂` / `-ŷ` / `-ẑ`, set `sectionPlane.constant` to the
    scene's bounding-box midpoint on that axis (so the cut starts in
    the middle of the geometry), apply to all meshes, show + position
    the visualization plane.
- When Off: `sectionAxis = null`, clipping arrays emptied on every
  mesh, visualization plane + TransformControls hidden.

### 2. Visualization plane

A `THREE.Mesh` of `PlaneGeometry`, sized to the scene bounding box
expanded by ~20%. `THREE.DoubleSide` so it shows from both camera
sides. Material: `MeshBasicMaterial` with `transparent: true,
opacity: 0.12, depthWrite: false, color: <axis color>` — red for X,
green for Y, blue for Z (matches the axes helper convention).

Position: `sectionPlane.coplanarPoint()`. Orientation: rotate so the
plane's local +Z matches `sectionPlane.normal`.

### 3. Drag handle (TransformControls)

`TransformControls` is the standard three.js gizmo for translating
objects. `controls.setMode("translate")`, `controls.showX/Y/Z` toggled
to the active axis only — the user can only drag along the section's
normal. On `change`, update `sectionPlane.constant` from the
visualization plane's world position and re-apply to materials (they
share the same `Plane` object so it's automatic).

`dragging-changed` toggles `OrbitControls.enabled` so the camera
doesn't move during a section drag.

### 4. Vendoring TransformControls

`scripts/fetch-vendor.sh` needs to download
`TransformControls.js` (and its util `Box.js` if not already inlined)
from the same three.js version that `three.module.js` was fetched
from. Pin the version that matches existing vendor.

Add the importmap entry in `index.html`:
`"three/addons/controls/TransformControls.js": "/vendor/TransformControls.js"`.

### 5. Hotkeys

Add to the existing `keydown` listener in `main.js`:

| Key | Action |
|-----|--------|
| `1` | toggle Cut X |
| `2` | toggle Cut Y |
| `3` | toggle Cut Z |
| `0` | section off |

Same guard as the existing `h` binding: ignore when the focused element
is a form input.

## Tests

- `tests/e2e/test_section_plane.py`:
  - Load a 20 mm cube via the existing prompt path.
  - Click `data-testid="section-x-btn"`. Assert
    `window.fastcad.sectionAxis === "x"`, scene contains the
    `section-plane-viz` mesh, every cube mesh's material has
    `clippingPlanes.length === 1`.
  - Click `section-off-btn`. Assert
    `window.fastcad.sectionAxis === null`, clipping arrays empty.

Manual verification on the bearing model: section the assembly along Z,
drag the plane up and down to confirm the rollers / disks / housing
all clip at the same plane.

## Acceptance Criteria

- [ ] Buttons visible in toolbar.
- [ ] Cut X / Y / Z each cut the cube cleanly at the midplane on the
      first click.
- [ ] Drag handle along the axis normal; geometry re-clips live.
- [ ] OrbitControls don't move while dragging the gizmo.
- [ ] Off button clears clipping and removes the visualization plane.
- [ ] Hotkeys 1 / 2 / 3 / 0 work when the canvas has focus.
- [ ] Newly-added meshes inherit the active section.
- [ ] E2E test passes.

## Push / merge

- Branch: `feat/section-plane` off `main`.
- Single PR titled `17 — Optional X/Y/Z section plane in the viewer`.
- Squash-merge to `main`.

## Verification

After merge:

1. `git pull && bash scripts/fetch-vendor.sh && bash scripts/dev.sh`.
2. Prompt: "make a 20 mm cube".
3. Click **Cut Z** — cube splits at z ≈ 10.
4. Drag the visualization plane up and down — the cut follows.
5. Open `~/src/3d-models/tightening_bearing.scad`, **Cut Z** again,
   drag through the assembly to verify the internal rollers and rod
   ends are visible at the cut.
