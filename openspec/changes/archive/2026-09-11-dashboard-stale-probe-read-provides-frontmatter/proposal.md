## Why

`router/dashboard.py`'s stale-bookkeeping probes decide whether a pending devkit
task already shipped by checking its declared file scope against git on base.
`_load_tasks` (`dashboard.py:349`) fills that scope solely from the `files:`
frontmatter key, and both `_pending_impl_stale` (`:749`) and `_pending_tail_stale`
(`:836`) drop any candidate whose `files` list is empty. Tasks authored with the
`provides:` frontmatter schema (a list of `{file, symbols, type}` entries; 112
TASK-*.md files across the fleet use it, 37 of them with no `files:` key at all)
therefore always load with `files: []` and can never be classified stale, even once
every one of their output files is merged. The spec sits at `tail-pending` (or
`ready-to-implement`) forever until someone hand-flips `status:`, which is exactly
what happened to `gracefully-giving-back` spec `008-precropped-slot-crops/TASK-008`
(fixed manually in that repo's PR #788).

The gap also cannot be closed by the existing frontmatter reader alone: the
hand-rolled `parse_frontmatter` in `taskformats/devkit/source.py` treats a block
list of maps as a flat list and returns `provides: ['file: src/a.py']` for a
two-entry list, so the file paths have to be read from the block with a real YAML
parse.

## What Changes

- Teach `_load_tasks` to derive a task's file scope from `provides[].file` when the
  task declares no `files:` (or an empty one), by YAML-parsing the frontmatter block
  with the package's existing `pyyaml` dependency and degrading to the current
  behaviour (empty scope) when the block does not parse or `provides` is malformed.
  An explicit non-empty `files:` keeps winning unchanged.
- Because the normalisation happens at parse time, `_pending_impl_stale`,
  `_pending_tail_stale`, `_count_tasks`, and the `stale-bookkeeping-sweep-check`
  reuse of `detect_stage` all pick it up without touching their probe logic.

Out of scope: the orchestrator's own devkit `TaskSource` (`source.py:288`) also
reads only `files:`; its scheduling contract (`runnable_frontier` treats an empty
file set as colliding with nothing) is a separate concern and is deliberately not
changed here.

## Capabilities

### New Capabilities

- `devkit-task-file-scope-resolution`: how the dashboard resolves a devkit task's
  file scope for stale-bookkeeping detection, including the `provides:` fallback and
  its precedence and failure rules.

### Modified Capabilities

None. `stale-bookkeeping-sweep-check` reuses `detect_stage` as-is and inherits the
fix; `openspec-stale-bookkeeping-detection` covers the OpenSpec path, which has no
per-task frontmatter and is unaffected.

## Impact

- `src/worktrail/router/dashboard.py` (`_load_tasks` and a small frontmatter helper).
- `tests/router/test_dashboard.py` (regression coverage for `provides:`-only impl and
  tail tasks, precedence, and malformed input).
- No change to the devkit `FIELD_SCHEMA`, `parse_frontmatter`, the orchestrator task
  source, the stale probes' git-freshness logic, or the plugin surface.
