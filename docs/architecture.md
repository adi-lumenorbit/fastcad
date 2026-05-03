# fastcad — architecture

A single-document tour of how fastcad is built: what each layer does,
how data moves through it, the design rules that keep changes
coherent, and the deployment surface. Written for someone joining
the project who has read `CLAUDE.md` and wants the system view.

This document deliberately avoids worked examples involving specific
standardized parts — per `CLAUDE.md`, hardcoded part references in
documentation tend to overfit the agent's behaviour to one example.

---

## 1. What fastcad is

fastcad is a local web app that turns natural-language prompts into
3D CAD models. The user types a request; an LLM agent reads the
current scene, decides on a change, and edits an OpenSCAD-compatible
spec. The browser re-renders only what changed. The exported `.scad`
file is identical to what the user saw on screen.

The core design constraint, repeated in every layer, is that the
**`.scad` source is the single source of truth**. The browser viewer,
the export button, the validator, and the vision critics all consume
the same source — there's no parallel internal representation that
could drift away from the file the user sees.

---

## 2. System view

```
┌──────────────────────────────┐         ┌──────────────────────────────────┐
│  Browser (Windows / Mac)     │         │  WSL / VM Linux host             │
│                              │         │                                  │
│  ┌────────────────────────┐  │         │  ┌────────────────────────────┐  │
│  │ index.html / main.js   │  │         │  │ FastAPI app  (uvicorn)     │  │
│  │  three.js viewer       │◄─┼── HTTP ─┤  │   /            static      │  │
│  │  WebSocket client      │  │         │  │   /ws          WebSocket   │  │
│  │  feedback overlay      │  │         │  │   /feedback    POST bundle │  │
│  └────────┬───────────────┘  │         │  └─────────┬──────────────────┘  │
│           │ WS frames        │         │            │                     │
└───────────┼──────────────────┘         │            ▼                     │
            │                            │  ┌────────────────────────────┐  │
            └────────── /ws ─────────────┼─►│ session.py   op log        │  │
                                         │  │              + parse cache │  │
                                         │  └─────────┬──────────────────┘  │
                                         │            │                     │
                                         │            ▼                     │
                                         │  ┌────────────────────────────┐  │
                                         │  │ agent loop (Anthropic SDK) │  │
                                         │  │   + tool dispatch          │  │
                                         │  │   + critics orchestrator   │  │
                                         │  └─┬────────┬──────────┬──────┘  │
                                         │    │        │          │         │
                                         │    ▼        ▼          ▼         │
                                         │  parser  manifold3d  OpenSCAD CLI│
                                         │  (lark)  (CSG eval)  (vision)   │
                                         └──────────────────────────────────┘
```

The user's browser only ever talks to the local server (or, in the
deployed configuration, to Caddy in front of it). Everything
LLM-related — the main agent, the research subagent, the vision
critics — runs server-side. The Anthropic API key never leaves the
host.

---

## 3. Repository layout

```
src/fastcad/
  __main__.py            uvicorn entry point (`python -m fastcad`)
  session.py             SessionState: spec source + parse cache + history
  server/
    app.py               FastAPI: static mount + /ws + /feedback + /healthz
    ws.py                WebSocket session loop, input sanitization, rate limit
    feedback.py          POST /feedback handler (writes bundles to tmp/)
  model/
    kernel.py            manifold3d wrappers + mesh-to-dict serialization
    scad_parser.py       lark grammar for the OpenSCAD subset
    scad_eval.py         AST → manifold; built-in primitives + transforms
    spec_diff.py         dependency-aware re-evaluation cache
    sections.py          2D cross-section extraction + PNG render + metrics
    validate.py          Channel 1 — structural validator + acceptance schema
    render.py            Channel 2 — OpenSCAD CLI → canonical-angle PNGs
  agent/
    client.py            Anthropic tool-use loop + ANTHROPIC_FAKE deterministic mode
    tools.py             tool schemas + dispatcher for the modeling agent
    system_prompt.py     modeling-agent system prompt (no part-specific examples)
    research.py          research subagent driver (Claude Code subprocess)
    critics/             multi-critic orchestrator + per-critic modules
      __init__.py        review_all, registry, persistence detection, sections
      _common.py         shared multimodal call + JSON parse + section image
      general.py         broad form / proportions / orientation critic
      threads.py         helical / swept-feature critic
      fixit.py           escalation critic — fires on persistent defects
web/
  index.html             two-pane layout (viewer + chat)
  main.js                three.js viewer, WS client, chat, progress panel
  feedback.js            point-mode overlay, rrweb, capture bundle POST
  vendor/                pinned three.js / rrweb / html2canvas (gitignored)
docs/
  architecture.md        this file
  research/              text-based cache of part specifications
  plans/                 NN-slug.md per CLAUDE-issue.md convention
deploy/                  Caddyfile, systemd unit, fail2ban, bootstrap.sh
tests/
  unit/                  fast, no-network, no-browser
  e2e/                   Playwright headless Chromium
```

