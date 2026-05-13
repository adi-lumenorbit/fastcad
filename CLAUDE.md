# fastcad — Claude Code Instructions

AI-driven incremental 3D modeler. Local web app: chat → CSG ops →
three.js viewer + `.scad` export. Single-user, runs on Ubuntu/WSL with the
browser on Windows.

## Communication Rules

- **Do not jump the gun.** When the user is asking questions or thinking
  out loud, ANSWER the questions. Do NOT start making code changes,
  deleting files, or refactoring until the user explicitly asks you to
  implement something.
- If unsure whether the user wants a change or is just exploring options, ASK.
- A question is not a request. "Why do we need X?" does not mean "delete X."
- Wait for a clear instruction like "go", "do it", "implement this", or
  similar before making changes.
- **Plan rejection means back to planning.** If the user rejects a plan or
  says "hang on" / "wait" / "not yet", stay in plan mode. Update the plan,
  present it again, get approval, then code.
- **Diagnose before fixing.** When a bug is reported, do a deep root-cause
  analysis before proposing code changes. Trace the failure path and
  explain WHY the design allowed it. Only then propose fixes.
- **Fix by making consistent, not by removing.** When something looks
  wrong, fix it by making it match the rest of the system — not by
  removing it or adding a special case.
- **Do not read files from other projects.** Stay within this repo.

## No hardcoded designs in prompts, plans, or specs

The agent's system prompt MUST NOT contain worked examples with
specific dimensions, standards lookups, or sample implementations of
a particular part. Each user request drives an independent recall-
and-design pass; baking specifics into the prompt teaches the agent
the wrong patterns and propagates bugs verbatim. We learned this the
hard way — a worked M3-screw example in `agent/system_prompt.py` had
a 12-start-thread bug, and the agent dutifully copied it.

This applies to:
- Tables of standardized dimensions (M-series threads, DIN/ISO head
  specs, NEMA frames, ISO bolts, etc.).
- Sample `.scad` source for a particular part baked into the prompt.
- "Worked example" sections that demonstrate one specific design.
- Plan files in `docs/plans/` that include reference dimensions in
  prose. The plan describes the architecture, not the implementation
  for a specific part.
