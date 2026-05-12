# SCAD Conversation Comment Spec

> **Canonical version:** `~/src/3d-models/docs/scad-conversation-spec.md`.
> This file is a vendored copy so fastcad's checked-in tests and agent
> system prompt have a stable in-repo reference. When the spec changes,
> update both copies in lockstep (or replace this with a symlink in a
> future cleanup pass).

A convention for embedding the design conversation that produced a `.scad`
file directly into the file itself, as ordinary OpenSCAD comments. The goal
is that the `.scad` is self-describing: anyone (or any tool — fastcad,
Claude Code, future LLMs) can read it and recover not only **what** the
geometry is, but **why** it is that way and **what was asked for**.

OpenSCAD comments are syntax-inert, so adding these never changes the
rendered geometry. The spec is purely about the comment text.

## Why

- An LLM-generated `.scad` is not just code, it's the end product of a
  conversation. Stripping that conversation makes the file harder to
  iterate on later — both for humans and for the next LLM session.
- `.scad` is portable. Git, fastcad, OpenSCAD, file managers — they all
  pass comments through unchanged. Embedding intent in the file means it
  travels with the geometry; no out-of-band notes to lose.
- Tools that consume `.scad` (fastcad on "Open .scad") can surface the
  conversation back to the user without needing a separate history store.

## Comment forms

All recognized tokens use the `fc-` prefix (fastcad) so that ordinary
inline comments (`// 0.5 mm clearance`, `// TODO: round this`) are not
mistaken for structured content.

### Line-level tokens

| Token | Use | Example |
|-------|-----|---------|
| `// fc-prompt: <text>` | A user prompt that drove this turn. | `// fc-prompt: make the cable bore wider` |
| `// fc-decision: <key> = <value>` | A choice the user made between options offered by the agent. | `// fc-decision: cage = short rollers, disks inside housing` |
| `// fc-note: <text>` | A design rationale or non-obvious constraint that's not directly from the user. | `// fc-note: 30 degrees gives ~tan(30) axial drag per revolution` |
| `// fc-ref: <url-or-path>` | A reference to an external resource that informed the design. | `// fc-ref: https://example.com/swaging-tools` |
| `// fc-turn: <N>` | Marks the start of turn N's contribution. Optional, but useful for tools that fold turns. | `// fc-turn: 3` |

Each token MUST be on its own line. Leading whitespace before `//` is
allowed. The body after the token name continues to the end of the line.
Multi-line content uses block form (next section), not line continuation.

### Block-level forms

For paragraphs of text, use a `/* */` block whose first non-whitespace
line is the token:

```scad
/* fc-prompt
try to create some sort of "tightening" bearing in OpenSCAD to
tighten an inner cable harness. ~5 mm cable, surrounded by rubber
rollers inclined at 30°...
*/

/* fc-note
The housing inner radius is `disk_outer_r + 0.4 mm` so the geometry
visibly clears in the viewer. A real build would either use an
interference fit or a tapered inner bore for actual compression.
*/
```

Recognized block tokens: `fc-prompt`, `fc-note`, `fc-decision`, `fc-ref`,
`fc-meta`.

### `fc-meta` — optional file-level header

A single `/* fc-meta ... */` block, when present, MUST be the first block
comment in the file. It carries machine-readable metadata as `key: value`
lines (one per line, YAML-style). Recognized keys:

| Key | Meaning |
|-----|---------|
| `title` | Short human title of the model. |
| `created` | ISO-8601 date of first generation. |
| `tool` | What generated the file (`claude-code`, `fastcad`, `human`). |
| `tool_version` | Optional version string of the tool. |
| `conversation` | A short id linking back to the source conversation, if one exists. Free-form. |

```scad
/* fc-meta
title: tightening bearing — helical-roller cable tensioner
created: 2026-05-12
tool: claude-code
tool_version: opus-4-7
*/
```

Unrecognized keys MUST be preserved by tools that round-trip the file
(opening, modifying, re-saving). Unknown keys are forward-compatibility.

## Placement rules

1. **`fc-meta` block** — first block comment in the file, before any
   geometry.
2. **`fc-prompt` and `fc-decision`** — adjacent to the geometry they drove.
   Place immediately *before* the affected parameter, module, or top-level
   statement. When a prompt motivated a whole section, place it before
   the first line of that section.
3. **`fc-note`** — wherever the rationale belongs. Often inline with a
   parameter or just above a `difference()`.
4. **Order is conversational order.** Within the file, `fc-prompt` and
   `fc-decision` tokens appear in the order they occurred in the
   conversation. Tools rendering the file as a transcript depend on this.
