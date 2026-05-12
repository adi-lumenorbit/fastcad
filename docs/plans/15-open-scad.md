# 15 — Open .scad button + conversation comments per spec

GitHub issue: https://github.com/adi-lumenorbit/fastcad/issues/15

## Problem

fastcad has `Export .scad` but no inverse: a user with an existing
`.scad` file (one they wrote, or one a different LLM produced) cannot
load it into a fastcad session for further editing. The roadmap row
"`.scad` import (round-trip from disk)" in `docs/plans/00-matrix.md`
has been outstanding since Stage 1.

The natural form of that feature has now become more useful: with the
SCAD-conversation comment spec (companion spec in
`~/src/3d-models/docs/scad-conversation-spec.md`), an opened `.scad`
carries its own design conversation as `fc-meta` / `fc-prompt` /
`fc-decision` / `fc-note` comments. fastcad should both **emit** those
comments when the agent makes changes, and **preserve** them on open so
that a future session resumes with full design history visible.

The original CLAUDE.md description (".scad is export only — never used
as input") was written for the pre-Stage-1 op-log architecture and
contradicts the post-Stage-1 code: `session.current_source` IS the
`.scad` text, and `set_source(text)` is the universal mutation
entrypoint. Adding `open_scad` is therefore architecturally trivial —
it's `read_text(path)` followed by `session.set_source`.

## Affected Components

| Component | Status | Notes |
|-----------|--------|-------|
| `web/index.html` | MISS | Add `<button id="open-btn">` + open-dialog markup. |
| `web/main.js` | MISS | Wire the button, send `open_scad` WS message, handle errors. |
| `web/style.css` | MISS | Style the dialog. |
| `src/fastcad/server/ws.py` | MISS | New `open_scad` inbound type, path validation, `set_source` call. |
| `src/fastcad/agent/system_prompt.py` | MISS | Add the `fc-*` comment-writing rules from the spec. |
| `docs/specs/scad-conversation-comments.md` | MISS | Vendor the spec into the fastcad repo (one source of truth in each consumer is OK; cross-link to the canonical version). |
| `tests/unit/test_open_scad.py` | MISS | Unit: path validation, WS handler success / error paths. |
| `tests/e2e/test_open_scad.py` | MISS | Playwright: click Open, enter path, see scene update. |
| `CLAUDE.md` | BUG | Stale: ".scad is export only" no longer matches code. Update post-Stage-1 description (out of scope for this issue but flagged). |

## Fix

### 1. WS protocol — `open_scad`

Add to `_ALLOWED_TYPES` in `src/fastcad/server/ws.py`. Inbound payload:

```json
{ "type": "open_scad", "path": "/home/adi/src/3d-models/tightening_bearing.scad" }
```

Server-side validation:

- `path` must be a string, max 4096 chars.
- `path` must resolve to a regular file (no symlinks-out-of-allowlist,
  no directories).
- `path` must end in `.scad` (case-insensitive).
- File size cap: 1 MiB. A larger `.scad` is almost certainly not a
  spec the parser can handle; reject before reading.
- `path` must be inside one of an allow-listed set of directories.
  Default allow-list:
    - `$HOME/src/3d-models`
    - The fastcad repo root (so the dev can experiment with checked-in
      `.scad` fixtures).
  Override via env var `FASTCAD_OPEN_ALLOWED_DIRS` (colon-separated).
  Outside the allow-list ⇒ reject with a clear error message.

Path resolution uses `Path.resolve(strict=True)` to canonicalize and
fail on missing files. The resolved real path must have one of the
allow-listed dirs as an ancestor (via `Path.is_relative_to`).

On a clean read + `set_source` success:

- Emit `scene_init` (the existing message — opening a file is a
  wholesale replacement, not a delta).
- Emit `agent_message` summarizing what was loaded (file name,
  byte count, top-level statement count, and, if the file contains a
  `fc-meta` block with a `title`, that title).
- Push the previous spec to the undo stack — `set_source` already does
  this, so undo works for free.

On any error (validation, read failure, parse / eval failure):

- Emit `error` with a human-readable message. No state mutation.
- Do NOT clear undo/redo.

### 2. Web UI

In `web/index.html`, add a button after `export-btn`:

```html
<button id="open-btn" data-testid="open-btn" type="button">Open .scad</button>
```

Plus a hidden modal/dialog:

```html
<dialog id="open-dialog" data-testid="open-dialog">
  <form method="dialog">
    <label for="open-path-input">Server path to .scad:</label>
    <input id="open-path-input" data-testid="open-path-input"
           type="text" placeholder="/home/adi/src/3d-models/foo.scad" />
    <p id="open-error" data-testid="open-error" hidden></p>
    <menu>
      <button type="button" id="open-cancel-btn"
              data-testid="open-cancel-btn">Cancel</button>
      <button type="submit" id="open-confirm-btn"
              data-testid="open-confirm-btn">Open</button>
    </menu>
  </form>
</dialog>
```

In `web/main.js`, wire:

- `open-btn` click → `dialog.showModal()`.
- `open-confirm-btn` submit → send `{type: "open_scad", path: input.value}`.
- Inbound `error` after an `open_scad` send → show in `open-error`,
  keep dialog open.
- Inbound `scene_init` after an `open_scad` send → close dialog,
  surface the `agent_message` in the chat panel as usual.

Styling in `web/style.css` to match the existing tool-strip aesthetic.

### 3. Agent system prompt — `fc-*` comment writing rules

In `src/fastcad/agent/system_prompt.py`, append a section (paraphrased
to the agent's existing voice, not verbatim from the spec) that:

- Tells the agent to emit a `fc-meta` block at the top of a fresh `.scad`.
- For each turn, place an `// fc-prompt: <user text>` line before the
  parameter, module, or statement that turn affects.
- When the user picks between options, record as
  `// fc-decision: <topic> = <choice>`.
- For non-obvious design constraints, add `// fc-note: <reason>`.
- Tokens in conversational order; no `fc-prompt` mid-statement.

The spec itself is vendored into the fastcad repo (next bullet) so the
agent can be pointed at it from the system prompt without breaking the
"no hardcoded designs in prompts" rule — the spec is a *convention*,
not a worked design.

### 4. Vendor the spec

Copy `~/src/3d-models/docs/scad-conversation-spec.md` to
`docs/specs/scad-conversation-comments.md` in the fastcad repo. Add a
note at the top:

```
Canonical version: ~/src/3d-models/docs/scad-conversation-spec.md
Vendored here so fastcad's checked-in tests and agent system prompt
have a stable reference. Update both copies in lockstep when the spec
changes.
```

### 5. CLAUDE.md correction (flag only, do not fix here)

`CLAUDE.md` lines 99-101 describe an op log that no longer exists.
Out of scope for this issue — file a separate cleanup issue after this
one ships. Flag with a `TODO(#XX)` comment in this plan, not in
CLAUDE.md (avoid drive-by edits to a normative file in this PR).

## Tests

### Unit (`tests/unit/test_open_scad.py`)

- `test_path_outside_allowlist_rejected`: a path under `/etc/` is
  rejected.
- `test_path_must_end_in_scad`: `.txt` is rejected.
- `test_missing_file_rejected`: nonexistent path rejected.
- `test_oversize_file_rejected`: > 1 MiB rejected without being read.
- `test_symlink_resolution`: symlink pointing outside the allow-list
  is rejected even if the symlink itself is inside.
- `test_happy_path_loads`: a valid in-allow-list `.scad` produces a
  `scene_init` and `agent_message`.
- `test_invalid_scad_does_not_mutate`: a `.scad` whose parser fails
  leaves `session.current_source` unchanged.
- `test_undo_after_open_restores_previous`: open → undo → previous
  spec is restored.
- `test_fc_meta_title_surfaced`: when the opened file has a
  `fc-meta` block with `title:`, the `agent_message` includes it.

### E2E (`tests/e2e/test_open_scad.py`)

Playwright:

1. Boot dev server, fixtures: a small `tests/e2e/fixtures/cube.scad`
   (10 mm cube + `fc-meta` block).
2. Click `data-testid="open-btn"` → dialog appears.
3. Enter fixture path → click `open-confirm-btn`.
4. Assert: `window.fastcad.scene` now has 1 mesh; the agent panel
   shows the loaded file name.
5. Press `data-testid="undo-btn"`; assert scene is empty again.

Allow-list for tests includes the fixtures directory via
`FASTCAD_OPEN_ALLOWED_DIRS` set in `tests/e2e/conftest.py`.

## Acceptance Criteria

- [ ] `open-btn` visible in the toolbar, accessible by keyboard.
- [ ] Valid path round-trip: open → scene updates → undo restores.
- [ ] Invalid path produces a clear error in the dialog without
      mutating state.
- [ ] Agent emits `fc-*` comments on the next turn after this PR
      (manual verification — the agent is non-deterministic, but the
      system prompt now contains the convention).
- [ ] Unit tests pass (`.venv/bin/pytest tests/unit -q`).
- [ ] E2E test passes (`bash scripts/e2e.sh` with chromium installed).
- [ ] Matrix row updated in `docs/plans/00-matrix.md`.
- [ ] PR opened against `main` from `feat/09-open-scad`. (Branch name
      pre-dates the issue number assignment — keep as-is; the PR title
      uses `15 —`.)

## Push / merge

- Branch: `feat/09-open-scad` (created before issue number was known).
- Commits scoped one per logical unit (WS handler, web UI, system
  prompt, vendored spec, tests). Each commit message references issue:
  `15 — <subject>`.
- PR title: `15 — Open .scad button + conversation comments per spec`.
- PR body: link to this plan, include screenshots of the dialog open
  + a loaded file, and a paragraph on the architecture (re-state the
  CLAUDE.md staleness for reviewers).
- Squash-merge to `main`.
- After merge: file the follow-up CLAUDE.md cleanup issue.

## Verification

After merge:

1. `git -C ../fastcad checkout main && git pull` and rebuild.
2. Start `bash scripts/dev.sh`.
3. In the browser, click **Open .scad**, paste
   `/home/adi/src/3d-models/tightening_bearing.scad`.
4. Confirm the bearing renders, the chat shows the loaded title from
   `fc-meta`, and Undo restores whatever was on screen before.
5. Type a new prompt; confirm the agent's next `set_source` writes
   `fc-prompt` / `fc-decision` / `fc-note` comments per spec.
