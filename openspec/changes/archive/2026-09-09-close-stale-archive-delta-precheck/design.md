## Context

`flip_and_archive` mutates `tasks.md` first and shells out to `openspec archive`
second. Any archive-time failure therefore lands on an already-mutated worktree.
The dashboard already owns two read-only structural checks over a change's delta
specs: `_openspec_delta_reconciled` (are all delta headings canonical?) and
`_openspec_delta_drift` (did an archived sibling overtake this delta?). Neither
is consulted by the close-stale command.

## Decisions

### D1: Pre-check runs before any checkbox flip

The pre-check is the first thing `flip_and_archive` does after locating
`tasks.md`. A refusal returns with `flipped == []`, `archived == False`, and the
worktree byte-for-byte unchanged. Rationale: the agent must be able to fix the
delta and re-run the same command without first reverting partial flips.

### D2: Three refusal classes, one structured field

`result["precheck"]` is always present once `checked` is true:

```
{"validate_ok": bool, "validate_output": str,
 "missing_canonical": [{"capability", "requirement", "kind"}],
 "delta_drift": [{"capability", "requirement", "archived_change_id"}],
 "drift_allowed": bool}
```

- **validate failure** -- `openspec validate <id> --strict` non-zero. Mocked in
  tests the same way `openspec archive` already is.
- **missing canonical target** -- a `MODIFIED` or `REMOVED` requirement heading,
  or a `RENAMED ... FROM:` name, that does not appear as a `### Requirement:`
  heading in `openspec/specs/<capability>/spec.md` (or that canonical file does
  not exist). This is the exact condition under which `openspec archive` errors.
  `ADDED` requirements are not checked here (a brand-new capability legitimately
  has no canonical file yet).
- **archived-sibling drift** -- `dashboard._openspec_delta_drift(change_dir,
  worktree)` returns findings. Reused, not duplicated: the git-timestamp rule is
  specified once in `openspec-delta-drift-detection`.

The error string names the class and the first offending
`capability/requirement` so a non-JSON caller still sees the cause.

### D3: `--allow-delta-drift` overrides only the drift class

Drift is time-based (last commit of the delta vs. add-commit of the archived
sibling). An agent that has just reconciled the delta in the fix-branch worktree
has not committed yet, so the delta's last-commit time is still old and the check
would refuse a delta that is actually current. The flag bypasses that class
alone and is recorded as `drift_allowed: true` in the result. Validate failure
and missing canonical targets cannot be overridden: archive would fail anyway.

### D4: Missing-canonical parsing reuses dashboard helpers

`_iter_openspec_delta_sections`, `_OPENSPEC_REQUIREMENT`, and `_OPENSPEC_RENAME`
from `dashboard.py` are imported rather than re-implemented, so the heading
grammar stays defined in one place. `_openspec_delta_reconciled` itself is not
reused because it answers a different question (is the delta already *applied*?)
and returns a bare bool with no offending-name detail.

## Alternatives considered

- **Only run `openspec validate --strict`.** Rejected: validate checks the delta's
  own structure, not its relationship to the canonical spec; a `MODIFIED`
  requirement renamed by a sibling passes validate and fails archive.
- **Auto-repair the delta.** Rejected: which of two divergent requirement texts
  is right is a judgment call; the command refuses and reports.
- **Refuse on drift with no override.** Rejected per D3.