- Test fixtures or test names that pin to a particular standard.
  Use neutral names like `test_threaded_extrude_fixture_*`. **Both
  the identifier *and* the fixture content matter**: if the test
  body recapitulates a specific standard ("here's an M3 spec, here's
  the matching .scad, assert they line up"), that's the same risk
  as a worked example in the prompt — the implementation pattern
  ends up reinforced via the agent's training/eval feedback loop
  and overfits to that one part. Test inputs may use any numbers
  (they're just numbers), but neither the identifier nor the
  fixture's structural content should encode a specific standard.

  Why this matters: the agent reads its own past output through the
  test corpus when iterating on prompts/system messages. A test
  asserting "for M3 the answer is X" steers the agent toward X for
  M3 specifically, even when the architectural intent was "for any
  standardized fastener, the validator catches Y." Overfitting at
  the prompt level breaks generalization.

What IS allowed in the system prompt:
- The OpenSCAD subset language description.
- Tool descriptions.
- Style / formatting / output conventions (the "beautify" rules).
- General modeling principles ("single-start threads by default",
  "state the standard you assumed", "recall, don't approximate").
- Pointers to the research cache (`docs/research/` once Stage 2
  ships) and how to use it.

If you find yourself adding a worked example or a dimension table to
the prompt, stop. The agent is capable of recalling the relevant
spec from its training — the example just constrains it to your
particular interpretation. For one-off references during a
conversation, use a chat reply, not a committed file.

This is parallel to the "no auto-memory" principle in `## Memory`
below: durable knowledge goes in repo files, not in the agent's
runtime context.

## Critical Design Rules

> **Before any change to `src/fastcad/model/kernel.py`,
> `src/fastcad/model/scad_eval.py`, `src/fastcad/model/scad_parser.py`,
> `src/fastcad/session.py`, `src/fastcad/agent/tools.py`, or
> `web/main.js`** — read the rules below. Tests in `tests/unit/`
> and `tests/equivalence/` enforce most of these contracts.

- **`kernel.py` is the only place that imports `manifold3d`.** All other
  modules treat `Manifold` as opaque. Swapping kernels later must be local
  to this file.
- **`session.current_source` is the single source of truth.** The whole
  scene is derived from this one OpenSCAD-compatible source string;
  there is no separate op log or scene graph that could drift. Every
  mutation goes through `SessionState.set_source(text)`, which parses,
  validates, runs the dependency-aware re-evaluation, and pushes the
  previous spec onto the undo stack. The `.scad` source is both the
  render input and the export — what the user sees IS what they get.
- **Every new parser/evaluator construct ships with an equivalence
  fixture.** Drop a small `.scad` file in `tests/equivalence/fixtures/`
  exercising the construct; the suite renders it through both
  OpenSCAD's CLI and fastcad's evaluator and asserts the resulting
  solid agrees on volume + bbox within tight tolerance. This is the
  long-running net against silent geometry divergences (it caught the
  rotate-axis-angle, cylinder-cone, 2D-translate, and twist-sign
  bugs).
- **Per-id mesh map in `web/main.js` is incremental rendering.** A
  `scene_delta` must only touch meshes whose ids are in `added` /
  `updated` / `removed`. Wholesale rebuilds are reserved for
  `scene_init` (sent on undo/redo/reset/open_scad).
- **Every UI change ships with a Playwright test.** No blind CSS/JS edits.
  See `tests/e2e/`. If you change a `data-testid` or a UI flow, update
  the test in the same commit.
- **Feedback bundles in `tmp/feedback/<ts>/` are how UI bugs are
  debugged.** When the user reports something, read those files; do not
  ask for re-screenshots.

## Project Overview

```
src/fastcad/
  __main__.py              uvicorn entry (`python -m fastcad`)
  session.py               SessionState: current_source + parse cache + undo/redo
  server/
    app.py                 FastAPI app, static mount, /ws, /feedback, /healthz
    ws.py                  WebSocket session loop (message protocol)
    feedback.py            POST /feedback handler -> tmp/feedback/<ts>/
  model/
    kernel.py              manifold3d wrappers + mesh-to-dict
    scad_parser.py         lark grammar + AST for the OpenSCAD subset
    scad_eval.py           AST -> manifold; built-in primitives, transforms, CSG
    spec_diff.py           dependency-aware per-node re-evaluation cache
    sections.py            2D cross-section extraction + PNG + metrics
    validate.py            Channel 1 — structural validator
    render.py              Channel 2 — OpenSCAD CLI -> canonical-angle PNGs
  agent/
    client.py              Anthropic tool-use loop + ANTHROPIC_FAKE mode
    tools.py               tool schemas + dispatcher
    system_prompt.py       modeling-agent system prompt
    critics/               vision critic orchestrator + per-critic modules
web/
  index.html               two-pane layout (viewer + chat) + Open .scad dialog
  main.js                  three.js viewer, WS client, chat, section feature
  feedback.js              point-mode overlay, rrweb, capture bundle POST
  vendor/                  pinned three.js + OrbitControls + TransformControls
                           + rrweb + html2canvas (gitignored)
tests/
  unit/                    fast, no-network, no-browser
  e2e/                     Playwright headless Chromium (skips if missing)
  equivalence/             OpenSCAD-vs-fastcad fixture suite — each fixture
                           rendered by both engines, volume + bbox compared
                           (skips if the `openscad` CLI isn't on PATH)
scripts/
  dev.sh                   uvicorn dev server
  fetch-vendor.sh          one-time download of browser libs into web/vendor/
  e2e.sh                   pytest tests/e2e
docs/plans/                NN-slug.md per CLAUDE-issue.md convention
docs/specs/                normative specs (e.g. SCAD-conversation comments)
tmp/feedback/<ts>/         user-submitted bug bundles (description, rrweb,
                           screenshots, ws log, camera, target, scad source)
```

## Setup

First time on a fresh checkout (or fresh WSL):

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium    # only needed for e2e
bash scripts/fetch-vendor.sh             # downloads three.js / rrweb / html2canvas
```

Set `ANTHROPIC_API_KEY` in the environment before running the dev server,
unless you set `ANTHROPIC_FAKE=1` (deterministic offline mode used by tests).

## Running

```
bash scripts/dev.sh                      # http://localhost:8765/
```

WSL2 forwards localhost; the Windows browser reaches it as
`http://localhost:8765/` directly. If Windows Defender Firewall blocks
inbound TCP on first run, allow port 8765.

## Running Tests

```
.venv/bin/pytest tests/unit -q           # fast, always available
bash scripts/e2e.sh                       # Playwright; needs chromium installed
```

## Key Conventions

- Python: 3.11+, type hints everywhere, dataclasses for value types,
  `from __future__ import annotations` at the top of every file.
- Pydantic only for HTTP/WS payload validation if/when needed; the
  internal model uses plain dataclasses.
- Coordinates are millimeters; +Z is up. Anchor names: `origin`, `top`,
  `bottom`, `center`. Adding a new anchor is a `scene.resolve_anchor` +
  `tools.py` change; do not invent ad-hoc keywords elsewhere.
- WebSocket message types: `scene_init`, `scene_delta`, `agent_message`
  (carries `text` + per-turn `stats`), `ask_user`, `tool_log` (server
  ships, frontend keeps in `wsLog` but doesn't render — progress panel
  is the canonical live view), `progress`, `scad`, `error` (out);
  `prompt`, `user_choice`, `undo`, `redo`, `reset`, `export_scad`,
  `open_scad` (server-side path), `ws_log_request` (in). Transport is
  uvicorn's `websockets-sansio` driver to avoid the legacy
  keepalive_ping race.
- Mesh transport: positions = base64(float32 flat xyz), indices =
  base64(uint32 flat triangle list). Decoded in `web/main.js`.
- Ids are stable strings: `<kind>_<n>` (e.g. `cube_1`). Tests rely on this.
- Frontend test hooks: `data-testid="..."` on interactive elements,
  `window.fastcad.{meshMap, scene, camera, wsLog, ready, snapshotViewer,
  cameraState, send}` for Playwright introspection.

## Banned Bash Patterns — NEVER USE

These trigger security prompts that block the console. Every violation
wastes user time. Use the listed alternative instead.

### Compound commands — NEVER combine in one Bash call

| Banned | Why | Use instead |
|--------|-----|-------------|
| `cd dir && git ...` | "bare repository attack" prompt | `git -C <path> ...` |
| `cd dir && gh ...` | same | `gh -R owner/repo ...` |
| `cmd1 && cmd2` | metachar prompt | separate Bash calls |
| `cmd1 ; cmd2` | metachar prompt | separate Bash calls |
| `cmd1 \|\| cmd2` | metachar prompt | separate Bash calls |
| `cd dir` + newline + `cmd` | compound command | `git -C` or separate calls |

### Shell operators — NEVER use in Bash

| Banned | Why | Use instead |
|--------|-----|-------------|
| `$(...)` | "shell operators" prompt | Write tool + `git commit -F tmp/commit-msg.txt` |
| heredocs (`<<`, `<<'EOF'`) | "shell operators" prompt | Write tool to create file, then run it |
| `>`, `<`, `>>` redirects | "output redirection" prompt | Write tool to create files |
| `2>&1` | redirect, not pipe | drop entirely (stderr flows to terminal) |
| `\;`, `\|` backslash-escapes | "backslash before operator" prompt | temp script in `tmp/` |
| `python -c "..."` | metachar prompts on quotes | Write to `tmp/*.py`, then `python3 tmp/script.py` |
| `python3 << 'EOF'` | heredoc prompt | same |

### Tool misuse — use dedicated tools

| Banned | Why | Use instead |
|--------|-----|-------------|
| `grep`/`rg` as primary command | metachar prompts on `&`, `\|`, `(` in patterns | Grep tool |
| `find` | same | Glob tool |
| `cat`/`head`/`tail` | same | Read tool |
| `git show ... \| grep` | piped git output triggers prompts | Grep tool, or `git show <ref>:<path>` (no pipe) |

### Destructive commands — NEVER use without explicit user request

| Banned | Why |
|--------|-----|
| `rm`, `rm -rf` | file deletion |
| `git rm` | tracked file deletion |
| `git reset --hard` | discards uncommitted work |
| `git clean -f` | deletes untracked files |
| `git push --force` / `-f` | overwrites remote history |
| `git stash drop` | discards stashed work |

### Path rules

- **Bash**: relative paths only. NEVER `/home/...` or any absolute path.
- **Read/Write/Edit tools**: absolute paths are OK (these tools require them).
- **git**: always `git -C <relative-path>` — never `cd` + `git`.

### Multi-pipe chains — NEVER use inline

| Banned | Why | Use instead |
|--------|-----|-------------|
| `ps aux \| grep X \| grep -v grep \| awk ...` | multi-pipe triggers prompt | Write to `tmp/*.sh` or `tmp/*.py`, run the script |
| Any chain with `\| awk`, `\| sed`, `\| cut` | triggers prompt | tmp script |

For process management (find PID, kill, restart), ALWAYS write a tmp script.

### What IS allowed

- Single commands with simple arguments
- ONE output pipe for filtering: `cmd | head`, `cmd | tail`, `cmd | grep`, `cmd | wc`
- `git -C path <subcommand>`

### WSL-specific bans

| Banned | Why | Use instead |
|--------|-----|-------------|
| `set -e` in scripts | invalid option on WSL bash | omit or use `set -o errexit` |
| backslash line continuations | breaks on WSL/CRLF | single-line commands or `--body-file` |

### Script Directories

| Directory | Purpose | Auto-approved |
|-----------|---------|---------------|
| `tmp/ro/` | Read-only checks, diagnostics | Yes |
| `tmp/rw/` | State-changing scripts | Selectively |
| `tmp/danger/` | Destructive operations | Never |

Write new scripts to the appropriate directory.

## Plans

All plans MUST be saved in `docs/plans/` as `NN-slug.md`. Every plan must
include:
- **Push/merge instructions**: explicit steps for how the changes get
  committed, pushed, and (if applicable) merged via PR.
- **Verification steps**: how to confirm the plan was executed correctly.

### Plan file location — never use `~/.claude/plans/`

NEVER write durable plans to `~/.claude/plans/` (the harness plan-mode
directory). That directory is per-machine, outside the repo, invisible
to PR review, and disappears when the harness session ends.

The harness plan-mode file (e.g. `~/.claude/plans/<adjective-noun>.md`)
is a transient scratch the harness creates when entering plan mode.
**Treat it as a draft to relocate.** As soon as the plan is meant to
survive the session — or whenever a user asks to save / share / review
it — move its content to `docs/plans/<NN>-<slug>.md` (or
`docs/plans/<slug>-draft.md` if no GitHub issue is filed yet) and
delete the harness file so there's no parallel copy that can drift.

Same principle as the "no auto-memory" rule below: durable knowledge
goes in repo files. The harness scratch is a working surface, not a
home.

## Issue Workflow

Every issue or work item should have an associated `docs/plans/NN-slug.md`
file. File the GitHub issue first to obtain the number, then create the
plan file. See `CLAUDE-issue.md` for the detailed process.

Title prefix: `NN — Title` (zero-padded issue number, em dash). Body plan
link: `[NN-slug.md]({REPO_URL}/blob/main/docs/plans/NN-slug.md)`. Source of
truth: `docs/plans/00-matrix.md`.

## Permissions

- Run read-only commands without asking for confirmation. NEVER block the
  console waiting for approval on read-only operations.
- No destructive git commands without explicit user request.
- Prefer editing existing files over creating new ones.
- Git commit messages via file: Write tool → `tmp/commit-msg.txt`, then
  `git commit -F tmp/commit-msg.txt`.
- PR bodies via file: Write tool → `tmp/pr-body.txt`, then
  `gh pr create --body-file tmp/pr-body.txt`.
- File issues for discovered problems — don't ad-hoc fix tangents.

## Subagents

Every subagent prompt MUST include: "Use Grep/Glob/Read tools, not
grep/find/cat. No heredocs, redirects, `$(...)`, compound commands. Use
`git -C`. ONE command per Bash call. Relative paths only in Bash."

## Memory

Do not use Claude Code's auto-memory. ALL durable knowledge goes in repo
files: `CLAUDE.md` for behavioral rules, `docs/` for context,
`docs/plans/` for plans, GitHub Issues for work tracking.
