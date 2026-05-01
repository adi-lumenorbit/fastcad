# Research cache

Text-based cache of dimensional / specification research the agent has
done on standardized parts (fasteners, motors, connectors, structural
profiles, anything with a real-world spec). The cache is the agent's
**source of truth** at modeling time — it reads what's here verbatim.

## How it gets populated

The main fastcad agent calls `research(topic)` when it's about to
model something it should look up. That tool spawns a Claude Code
subagent in this repo's working directory; the subagent uses its
own WebSearch / WebFetch / Read / Write tools to find the spec, and
writes its findings to `docs/research/<slug>.md` as the last step
of its work.

Subsequent calls to `research(topic)` for the same slug return the
cached file without re-running the subagent.

## How to read / edit / delete

These files are **agent-written but human-editable.** If a value is
wrong:

- **Edit it.** The agent will use whatever's in the file on the next
  modeling turn.
- **Delete the file** if you want the agent to re-research.
- **Commit the edit** in a regular PR. The cache is git-tracked so
  changes show up in review.

There is no approval queue. Trust here lives at the source-control
level — every cache change goes through `git diff` like any other
file.

## File format

Loose markdown. The agent reads section headings to find what it
needs, so keep the H2s but the body content under each can be
freeform. Required structure:

```markdown
# <Canonical part name>

researched: <YYYY-MM-DD>
researcher: <model> via Claude Code (subagent)
slug: <kebab-case-slug>

## Canonical name
<Standard family / variant identifier — e.g. "ISO 4762, hexagon
socket head cap screw, M-series">

## Key dimensions
- <field>: <value> [unit]
- <field>: <value>
- ...

## Variants
- <variant 1 description>
- <variant 2 description>
- ...

## Sources
- <url 1>
- <url 2>
- ...

## Acceptance

```json
{ … structural validator schema … }
```

## Implementation guidance

Free-form prose + OpenSCAD snippets describing the canonical
construction pattern for this part type. The modeling agent reads
this every turn and uses it as a starting template. See the section
below for what to include.
```

Notes:

- **All dimensions in millimetres** unless the part is intrinsically
  imperial (e.g. UNF threads). Mixing units in one file is
  forbidden — pick one.
- **Sources are URLs**, one per line, ideally to the standard's PDF
  or a manufacturer datasheet. Wikipedia is acceptable for
  triangulation but not as a sole source.
- **Slug** is kebab-case, descriptive enough that another engineer
  could guess what the file is about from its filename. Examples:
  `m3-iso4762-socket-cap`, `nema-17-stepper`,
  `iso-7045-pan-head-phillips`.

## Acceptance schema (Stage 3 validator)

The `## Acceptance` section is required for new entries. Older
entries that lack it opt out of automatic validation gracefully.
The block is a JSON object the structural validator runs against
the agent's modeled geometry after every `set_source`. Any defect
becomes a tool error so the agent self-corrects in the same turn.

```json
{
  "bbox_z_extent": [<min>, <max>],
  "bbox_xy_max":   [<min>, <max>],
  "volume_range":  [<min>, <max>],
  "connected_components": <int>,
  "expected_modules": ["<regex-fragment>", "..."],
  "axial_consistency": "helical",
  "pitch": <mm>,
  "horizontal_slices_at_z": [
    {"z": <mm>, "outer_protrusions": <int>, "radius_range": [<min>, <max>]}
  ],
  "axial_section": {
    "plane": "XZ",
    "offset": 0.0,
    "peak_count": [<min>, <max>],
    "pitch": <mm>,
    "peak_axial_extent_pct_of_pitch": [<min>, <max>],
    "flank_angle_deg": [<min>, <max>]
  }
}
```

Field meanings:

- **bbox_z_extent** — axial extent of the primary geometry (mm).
- **bbox_xy_max** — `max(x_extent, y_extent)` of the primary
  geometry (mm). For a fastener, this is typically the head Ø.
- **volume_range** — total volume in mm³.
- **connected_components** — should be `1` for a single fastener.
  More than one means the parts weren't union'd; that's a
  construction bug.
- **expected_modules** — each entry is a regex fragment; at least
  one defined module's name must match. Catches the agent forgetting
  a feature (no `head|cap` module → fail).
