# Stage 2 — research subsystem + cache + progress panel (DRAFT)

> **Status:** draft. No GitHub issue filed yet — rename this file to
> `<NN>-stage2-research.md` once the issue exists, per
> `CLAUDE-issue.md`. The companion Phase A cleanup ships first as part
> of PR #3.

## Context

Stage 1 (PR #3) shipped the `.scad`-as-spec architecture and put a
real-Claude turn through its paces on a standardized-part prompt.
Two problems surfaced:

1. **Wrong geometry.** The part the agent rendered wasn't standards-
   compliant — multi-start thread instead of single, wrong minor
   diameter, invented head proportions. Root cause: a worked example
   for a specific standardized part had been baked into the system
   prompt, and the agent dutifully copied its bugs verbatim.
   Implementations belong in the agent's per-turn reasoning, **not**
   in the prompt or any committed reference. The agent's job is to
   recall the right spec from its training each time.

2. **No domain research, no progress visibility, no memory.** When
   the agent does need to look up a standard, it has no way to
   research, no place to cache results, and the user can't see what
   it's doing during long operations.

Phase A (cleanup, in PR #3) fixes (1) — surgical strip of hardcoded
references. This document, Phase B, builds the missing research
subsystem and progress UI.

---

# Phase A — recap (already planned for PR #3)

| File | Change |
|---|---|
| `CLAUDE.md` | New rule: no hardcoded examples / dimensions / standards in system prompts, plans, or specs. Each design self-researches. |
| `src/fastcad/agent/system_prompt.py` | Strip the worked screw example, the standards tables, the head-dimension tables. Keep the language subset doc, tool docs, conventions, and the **beautify rules**. Replace the worked example with a *general* "modeling standardized parts" rule (recall-don't-approximate, single-start threads default, state-your-assumption in chat reply). |
| `docs/plans/02-stage1-scad-spec.md` | Replace screw-specific worked example with a generic threaded-extrude example. Generalize Context's "try" recap. Verification prompts use neutral language ("a standardized fastener of your choice"). |
| `tests/unit/test_scad_parser.py` | Rename screw-specific fixture / test names to neutral ones. Fixture content stays (it's a useful complex test of `linear_extrude` + twist + nested for/rotate/translate/polygon/difference); only identifiers change. |
| `tests/unit/test_scad_eval.py` | Same rename. |

Verification:

```
.venv/bin/pytest tests/unit -q
bash scripts/e2e.sh
grep -rin "<standardized-part-keyword>\|<DIN/ISO ref>" \
  src tests docs CLAUDE.md   # nothing standard-specific should remain
```

Manual real-Claude smoke (post-cleanup):

```
ANTHROPIC_API_KEY=… bash scripts/dev.sh
# In browser: "Design a standardized fastener of your choice."
```

Acceptance: agent reaches for accurate single-start threads and
standard head dims from training, *and* announces its assumed
specification.

---

# Phase B — Stage 2 (this plan)

New GitHub issue, new plan file (rename this draft), new branch
`stage2-research`, new PR. Builds on `main` after Stage 1 merges (or
branches off `stage1-scad-spec` if Stage 1 hasn't merged yet).

## Goal

Give the agent the ability to deeply research a topic — multi-step,
web-aware, autonomous — without leaving fastcad. Cache results
text-based in the repo so re-research isn't needed. Stream progress
to a new UI panel so the user sees what's happening in real time.

## Design

### Research backend: Claude Code CLI subprocess

Decided: Claude Code subprocess (not in-process web_search). The
agent invokes a separate `claude --print` (Claude Code CLI) process
that runs a deep-research workflow autonomously, with full access to
its built-in WebSearch / WebFetch / Read / Write tools. Karpathy-
style: the subagent decides its own search queries, fetches sources,
distills, returns markdown.

Subprocess invocation (sketch):

```
claude --print \
  --model claude-opus-4-7 \
  --output-format stream-json \
  --max-turns 20 \
  --append-system-prompt "$RESEARCH_DIRECTIVE" \
  --permission-mode bypassPermissions \
  "Research <topic> for use in a CAD model. Output a single markdown
   document with sections: 'Canonical name', 'Key dimensions',
   'Variants', 'Sources'. Save to docs/research/<slug>.md when done."
```

`stream-json` so we can parse incremental events for the progress
panel. `--max-turns` bounds cost. The directive system prompt
constrains the subagent: single-document output, dimensions in mm,
sources with links, no executable code.

Run from the repo root so the subagent can write to
`docs/research/`. Subagent's working directory is fastcad's root.

### Cache layout

```
docs/research/
  README.md           # explains the format, agent-readable
  <slug>.md           # one file per researched topic
  ...
```

Each cache file:

```markdown
# <Canonical name of the part>

researched: <YYYY-MM-DD>
researcher: <model> via Claude Code (subagent)
slug: <kebab-case-slug>

## Canonical name
<Standard family / variant identifier>

## Key dimensions
- <field>: <value>
- ...

## Variants
- <variant 1>
- ...

## Sources
- <url>
- ...
```

Format is loose markdown. Agent reads section headings to find what
it needs. The subagent is told to write this exact structure.

Slug rules: kebab-case, descriptive. Subagent picks the slug; main
agent passes through.

### New tools (agent-facing)

Added to `agent/tools.py`:

| Tool | Args | Behavior |
|---|---|---|
| `list_research` | none | Returns `[{slug, title, researched_date}, …]` for every file in `docs/research/`. Cheap; the agent calls this whenever it's modeling a standardized part. |
| `read_research` | `{slug}` | Returns the full markdown content of `docs/research/<slug>.md`. The agent applies the dimensions to its spec. |
| `research` | `{topic, slug?}` | Spawns the Claude Code subagent. Streams progress to WS. On success: writes `docs/research/<slug>.md`, returns the slug + brief summary. Idempotent: if a fresh cache file already exists, returns it without re-running. |

System prompt teaches the agent: "Before modeling a standardized
part, call `list_research` and `read_research(slug)` if a relevant
entry exists. If not, call `research(topic)` and use the resulting
cache file."

### Progress panel UI

New region in `web/index.html`, below the existing chat-log,
inside `chat-pane`:

```
chat-pane
├── chat-log              (existing)
├── ask-user-area         (existing)
├── progress-panel        ← NEW
├── chat-form             (existing)
└── feedback-bar          (existing)
```

`progress-panel` renders a compact tree of current/recent activity:

```
▸ research(<topic>)
    ◷ subagent searching… (12s)
    ✓ 5 sources fetched
    ✓ wrote docs/research/<slug>.md
✓ set_source (1842 chars, +3 modules)
◷ evaluating <module>…
✓ scene_delta: added=[<module>]
```

Each entry is a node with state (◷ running, ✓ done, ✗ error). User
can click to expand and see details (full tool args, subagent
transcript chunks, etc.).

Backed by a new WS message type `progress` with structured events:

```
{
  "type": "progress",
  "id": "evt_42",          # stable per-event id for status updates
  "parent_id": "evt_41",   # nesting (research subagent's chunks under the research call)
  "kind": "tool_call" | "research_chunk" | "research_done" | "scene_delta" | "agent_message" | "error",
  "label": "research(<topic>)",
  "status": "running" | "done" | "error",
  "detail": "...",         # optional expanded content
  "ts": 1714519201.43
}
```

Existing `tool_log` chat messages stay (compatibility) but the new
panel is the primary surface for what's happening.

### Multi-agent / autonomy

The `research` tool *is* the multi-agent boundary. The main agent
issues one `research(topic)` call; that call subprocesses Claude
Code, which is itself a fully agentic loop with web search, file
read/write, and reasoning. The user sees the subagent's running
output in the progress panel.

Future extension: the research tool can spawn N independent
subagents in parallel for unrelated topics. Out of scope for v1.

### Files (Phase B)

| File | Status | Purpose |
|---|---|---|
| `src/fastcad/agent/research.py` | NEW | Subprocess management. `run_research(topic, slug, on_chunk)` spawns `claude --print --output-format stream-json …`, parses events, calls `on_chunk` for streaming, returns final summary + cache path. |
| `src/fastcad/agent/tools.py` | EXTEND | Add `research`, `read_research`, `list_research` tool defs + dispatchers. |
| `src/fastcad/agent/client.py` | EDIT | Real-mode loop forwards subagent stream events to `WSContext.emit_progress` so the UI sees them in real time. |
| `src/fastcad/agent/system_prompt.py` | EDIT | Add "research cache & how to use it" section pointing at `docs/research/`. Still no hardcoded examples. |
| `src/fastcad/server/ws.py` | EDIT | New `progress` message type; `_emit_progress(ctx, event)` helper. |
| `web/index.html` | EDIT | Add `<div id="progress-panel" data-testid="progress-panel">`. |
| `web/main.js` | EDIT | Handle `progress` messages; render tree; expand/collapse. |
| `web/style.css` | EDIT | Panel styles, status icons. |
| `docs/research/README.md` | NEW | Explain format, slug rules, "edit me freely" semantics. |
| `tests/unit/test_research.py` | NEW | Fake-mode subprocess (`monkeypatch` `subprocess.Popen` with a fixture stream); cache idempotency; progress events emitted. |
| `tests/e2e/test_progress_panel.py` | NEW | Playwright: prompt that triggers research; assert `progress-panel` populates with tree entries; status transitions to `done`. |

### Existing functions to reuse

- `agent/client._real_turn` framing — same loop, with `on_progress`
  hook added.
- `agent/tools.dispatch` — extend, don't fork.
- `server/ws._send` / `_summary` — emit progress events through the
  same path; `_summary` learns about the new type.
- Existing fake-mode regex matchers — extend with one or two patterns
  that exercise the research tool to give e2e tests something to
  push.
- `web/main.js`'s `handleServerMessage` switch — add a `progress`
  case; existing `tool_log` case stays.

### Cache approval flow

Auto-cache (no approval queue). Reasoning:

- The cache lives in git. Every commit of `docs/research/*.md` shows
  in `git diff` and PR review — that's the review surface.
- Approval queues add UX friction without preventing bad data; the
  user can edit / delete the cache file directly if it's wrong.
- The subagent is told to include sources; the user can audit.

Note in `docs/research/README.md`: "Files in this directory are
agent-written but human-editable. If a value is wrong, fix it; the
agent reads what's here verbatim."

### Verification (Phase B)

```
.venv/bin/pytest tests/unit -q
bash scripts/e2e.sh
```

Plus:

- Unit: `test_research.py` — subprocess parsing (mock); cache
  idempotency (re-call returns cache hit); progress event shape;
  concurrent calls don't race-write (lock per slug).
- E2E: `test_progress_panel.py` — fake-mode stub research returns
  immediately; progress panel shows tree; entries collapse/expand.
- Manual:
  1. Prompt for a standardized part. Agent calls `list_research`
     (returns `[]`), then `research(<topic>)`, observable in panel.
     Subagent writes `docs/research/<slug>.md`. Agent then calls
     `set_source` using cached dimensions. Result: standards-
     compliant geometry.
  2. Repeat the same prompt in a fresh session. Agent calls
     `list_research`, finds the entry, calls `read_research`, skips
     research entirely. Faster turn (~5s vs ~30s).
  3. Manually edit the cache file to use a different value, prompt
     for the same part. Result reflects the edited dimensions —
     proving cache-as-source-of-truth.

### Push (Phase B)

1. File issue: "Stage 2: research subsystem + cache + progress
   panel". Get number `N`.
2. Rename this plan file: `mv stage2-research-draft.md
   <NN>-stage2-research.md`.
3. Branch: `stage2-research` off `main` (post-Stage-1 merge) or off
   `stage1-scad-spec` if Stage 1 still pending.
4. Commits:
   - `agent/research.py` + tests
   - tools + system prompt edits
   - ws + frontend (panel + progress events)
   - docs/research/ scaffold + README
   - e2e tests + manual verification
5. PR titled "NN — Stage 2: research subsystem + cache + progress
   panel".

Estimated effort: ~12–18 hours.

## Out of scope (Phase B follow-ups)

- Parallel research subagents (sequential is fine in v1).
- Cache eviction / staleness checks beyond a date heuristic.
- Vector / semantic search over cache (filename-slug lookup is fine
  for v1).
- Subagent transcript UI (panel shows summary; full transcripts go
  to `tmp/research/<slug>-<ts>.transcript.json` for debug, viewable
  via the feedback bundle).
- Cross-session cache sync (each repo carries its own cache).

## Acceptance criteria (Phase B)

- [ ] `agent/research.py` spawns Claude Code subagent and streams
      events.
- [ ] `docs/research/<slug>.md` is created on first research; reused
      on second.
- [ ] Progress panel renders tree of activity in real time.
- [ ] System prompt has the "check cache first" rule but no
      hardcoded specs.
- [ ] Manual standardized-part test produces a standards-compliant
      model with single-start thread (when applicable).
- [ ] Same prompt second time reuses the cache (verifiable in panel
      and via timing).
- [ ] Editing the cache file changes the model — cache is the
      authority.
- [ ] All unit tests + e2e tests pass.
- [ ] PR opened, linked to issue, manual results posted as comment.
