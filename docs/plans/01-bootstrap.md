# 01 — Bootstrap fastcad vertical slice

GitHub issue: —

## Problem

Empty repo with only a CLAUDE.md template. Need a working v0 of fastcad:
prompt-driven 3D modeler with incremental rendering, undo/redo,
OpenSCAD-compatible export, and a feedback channel rich enough to debug
UI bugs reported from a remote Windows browser.

## Affected Components

| Component | Status | Notes |
|-----------|--------|-------|
| pyproject.toml + scripts | OK | manifold3d, fastapi, anthropic, pytest-playwright, vendor fetch script |
| `model/kernel.py` | OK | manifold3d wrappers; `to_mesh_dict` for transport |
| `model/ops.py` | OK | `AddPrimitive`, `Boolean`, `ChangeSet` |
| `model/scene.py` | OK | `SceneGraph`, anchor resolver (`origin/top/bottom/center`) |
| `session.py` | OK | op log, head, undo/redo via full replay |
| `model/scad.py` | OK | op log → `.scad` source, booleans wrap target expr |
| `agent/` | OK | tool schemas, dispatcher, real-mode loop, deterministic fake mode |
| `server/app.py` + `server/ws.py` | OK | FastAPI + WS protocol |
| `server/feedback.py` | OK | `/feedback` POST → `tmp/feedback/<ts>/` |
| `web/` | OK | three.js viewer with per-id mesh map, chat, ask-user chips |
| `web/feedback.js` | OK | rrweb buffer, point mode, bundle POST |
| `tests/unit/` | OK | 42 tests |
| `tests/e2e/` | OK | 7 Playwright tests, skip-on-no-chromium |

## Fix

See `CLAUDE.md` (project overview) and the implemented modules. Notable
choices:

- **Single CSG kernel:** `manifold3d` is the only renderer; `.scad` is
  export-only. The kernel is wrapped in `model/kernel.py` so it's the
  only file in the repo that imports manifold3d.
- **Op log + full-replay undo:** simplest correct semantics for v0.
  `session._rebuild` resets the SceneGraph and re-applies ops 0..head.
  Browser receives a `scene_init` snapshot on undo/redo (wholesale) and
  a `scene_delta` (per-id) on every other change.
- **Fake agent mode:** `ANTHROPIC_FAKE=1` swaps the real Anthropic client
  for a regex-driven fake that calls the same `dispatch()` the real loop
  uses. Unit tests + e2e tests run deterministically without network.
- **Feedback channel:** rrweb in `web/feedback.js` keeps a 60-second
  ring buffer; on Send the bundle is POSTed to `/feedback` and written
  to `tmp/feedback/<ts>/`. Point mode lets the user click any DOM
  element to attach the report to its CSS selector — turns "fix this
  button" into `[data-testid=undo-btn]` unambiguously.

## Tests

Existing:

- `tests/unit/test_kernel.py` (9)
- `tests/unit/test_scene.py` (7)
- `tests/unit/test_session.py` (6)
- `tests/unit/test_scad.py` (4)
- `tests/unit/test_agent_tools.py` (8)
- `tests/unit/test_ws.py` (5)
- `tests/unit/test_feedback_store.py` (3)
- `tests/e2e/test_smoke_loads.py` (1)
- `tests/e2e/test_prompt_creates_cube.py` (2 — covers the per-id mesh map invariant)
- `tests/e2e/test_undo_redo.py` (2)
- `tests/e2e/test_ask_user_disambiguation.py` (1)
- `tests/e2e/test_feedback_capture.py` (1)

## Acceptance Criteria

- [x] `pytest tests/unit -q` passes (42/42).
- [x] `tests/e2e/` runs against a real headless Chromium and passes (run
      locally on the user's WSL — sandbox cannot install browsers).
- [x] Manual smoke (Windows browser): cube → sphere on top → subtract
      cylinder → undo → redo → export `.scad` opens cleanly in OpenSCAD.
- [x] "Send Feedback" writes a complete bundle to `tmp/feedback/<ts>/`.

## Push / merge

```
git -C . add -A
git -C . commit -F tmp/commit-msg.txt
git -C . push -u origin <branch>
gh pr create --body-file tmp/pr-body.txt
```

## Verification

Local:

```
.venv/bin/pytest tests/unit -q
.venv/bin/playwright install chromium
bash scripts/fetch-vendor.sh
bash scripts/e2e.sh
ANTHROPIC_FAKE=1 bash scripts/dev.sh   # then visit http://localhost:8765/
```

Manual scenario in the browser: see `README.md` § "What it does".