---

## 4. Spec model — `.scad` as source of truth

The model has no internal IR for the scene. The current state of the
design is a single OpenSCAD-compatible source string in
`SessionState.current_source`. Every observable property of the
scene — the meshes the viewer renders, the bounding boxes the
validators check, the export — is derived from this string.

### 4.1 Supported language subset

The parser (`model/scad_parser.py`, lark grammar) accepts a
deliberately small subset of OpenSCAD:

- **Statements**: `name = expr;`, `module name(args) { ... }`, module
  calls, `for (var = [start:end])`, `if/else`, `let(...) statement`,
  block statements.
- **Expressions**: numbers, vectors, arithmetic / comparison / boolean,
  ternary, function calls, vector indexing.
- **Built-in functions**: `sin / cos / tan / asin / acos / atan /
  atan2 / sqrt / pow / abs / min / max / floor / ceil / round / len /
  concat`, plus the constant `PI`.
- **Built-in modules**: 3D primitives `cube / sphere / cylinder /
  polyhedron`; 2D primitives `circle / square / polygon`; extrusions
  `linear_extrude / rotate_extrude`; transforms `translate / rotate /
  scale / mirror`; CSG `union / difference / intersection`.
- **Special vars**: `$fn`.

Out of scope (parser rejects): `function` definitions, `include`,
`use`, `import`, `hull`, `minkowski`, `offset`, `projection`, `text`,
`surface`, `assert`, `echo`. The agent is told it cannot rely on
external libraries — there is no `include` mechanism.

### 4.2 Evaluation and the dependency cache

`model/scad_eval.py` walks the AST and produces a `manifold3d.Manifold`
for each top-level renderable module call. Top-level statements that
emit geometry are the **scene nodes**. A module's `id` is its name —
the agent's chosen identifier flows through to the wire layer
unchanged.

`model/spec_diff.py` keeps a **per-id evaluation cache**. On each
`set_source(text)` it:

1. Parses the new source.
2. Computes a content hash for each top-level node, scoped to its
   referenced symbols (a top-level call hashes its own AST plus any
   modules / parameters it reaches transitively).
3. For each node id, compares its new hash against the cached one:
   - hit → reuse cached `ModuleEval` (no re-evaluation).
   - miss → re-evaluate just that node.
   - removed → drop from cache.
4. Returns a `ChangeSet { added, updated, removed }` of node ids.

The result: a one-line edit to a parameter at the top of the file
re-evaluates only the modules that reference it, not the entire
scene. The browser receives a `scene_delta` listing the affected
ids; meshes for unaffected ids stay in place.

### 4.3 Why this shape

The `.scad`-as-spec model traded an internal op-log API
(`add_primitive`, `boolean`, …) for plain text the agent edits as a
whole. Two reasons:

1. **No translation discrepancy at export.** What the user sees IS
   the file they get from the Export button. There is no path where
   the renderer and the export disagree.
2. **The agent gets to use the language it's already fluent in.**
   OpenSCAD is widely represented in training data; the agent
   consistently writes valid spec on the first try, including
   parametric constructs the previous op-log API couldn't express
   without bespoke tools (`for`, `let`, `union { … }` patterns).

