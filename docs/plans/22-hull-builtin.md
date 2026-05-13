# 22 — hull() + reserved-keyword fix + $preview default

GitHub issue: https://github.com/adi-lumenorbit/fastcad/issues/22

## Problem

Three parser / evaluator restrictions blocked real-world `.scad`
files from loading through the Open .scad dialog:

1. `hull()` was banned at the parser level — the first roadmap item
   in Stage 1.5 (`function / hull / minkowski / offset`).
2. `rotate(...) for(...) { ... }` (bare `for` after a transform,
   common in real `.scad`) parsed the inner `for(...)` as a module
   call named `for` and died at eval time with "unknown module:
   'for'". Cause: the grammar's `mod_call` used a bare `CNAME` for
   the module name, so `for` matched as an identifier.
3. Files that branched on `$preview` (the OpenSCAD GUI sets this to
   `true` in preview mode, `false` on render) raised
   `EvalError: unknown variable '$preview'` because the evaluator
   only seeded `$fn`.

## Affected Components

| Component | Status | Notes |
|-----------|--------|-------|
| `src/fastcad/model/scad_eval.py` | OK | `_builtin_hull` added (backed by `manifold3d.Manifold.hull` / `CrossSection.hull`); `_SPECIAL_VAR_DEFAULTS` seeds `$preview = false` and `$t = 0.0` into every `Env`. |
| `src/fastcad/model/scad_parser.py` | OK | `mod_call` now uses a `MOD_NAME` terminal that excludes `for / if / else / let / module / function / true / false / undef`. Banned-construct hint removed for `hull(`. |
| `src/fastcad/agent/system_prompt.py` | OK | `hull` moved from "out of scope" to the CSG list. |
| `tests/unit/test_scad_eval.py` | OK | Four new cases: 3D hull bbox + volume; wire-twister-pattern hull of two cylinders; `$preview` default false; `rotate(...) for(...)` parses. |

## Out of scope

`minkowski`, `offset`, `projection`, `function` — the rest of Stage
1.5 — stay banned for now. They need design work that hull didn't
(offset's join styles, minkowski's domain restrictions, function
syntax).
