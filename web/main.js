// fastcad — three.js viewer + WS client + chat UI.
// Per-id mesh map: only listed nodes ever change in the scene; every other
// mesh is preserved across deltas. That's what makes rendering "incremental".

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";

// ---------------------------------------------------------------------------
// Vendor sanity-check: if vendor/ is empty we never reach this file (import
// would fail) — index.html shows a hint anyway. We expose a flag for tests.
// ---------------------------------------------------------------------------
window.fastcad = window.fastcad || {};
window.fastcad.ready = false;

// ---------------------------------------------------------------------------
// Three.js setup
// ---------------------------------------------------------------------------

const canvas = document.getElementById("viewer");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
// Enable per-material clipping planes so the section feature can hide
// the half of each mesh that's past the cut. Without this the material
// `clippingPlanes` array is silently ignored.
renderer.localClippingEnabled = true;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x202020);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 5000);
const HOME_POSITION = new THREE.Vector3(80, -80, 60);
const HOME_TARGET = new THREE.Vector3(0, 0, 0);
camera.position.copy(HOME_POSITION);
camera.up.set(0, 0, 1);

const controls = new OrbitControls(camera, canvas);
controls.target.copy(HOME_TARGET);
controls.screenSpacePanning = true;        // CAD-style: pan slides parallel to screen
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.listenToKeyEvents(window);        // arrow keys pan when window has focus
controls.keyPanSpeed = 14;
controls.update();

function recenterCamera() {
  // Auto-frame on visible meshes if any, else go home.
  if (window.fastcad && window.fastcad.meshMap && window.fastcad.meshMap.size > 0) {
    const box = new THREE.Box3();
    for (const m of window.fastcad.meshMap.values()) box.expandByObject(m);
    if (!box.isEmpty()) {
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3()).length() || 50;
      controls.target.copy(center);
      const dir = new THREE.Vector3(1, -1, 0.75).normalize();
      camera.position.copy(center).addScaledVector(dir, size * 1.6);
      camera.up.set(0, 0, 1);
      controls.update();
      return;
    }
  }
  camera.position.copy(HOME_POSITION);
  controls.target.copy(HOME_TARGET);
  camera.up.set(0, 0, 1);
  controls.update();
}

scene.add(new THREE.AmbientLight(0xffffff, 0.45));
const dir = new THREE.DirectionalLight(0xffffff, 0.85);
dir.position.set(60, -60, 100);
scene.add(dir);

// Reference grid + axes
const axesHelper = new THREE.AxesHelper(20);
axesHelper.name = "axes-helper";
scene.add(axesHelper);
const grid = new THREE.GridHelper(200, 20, 0x444444, 0x333333);
grid.rotation.x = Math.PI / 2;
scene.add(grid);

// flatShading: every triangle gets its own normal. This is the right
// default for CAD: sharp edges between cylinder side / top stay sharp;
// thread teeth read as faceted teeth instead of being smoothed into a
// continuous spiral that looks like dust. Curved surfaces look slightly
// faceted but $fn=64 makes that nearly invisible.
//
// Each mesh gets its own material so colors can differ per node. The
// color is a deterministic FNV-1a hash of `node.id` mapped to an HSL
// hue with muted CAD-friendly saturation/lightness — same id always
// renders in the same color across sessions, and adjacent nodes are
// almost always distinguishable.
function colorForId(id) {
  let h = 0x811c9dc5;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  const hue = ((h >>> 0) % 360) / 360;
  return new THREE.Color().setHSL(hue, 0.4, 0.55);
}

function makeMeshMaterial(id) {
  const material = new THREE.MeshStandardMaterial({
    color: colorForId(id),
    metalness: 0.05,
    roughness: 0.65,
    flatShading: true,
  });
  // clipShadows = true so shadow casters are also clipped — otherwise
  // a half-mesh would still cast a full shadow, giving away the half
  // the section hides.
  material.clipShadows = true;
  // Inherit the currently-active section (if any) so meshes added
  // after section activation clip the same way as the pre-existing
  // ones. Updated in lockstep by `setSection`.
  applyCurrentSectionTo(material);
  return material;
}

// Set the clipping planes on a freshly-built material to match the
// section state. Pulled out so `setSection` and `makeMeshMaterial`
// can both call it without duplicating policy.
function applyCurrentSectionTo(material) {
  material.clippingPlanes = sectionAxis === null ? [] : [sectionPlane];
}

// nodeId -> THREE.Mesh
const meshMap = new Map();

// ---------------------------------------------------------------------------
// Section plane — interactive X / Y / Z clipping
// ---------------------------------------------------------------------------
// One THREE.Plane object is shared between the renderer's clipping
// pipeline (via the shared mesh material's `clippingPlanes` array)
// and a TransformControls drag handle. Moving the handle mutates the
// Plane in place, and three.js re-uses the same Plane reference each
// frame so geometry re-clips live without a per-frame material
// rebuild. Color convention matches the AxesHelper: red/green/blue
// for x/y/z.

const SECTION_AXES = {
  x: { normal: new THREE.Vector3(-1, 0, 0), color: 0xee5555 },
  y: { normal: new THREE.Vector3(0, -1, 0), color: 0x55cc55 },
  z: { normal: new THREE.Vector3(0, 0, -1), color: 0x6699ee },
};

let sectionAxis = null;
const sectionPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);

// Translucent quad that visualizes the cut. Sized at activation time
// from the scene bbox. PlaneGeometry's face lives in local XY; we'll
// rotate the quad so its +Z matches the cut normal.
const sectionViz = new THREE.Mesh(
  new THREE.PlaneGeometry(1, 1),
  new THREE.MeshBasicMaterial({
    color: 0xffffff,
    transparent: true,
    opacity: 0.18,
    depthWrite: false,
    side: THREE.DoubleSide,
  })
);
sectionViz.name = "section-plane-viz";
sectionViz.visible = false;
scene.add(sectionViz);

// Outline so the plane reads against the dark grid.
const sectionEdge = new THREE.LineSegments(
  new THREE.EdgesGeometry(sectionViz.geometry),
  new THREE.LineBasicMaterial({ color: 0xffffff })
);
sectionViz.add(sectionEdge);

