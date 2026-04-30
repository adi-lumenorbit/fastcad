// fastcad — three.js viewer + WS client + chat UI.
// Per-id mesh map: only listed nodes ever change in the scene; every other
// mesh is preserved across deltas. That's what makes rendering "incremental".

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

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
scene.add(new THREE.AxesHelper(20));
const grid = new THREE.GridHelper(200, 20, 0x444444, 0x333333);
grid.rotation.x = Math.PI / 2;
scene.add(grid);

// flatShading: every triangle gets its own normal. This is the right
// default for CAD: sharp edges between cylinder side / top stay sharp;
// thread teeth read as faceted teeth instead of being smoothed into a
// continuous spiral that looks like dust. Curved surfaces look slightly
// faceted but $fn=64 makes that nearly invisible.
const meshMaterial = new THREE.MeshStandardMaterial({
  color: 0xc9c1a8,
  metalness: 0.05,
  roughness: 0.65,
  flatShading: true,
});

// nodeId -> THREE.Mesh
const meshMap = new Map();

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
    mesh = new THREE.Mesh(geom, meshMaterial);
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
  if (renderer.domElement.width !== w || renderer.domElement.height !== h) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
}

function frame() {
  resize();
  controls.update();
  renderer.render(scene, camera);
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

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  div.dataset.role = role;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
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
  ws.addEventListener("close", () => {
    document.body.dataset.wsState = "closed";
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
    case "agent_message": addMessage("agent", payload.text); break;
    case "ask_user": showAsk(payload.question, payload.options); break;
    case "tool_log":
      for (const c of payload.calls) addMessage("tool", `${c.name}(${JSON.stringify(c.args)})`);
      break;
    case "progress": handleProgress(payload); break;
    case "scad": exportScad(payload.source); break;
    case "error": addMessage("agent", `[error] ${payload.message}`); break;
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
    finalizeMatching("tool_call", ev.tool, "done", `✓ ${ev.tool}` + summarySuffix(ev.summary));
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

if (progressClearBtn) {
  progressClearBtn.addEventListener("click", () => {
    progressPanel.innerHTML = "";
    progressStack.length = 0;
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
  chatInput.value = "";
});

document.getElementById("undo-btn").addEventListener("click", () => send({ type: "undo" }));
document.getElementById("redo-btn").addEventListener("click", () => send({ type: "redo" }));
document.getElementById("export-btn").addEventListener("click", () => send({ type: "export_scad" }));
document.getElementById("reset-btn").addEventListener("click", () => send({ type: "reset" }));
const homeBtn = document.getElementById("home-btn");
if (homeBtn) homeBtn.addEventListener("click", recenterCamera);

// Keyboard: 'h' to recenter (when canvas has focus)
window.addEventListener("keydown", (ev) => {
  if (ev.target instanceof HTMLInputElement) return;  // don't steal chat input
  if (ev.key === "h" || ev.key === "H") recenterCamera();
});

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
