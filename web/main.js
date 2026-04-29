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

const meshMaterial = new THREE.MeshStandardMaterial({
  color: 0xc9c1a8,
  metalness: 0.05,
  roughness: 0.65,
  flatShading: false,
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
    case "scad": exportScad(payload.source); break;
    case "error": addMessage("agent", `[error] ${payload.message}`); break;
  }
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
window.fastcad.snapshotViewer = () => renderer.domElement.toDataURL("image/png");
window.fastcad.cameraState = () => ({
  position: camera.position.toArray(),
  target: controls.target.toArray(),
  up: camera.up.toArray(),
});