// TransformControls: translation-only handle along the active axis.
// Dragging fires `change`; we re-derive `sectionPlane.constant` from
// the viz position so the renderer re-clips on the next frame.
const transformControls = new TransformControls(camera, renderer.domElement);
transformControls.setMode("translate");
transformControls.setSize(0.8);
transformControls.visible = false;
transformControls.enabled = false;
scene.add(transformControls);
transformControls.addEventListener("dragging-changed", (ev) => {
  // Freeze the camera during a section drag so the two controls don't
  // fight each other.
  controls.enabled = !ev.value;
});
transformControls.addEventListener("change", () => {
  if (!sectionAxis) return;
  sectionPlane.setFromNormalAndCoplanarPoint(
    sectionPlane.normal,
    sectionViz.position,
  );
});

// ---------------------------------------------------------------------------
// Stencil-buffer section caps
// ---------------------------------------------------------------------------
// When the section plane is active, each cut solid normally renders as
// an empty shell (you see straight through to the back of the same
// mesh). To make cuts read as solid CAD-style cross-sections, we run a
// per-mesh stencil/cap pass before the final color pass:
//
//   For each mesh:
//     1) clear stencil
//     2) render back-faces with stencil += 1 (color/depth writes off)
//     3) render front-faces with stencil -= 1 (color/depth writes off)
//        — after these two passes, stencil != 0 exactly where the
//          camera ray is currently inside this solid (between an
//          unmatched entry and exit), which is exactly the
//          cross-section region we want to fill
//     4) render a single bbox-scale cap quad, masked by stencil != 0,
//        in the axis tint color
//   Then a final pass renders the regular clipped geometry on top.
//
// Long-term, this should be replaced by real-mesh capping computed
// from `manifold3d-wasm` in a worker — the cap would then be a first-
// class scene node that can be exported / picked / measured. See
// follow-up issue. Until then, this stencil approach is the smallest
// way to satisfy the visual need.

const stencilBackMat = new THREE.MeshBasicMaterial({
  side: THREE.BackSide,
  colorWrite: false,
  depthWrite: false,
  stencilWrite: true,
  stencilFunc: THREE.AlwaysStencilFunc,
  stencilFail: THREE.IncrementWrapStencilOp,
  stencilZFail: THREE.IncrementWrapStencilOp,
  stencilZPass: THREE.IncrementWrapStencilOp,
  clippingPlanes: [sectionPlane],
  clipShadows: true,
});
const stencilFrontMat = new THREE.MeshBasicMaterial({
  side: THREE.FrontSide,
  colorWrite: false,
  depthWrite: false,
  stencilWrite: true,
  stencilFunc: THREE.AlwaysStencilFunc,
  stencilFail: THREE.DecrementWrapStencilOp,
  stencilZFail: THREE.DecrementWrapStencilOp,
  stencilZPass: THREE.DecrementWrapStencilOp,
  clippingPlanes: [sectionPlane],
  clipShadows: true,
});

// Cap quad: rendered only where stencil != 0. depthWrite=false so it
// doesn't occlude the kept geometry in the final color pass.
const capQuad = new THREE.Mesh(
  new THREE.PlaneGeometry(1, 1),
  new THREE.MeshBasicMaterial({
    color: 0xffffff,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: 0.92,
    stencilWrite: true,
    stencilRef: 0,
    stencilFunc: THREE.NotEqualStencilFunc,
    stencilFail: THREE.KeepStencilOp,
    stencilZFail: THREE.KeepStencilOp,
    stencilZPass: THREE.KeepStencilOp,
    depthWrite: false,
  }),
);
capQuad.name = "section-cap-quad";
capQuad.frustumCulled = false;

// The cap quad rides in its own scene so the final color pass doesn't
// re-render it. (Mirrors capScene = scene approach but isolates cap
// rendering to one draw call per mesh per frame.)
const capScene = new THREE.Scene();

// Helpers that must be hidden during stencil/cap passes so they don't
// pollute the color buffer. Lights stay in `scene` but they don't
// write fragments so they're safe to leave.
const sectionHelperHideList = [grid, axesHelper, sectionViz, transformControls];

function renderSectionWithCaps() {
  const meshes = [...meshMap.values()];
  if (meshes.length === 0) {
    renderer.render(scene, camera);
    return;
  }

  // Lock the cap quad to the visualization plane every frame; the
  // user may have dragged the gizmo since the last frame.
  capQuad.position.copy(sectionViz.position);
  capQuad.quaternion.copy(sectionViz.quaternion);
  capQuad.scale.copy(sectionViz.scale);
  capQuad.material.color.setHex(SECTION_AXES[sectionAxis].color);
  capScene.children.length = 0;
  capScene.add(capQuad);

  renderer.autoClear = false;
  renderer.clear(true, true, true);  // color + depth + stencil

  // Stash original visibility on user meshes + helpers so we can
  // toggle and restore.
  const savedMeshVis = meshes.map((m) => m.visible);
  const savedHelperVis = sectionHelperHideList.map((h) => h.visible);
  for (const h of sectionHelperHideList) h.visible = false;

  for (let i = 0; i < meshes.length; i++) {
    const target = meshes[i];
    if (!savedMeshVis[i]) continue;  // user hid this mesh; skip
    const realMat = target.material;

    // Only the target mesh contributes to this stencil/cap iteration.
    for (let j = 0; j < meshes.length; j++) {
      meshes[j].visible = j === i && savedMeshVis[j];
    }

    renderer.clearStencil();

    // Back faces: stencil increment.
    target.material = stencilBackMat;
    renderer.render(scene, camera);

    // Front faces: stencil decrement.
    target.material = stencilFrontMat;
    renderer.render(scene, camera);

    // Restore the real material now (the final color pass will see it).
    target.material = realMat;

    // Cap pass: render the cap quad in its own scene, masked by the
    // stencil we just built.
    renderer.render(capScene, camera);
  }

  // Restore everything before the final pass.
  for (let i = 0; i < meshes.length; i++) meshes[i].visible = savedMeshVis[i];
  for (let k = 0; k < sectionHelperHideList.length; k++) {
    sectionHelperHideList[k].visible = savedHelperVis[k];
  }

  // Final color pass with clipping. We need to clear depth+stencil
  // so the kept geometry isn't depth-shadowed by leftover values
  // from the stencil passes (those had depthWrite off, but the cap
  // quad's depth lives in the buffer too).
  renderer.clearDepth();
  renderer.clearStencil();
  renderer.render(scene, camera);

  renderer.autoClear = true;
}

