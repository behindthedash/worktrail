## Context

`dashboard.py` owns the canonical ambiguity rule via `find_spec_file()`'s
`_is_spec_doc`/`_rank`/tie-check logic (see `dashboard.py:150-211`; `_rank` is
a closure nested inside `find_spec_file()`, not importable on its own). Both
existing self-checks (`policy_selfcheck.py`, `automerge_selfcheck.py`) live
in `src/worktrail/router/`, expose `check_repo`/`sweep`/`main`, and are
registered as `worktrail-*-selfcheck` console scripts. This change adds a
third self-check for a third drift class (spec-file naming ambiguity)
following the same shape.

## Goals / Non-Goals

**Goals:**
- Flag every spec directory where `find_spec_file()` would return `None` due
  to 2+ tied no-signal candidates, across one repo or a `--repos-root` sweep.
- Match the existing two self-checks' CLI shape, output shape, and exit-code
  convention exactly, so tooling that already knows how to invoke/parse one
  self-check (or a future fleet-guard sweep) needs no new integration logic.

**Non-Goals:**
- Changing `find_spec_file()`'s own behavior or return value.
- Wiring this detector into any cron/fleet-guard sweep (devops repo, out of
  scope for this change — see proposal.md Impact).
- Detecting any other kind of spec-directory drift (stale specs, missing
  tasks, etc.) — those are separate concerns with their own detectors
  (`check_spec_sync.py`, `staleness_warnings` in `dashboard.py`).

## Decisions

**Reuse `dashboard.py`'s ambiguity logic directly, not reimplement it.**
`dashboard_selfcheck.py` imports `_is_spec_doc` (candidate classification)
and `find_spec_file` (the tie decision itself) from `dashboard.py`, rather
than duplicating the candidate-classification or ranking rules. `_rank` is a
closure nested inside `find_spec_file()`, not a module-level name, so it
cannot be imported directly without promoting it to module scope in
`dashboard.py` — out of this change's scope (see Non-Goals). Calling
`find_spec_file()` itself is the correct-by-construction alternative: for a
spec directory with a non-empty `_is_spec_doc` candidate set, it returns
`None` for exactly one reason — 2+ candidates tied at the lowest,
no-signal rank — so this stays in sync with the tie condition by
construction, the same guarantee direct `_rank` reuse would have given.
Alternative considered: reimplement the rank/exclusion logic locally (as
`policy_selfcheck.py` does for its own regex-based signals, since it has no
shared logic to import). Rejected — a second independent implementation of
the same classification would drift the moment one side changes (this is
the same class of bug — undetected silent divergence — the check itself
exists to catch, applied to itself).

**Scope the scan to `docs/specs/*/` only, not `openspec/changes/*/`.**
`find_spec_file()` and its ambiguity rule apply to the devkit spec-folder
format (`docs/specs/<id>/`). OpenSpec changes (`openspec/changes/<id>/`) use
a single fixed `proposal.md`/`design.md`/`tasks.md`/`specs/` shape with no
equivalent free-naming ambiguity — there is no analogous "which file is the
spec" guess to make. Scanning only `docs/specs/*/` keeps the check aligned
with the actual risk surface instead of flagging directories where the
question doesn't apply.

**Return the tied candidate filenames in the finding, not just a boolean.**
Mirrors `policy_selfcheck.py`/`automerge_selfcheck.py`'s `detail` string
convention — a human or agent triaging the flagged repo needs to see which
files are actually tied to resolve it (rename one to a recognized pattern,
or delete/move the extra), not just that "some directory is ambiguous."

## Risks / Trade-offs

- [Risk] `dashboard.py`'s private `_is_spec_doc` helper and public
  `find_spec_file` are not a published API contract; a future rename inside
  `dashboard.py` would break this import silently until tests catch it. →
  Mitigation: `test_dashboard_selfcheck.py` imports and exercises these
  directly (not just via subprocess), so a rename fails the test suite
  immediately rather than at runtime.
- [Risk] A repo with a very large number of spec directories could make the
  sweep slow. → Mitigation: same glob-based scan `find_spec_file()` already
  does per directory (already runs on every `dashboard.py` invocation);
  bounded by repo size, no new complexity class introduced.
