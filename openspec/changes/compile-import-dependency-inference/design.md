## Context

See proposal.md — Why. The relevant current state:

- `compile._plan_from_tasks()` (seed/baseline path) and `compile._validate()` (model path)
  each build `TaskPlan.deps` as `_union_deps(<path's own deps>, prose_deps[tid])`, where
  `prose_dep_edges()` deterministically extracts "depends on 1.1" sentences. That is the exact
  seam for a second deterministic edge source.
- `taskformats/openspec/schema.parse_tasks_md()` scans the window under each task line for
  indented `files:` (`FILES_RE`) and `review:` (`REVIEW_RE`) continuation lines.
  `source.OpenSpecTaskSource.load()` derives baseline `deps` (within-group sequential) and
  `validate_dependencies()` already reports a `deps` entry that names no task in the change.
- The compile `PROMPT` already carries a "files are not the only source of ordering"
  paragraph for prose references.
- Tasks are compiled **before any worker runs**, against the base branch's tree. A module a
  task will create is not on disk, and a consumer's not-yet-written import line is not on disk
  either.

## Goals / Non-Goals

**Goals:**
- Turn an import relationship that *is* observable on disk into an edge with no model call.
- Give an author a one-line, unambiguous way to state the coupling the on-disk parse cannot
  see, and tell them when to use it.
- Never make a plan more parallel, never introduce a cycle, never fail a compile over an
  unparseable file.

**Non-Goals:**
- Inferring that a consumer *will* import a module it does not import yet. That is not
  derivable from the tree; it is what `depends:` and the authoring rule are for.
- Languages other than Python. `ast` is in the standard library and the repo's task files are
  Python; a general import grammar is speculative.
- Reading imports from a model's answer or re-prompting the model. The inference stays on
  the deterministic side so the seeded path benefits equally.
- Changing `runnable_frontier`, `runplan.apply_to_tasks`, or the devkit format.

## Decisions

**Resolve both relative and absolute in-repo imports, to paths, not module names.**
The brief's open question was whether to limit inference to `from .x import` for cheapness.
Resolving to a repo-relative path makes both forms equally cheap and unambiguous: a relative
import resolves against the importing file's package directory; an absolute import
`worktrail.router.smoke_flake_selfcheck` is tried under each source root (`src/`, then the
repo root) as `<root>/<dotted as path>.py` and `<root>/<dotted as path>/__init__.py`. The
first that exists wins; one that resolves nowhere is not in-repo and is ignored. Matching is
then a plain path-equality test against every other task's declared `files`. This repo's own
sibling detectors in `dashboard.py` use absolute imports (`from worktrail.router import
journal_selfcheck`), so relative-only would have missed the reproducing case even if the
import had been on disk.
*Alternative considered:* match on module stem only (`smoke_flake_selfcheck`). Cheaper to
implement, but two packages with a same-named module would produce a false edge, and a false
edge is a silent serialisation nobody will notice or debug.

**The importer depends on the owner, regardless of authored order.**
A task that modifies a file importing module M consumes M's interface; if another task
modifies M, the consumer should see the finished M. Direction is semantic, not positional.
Because a backward edge can close a cycle against the forward-only baseline edges and prose
edges, each inferred edge is added only if the owner cannot already reach the importer
through the edges present so far; an edge that would close a cycle is skipped and recorded as
a compile warning naming both tasks. Mutual imports between two tasks' files therefore keep
whichever edge was reached first in authored order — deterministic and never a deadlocked
frontier.

**`depends:` is a parser-level declaration that lands in baseline `deps`.**
It is parsed by `parse_tasks_md()` into `ParsedTask.depends`, unioned into the loaded task's
`deps` by `source.load()`, and so reaches every consumer (compile, coordinator, dependency
validation) with no new plumbing. A duplicated line or an empty value warns, mirroring
`files:`/`review:`. An id naming no task is reported by the existing
`validate_dependencies()`, so the error message and its remediation are the ones operators
already know. Self-references are dropped at parse time.
*Alternative considered:* recognise `depends:` only in compile, like prose references. That
would leave the coordinator's `--no-llm` baseline blind to it, and would duplicate the
resolution error that `validate_dependencies()` already produces.

**Union on both compile paths, in the same call sites as prose references.**
`import_deps.import_dep_edges(tasks, repo)` returns `({tid: [owner ids]}, warnings)`. Both
`_plan_from_tasks()` and `_validate()` union its result exactly where they union
`prose_dep_edges()`. The seeded path is the one that produced the incident (every task had
`files:`), so covering only the model path would not have prevented it.

**Skip, never raise, on a file that will not parse.**
A task file may be mid-refactor on the base branch, be Python 2, or be a `.py` that is not
importable. `SyntaxError`, `UnicodeDecodeError`, and a missing file all mean "no evidence",
not "compile problem". Only `.py` paths are parsed at all.

**Authoring rule over a heuristic for the not-yet-written import.**
The incident's consumer had no import line on disk at compile time, so no parse can catch it.
Rather than guess from prose ("calling the detector"), the `openspec-propose` rule tells the
author: when a task's `files:` names a path that does not exist on the base branch and
another task will import it, the importing task carries `depends: <creator id>`. This is the
same posture the `files:` co-scoping rules already take — authoring discipline that
`worktrail-compile` then enforces mechanically where it can.

## Risks / Trade-offs

- **False edges from stale imports.** A task file may import a module it no longer really
  depends on; the inferred edge then serialises two tasks unnecessarily. Accepted: a
  needless serialisation costs wall-clock, a missing edge cost a quarantine and a manual
  repair. The direction of error matches `runplan.py`'s existing safety posture.
- **Cycle skipping hides a real constraint.** When an inferred edge is skipped to avoid a
  cycle, the warning is logged but the plan proceeds. Accepted because the alternative
  (failing compile) blocks on a shape that today already compiles, and the warning names both
  tasks for the author to resolve with `depends:` or a task split.
- **Parsing cost.** One `ast.parse` per declared existing `.py` file per compile — dozens of
  files at most, well under a second, and the result is cached with the plan under the
  content fingerprint like everything else compile produces.