function sectionSceneBbox() {
  const box = new THREE.Box3();
  for (const m of meshMap.values()) box.expandByObject(m);
  return box.isEmpty() ? null : box;
}

function setSection(axis) {
  // Re-clicking the active axis is treated as Off.
  if (axis === sectionAxis) axis = null;
  sectionAxis = axis;

  // Sync axis-active CSS on the toolbar.
  for (const a of ["x", "y", "z"]) {
    const btn = document.getElementById(`section-${a}-btn`);
    if (!btn) continue;
    if (axis === a) {
      btn.classList.add("section-active");
      btn.dataset.axis = a;
    } else {
      btn.classList.remove("section-active");
      delete btn.dataset.axis;
    }
  }

  if (axis === null) {
    for (const mesh of meshMap.values()) applyCurrentSectionTo(mesh.material);
    sectionViz.visible = false;
    transformControls.enabled = false;
    transformControls.visible = false;
    transformControls.detach();
    return;
  }

  const cfg = SECTION_AXES[axis];
  sectionPlane.normal.copy(cfg.normal);

  // Position the cut at the scene's bbox midpoint along the chosen
  // axis. Empty-scene fallback: origin.
  const bbox = sectionSceneBbox();
  const mid = new THREE.Vector3();
  if (bbox) bbox.getCenter(mid);
  sectionViz.position.copy(mid);

  // Orient the viz so its local +Z matches the cut normal — the
  // PlaneGeometry face then lies coplanar with the cut.
  const zHat = new THREE.Vector3(0, 0, 1);
  sectionViz.quaternion.setFromUnitVectors(zHat, cfg.normal);

  // Size the viz quad to the bbox extent so it's clearly visible
  // outside the cut geometry. 1.4× margin keeps the outline visible.
  const size = bbox
    ? Math.max(
        bbox.max.x - bbox.min.x,
        bbox.max.y - bbox.min.y,
        bbox.max.z - bbox.min.z,
      ) * 1.4
    : 100;
  sectionViz.scale.set(size, size, 1);

  // Recolor viz + outline per axis.
  sectionViz.material.color.setHex(cfg.color);
  sectionEdge.material.color.setHex(cfg.color);

  // Bind sectionPlane to the viz position. The renderer reads the
  // same Plane reference each frame, so further drags update the
  // clip without re-touching the material.
  sectionPlane.setFromNormalAndCoplanarPoint(sectionPlane.normal, mid);
  for (const mesh of meshMap.values()) applyCurrentSectionTo(mesh.material);

  // TransformControls: show only the active-axis handle so the user
  // can't accidentally drag off-plane.
  transformControls.showX = axis === "x";
  transformControls.showY = axis === "y";
  transformControls.showZ = axis === "z";
  transformControls.attach(sectionViz);
  transformControls.enabled = true;
  transformControls.visible = true;
  sectionViz.visible = true;
}

function decodeBase64ToBuffer(b64) {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function decodeMesh(meshDict) {
  const positions = new Float32Array(decodeBase64ToBuffer(meshDict.positions_b64));
  const indices = new Uint32Array(decodeBase64ToBuffer(meshDict.indices_b64));
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom.setIndex(new THREE.BufferAttribute(indices, 1));
  geom.computeVertexNormals();
  return geom;
}

function applyNodeUpdate(node) {
  let mesh = meshMap.get(node.id);
  const geom = decodeMesh(node.mesh);
  if (!mesh) {
    const material = makeMeshMaterial(node.id);
    mesh = new THREE.Mesh(geom, material);
    mesh.name = node.id;
    mesh.userData = { id: node.id, kind: node.kind };
    meshMap.set(node.id, mesh);
    scene.add(mesh);
  } else {
    mesh.geometry.dispose();
    mesh.geometry = geom;
    mesh.userData.kind = node.kind;
  }
}

function removeNode(nodeId) {
  const mesh = meshMap.get(nodeId);
  if (!mesh) return;
  scene.remove(mesh);
  mesh.geometry.dispose();
  // Per-mesh materials live and die with their mesh — clean up the
  // material too so we don't leak texture/uniform handles.
  if (mesh.material && typeof mesh.material.dispose === "function") {
    mesh.material.dispose();
  }
  meshMap.delete(nodeId);
}

function clearAllNodes() {
  for (const id of [...meshMap.keys()]) removeNode(id);
}

function applySceneInit(payload) {
  clearAllNodes();
  for (const node of payload.nodes) applyNodeUpdate(node);
}

function applySceneDelta(payload) {
  for (const node of payload.added || []) applyNodeUpdate(node);
  for (const node of payload.updated || []) applyNodeUpdate(node);
  for (const id of payload.removed || []) removeNode(id);
}

// ---------------------------------------------------------------------------
// Resize
// ---------------------------------------------------------------------------

function resize() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (w === 0 || h === 0) return;        // not laid out yet
  // Bug: the previous guard compared the drawing-buffer dimensions
  // (renderer.domElement.width = w * devicePixelRatio) against the
  // CSS dimensions (w). At pixelRatio === 1 the two coincidentally
  // match on the first frame, the guard skips, and camera.aspect
  // stays at its constructor default of 1 → everything renders
  // stretched along whichever axis the canvas is longer on. Compare
  // against camera.aspect directly so the fix doesn't depend on
  // display DPI.
  const aspect = w / h;
  if (camera.aspect !== aspect) {
    renderer.setSize(w, h, false);
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
  }
}

function frame() {
  resize();
  controls.update();
  if (sectionAxis !== null) {
    renderSectionWithCaps();
  } else {
    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);
}
frame();

// ---------------------------------------------------------------------------
// Chat UI
// ---------------------------------------------------------------------------

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const askArea = document.getElementById("ask-user-area");