- **horizontal_slices_at_z** — at each Z height, slice the manifold
  and verify the cross-section's radial topology:
  - `outer_protrusions` — count of radial peaks. **A single-start
    thread = 1; a smooth cylinder = 0; a 12-start thread = 12.**
    This is the structural check that catches the multi-start-
    thread bugs.
  - `radius_range` — optional `[min, max]` mm; the slice's radial
    extent must overlap.
- **axial_consistency** — set to `"helical"` for threaded parts.
  Triggers a peak-azimuth-rotation check across slices to detect
  stacked-rings constructions. Pair with **pitch** so the validator
  samples at non-integer-pitch z's (otherwise integer-pitch slices
  always land at the same azimuth and false-positive).
- **pitch** — thread pitch in mm. Read by `axial_consistency`,
  `axial_section.peak_axial_extent_pct_of_pitch`.
- **axial_section** — programmatic measurements on an XZ or YZ
  cross-section through the geometry. **Catches paper-thin threads
  deterministically** (the failure mode where
  `linear_extrude(twist=)` with a too-narrow tooth cross-section
  produces a helical band of ~zero axial thickness). Fields:
  - `plane` — "XZ" (default) or "YZ".
  - `offset` — axial-plane offset in mm. Default 0.
  - `peak_count` — `[min, max]` thread peaks the section should
    show. For a single-start thread of length L and pitch P,
    expect ~L/P peaks (with slack: e.g. 18mm/1mm → 14–22).
  - `peak_axial_extent` or `peak_axial_extent_pct_of_pitch` —
    expected axial thickness of one tooth. The pct form is
    multiplied by `pitch` (which must also be present). For a
    proper ISO 60° thread, 0.30–0.95 × pitch is reasonable;
    paper-thin threads come in at < 0.05 × pitch.
  - `flank_angle_deg` — expected flank angle. ISO 60° threads
    show 50°–70° at the rendered mesh resolution; pad to 40–80
    to absorb tessellation noise.

Use generous tolerances: ±5% on dimensions, ±15% on volume.
Manifold tessellation introduces small numerical noise; tight
tolerances produce false-failures.

## Implementation guidance section (Stage 3 / construction template)

Required for new entries. Distinct from the dimension table, which
says *what the part is*; the implementation guidance says *how to
build it in OpenSCAD*. Covers the construction idioms an agent
needs to write working code on the first try, without us hardcoding
worked examples in the system prompt.

What to put in `## Implementation guidance`:

- **Module decomposition.** What sub-modules the agent should
  create for THIS part class. For a fastener: head + shank + thread
  + drive recess. For a flanged bushing: flange + bore + outer
  cylinder. For a stepper-motor mount: bolt-circle pattern + bore
  + body. The agent reads the list and uses it as a skeleton.
- **The canonical construction pattern for any helical / swept /
  revolved feature.** Especially: the `linear_extrude` cross-
  section shape, the `twist` and `slices` formulas, ridge-vs-groove
  orientation. (This is the blind spot agents repeatedly hit on
  threads.)
- **Common pitfalls to AVOID for this part type.** "Do NOT use
  difference() to carve threads from a major-diameter cylinder; use
  union() of a minor-diameter core + one helical ridge." "Do NOT
  emit `cylinder(...,$fn=6)` for a hex socket without converting
  across-flats to across-corners; the polygon size is wrong by a
  factor of 1/cos(30°)." Concrete, with reasoning.
- **Parameter names.** Tie OpenSCAD identifier suggestions to the
  dimension table above, so the agent's source uses the same names
  the cache uses. Keeps the cross-references obvious.

What does NOT belong here:
- A complete, copy-pasteable model. The agent writes the full
  source; the guidance is the *skeleton + idioms*, not the body.
- Hardcoded numeric values. Reference dimensions by name (use
  `pitch`, not `0.5`).

The research subagent generates this section per-part from training
+ web sources. For older cache entries that lack it, the validator
opts out gracefully (no guidance = agent works without a template).

## What does NOT belong here

- Implementation `.scad` source. The cache stores *spec data* (the
  dimensions), not modeled geometry. The agent uses the dimensions to
  produce its own `.scad` per turn.
- Tutorials, design rationale, anything not derivable from a
  published standard or datasheet.
- Project-specific design choices ("we always use 5 mm fasteners
  here"). Those go in `CLAUDE.md` or a project doc, not in the
  research cache.
