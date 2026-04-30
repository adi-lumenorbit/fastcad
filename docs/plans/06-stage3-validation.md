# 06 — Stage 3: adversarial design validation

GitHub issue: https://github.com/adi-lumenorbit/fastcad/issues/6

Builds on Stage 2 (PR #5). Branch `stage3-validation` off
`stage2-research`; rebase onto `main` once #5 merges.

## Context

Stage 2 surfaced a class of bugs nobody in the loop can catch on
their own:

- **The agent works only in text.** It can't see the rendered
  manifold or the screenshot. Whatever the system builds, the agent
  confidently announces as a screw / bracket / etc.
- **The system silently drifts.** The first M3-screw-with-real-Claude
  test produced a "spiky non-screw" because our `linear_extrude`
  evaluator was reading `$fn` for twist resolution while the agent
  emitted `slices=1152, $fn=1` — both correct OpenSCAD, but our
  evaluator picked the wrong one.
- **The user is the only validator.** They open the dev server, look
  at the render, say "this is not a screw," and the system has no
  way to act on that signal beyond a chat reply.

The fix isn't more careful evaluator code (we'll keep finding silent
drifts); it's adding an **independent verifier** in the loop that
checks the geometry against the user's request after every
`set_source`. Defects flow back as tool errors; the agent revises in
the same turn.

The architecture is **adversarial** — the verifier's job is to find
what's WRONG, not confirm what's right. Self-review by the same
agent has the same blind spot as the original bug; a separate critic
with its own tools (vision, geometric analysis) breaks it.

## Affected Components

| Component | Status | Notes |
|---|---|---|
| `model/validate.py` | NEW | Channel 1: symbolic / structural assertions |
| `model/render.py` | NEW | Manifold → PNG (headless render for Channel 2) |
| `agent/critic.py` | NEW | Channel 2: vision subagent (subprocess Claude Code) |
| `agent/tools.py` | EDIT | New `validate_design` tool; default-on auto-invoke after `set_source` |
| `agent/research.py` / cache schema | EDIT | Researcher emits `## Acceptance` block in cache files |
| `agent/system_prompt.py` | EDIT | Documents validate_design + the critic-defect-revision loop |
| `server/ws.py` | EDIT | Validator events flow through existing `progress` event path; no new message types |
| `tests/unit/test_validate.py` | NEW | Acceptance schema parsing, structural checks (bbox, volume, slice topology), critic protocol mocked |
| `tests/unit/test_render.py` | NEW | Manifold → PNG smoke (image exists, plausible bbox in pixel space) |
| `tests/e2e/test_validation_panel.py` | NEW | Failed validation surfaces as a defect entry in the progress panel |

## Three layered validation channels

Layered cheapest-first; each runs only if the previous passed.

### Channel 1 — Symbolic / structural (free, deterministic)

After `set_source`, before returning success to the agent:

1. Resolve which cache entry the agent referenced (if any). The most
   recent `read_research(slug)` call in this turn picks it.
2. Parse the cache file's `## Acceptance` section into a schema.
3. Run pure-function checks against the manifold + AST. Any failure
   becomes a defect.

Acceptance schema (the researcher subagent learns to emit this):

```markdown
## Acceptance

# Bbox & volume — broad sanity, catch order-of-magnitude wrongness.
bbox_z_extent: [22.0, 23.5]      # mm; 20mm shaft + 3mm head ± slack
bbox_xy_max:   [5.32, 5.50]      # mm; head Ø
volume_range:  [120, 220]        # mm³

# Topology — single connected solid.
connected_components: 1

# Module presence — the agent's source must define modules whose
# names match these regex fragments. Catches "agent forgot the head."
expected_modules:
  - shaft|threaded|screw_thread
  - head|cap

# Thread topology — slice horizontally at sample Z heights and count
# outer protrusions. A single-start thread shows ONE protrusion at
# every slice on the threaded portion (rotated by azimuth).
horizontal_slices_at_z:
  - { z: 5.0,  outer_protrusions: 1, radius_range: [1.10, 1.55] }
  - { z: 10.0, outer_protrusions: 1, radius_range: [1.10, 1.55] }
  - { z: 15.0, outer_protrusions: 1, radius_range: [1.10, 1.55] }
```

