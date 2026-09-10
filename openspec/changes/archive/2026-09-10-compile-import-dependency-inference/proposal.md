## Why

`worktrail-compile` orders tasks by declared file scope: two tasks whose `files:` sets are
disjoint are scheduled into the same frontier. Run `go-20260910-085218` (change
`smoke-flake-dashboard-surface`) showed why that is not enough. Task 1.1 created
`src/worktrail/router/smoke_flake_selfcheck.py`; task 2.1 modified
`src/worktrail/router/dashboard.py` to import it. The file sets were disjoint, no prose
dependency was written, so both fanned out at once — 2.1's worker found no module to import,
papered over it with a `try/except ImportError`, review rejected that, the fix worker could not
resolve it without 1.1's file, and the group quarantined. Recovery cost a 25-minute
`integration_repair` intervention on the run record.

The general defect: file-scope disjointness is not import-dependency disjointness. Compile
catches two tasks that **write** the same file, but nothing catches a task that **reads**
(imports) a module another task owns. "New module plus its first consumer" is one of the most
common change shapes, so this recurs unless compile learns to see it. Today
`grep -rn 'ast\.parse\|ImportFrom\|depends:' src/worktrail/conductor/ src/worktrail/taskformats/openspec/`
returns nothing: there is no import inference and no way for an author to declare an
ordering constraint on the task line short of writing a prose sentence.

## What Changes

- **Import-dependency inference in compile.** For every task with a Python file in its
  declared scope that exists on disk, parse that file's imports (relative and absolute
  in-repo), resolve each to a repo-relative path, and where another task in the change
  declares that path, add a dependency edge from the importer to the owner. Deterministic,
  model-free, applied on both the seeded (no-model) and model compile paths, additive only,
  and never closing a cycle. An import of a module that is not on disk yet cannot be parsed
  from the consumer, which is exactly why the next two items exist.
- **A `depends:` continuation line in `tasks.md`.** Alongside the existing `files:` and
  `review:` lines, an author can write `depends: 1.1, 1.3` under a task to state a coupling
  that file overlap cannot express — a module created by one task and first imported by
  another. Parsed by the OpenSpec checklist parser, unioned into the task's baseline `deps`,
  and an id naming no task in the change is reported by the existing dependency validation.
- **Authoring guidance and prompt alignment.** The `openspec-propose` skill's `tasks.md`
  rules gain a "declare the consumer's dependency on the module it imports" rule with the
  `depends:` syntax, and the compile `PROMPT` tells the model an import relationship between
  two tasks' files is an ordering constraint even when the file sets are disjoint.
- **Regression fixture.** A test reproducing the exact incident shape — task A owns a module,
  task B's file imports it, disjoint `files:` — asserts the compiled plan records B depending
  on A rather than scheduling them in parallel, once by inference and once by `depends:`.

## Capabilities

### New Capabilities
- `compile-import-dependency-inference`: how the compile step derives a dependency edge from
  a Python import between two tasks' declared files, on every compile path, and the limits
  of what an on-disk parse can see.

### Modified Capabilities
- `openspec-task-file-declaration`: gains an indented `depends:` continuation line, parsed
  the same way `files:` and `review:` are, that unions authored task ids into the task's
  dependency list.

## Impact

- **New:** `src/worktrail/conductor/import_deps.py` (import extraction, in-repo resolution,
  edge derivation) and its test module.
- **Modified:** `src/worktrail/conductor/compile.py` — union the inferred edges in both
  `_plan_from_tasks()` and `_validate()`, next to the existing prose-reference union; extend
  `PROMPT`.
- **Modified:** `src/worktrail/taskformats/openspec/schema.py` and `source.py` — parse
  `depends:` and carry it into the loaded task's `deps`.
- **Modified:** `skills/openspec-propose/SKILL.md` — `tasks.md` authoring rule.
- Only ever adds edges; no authored, baseline, or model-supplied `deps` entry is removed, so
  a compiled plan cannot become more parallel than it is today. `runplan.py`'s edge-dropping
  safety rule and the devkit format are untouched.
