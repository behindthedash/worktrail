# devkit-task-file-scope-resolution Specification

## Purpose
Defines how the resume dashboard resolves a devkit task's file scope for
stale-bookkeeping detection so that tasks authored with the `provides:` frontmatter
schema are probed against git exactly like tasks authored with `files:`.
## Requirements
### Requirement: Task file scope falls back to provides entries

When `_load_tasks` builds a task row from a devkit `TASK-*.md` whose frontmatter has
no `files:` key or an empty `files:` list, it SHALL populate the row's `files` from
the `file` value of every map entry in the frontmatter's `provides:` list, in
declared order and without duplicates. The resulting row SHALL be indistinguishable
to `_pending_impl_stale`, `_pending_tail_stale`, and `_count_tasks` from a row whose
`files:` listed the same paths.

#### Scenario: Provides-only tail task whose output shipped is stale
- **WHEN** a spec's only non-completed task is `kind: e2e`, declares
  `provides: [{file: e2e/x.spec.ts, symbols: [], type: e2e_test}]` and no `files:`,
  and `e2e/x.spec.ts` is git-tracked on base and newer than the spec directory
- **THEN** `detect_stage` reports the spec as `stale-bookkeeping` with that task id
  in `stale_task_ids`, instead of `tail-pending`

#### Scenario: Provides-only impl task whose outputs shipped is stale
- **WHEN** a `kind: impl` task with `status: pending` declares two `provides:`
  entries and no `files:`, and both files are git-tracked on base and newer than the
  spec directory
- **THEN** the task is listed by `_pending_impl_stale` and the spec is reported as
  `stale-bookkeeping`

#### Scenario: Provides-only task with an unshipped output is not stale
- **WHEN** a `provides:`-only pending impl task lists two files and only one is
  git-tracked on base
- **THEN** the task is not classified stale and the spec still routes to the
  orchestrator

### Requirement: An explicit files list takes precedence over provides

When a task declares a non-empty `files:` list, the row's `files` SHALL be that list
exactly, and `provides:` entries SHALL NOT be added to it.

#### Scenario: Both keys present
- **WHEN** a pending task declares `files: [src/a.py]` and
  `provides: [{file: src/b.py}]`, and only `src/a.py` is git-tracked on base
- **THEN** the task is classified stale, because only the `files:` scope is probed

### Requirement: Malformed provides degrades to empty scope

If the frontmatter block cannot be parsed as YAML, `provides:` is not a list, or an
entry is not a map with a string `file`, the fallback SHALL contribute nothing for
the unusable part and SHALL NOT raise. A task that ends up with no resolvable scope
behaves exactly as an empty-`files` task does today.

#### Scenario: Provides is not a list
- **WHEN** a pending task declares `provides: done` and no `files:`
- **THEN** `_load_tasks` returns the row with `files: []` and `detect_stage`
  completes without error

#### Scenario: Entry without a file key is skipped
- **WHEN** a `provides:` list holds one map with a `file` and one map without,
  and no `files:` is declared
- **THEN** the row's `files` contains only the path from the entry that has one