function addMessage(role, text, details, stats) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  // Mark error-shaped agent messages so CSS can style them red.
  // The convention is leading "[error]" — already used by the
  // server's `error` WS message and by the WS close handler.
  // [warning] gets the amber treatment for persistence + similar
  // soft alerts that aren't fatal but the user should notice.
  if (role === "agent" && typeof text === "string") {
    if (text.startsWith("[error]")) div.classList.add("error");
    else if (text.startsWith("[warning]")) div.classList.add("warning");
  }
  div.dataset.role = role;
  // Body text via textContent (XSS-safe). Optional `details` arg is
  // a string; when present, render as a collapsible <details> block
  // so the user can expand to see the underlying payload, WS close
  // code, progress timeline, etc.
  const body = document.createElement("span");
  body.textContent = text;
  div.appendChild(body);
  if (typeof details === "string" && details.length > 0) {
    const det = document.createElement("details");
    det.className = "msg-details";
    const summary = document.createElement("summary");
    summary.textContent = "Show details";
    det.appendChild(summary);
    const pre = document.createElement("pre");
    pre.textContent = details;
    det.appendChild(pre);
    div.appendChild(det);
  }
  if (stats && typeof stats === "object") {
    const footer = renderStatsFooter(stats);
    if (footer) div.appendChild(footer);
  }
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}


function renderStatsFooter(stats) {
  // Compact one-line summary appended after the agent's reply.
  // Shows: $ spent, elapsed time, model + token totals (input/output
  // and any cached). Hover for the full per-field breakdown.
  const cost = typeof stats.cost_usd === "number" ? stats.cost_usd : 0;
  const elapsed = typeof stats.elapsed_s === "number" ? stats.elapsed_s : 0;
  const inT = stats.input_tokens || 0;
  const outT = stats.output_tokens || 0;
  const cR = stats.cache_read_tokens || 0;
  const cC = stats.cache_create_tokens || 0;
  const iters = stats.iterations || 0;
  const model = stats.model || "";
  // Skip the footer entirely if there's no signal to report (e.g.
  // fake mode with zero tokens AND zero elapsed).
  if (cost === 0 && elapsed === 0 && inT === 0 && outT === 0) return null;
  const tokenSummary = cR > 0
    ? `${inT}↑ ${outT}↓ ${cR} cached`
    : `${inT}↑ ${outT}↓`;
  const el = document.createElement("div");
  el.className = "msg-stats";
  el.dataset.testid = "agent-stats";
  el.textContent = `${formatCost(cost)} · ${formatElapsed(elapsed)} · ${tokenSummary}`;
  el.title = (
    `model: ${model}\n` +
    `cost: $${cost.toFixed(6)}\n` +
    `elapsed: ${elapsed.toFixed(2)} s\n` +
    `input tokens:    ${inT}\n` +
    `output tokens:   ${outT}\n` +
    `cache read:      ${cR}\n` +
    `cache create:    ${cC}\n` +
    `iterations:      ${iters}`
  );
  return el;
}


function formatCost(cost) {
  // Sub-cent → mils; cent → cents; over a dollar → dollars-and-cents.
  if (cost <= 0) return "$0";
  if (cost < 0.01) return `${(cost * 1000).toFixed(2)}m¢`;  // 1.23m¢
  if (cost < 1)    return `${(cost * 100).toFixed(2)}¢`;     // 23.45¢
  return `$${cost.toFixed(2)}`;
}


function formatElapsed(s) {
  if (s < 1)  return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  const mins = Math.floor(s / 60);
  const secs = Math.round(s - mins * 60);
  return `${mins}m ${secs}s`;
}

function clearAsk() {
  askArea.hidden = true;
  askArea.innerHTML = "";
}

function showAsk(question, options) {
  askArea.hidden = false;
  askArea.innerHTML = "";
  const q = document.createElement("div");
  q.textContent = question;
  q.className = "question";
  askArea.appendChild(q);
  const opts = document.createElement("div");
  opts.className = "options";
  for (const opt of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = opt;
    b.dataset.testid = "ask-option";
    b.dataset.value = opt;
    b.addEventListener("click", () => {
      addMessage("user", opt);
      send({ type: "user_choice", text: opt });
      clearAsk();
      setAgentStatus("thinking");
      bumpStuckTimer();
    });
    opts.appendChild(b);
  }
  askArea.appendChild(opts);
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
let ws;
const wsLog = []; // for feedback bundles

function logWs(dir, payload) {
  wsLog.push({ dir, t: Date.now(), type: payload.type, summary: payload });
  if (wsLog.length > 500) wsLog.splice(0, wsLog.length - 500);
}

function send(payload) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  logWs("out", payload);
  ws.send(JSON.stringify(payload));
}

function connect() {
  ws = new WebSocket(wsUrl);
  ws.addEventListener("open", () => {
    window.fastcad.ready = true;
    document.body.dataset.wsState = "open";
  });
  ws.addEventListener("close", (ev) => {
    document.body.dataset.wsState = "closed";
    // Tell the user the connection died so the indicator stops
    // pretending to "think". They have to refresh to recover.
    setAgentStatus("disconnected");
    // Surface the *cause* in chat too — otherwise the only sign of
    // the disconnect is the indicator and the user has no idea why
    // their last prompt produced nothing. The disclosure attaches
    // recent WS messages so the user can see exactly how far the
    // turn got before the drop.
    const reason = ev && ev.reason ? `: ${ev.reason}` : "";
    const code   = ev && ev.code ? ` [code ${ev.code}]` : "";
    addMessage(
      "agent",
      `[error] Connection to server lost${code}${reason}. ` +
      `Refresh the page to reconnect. If this happens repeatedly, ` +
      `check the server log (journalctl -u fastcad).`,
      buildDisconnectDetails(ev),
    );
  });
  ws.addEventListener("message", (ev) => {
    let payload;
    try { payload = JSON.parse(ev.data); } catch (e) { return; }
    logWs("in", payload);
    handleServerMessage(payload);
  });
}

