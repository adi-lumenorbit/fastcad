// fastcad — feedback overlay + rrweb capture.
// On load, start an rrweb session and ring-buffer ~60s of events. The
// "Send Feedback" button bundles { description, rrweb events, screenshots,
// op log, ws log, camera state, target } into a multipart POST to /feedback.
// "Point" mode lets the user click a DOM element to anchor the report
// to a specific selector — so "fix this button" becomes unambiguous.

const FEEDBACK_BUFFER_MS = 60_000;

const events = [];
let stopRrweb = null;

function safe(fn, fallback) {
  try { return fn(); } catch (e) { return fallback; }
}

function startRrweb() {
  if (typeof rrweb === "undefined") {
    console.warn("rrweb not loaded; feedback bundles will lack DOM session");
    return;
  }
  stopRrweb = rrweb.record({
    emit(ev) {
      events.push(ev);
      const cutoff = Date.now() - FEEDBACK_BUFFER_MS;
      while (events.length && events[0].timestamp < cutoff) events.shift();
    },
  });
}
startRrweb();

// ---------------------------------------------------------------------------
// Point mode
// ---------------------------------------------------------------------------

let pointMode = false;
let pointed = null; // {selector, rect}

const pointBtn = document.getElementById("feedback-point-btn");
const sendBtn = document.getElementById("feedback-send-btn");
const status = document.getElementById("feedback-status");

function cssPath(el) {
  if (!(el instanceof Element)) return "";
  if (el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;
  if (el.id) return `#${el.id}`;
  const parts = [];
  let cur = el;
  while (cur && cur.nodeType === 1 && parts.length < 6) {
    let part = cur.tagName.toLowerCase();
    if (cur.classList.length) part += "." + [...cur.classList].slice(0, 2).join(".");
    const parent = cur.parentNode;
    if (parent) {
      const same = [...parent.children].filter(c => c.tagName === cur.tagName);
      if (same.length > 1) part += `:nth-of-type(${same.indexOf(cur) + 1})`;
    }
    parts.unshift(part);
    if (cur.id) break;
    cur = cur.parentElement;
  }
  return parts.join(" > ");
}

let hoverEl = null;
function onHover(ev) {
  if (!pointMode) return;
  if (hoverEl) hoverEl.classList.remove("fastcad-point-hover");
  hoverEl = ev.target;
  if (hoverEl && hoverEl.classList) hoverEl.classList.add("fastcad-point-hover");
}
function onPointClick(ev) {
  if (!pointMode) return;
  if (sendBtn.contains(ev.target) || pointBtn.contains(ev.target)) return;
  ev.preventDefault();
  ev.stopPropagation();
  const el = ev.target;
  const rect = el.getBoundingClientRect();
  pointed = {
    selector: cssPath(el),
    rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
    text: (el.innerText || "").slice(0, 200),
    tag: el.tagName.toLowerCase(),
  };
  status.textContent = `pinned: ${pointed.selector}`;
  pointMode = false;
  pointBtn.classList.remove("active");
  if (hoverEl) hoverEl.classList.remove("fastcad-point-hover");
}

pointBtn.addEventListener("click", () => {
  pointMode = !pointMode;
  pointBtn.classList.toggle("active", pointMode);
  status.textContent = pointMode ? "click any element to pin it" : "";
});
document.addEventListener("mouseover", onHover, true);
document.addEventListener("click", onPointClick, true);

// ---------------------------------------------------------------------------
// Send
// ---------------------------------------------------------------------------

async function captureDomPng() {
  if (typeof html2canvas === "undefined") return null;
  try {
    const canvas = await html2canvas(document.body, { logging: false, useCORS: true });
    return await new Promise(res => canvas.toBlob(res, "image/png"));
  } catch (e) {
    console.warn("html2canvas failed", e);
    return null;
  }
}

async function captureViewerPng() {
  if (!window.fastcad || !window.fastcad.renderer) return null;
  const dataUrl = window.fastcad.snapshotViewer();
  const blob = await (await fetch(dataUrl)).blob();
  return blob;
}

async function sendFeedback() {
  const description = window.prompt("Describe the issue (one or two sentences):", "");
  if (description === null) return;
  status.textContent = "capturing...";
  const fd = new FormData();
  fd.append("description", description);
  fd.append("target", JSON.stringify(pointed || {}));
  fd.append("rrweb_events", JSON.stringify(events));
  fd.append("camera", JSON.stringify(safe(() => window.fastcad.cameraState(), {})));
  fd.append("oplog", JSON.stringify([])); // server-side oplog is captured server-side too
  fd.append("ws_log", JSON.stringify(window.fastcad?.wsLog || []));

  const dom = await captureDomPng();
  if (dom) fd.append("dom_png", dom, "dom.png");
  const viewer = await captureViewerPng();
  if (viewer) fd.append("viewer_png", viewer, "viewer.png");

  try {
    const resp = await fetch("/feedback", { method: "POST", body: fd });
    const out = await resp.json();
    if (out.ok) {
      status.textContent = `saved: ${out.dir}`;
      pointed = null;
    } else {
      status.textContent = `error: ${out.error || resp.status}`;
    }
  } catch (e) {
    status.textContent = `error: ${e.message}`;
  }
}

sendBtn.addEventListener("click", sendFeedback);

// Expose for tests
window.fastcad = window.fastcad || {};
window.fastcad.feedbackCapture = sendFeedback;
window.fastcad.setPointed = (p) => { pointed = p; };