`model/validate.py` exposes:

```python
@dataclass
class Defect:
    severity: Literal["error", "warning"]
    where: str          # e.g. "bbox_z_extent" or "module presence: head|cap"
    expected: str
    actual: str
    hint: str           # short remediation suggestion

def validate_against_cache(
    spec_source: str,
    cache_md: str,
    cache: dict[str, ModuleEval],   # session.cache
) -> list[Defect]:
    ...
```

This single-channel implementation catches:

- **Wrong dimensions** (bbox / volume out of range) — would have
  caught the original 12-start-thread bug since multi-start threads
  produce volumes outside the spec range.
- **Missing features** (no `head` module) — common agent oversight.
- **Multi-start threads** masquerading as single-start — by slicing
  and counting protrusions; this would have caught the Stage 1 M3
  bug and the Stage 2 ribbon-thread bug too.
- **Disconnected geometry** — connected_components > 1 means the
  parts were never unioned.

### Channel 2 — Vision-based critic (medium cost, heuristic)

Render the manifold to PNG from N canonical angles (front, top, iso),
then dispatch a separate Claude Code subagent in a "critic" role:

```
You are reviewing a CAD design adversarially. The user asked for:
  <prompt>

The reference spec (cached research) is:
  <cache_md>

The agent wrote this OpenSCAD source:
  <.scad>

Here are renders from three canonical angles:
  [front view] [top view] [isometric view]

Identify SPECIFIC defects. Be skeptical — your job is to find what's
wrong, not confirm what's right. For each defect:
  - severity: error | warning
  - where:    image and rough region (e.g. "head — top view")
  - what:     concrete description
  - hint:     what the agent could change

If the design matches the spec, return defects: [].
```

The critic responds in JSON. Severity `error` blocks the turn (agent
gets a tool error, must address); `warning` shows in the progress
panel.

Catches the renderer-related defects Channel 1 can't see by
construction:

- **Visual proportions wrong** when bbox happens to be in range
  (e.g. squat / elongated geometry that nominally fits the bbox).
- **Missing features that aren't in the module name list** (e.g.
  hex socket geometrically present but rendered as a flat dome).
- **Rendering artifacts that suggest evaluator bugs** — the critic
  will flag "the threads look like disconnected spurs," which is
  the signal we'd want to drive back into the evaluator.