The trade-off is that every set of edits is a whole-file rewrite.
The dependency cache absorbs that cost — only the touched modules
get re-evaluated.

---

## 5. Agent loop

The modeling agent is an Anthropic tool-use loop driven from
`agent/client.py`. Each user prompt corresponds to one **turn**, and
a turn is a sequence of tool calls bracketed by an opening user
message and a closing assistant message. The system prompt is
static (`agent/system_prompt.py`) and embeds the current spec
verbatim each turn so the agent sees exactly what it's editing.

### 5.1 Tools

| Tool | Purpose |
|------|---------|
| `read_source` | Return the current spec. Rarely used — the spec is in the system prompt. |
| `set_source(text)` | Replace the spec. Triggers parse + diff + re-eval + auto-validation. The primary tool. |
| `validate(text)` | Dry-run a candidate spec without committing. Used sparingly to pre-check syntactic risks. |
| `select_face(node_id, face_name)` | Resolve a named face on a top-level call to `{point, normal}`. Lets the agent place follow-up parts on existing features without bbox math. |
| `ask_user(question, options)` | Pause for disambiguation. Only fires when the request is genuinely ambiguous. |
| `list_research`, `read_research`, `research` | Cache-first lookup of standardized-part specs. |
| `validate_design(against_slug?)` | Run the structural validator explicitly. Auto-invoked after `set_source` when configured. |
| `inspect_section(plane, offset, ...)` | Cut a 2D cross-section through the current geometry; return polygons + computed metrics. The agent's eyes for thread / sweep features. |

### 5.2 The set_source pipeline

When the agent calls `set_source(text)`:

```
text ─► parse  ─► AST ─► diff_and_evaluate ─► ChangeSet ─► emit scene_delta
                                       │
                                       ▼
                          structural validator (Channel 1)
                                       │
                                       ▼
                              vision critics (Channel 2)
                                       │
                                       ▼
                                defects → tool result
```

If any step fails (parse error, evaluation error), the spec is
**unchanged** and the error returns to the agent. The agent reads
the error in the next turn and tries again — there's no half-applied
state to recover from.

If parsing succeeds and a research cache is in scope, the structural
validator runs automatically and packs any defects into the tool
result. Vision critics run when configured (`FASTCAD_AUTO_VALIDATE`
includes `vision`).

### 5.3 Persistence detection and escalation

Iteration history is tracked in
`SessionState.defect_history: list[list[Defect]]`. After each turn
the orchestrator inspects the last N iterations for defect classes
that keep recurring; persistence triggers three layered responses:

1. **Fix-it critic** (threshold 3 — `agent/critics/fixit.py`). Fires
   in the vision-critic round and produces a complete replacement
   code fragment rather than abstract advice.
2. **UI warning** (threshold 3 — `critics_escalation` progress
   event). The frontend renders an amber row in the progress panel
   and posts a one-time `[warning]` chat banner so the user sees
   "the agent is stuck on X" in real time.
3. **Smart turn exit** (threshold 4 — `agent/client.py:_real_turn`
   inline check). When the same defect class has appeared on each
   of the last four iterations and the fix-it critic has already
   fired, the turn ends with a concrete explanation pointing at
   the recurring defect, instead of running to the
   `max_tool_iterations` cap.

The three layers form a graceful escalation: feedback at iteration
3 → user notice at iteration 3 → forced exit at iteration 4. The
system's safety valve when the regular feedback loop converges to
a fixed point that isn't the right answer.

---

## 6. Three feedback channels

The agent's blind spot is that it predicts 3D geometry from `.scad`
source alone — and is consistently wrong about helical / swept
features. fastcad uses three complementary channels of feedback to
close the loop.

### 6.1 Channel 1 — Structural validator

`model/validate.py` reads each part's `## Acceptance` JSON schema
from its research cache file and produces deterministic `Defect`
objects. Schema fields:

- **`bbox_z_extent`, `bbox_xy_max`, `bbox_xy_symmetric`** — overall
  size and rotational symmetry constraints.
