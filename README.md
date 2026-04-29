# fastcad

AI-driven incremental 3D modeler — OpenSCAD-compatible export, three.js
viewer, Anthropic Claude as the modeling agent. Local web app, runs on
Ubuntu/WSL with the browser on Windows.

## Quick start (WSL)

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium      # only needed for e2e tests
bash scripts/fetch-vendor.sh                # downloads three.js, rrweb, html2canvas
export ANTHROPIC_API_KEY=sk-ant-...
bash scripts/dev.sh                         # http://localhost:8765/
```

Open `http://localhost:8765/` in your Windows browser. WSL2 forwards
localhost automatically. If Windows Defender Firewall blocks inbound TCP
on first run, allow port 8765.

## What it does

- Type a prompt: *"Make a 20mm cube"*, *"Add a 10mm sphere on top
  centered"*, *"Subtract a 5mm cylinder through it"*.
- The agent picks references from the current scene and emits CSG ops.
- Each step re-meshes only the affected branch — the rest of the scene
  stays put. (Real OpenSCAD recompiles everything.)
- Undo / Redo per step.
- Export `.scad` at any time and open it in real OpenSCAD.

## Tests

```
.venv/bin/pytest tests/unit -q             # 42 unit tests (kernel, scene, ops,
                                           # session, scad, agent, ws, feedback)
bash scripts/e2e.sh                         # Playwright headless Chromium
```

E2E tests skip cleanly if Chromium isn't installed, so a fresh `pytest`
won't fail on a machine that hasn't run `playwright install` yet.

## Reporting UI bugs

Click **Send Feedback** in the chat pane (toggle **Point** first to anchor
the report to a specific element). The bundle lands in
`tmp/feedback/<timestamp>/` containing:

- `description.txt` — your text
- `target.json` — selector + bounding rect of the pointed element
- `rrweb.json` — last ~60s of DOM/input events
- `dom.png` — `html2canvas` snapshot of the page
- `viewer.png` — three.js canvas snapshot
- `camera.json` — camera position + target
- `oplog.json` — server-side op log up to that moment
- `ws_log.json` — last 200 WebSocket messages

The Claude agent reads those files directly to debug — no need to attach
screenshots in chat.

## Offline / deterministic mode

`ANTHROPIC_FAKE=1` swaps the real Anthropic client for a regex-driven
fake that handles a small set of demo prompts deterministically. Used by
all e2e tests; useful for offline development.

## Architecture

See `docs/plans/01-bootstrap.md` for the bootstrap plan and `CLAUDE.md`
for project-wide conventions.