`agent/critic.py` reuses Stage 2's `agent/research.py` subprocess
pattern — same `claude --print --output-format stream-json` setup,
different system-prompt directive and different tool-affordance set
(no Write; the critic returns structured JSON, doesn't write files).

### Channel 3 — Property-based fuzzing (heavy, optional)

For each part class, define topological invariants:

- A screw has rotational symmetry around exactly one axis.
- A threaded shaft's horizontal cross-sections are pairwise similar
  under rotation (single-start thread invariant).
- A hex-head bolt's top view shows 6-fold rotational symmetry.

Random small perturbations of named parameters (length ±10%, diameter
±5%) must preserve these invariants. Failures point to brittle
construction.

Out of scope for v1; the cache schema reserves a `## Properties`
section for forward compatibility.

## Pipeline integration

After `set_source` succeeds (manifold built, before returning ok=true
to the agent's tool result):

```
def _validate_after_set_source(session, on_progress) -> list[Defect]:
    cache_slug = session.last_referenced_research_slug   # set by read_research
    if cache_slug is None:
        return []   # agent didn't reference a standard; nothing to validate against
    cache_md = research.read_research(cache_slug)
    defects = validate_against_cache(session.current_source, cache_md, session.cache)
    if not defects and CRITIC_ENABLED:
        defects.extend(critic.review(...))
    for d in defects:
        on_progress({"type": "validation_defect", ...d})
    return defects
```

`set_source` then either:

- `defects = []` → return `{ok: true, validated_against: <slug>}`
- `defects = [errors]` → return `{ok: false, defects: [...],
  hint: "fix and re-call set_source"}`. Spec is **not** mutated.

The agent sees the defects in its tool_result and calls set_source
again with revised geometry.

Configuration:

- `FASTCAD_AUTO_VALIDATE` env var: `off` / `structural` /
  `structural+vision` (default: `structural`).
- The agent can also call `validate_design(against_slug)` explicitly
  to run a check at any time.

## Cache schema extension

`docs/research/README.md` updated to require `## Acceptance` and
permit `## Properties`. The research subagent's directive
(`RESEARCH_DIRECTIVE` in `agent/research.py`) updated to teach this:

```
... output a single markdown document with sections: 'Canonical
name', 'Key dimensions', 'Variants', 'Sources', 'Acceptance'. The
Acceptance section is the spec a downstream validator runs against
the modeled geometry. Use this schema:

  ## Acceptance
  bbox_z_extent: [<min>, <max>]      # mm
  bbox_xy_max:   [<min>, <max>]      # mm
  volume_range:  [<min>, <max>]      # mm³
  connected_components: <int>
  expected_modules: [<regex>, ...]
  horizontal_slices_at_z:
    - {z: <mm>, outer_protrusions: <int>, radius_range: [<min>, <max>]}

Pick reasonable tolerances (typically ±5% on dimensions, ±15% on
volume). Slack lets the evaluator's discretization noise pass.
```

Existing cache files (e.g. `m3-socket-head-cap-screw.md`) need an
`## Acceptance` block back-filled. A migration commit handles that
manually for the one extant entry; future entries get it from the
research subagent automatically.

## Render pipeline (`model/render.py`)

Manifold → PNG via `trimesh.scene.Scene` + `pyrender` for offscreen
EGL rendering. Headless WSL is the constraint; pyrender + EGL works
without an X server. Alternative if EGL is fragile: render via
matplotlib3d (slower, lower quality, but always works).

```python
def render_manifold(
    manifold: Any,
    angles: list[str] = ["front", "top", "iso"],
    width: int = 768,
    height: int = 768,
) -> dict[str, bytes]:
    """Returns {angle: png_bytes}."""
```

For the critic call, png_bytes are base64'd into the multimodal
content array per Anthropic SDK.

## Existing functions to reuse

- `agent/research.py` subprocess scaffolding — `agent/critic.py` is
  a near-identical second invocation with a different directive;
  factor common bits into `agent/_subagent.py` if the duplication
  pays back.
- `agent/tools.py` `dispatch` outer wrapper — `validate_design` is
  just another dispatch case; progress events flow through the
  existing path.
- `model/scad_eval.py`'s top-level node-id resolution — the
  validator iterates `session.cache.values()` for bbox / volume /
  manifold access.
- `manifold3d.Manifold.bounding_box`, `.volume`, `.slice` (or
  `.cross_section`) — Channel 1's primitives.

## Out of scope (Stage 3 follow-ups)

- Channel 3 (property-based fuzzing). Schema reserves `## Properties`.
- Multi-modal Anthropic API direct (vs. Claude Code subprocess) for
  the vision critic. Subprocess pattern keeps Stage 2 + Stage 3
  symmetric.
- Validator caching (re-validation skipped when neither source nor
  cache changed). Premature.
- Auto-fixing — when the critic suggests a fix, the agent is the one
  who applies it; the system doesn't try to patch the source itself.

## Verification

### Unit (`.venv/bin/pytest tests/unit -q`)

- `test_validate.py`:
  - Acceptance schema parser handles the documented shape; rejects
    malformed values with helpful errors.
  - bbox / volume checks: in-range passes, out-of-range produces
    a single Defect with the right `where`.
  - module-presence regex: missing module produces Defect.
  - horizontal_slices: a single-start helix passes; a 12-start helix
    fails with `outer_protrusions: 12 expected 1`.
  - connected_components: two disjoint cubes fail with `2 expected 1`.
- `test_render.py`:
  - `render_manifold(cube)` produces a PNG with non-zero size and
    plausible bbox in pixel space.
  - Skips cleanly if pyrender's EGL backend isn't available
    (fallback path takes over).
- `test_critic.py`:
  - Subprocess injection (same `spawn` parameter pattern as
    research) — fake stream returns a JSON defect list.
  - Critic timeout / non-zero exit propagated as a single
    `severity: error` defect with the subprocess error.

### E2E (`bash scripts/e2e.sh`)

- `test_validation_panel.py`: prompt that triggers `set_source` with
  a deliberately malformed acceptance fixture (or against a known-
  failing spec); progress panel shows a `validation_defect` entry;
  the agent's next set_source clears it.

### Manual real-Claude verification (merge gate)

In `dev.sh` with `ANTHROPIC_API_KEY`, `claude` CLI on PATH, and
`FASTCAD_AUTO_VALIDATE=structural+vision`:

- [ ] **Test 1 — happy path.** "Design an M3 screw, 20 mm long. Do
      not use any external libraries." Agent calls research →
      read_research → set_source. Channel 1 passes (bbox, volume,
      thread topology against the M3 cache acceptance). Channel 2
      passes ("looks like an M3 socket cap"). Final state: clean
      screw with passing validators visible in the progress panel.
- [ ] **Test 2 — manually break the acceptance.** Edit
      `docs/research/m3-socket-head-cap-screw.md`'s acceptance
      section to require `bbox_z_extent: [50, 60]` (much taller than
      a 20mm screw). Re-run the prompt. Expected: validator fires,
      defect surfaces as a tool error, agent revises (probably
      stretches `length` to match the cache's stated range or pushes
      back via `ask_user`). The validation defect is visible in the
      progress panel.
- [ ] **Test 3 — multi-start thread regression test.** Force the
      agent (via prompt) to emit a multi-start thread by saying
      "make the thread 3-start." Channel 1's slice-protrusions check
      fires; defect surfaces. (This validates the regression that
      motivated the architecture.)

## Push / merge

1. Issue: https://github.com/adi-lumenorbit/fastcad/issues/6
2. Plan file: `docs/plans/06-stage3-validation.md` (this file).
3. Branch: `stage3-validation` off `stage2-research`; rebase onto
   `main` once Stage 2 (#5) merges.
4. Commits, in order:
   1. Plan + matrix row + cache `## Acceptance` schema doc.
   2. `model/validate.py` + tests (Channel 1).
   3. `agent/research.py` directive update + back-fill existing
      cache acceptance. Re-run subagent on the M3 cache or hand-fill.
   4. `model/render.py` + tests.
   5. `agent/critic.py` + tests (Channel 2).
   6. `agent/tools.py` `validate_design` + auto-invoke wiring +
      `system_prompt.py` doc.
   7. `server/ws.py` validation_defect progress event +
      `web/main.js` panel rendering.
   8. e2e tests.
   9. Manual real-Claude verification + matrix flip.
5. PR: `gh pr create --base stage2-research` (will retarget to
   `main` once #5 merges).

Estimated effort: ~12–16 hours focused.

## Acceptance Criteria

- [ ] `model/validate.py`, `model/render.py`, `agent/critic.py`
      exist and have tests.
- [ ] Cache schema documented in `docs/research/README.md` requires
      `## Acceptance` for new entries.
- [ ] Existing cache entry back-filled with acceptance schema.
- [ ] Auto-validate after `set_source` when `FASTCAD_AUTO_VALIDATE`
      configured (default: `structural`).
- [ ] Progress panel renders `validation_defect` entries with
      severity styling.
- [ ] All unit + e2e tests pass.
- [ ] Manual real-Claude tests 1–3 pass; results posted to PR.
- [ ] PR opened, linked to issue, base set to `stage2-research`.