- **`volume_range`** — total volume in mm³.
- **`connected_components`** — number of disconnected manifold pieces
  (catches missing `union()`).
- **`expected_modules`** — regex fragments at least one defined
  module name must match (catches "agent forgot a feature").
- **`horizontal_slices_at_z`** — at each z, expected radial peak
  count (single-start vs multi-start) and radius range.
- **`axial_consistency`** + **`pitch`** — stacked-rings detector.
  Sample points are perturbed by per-slice fractions of pitch so
  uniformly-spaced z values don't collapse to identical azimuths
  on a real helix.
- **`axial_section`** — peak count, axial extent, flank angle on an
  XZ or YZ section. Catches "helical band of zero axial thickness"
  failures deterministically (peak axial extent ≪ pitch × 0.4 ⇒
  defect).

The validator is **pure** — no API calls, no rendering. Free to run
on every `set_source`. Trade-off: it can only catch what the schema
names.

### 6.2 Channel 2 — Vision critics

`agent/critics/` runs three vision critics in parallel via
`ThreadPoolExecutor`:

- **general** — broad form, proportions, axis orientation.
- **threads** — helical / swept-feature correctness.
- **fixit** — escalation critic, only fires when persistence is
  detected.

Each critic receives the same iso-angle PNGs (rendered once via the
OpenSCAD CLI in `model/render.py`) plus the canonical 2D sections
(see § 6.3). Each is asked to produce a JSON `{defects: [...]}` with
hints that reference identifiers actually present in the agent's
source — abstract advice is rejected by prompt design.

Wall-clock cost is one critic's runtime, not N × runtime. The
`fixit` critic is gated on persistence so the typical iteration
costs two parallel vision calls, not three.

### 6.3 Section channel

`model/sections.py` extracts 2D cross-sections through the current
manifold:

- **Axis-aligned**: XY at any z, XZ at any y, YZ at any x.
- **Oblique**: any plane defined by a normal vector + a point on it,
  for parts whose interesting features don't align with world axes.

Each section is rendered to a PNG (Pillow) and shipped alongside
the iso renders to every vision critic. Each section also carries
*computed metrics*: peak count, axial peak extent, flank angle
(axial); outer protrusion count, radius range (radial).

The agent itself can request additional sections via
`inspect_section`, receiving raw polygon vertices plus the same
metrics. This lets the agent program against the geometry — count
peaks, measure tooth thickness — instead of guessing at it from
3D iso renders.

This channel exists because vision-of-iso-render confused itself
on tessellation artifacts and stacked-ring optical illusions.
2D sections are unambiguous: a thread tooth in axial section is
either present at expected thickness or it isn't.

---

## 7. Research cache

`docs/research/` is a text-based, git-tracked cache of dimensional
and construction guidance for standardized parts (fasteners,
profiles, motor frames, anything with a published spec). Each entry
is `<slug>.md` containing:

- A `## Key dimensions` block (human + machine-readable list).
- A `## Variants` block enumerating reasonable interpretations.
- A `## Sources` block with URLs.
- A `## Acceptance` block — the JSON schema Channel 1 evaluates.
- A `## Implementation guidance` block — prose + OpenSCAD snippets
  describing the canonical construction pattern for THIS part class.

The agent reads what's there verbatim. If a relevant entry is
missing, the agent invokes `research(topic)`, which spawns the
**research subagent** — a separate Claude Code CLI subprocess with
WebSearch / WebFetch / Read / Write tools — to populate the cache.
The subagent writes only inside `docs/research/`, and its slug is
validated against a kebab-case whitelist before any filesystem
operation.

The cache is intentionally human-editable. If a value is wrong,
fix it in the file and commit; the agent picks up the change on
the next turn. No approval queue — trust lives at the source-control
level, like any other repo file.

The cache also matters for safety: hardcoded part specifics in the
agent's system prompt overfit the agent's behaviour. By moving
spec data into per-part cache entries the system prompt stays
generic and the per-part details get a code-review surface.

---