function handleServerMessage(payload) {
  switch (payload.type) {
    case "scene_init": applySceneInit(payload); break;
    case "scene_delta": applySceneDelta(payload); break;
    case "agent_message":
      addMessage("agent", payload.text, undefined, payload.stats);
      // Final assistant message = end of turn.
      setAgentStatus("idle");
      break;
    case "ask_user":
      showAsk(payload.question, payload.options);
      // Agent paused on you — distinct from idle so the user can
      // tell at a glance "it's my move now" vs "nothing to do".
      setAgentStatus("waiting");
      break;
    case "tool_log":
      // Tool calls are surfaced live in the progress panel (with
      // results, success/failure colors, and timing). The chat
      // panel used to mirror them as args-only rows, but that
      // was redundant + non-actionable (`set_source({"text":"<7058
      // chars>"})` tells you nothing). We keep the WS payload for
      // feedback bundles + ws_log inspection but render nothing
      // in chat.
      break;
    case "progress": {
      handleProgress(payload);
      // Any progress event = agent is doing something. Reset stuck
      // timer; keep state as thinking. Only flip out of `waiting`
      // when fresh progress arrives — that means the agent has
      // resumed after the user's reply.
      const cur = agentStatus && agentStatus.dataset.state;
      if (cur !== "idle") {
        setAgentStatus("thinking");
        bumpStuckTimer();
      }
      break;
    }
    case "scad": exportScad(payload.source); break;
    case "error":
      addMessage(
        "agent",
        `[error] ${payload.message}`,
        buildErrorDetails(payload),
      );
      setAgentStatus("idle");
      break;
  }
}

// ---------------------------------------------------------------------------
// Progress panel — live tool / research events
// ---------------------------------------------------------------------------

const progressPanel = document.getElementById("progress-panel");
const progressClearBtn = document.getElementById("progress-clear-btn");
// Stack of currently-running entries: {kind: "tool_call"|"research", tool?, el}.
// On each `*_done` / `*_error` we pop the matching topmost entry.
const progressStack = [];

function handleProgress(payload) {
  const ev = payload.event || {};
  const t = ev.type || "";

  if (t === "tool_call_started") {
    const el = appendProgressEntry("running", `◷ ${ev.tool}`);
    progressStack.push({ kind: "tool_call", tool: ev.tool, el });
    return;
  }
  if (t === "tool_call_done") {
    // A tool can complete without raising but still report ok=false
    // in its summary (parse error, validation defect, etc.). Render
    // those as error rows (✗ red), not success rows.
    const failed = ev.summary && ev.summary.ok === false;
    const cls = failed ? "error" : "done";
    const icon = failed ? "✗" : "✓";
    finalizeMatching("tool_call", ev.tool, cls, `${icon} ${ev.tool}` + summarySuffix(ev.summary));
    return;
  }
  if (t === "tool_call_error") {
    finalizeMatching("tool_call", ev.tool, "error", `✗ ${ev.tool} — ${truncate(ev.error || "error", 80)}`);
    return;
  }
  if (t === "research_started") {
    const label = ev.topic || ev.slug || "(unknown)";
    const el = appendProgressEntry("running", `▸ research(${label})`);
    progressStack.push({ kind: "research", el });
    return;
  }
  if (t === "research_done") {
    const label = ev.title || ev.slug || "(done)";
    const detail = ev.cache_path ? ` → ${ev.cache_path}` : "";
    finalizeMatching("research", null, "done", `✓ research(${label})${detail}`);
    return;
  }
  if (t === "research_error") {
    finalizeMatching("research", null, "error", `✗ research — ${truncate(ev.error || "error", 100)}`);
    return;
  }

  if (t === "validation_defect") {
    const cls = ev.severity === "error" ? "error" : "warning";
    const text = `${ev.severity === "error" ? "✗" : "⚠"} ${ev.where} — expected ${truncate(ev.expected, 40)}, got ${truncate(ev.actual, 40)}`;
    appendProgressEntry(cls, text);
    return;
  }
  if (t === "validation_pass") {
    appendProgressEntry("done", `✓ validation passed (${ev.slug})`);
    return;
  }

  // Persistence detected — same defect class repeated N iterations.
  // Surface prominently in both the progress panel and the chat so
  // the user knows the agent is stuck instead of silently waiting
  // for the iteration cap to hit.
  if (t === "critics_escalation") {
    const keys = (ev.persistent_keys || []).join(", ");
    const fixit = ev.fixit_will_fire ? " — fix-it critic firing this iteration" : "";
    const text = `⚠ persistent defects: ${keys || "(unspecified)"}${fixit}`;
    appendProgressEntry("warning", text);
    // Only post the chat banner once per turn. Track via a flag on
    // window.fastcad so a fresh prompt resets it.
    if (!window.fastcad._persistenceBannerShown) {
      window.fastcad._persistenceBannerShown = true;
      addMessage(
        "agent",
        `[warning] The agent has hit the same defect class ` +
        `(${keys || "unspecified"}) on multiple iterations. ` +
        `It may not converge on its own; you can wait, ` +
        `or interrupt and rephrase the prompt.`,
      );
    }
    return;
  }

  // Subagent stream chunks: render as nested sub-entries when a research
  // call is currently active; ignore otherwise (the parent tool_call
  // events already cover non-research tools).
  if (progressStack.some((e) => e.kind === "research")) {
    const text = describeStreamEvent(ev);
    if (text) appendProgressEntry("sub", `  · ${text}`);
  }
}

function appendProgressEntry(statusClass, text) {
  const el = document.createElement("div");
  el.className = `progress-entry ${statusClass}`;
  el.textContent = text;
  el.dataset.testid = "progress-entry";
  progressPanel.appendChild(el);
  progressPanel.scrollTop = progressPanel.scrollHeight;
  return el;
}

function finalizeMatching(kind, tool, statusClass, text) {
  // Pop the topmost matching open entry (LIFO so nested tool_calls
  // close in the right order).
  for (let i = progressStack.length - 1; i >= 0; i--) {
    const entry = progressStack[i];
    if (entry.kind !== kind) continue;
    if (tool != null && entry.tool !== tool) continue;
    progressStack.splice(i, 1);
    entry.el.classList.remove("running");
    entry.el.classList.add(statusClass);
    entry.el.textContent = text;
    return;
  }
  // No matching open entry — emit a new line so the user still sees it.
  appendProgressEntry(statusClass, text);
}

function summarySuffix(summary) {
  if (!summary || typeof summary !== "object") return "";
  if (summary.added || summary.updated || summary.removed) {
    const parts = [];
    if (summary.added && summary.added.length) parts.push(`+${summary.added.join(",")}`);
    if (summary.updated && summary.updated.length) parts.push(`~${summary.updated.join(",")}`);
    if (summary.removed && summary.removed.length) parts.push(`-${summary.removed.join(",")}`);
    if (parts.length) return ` — ${parts.join(" ")}`;
  }
  if (summary.cache_path) return ` — ${summary.cache_path}`;
  if (typeof summary.count === "number") return ` — ${summary.count} entries`;
  if (summary.ok === false) return ` — error`;
  return "";
}

