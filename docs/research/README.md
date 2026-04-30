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
  "horizontal_slices_at_z": [
    {"z": <mm>, "outer_protrusions": <int>, "radius_range": [<min>, <max>]}
  ]
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

Use generous tolerances: ±5% on dimensions, ±15% on volume.
Manifold tessellation introduces small numerical noise; tight
tolerances produce false-failures.

## What does NOT belong here

- Implementation `.scad` source. The cache stores *spec data* (the
  dimensions), not modeled geometry. The agent uses the dimensions to
  produce its own `.scad` per turn.
- Tutorials, design rationale, anything not derivable from a
  published standard or datasheet.
- Project-specific design choices ("we always use 5 mm fasteners
  here"). Those go in `CLAUDE.md` or a project doc, not in the
  research cache.