## 8. Wire protocol

WebSocket messages between browser and server.

**Inbound** (browser → server):

| Type | Fields | Effect |
|------|--------|--------|
| `prompt` | `text` | Run an agent turn. Subject to per-session rate limit and prompt sanitization. |
| `user_choice` | `text` | Reply to a pending `ask_user`; must match one of the offered options. |
| `undo` / `redo` / `reset` | — | Move along the op log; emit a fresh `scene_init`. |
| `export_scad` | — | Server replies with `{type: "scad", source}`. |
| `ws_log_request` | — | Server replies with the recent inbound + outbound message log (used for feedback bundles). |

Inbound frames are size-capped (64 KB), JSON-validated, type-
whitelisted, and passed through a control-character stripper. The
WS upgrade is gated by an Origin-header check when
`FASTCAD_ALLOWED_ORIGINS` is set.

**Outbound** (server → browser):

| Type | Fields | When |
|------|--------|------|
| `scene_init` | `nodes` | Initial scene + after undo / redo / reset. |
| `scene_delta` | `added` / `updated` / `removed` | Incremental diff after a successful agent edit. |
| `agent_message` | `text`, `stats` | Final assistant reply for the turn. `stats` carries cost / elapsed / token totals. |
| `ask_user` | `question`, `options` | Agent paused for disambiguation. |
| `tool_log` | `calls` | One entry per tool call this turn. The frontend keeps the payload for feedback bundles + ws_log inspection but does not render it in chat — the progress panel is the canonical live view. |
| `progress` | `id`, `event`, `ts` | Streamed live during long-running tools (research subagent, critics). Notable `event.type` values: `tool_call_started/done/error`, `research_started/done/error`, `validation_defect`, `validation_pass`, `critics_escalation` (persistence detected — same defect class repeated). |
| `scad` | `source` | Reply to `export_scad`. |
| `error` | `message` | Protocol or agent error; rendered with a `<details>` disclosure exposing recent WS traffic. |

Mesh data flows in `nodes` / `added` / `updated` entries as base64-
encoded `Float32Array` of positions and `Uint32Array` of indices.
Decoding happens once on the client; the per-id mesh map in
`web/main.js` keeps every other mesh untouched across deltas — this
is what makes rendering "incremental" rather than re-uploaded.

The transport is uvicorn's `websockets-sansio` driver. The legacy
`websockets` driver had a keepalive_ping race that severed the WS
mid-turn under load (assertion in `_drain_helper`); switching to the
sans-io path eliminates that class of bug.

---

## 9. Frontend

`web/index.html` lays out two panes — viewer on the left, chat /
progress panel on the right. Two draggable dividers split the
layout: an `#app-divider` between viewer and chat-pane (vertical),
and a `#pane-divider` between chat-log and progress-panel
(horizontal). Both share a single `makeResizable` helper in
`main.js` that uses `event.movementX/Y` deltas — eliminates the
dead-zone bug of an "anchored-delta" approach when the drag passes
its clamp and reverses.

`web/main.js` is the single ES module that:

- Sets up the three.js renderer + scene + camera + OrbitControls.
- Maintains `meshMap: Map<id, THREE.Mesh>`. Every WS `scene_delta`
  touches only ids in its `added` / `updated` / `removed` lists.
- Connects the WS, dispatches inbound messages, batches outbound.
- Renders the chat + ask-user UI; uses `textContent` (not
  `innerHTML`) for any agent-supplied text. `[error]` and
  `[warning]` agent messages get colored borders and an optional
  `<details>` disclosure with recent WS traffic.
- Streams `progress` events into the side panel as a running list
  with per-tool-call status icons (▸ → ✓ / ✗ / ⚠).
- Drives the **agent-status indicator** with five states, each with
  a glyph and a visible text label:
  - `idle` (●, gray, "Idle")
  - `thinking` (braille spinner, yellow, "Thinking…")
  - `waiting` (◉, blue, "Waiting for you" — set on `ask_user`)
  - `stuck` (⚠ pulsing, red, after 90 s of no progress events)
  - `disconnected` (○, orange, on WS close — chat banner explains
    why with a copy of the WS close code/reason and recent traffic).