function describeStreamEvent(ev) {
  if (ev.type === "system") return `init`;
  if (ev.type === "assistant" && ev.message && Array.isArray(ev.message.content)) {
    const tool = ev.message.content.find((b) => b.type === "tool_use");
    if (tool) return `tool: ${tool.name}`;
    const text = ev.message.content.find((b) => b.type === "text");
    if (text && text.text) return truncate(text.text, 80);
  }
  if (ev.type === "user" && ev.message) return "tool result";
  if (ev.type === "result") return `result: ${ev.subtype || ""}`.trim();
  return "";
}

function truncate(s, n) {
  if (!s) return "";
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function formatToolArgs(args) {
  // Single-line JSON for short args; for any string field longer than
  // 120 chars (typically `set_source.text` or `validate.text` dumping
  // the entire .scad), substitute `<N chars>` so the chat-log stays
  // scrollable. Mirrors the agent-tools _safe_args truncation.
  if (!args || typeof args !== "object") return JSON.stringify(args);
  const trimmed = {};
  for (const [k, v] of Object.entries(args)) {
    if (typeof v === "string" && v.length > 120) {
      trimmed[k] = `<${v.length} chars>`;
    } else {
      trimmed[k] = v;
    }
  }
  return JSON.stringify(trimmed);
}

if (progressClearBtn) {
  progressClearBtn.addEventListener("click", () => {
    progressPanel.innerHTML = "";
    progressStack.length = 0;
  });
}

// ---------------------------------------------------------------------------
// Agent status indicator — visual cue for what the agent is doing.
// Five states with distinct glyphs, colors, and labels:
//
//   idle         ●  gray      "Idle"                 — nothing pending.
//   thinking     ⠋  yellow    "Thinking…"            — turn in flight.
//   waiting      ◉  blue      "Waiting for you"      — agent paused
//                                                        on ask_user.
//   stuck        ⚠  red pulse "Stuck (no progress)"  — long silence.
//   disconnected ○  orange    "Disconnected — refresh" — WS dropped.
//
// The label text is what the user reads. The glyph is decoration.
// ---------------------------------------------------------------------------

const agentStatus = document.getElementById("agent-status");
const agentStatusGlyph = agentStatus && agentStatus.querySelector(".glyph");
const agentStatusLabel = agentStatus && agentStatus.querySelector(".label");
const SPINNER_FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"];
let spinnerIdx = 0;
let spinnerInterval = null;
let stuckTimer = null;
// 90s — long enough that a research-subagent + critic round-trip
// completes naturally without tripping the stuck warning.
const STUCK_AFTER_MS = 90000;

const STATUS_DESCRIPTIONS = {
  "idle":         { glyph: "●", label: "Idle" },
  "thinking":     { glyph: "⠋", label: "Thinking…" },
  "waiting":      { glyph: "◉", label: "Waiting for you" },
  "stuck":        { glyph: "⚠", label: "Stuck — no progress for 90s" },
  "disconnected": { glyph: "○", label: "Disconnected — refresh" },
};
const _agentStatusHistory = [];

function setAgentStatus(state) {
  if (!agentStatus) return;
  const desc = STATUS_DESCRIPTIONS[state] || STATUS_DESCRIPTIONS.idle;
  agentStatus.dataset.state = state;
  agentStatus.title = desc.label;
  if (agentStatusLabel) agentStatusLabel.textContent = desc.label;
  _agentStatusHistory.push(state);
  if (_agentStatusHistory.length > 100) _agentStatusHistory.splice(0, 50);
  if (state === "thinking") {
    if (!spinnerInterval) {
      spinnerInterval = setInterval(() => {
        spinnerIdx = (spinnerIdx + 1) % SPINNER_FRAMES.length;
        if (agentStatusGlyph) agentStatusGlyph.textContent = SPINNER_FRAMES[spinnerIdx];
      }, 100);
    }
  } else {
    if (spinnerInterval) {
      clearInterval(spinnerInterval);
      spinnerInterval = null;
    }
    if (agentStatusGlyph) agentStatusGlyph.textContent = desc.glyph;
  }
}

function bumpStuckTimer() {
  if (stuckTimer) clearTimeout(stuckTimer);
  stuckTimer = setTimeout(() => {
    if (agentStatus && agentStatus.dataset.state === "thinking") {
      setAgentStatus("stuck");
    }
  }, STUCK_AFTER_MS);
}

setAgentStatus("idle");

// Test hook. `agentStatusHistory` records every transition (capped
// at 100) so e2e can assert sequences even when intermediate states
// are too short-lived for polling to catch.
if (window.fastcad) {
  window.fastcad.agentStatus = () => agentStatus ? agentStatus.dataset.state : null;
  window.fastcad.agentStatusHistory = () => _agentStatusHistory.slice();
}


// ---------------------------------------------------------------------------
// Error-detail formatters — populate the `<details>` disclosure on
// `[error]` chat messages so the user can see what actually happened.
// ---------------------------------------------------------------------------

function buildErrorDetails(payload) {
  // Server-side `error` message. Include the full payload + the most
  // recent few WS log entries so the user has the request → response
  // sequence handy.
  const lines = [
    `time: ${new Date().toISOString()}`,
    `payload: ${JSON.stringify(payload, null, 2)}`,
    "",
    "recent ws messages:",
    ...recentWsLogLines(10),
  ];
  return lines.join("\n");
}

function buildDisconnectDetails(ev) {
  const lines = [
    `time: ${new Date().toISOString()}`,
    `close code: ${ev && ev.code}`,
    `close reason: ${(ev && ev.reason) || "(none provided)"}`,
    `was clean: ${ev && ev.wasClean}`,
    "",
    "recent ws messages (oldest first):",
    ...recentWsLogLines(20),
  ];
  return lines.join("\n");
}

function recentWsLogLines(n) {
  const tail = wsLog.slice(-n);
  return tail.map((entry) => {
    const ts = new Date(entry.t).toISOString().slice(11, 23);
    const summary = entry.summary
      ? JSON.stringify(entry.summary).slice(0, 200)
      : "";
    return `  ${ts} ${entry.dir.padEnd(3)} ${entry.type}${summary ? "  " + summary : ""}`;
  });
}


// ---------------------------------------------------------------------------
// Chat input history — up/down arrows scroll through previous prompts,
// like a shell's readline. Persisted in localStorage so it survives
// reloads. Capped to the last 50 entries.
// ---------------------------------------------------------------------------

const INPUT_HISTORY_KEY = "fastcad.chatHistory";
const INPUT_HISTORY_MAX = 50;

let inputHistory = [];
try {
  const saved = localStorage.getItem(INPUT_HISTORY_KEY);
  if (saved) inputHistory = JSON.parse(saved) || [];
} catch (_) { /* ignore */ }

// `historyCursor` is null when the user is editing fresh text. When
// they hit ArrowUp it becomes an index into `inputHistory` (counting
// from the end). `historyDraft` snapshots whatever they had typed
// before they started navigating, so ArrowDown back to the bottom
// restores it.
let historyCursor = null;
let historyDraft = "";

function pushHistory(text) {
  if (!text) return;
  // Don't insert duplicate of the most-recent entry — typing the
  // same prompt twice in a row shouldn't bloat history.
  if (inputHistory[inputHistory.length - 1] === text) return;
  inputHistory.push(text);
  if (inputHistory.length > INPUT_HISTORY_MAX) {
    inputHistory.splice(0, inputHistory.length - INPUT_HISTORY_MAX);
  }
  try {
    localStorage.setItem(INPUT_HISTORY_KEY, JSON.stringify(inputHistory));
  } catch (_) { /* ignore quota / private mode */ }
  historyCursor = null;
  historyDraft = "";
}

function navigateHistory(direction) {
  if (inputHistory.length === 0) return;
  if (historyCursor === null) {
    if (direction !== -1) return;   // ArrowDown at fresh-text does nothing.
    historyDraft = chatInput.value;
    historyCursor = inputHistory.length - 1;
  } else {
    historyCursor += direction;     // -1 = older, +1 = newer
    if (historyCursor < 0) historyCursor = 0;
    if (historyCursor >= inputHistory.length) {
      // Past the newest → restore the fresh draft.
      historyCursor = null;
      chatInput.value = historyDraft;
      // Cursor at end after restore.
      requestAnimationFrame(() => {
        chatInput.setSelectionRange(chatInput.value.length, chatInput.value.length);
      });
      return;
    }
  }
  chatInput.value = inputHistory[historyCursor];
  // Move cursor to end so subsequent typing appends, mimicking shell.
  requestAnimationFrame(() => {
    chatInput.setSelectionRange(chatInput.value.length, chatInput.value.length);
  });
}

if (chatInput) {
  chatInput.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowUp") {
      navigateHistory(-1);
      ev.preventDefault();
    } else if (ev.key === "ArrowDown") {
      navigateHistory(+1);
      ev.preventDefault();
    } else if (ev.key !== "Enter") {
      // Any other keystroke = user is editing again. Cancel the
      // history-navigation cursor so a future ArrowUp starts fresh.
      historyCursor = null;
    }
  });
}