5. **No `fc-prompt` mid-statement.** A token attached to a parameter goes
   *above* the parameter line, never on the same line, never inside a
   parameter list.

## Parsing rules (for tools)

A consumer extracting conversation history from a `.scad`:

1. Read the file line by line.
2. For line comments matching `^\s*//\s*fc-(prompt|note|decision|ref|turn):\s*(.*)$`,
   capture the token and the rest of the line as the value.
3. For block comments, capture the leading token (if it matches one of
   the recognized names on the first non-empty line) and treat the rest
   of the block as the value, with leading whitespace on each line
   trimmed to the indent of the first content line.
4. Block content preserves blank lines.
5. Anything that doesn't match a recognized token is an ordinary comment
   and is ignored by the parser (passed through unchanged on re-save).

## Reference grammar

```
file        := (meta_block? | nothing) (statement | fc_block | line_comment | blank)*

meta_block  := "/*" WS* "fc-meta" NL meta_line+ "*/"
meta_line   := WS* key ":" WS* value NL
key         := [a-zA-Z_][a-zA-Z0-9_]*

fc_block    := "/*" WS* fc_token NL (any_line)* "*/"
fc_token    := "fc-prompt" | "fc-note" | "fc-decision" | "fc-ref"

line_comment:= "//" WS* (fc_line_token | text) NL
fc_line_token := ("fc-prompt:" | "fc-note:" | "fc-decision:" |
                  "fc-ref:" | "fc-turn:") WS* text
```

## Writing convention — boilerplate for LLM system prompts

The following block is intended to be copy-pasted into the system prompt
of any LLM that generates `.scad` files in this convention (Claude Code
in `~/src/3d-models/`, the fastcad agent, future tools). It is short on
purpose — it tells the model what to do, not how the parser works.

> When you generate or modify a `.scad` file under `~/src/3d-models/`,
> embed the design conversation in the file itself, following
> `docs/scad-conversation-spec.md`:
>
> - At the top, write a `/* fc-meta ... */` block with `title`,
>   `created` (today's date, ISO-8601), and `tool: claude-code` (or
>   `tool: <agent name>` if you're a different agent).
> - For each user prompt that materially changed the design, write a
>   `// fc-prompt: <verbatim or close-paraphrase>` line — placed
>   immediately above the parameter, module, or statement that prompt
>   affected. For prompts longer than one line, use a
>   `/* fc-prompt ... */` block.
> - When the user picked between options you offered, record the
>   chosen option as `// fc-decision: <topic> = <choice>`.
> - When a parameter or constraint has a non-obvious reason, add a
>   `// fc-note: <reason>`.
> - Tokens MUST appear in conversational order within the file.
> - Do NOT invent prompts that didn't happen. Do NOT paraphrase to
>   the point of distortion. If the user's wording was concise, keep
>   it verbatim.
> - Ordinary explanatory comments (no `fc-` prefix) remain welcome
>   and are not interpreted as conversation.
>
> The `.scad` file is the durable record. Anything worth remembering
> about *why* the design is what it is belongs in `fc-` comments, not
> in the chat reply.

## Worked example (excerpt)

```scad
/* fc-meta
title: tightening bearing — helical-roller cable tensioner
created: 2026-05-12
tool: claude-code
tool_version: opus-4-7
*/

/* fc-prompt
try to create some sort of "tightening" bearing in OpenSCAD to
tighten an inner cable harness. The harness has ~4-5 mm diameter,
surrounded by rubber rollers rotating around the cable, inclined at
30° from axial.
*/
// fc-decision: cable_size = diameter ~4-5 mm
// fc-decision: cage = short rollers, disks inside the housing
// fc-decision: helix_sense = +30° (drag direction is positive Z)

/* [Cable] */
cable_diameter      = 5;
cable_length        = 60;

// fc-prompt: see screenshot — add an Open .scad button next to Export
// (this prompt belongs to a downstream file, not this one — example only)
```

## Versioning

This spec is **v1**. Breaking changes get a new version, surfaced via
`fc-meta`:

```
/* fc-meta
spec_version: 1
... */
```

When the `spec_version` key is omitted, tools assume v1.

## Non-goals

- This spec does NOT define how to round-trip an agent's *replies*. Only
  user prompts, decisions, and notes belong in the file. Agent prose
  belongs in chat logs / commit messages — embedding it in the geometry
  source bloats the file and dates poorly.
- This spec does NOT define a JSON format. Tools that need structured
  output should parse this format and emit JSON internally; the
  on-disk form stays human-readable `.scad` comments.
- This spec does NOT change OpenSCAD semantics. It is purely a comment
  convention.