- Renders a per-turn **stats footer** under each `agent_message`:
  cost (formatted as `Nm¢` / `N¢` / `$N`), elapsed (`Nms` / `N s` /
  `Nm Ns`), and tokens (`input↑ output↓` plus `cached`). Hover for
  full per-field breakdown.
- Persists chat-input history (last 50 prompts) in localStorage.
  ArrowUp / ArrowDown navigate; ArrowDown past newest restores the
  in-progress draft (bash-readline pattern).

`web/feedback.js` is loaded alongside and provides the
"point at this thing and tell us what's wrong" overlay: it captures
a screenshot, the rrweb session, the WS log, the current camera and
target, and POSTs them as a multipart bundle to `/feedback`. The
bundles are how UI bugs get debugged without round-trips to the
user.

The vendor libraries (three.js + addons, rrweb, html2canvas) are
pinned and downloaded by `scripts/fetch-vendor.sh` into
`web/vendor/`. Nothing on the page loads from a third-party CDN
at runtime.

---

## 10. Server, sessions, and concurrency

`server/app.py` mounts the static `web/` directory and registers
three endpoints: `/ws`, `/feedback`, `/healthz`.

A **session** is a per-WebSocket `WSContext` containing:

- The `SessionState` (current source + parse cache + history).
- The transcript of agent messages this session.
- A `pending_ask` slot used to validate `user_choice` replies.
- The `ws_log` buffer used by feedback bundles.
- A `prompt_times` deque — the per-session rate-limit window.
- An `asyncio.Lock` serialising all outbound sends (the agent runs
  in a worker thread; progress events fire off the executor; the
  lock keeps frame ordering coherent).

Agent turns run on a thread-pool executor so the WS event loop stays
free to flush progress events. The bridge from the executor thread
back to asyncio is `asyncio.run_coroutine_threadsafe`. A
`RuntimeError`-after-close in `_send` is swallowed silently — a
client that disconnects mid-turn must not poison the server's
state.

Sessions are **in-memory and not shared** between connections. Two
browsers attached to the same instance get two independent scenes.
This is by design (single-user per host) and is one of the
properties the deploy stack relies on (one VM = one user).

---

## 11. Security posture

The deployed surface is one Caddy instance fronting one fastcad on
loopback. The Anthropic API key is the highest-value asset on the
host; the secondary concerns are abuse via the prompt path
(burning the key) and code execution via the subprocess deps.

| Layer | Defense |
|-------|---------|
| Network edge | Cloud firewall (only :22 + :80 + :443). UFW on the host. SSH key-only. |
| TLS | Caddy automatic Let's Encrypt; HSTS + strict CSP + standard hardening headers. |
| Auth | basic_auth over TLS with bcrypt-hashed password. fail2ban bans IPs after 5 × 401 in 10 min. |
| WebSocket | Origin allow-list, frame size cap (64 KB), JSON validation, message-type whitelist, control-character stripping on prompt text. |
| Rate limiting | Caddy per-IP burst (30 / 10 s) + sustained (600 / hr); per-session prompt cap (10 / 60 s burst, 200 / day). |
| Auth → app | fastcad binds 127.0.0.1 only — Caddy is the single ingress path. |
| Subprocess | Dedicated `fastcad` UID, systemd `Protect*` directives, syscall filter, capability drop, MemoryMax / CPUQuota / TasksMax. |
| Subagent file I/O | Slug whitelist (`^[a-z0-9][a-z0-9-]{0,63}$`) with resolved-path containment check before any filesystem read or write. |
| API key | `/etc/fastcad/fastcad.env` mode 0600 owned by root; loaded by systemd before privilege drop. The fastcad UID never has read access to the file itself — only the env vars. |
| Cost cap | Anthropic Console spend cap on the key is the real backstop. |

The threat model accepts that a single authenticated user is
trusted with the API-key spend potential — the per-session rate
limit and the spend cap on the key are the in-depth defenses
against an account compromise.