// ---------------------------------------------------------------------------
// Resizable splits — both the vertical viewer/chat divider and the
// horizontal chat-log/progress-pane divider share the same drag
// machinery. The handler is movementX/movementY based: each pointer
// move adds the per-event delta to the current size, with a clamp.
// This avoids the dead-zone bug of the previous "anchored" approach
// (where dragging past the clamp and reversing left a gap before the
// divider tracked the cursor again — visible as the cursor "jumping"
// because the divider stayed put while the cursor came back).
// ---------------------------------------------------------------------------

function makeResizable({ divider, axis, getSize, setSize, storageKey }) {
  if (!divider) return;
  let dragging = false;

  // Restore last drag position across reloads.
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved !== null) setSize(parseFloat(saved));
  } catch (_) { /* ignore */ }

  function applyAndSave(value) {
    const stored = setSize(value);
    if (stored == null) return;
    try { localStorage.setItem(storageKey, String(stored)); }
    catch (_) { /* ignore */ }
  }

  function startDrag(ev) {
    dragging = true;
    divider.classList.add("dragging");
    ev.preventDefault();
    divider.setPointerCapture(ev.pointerId);
  }

  function moveDrag(ev) {
    if (!dragging) return;
    // movementX/Y is the delta since the previous pointermove. Adding
    // it to the *current* size means clamps don't create dead zones —
    // when the user reverses past a clamp, the divider follows the
    // cursor immediately because it's reading current size, not a
    // value frozen at drag-start.
    const delta = axis === "x" ? ev.movementX : ev.movementY;
    if (!delta) return;
    applyAndSave(getSize() + delta);
  }

  function endDrag(ev) {
    if (!dragging) return;
    dragging = false;
    divider.classList.remove("dragging");
    try { divider.releasePointerCapture(ev.pointerId); } catch (_) { /* ignore */ }
  }

  divider.addEventListener("pointerdown", startDrag);
  divider.addEventListener("pointermove", moveDrag);
  divider.addEventListener("pointerup", endDrag);
  divider.addEventListener("pointercancel", endDrag);

  // Keyboard accessibility: arrow keys nudge by 24 px.
  divider.addEventListener("keydown", (ev) => {
    const step = ev.shiftKey ? 60 : 24;
    if (axis === "y" && ev.key === "ArrowUp")    { applyAndSave(getSize() - step); ev.preventDefault(); }
    if (axis === "y" && ev.key === "ArrowDown")  { applyAndSave(getSize() + step); ev.preventDefault(); }
    if (axis === "x" && ev.key === "ArrowLeft")  { applyAndSave(getSize() - step); ev.preventDefault(); }
    if (axis === "x" && ev.key === "ArrowRight") { applyAndSave(getSize() + step); ev.preventDefault(); }
  });
}


// Horizontal divider (chat-log / progress-pane). Size is stored as a
// percentage on `--pane-split` (CSS reads `calc(var(--pane-split) * 1%)`
// for chat-log's flex-basis). We map pointermove movementY → percent
// of chatPane height each frame.
const paneDivider = document.getElementById("pane-divider");
const chatPane = document.getElementById("chat-pane");

