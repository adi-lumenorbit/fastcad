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

## What does NOT belong here

- Implementation `.scad` source. The cache stores *spec data* (the
  dimensions), not modeled geometry. The agent uses the dimensions to
  produce its own `.scad` per turn.
- Tutorials, design rationale, anything not derivable from a
  published standard or datasheet.
- Project-specific design choices ("we always use 5 mm fasteners
  here"). Those go in `CLAUDE.md` or a project doc, not in the
  research cache.