See `deploy/README.md` for the operational view.

---

## 12. Testing

| Layer | Where | How |
|-------|-------|-----|
| Parser | `tests/unit/test_scad_parser.py` | All grammar productions; round-trip; rejection of out-of-scope features. |
| Evaluator | `tests/unit/test_scad_eval.py` | Each primitive + transform + extrude path. |
| Kernel | `tests/unit/test_kernel*.py` | manifold3d wrapper invariants and mesh-to-dict. |
| Diff cache | `tests/unit/test_spec_diff.py` | Hit / miss / removed / dependency-aware re-eval. |
| Validator | `tests/unit/test_validate.py` | Each schema field; the slice-z-trap fix; paper-thin axial-section detection. |
| Sections | `tests/unit/test_sections.py` | Extraction on cube / cylinder / oblique; PNG render; metrics on synthesised correct + broken threads. |
| Critics | `tests/unit/test_critic.py` | Orchestrator; per-critic shape; persistence detection. |
| Tools | `tests/unit/test_agent_tools.py` | Each tool dispatch; `inspect_section` axes + oblique + error paths. |
| Sanitization | `tests/unit/test_security.py` | Slug whitelist; topic sanitizer; prompt cleaner; rate limiter. |
| Sessions / WS | `tests/unit/test_session.py`, `test_ws.py` | State transitions; message dispatch. |
| End-to-end | `tests/e2e/` | Playwright headless Chromium against a live dev server. |

The unit suite is fast (~5 s) and runs without the browser, without
the Anthropic API, and without OpenSCAD installed (vision-critic
paths skip cleanly when the renderer isn't available). The e2e
suite needs Playwright + Chromium and exercises the WS protocol
against the deployed page.

---

## 13. Deployment

The serving stack is intentionally minimal. See `deploy/README.md`
for the full flow; in summary:

- One VM (Ubuntu 24.04 LTS) on a public IP with ports 80 / 443
  open. GCP `e2-small` is the recommended machine type; the
  always-free `e2-micro` works for light loads.
- Caddy 2 in front, with automatic Let's Encrypt, basic_auth, the
  ratelimit module, and hardening headers.
- fastcad as a hardened systemd unit, bound to 127.0.0.1, running
  as a dedicated `fastcad` user.
- fail2ban watching Caddy's access log for 401 spikes.
- The bootstrap script (`deploy/bootstrap.sh`) is idempotent — it
  prompts for the parameters, generates a strong password, and
  installs everything in one pass.

What's deliberately absent: container runtime, managed service
dependencies (Cloud Run / App Service / Container Apps), reverse-
proxy provider in front of Caddy, secret store integration, load
balancer. fastcad's session model is single-instance by design;
adding orchestration buys nothing and adds drift.

---

## 14. Design rules to preserve

These rules are enforced by tests in `tests/unit/`, but the tests
don't substitute for understanding. Before changing any of the
files listed in `CLAUDE.md`'s "Critical Design Rules" section:

- **`kernel.py` is the only place that imports `manifold3d`.** Every
  other module treats `Manifold` as opaque. Swapping the CSG kernel
  later must remain local to that file.
- **`SessionState` is the render source. The `.scad` file is
  export only.** If renderer and `.scad` ever disagree, the
  renderer is correct by definition; fix the export path.
- **Per-id mesh map in `web/main.js` is incremental rendering.** A
  `scene_delta` must touch only ids in `added` / `updated` /
  `removed`. Wholesale rebuilds are reserved for `scene_init`
  (sent on undo / redo / reset).
- **Every UI change ships with a Playwright test.** No blind CSS /
  JS edits. If you change a `data-testid` or a UI flow, update the
  test in the same commit.
- **No hardcoded part specifics in the agent's system prompt or
  test fixtures.** Each design researches itself; baking specifics
  into the prompt or the test corpus overfits the agent at the
  prompt level. Per-part details belong in the research cache,
  with a code-review surface.

These constraints are what keep the system coherent across
versions and contributors. Read them before any large change to
the corresponding modules.