if (paneDivider && chatPane) {
  const MIN_PCT = 15;
  const MAX_PCT = 85;

  const getPaneSplitPct = () => {
    const v = parseFloat(getComputedStyle(chatPane).getPropertyValue("--pane-split"));
    return Number.isFinite(v) ? v : 60;
  };

  // Convert a Y-pixel delta to a percent delta against the current
  // chatPane height. Because we add *deltas* (not absolutes), there
  // is no dead-zone when the clamp is hit.
  let lastPct = getPaneSplitPct();

  makeResizable({
    divider: paneDivider,
    axis: "y",
    getSize: () => {
      // For movementY-based math, we want size in *pixels* so the
      // delta math doesn't have to convert each event. Use chatPane
      // height × current%.
      const h = chatPane.getBoundingClientRect().height || 1;
      return getPaneSplitPct() * h / 100;
    },
    setSize: (px) => {
      const h = chatPane.getBoundingClientRect().height || 1;
      const pct = Math.max(MIN_PCT, Math.min(MAX_PCT, (px / h) * 100));
      chatPane.style.setProperty("--pane-split", String(pct));
      lastPct = pct;
      return pct;
    },
    storageKey: "fastcad.paneSplit",
  });
}


// Vertical divider (viewer / chat-pane). The convention used by
// `makeResizable` is that `getSize()` returns the size of the pane
// *before* the divider in the flex order — for the vertical divider
// that's the viewer's width. `delta = movementX` is the number of
// pixels the cursor moved right, which is also the number of pixels
// the viewer should grow. We invert at storage time to keep the CSS
// variable as right-pane width (which is what flex-shrink: 0 keys off).
const appDivider = document.getElementById("app-divider");
const appEl = document.getElementById("app");

if (appDivider && appEl) {
  const MIN_RIGHT_PX = 280;
  // Cap so the viewer can never disappear entirely.
  const maxRightPx = () => Math.max(MIN_RIGHT_PX, Math.floor(window.innerWidth * 0.8));
  const getRightPx = () => {
    const v = parseFloat(getComputedStyle(appEl).getPropertyValue("--right-pane-width"));
    return Number.isFinite(v) ? v : 380;
  };

  makeResizable({
    divider: appDivider,
    axis: "x",
    // Track viewer width = window width − right pane width.
    getSize: () => Math.max(0, window.innerWidth - getRightPx()),
    setSize: (viewerPx) => {
      // Viewer grew → right pane shrunk by the same amount.
      const right = window.innerWidth - viewerPx;
      const clamped = Math.max(MIN_RIGHT_PX, Math.min(maxRightPx(), right));
      appEl.style.setProperty("--right-pane-width", `${clamped}px`);
      return clamped;
    },
    storageKey: "fastcad.rightPaneWidth",
  });

  // Ensure the saved width survives window resize: re-clamp on resize
  // so a wider window doesn't leave us stuck at the old max.
  window.addEventListener("resize", () => {
    const cur = getRightPx();
    const clamped = Math.max(MIN_RIGHT_PX, Math.min(maxRightPx(), cur));
    if (clamped !== cur) appEl.style.setProperty("--right-pane-width", `${clamped}px`);
  });
}

function exportScad(source) {
  const blob = new Blob([source], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "fastcad.scad";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  addMessage("agent", "exported fastcad.scad");
}

connect();

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

chatForm.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  addMessage("user", text);
  send({ type: "prompt", text });
  pushHistory(text);
  chatInput.value = "";
  // Fresh turn → allow a new persistence banner if it triggers.
  if (window.fastcad) window.fastcad._persistenceBannerShown = false;
  setAgentStatus("thinking");
  bumpStuckTimer();
});

document.getElementById("undo-btn").addEventListener("click", () => send({ type: "undo" }));
document.getElementById("redo-btn").addEventListener("click", () => send({ type: "redo" }));
document.getElementById("export-btn").addEventListener("click", () => send({ type: "export_scad" }));
document.getElementById("reset-btn").addEventListener("click", () => send({ type: "reset" }));
const homeBtn = document.getElementById("home-btn");
if (homeBtn) homeBtn.addEventListener("click", recenterCamera);

// Keyboard: 'h' to recenter (when canvas has focus). 1/2/3 toggle the
// X/Y/Z section plane; 0 turns the section off.
window.addEventListener("keydown", (ev) => {
  if (ev.target instanceof HTMLInputElement) return;  // don't steal chat input
  if (ev.key === "h" || ev.key === "H") recenterCamera();
  else if (ev.key === "1") setSection("x");
  else if (ev.key === "2") setSection("y");
  else if (ev.key === "3") setSection("z");
  else if (ev.key === "0") setSection(null);
});

// Section toolbar buttons.
for (const a of ["x", "y", "z"]) {
  const btn = document.getElementById(`section-${a}-btn`);
  if (btn) btn.addEventListener("click", () => setSection(a));
}
const sectionOffBtn = document.getElementById("section-off-btn");
if (sectionOffBtn) sectionOffBtn.addEventListener("click", () => setSection(null));

// ---------------------------------------------------------------------------
// Hooks for feedback.js + e2e tests
// ---------------------------------------------------------------------------

window.fastcad.scene = scene;
window.fastcad.camera = camera;
window.fastcad.renderer = renderer;
window.fastcad.meshMap = meshMap;
window.fastcad.wsLog = wsLog;
window.fastcad.send = send;
window.fastcad.progressPanel = progressPanel;
window.fastcad.progressEntryCount = () => progressPanel.querySelectorAll(".progress-entry").length;
window.fastcad.snapshotViewer = () => renderer.domElement.toDataURL("image/png");
window.fastcad.cameraState = () => ({
  position: camera.position.toArray(),
  target: controls.target.toArray(),
  up: camera.up.toArray(),
});
// Section plane introspection for tests.
window.fastcad.setSection = setSection;
window.fastcad.capQuad = capQuad;
window.fastcad.colorForId = colorForId;
window.fastcad.sectionState = () => {
  // Every per-mesh material gets the same clipping plane setup, so
  // looking at any one of them is enough; tests just want to know
  // "is clipping currently active". Empty scene → 0.
  const first = meshMap.values().next().value;
  const planes = first ? (first.material.clippingPlanes || []) : [];
  return {
    axis: sectionAxis,
    clippingPlaneCount: planes.length,
    vizVisible: sectionViz.visible,
    planeConstant: sectionPlane.constant,
    planeNormal: sectionPlane.normal.toArray(),
  };
};
