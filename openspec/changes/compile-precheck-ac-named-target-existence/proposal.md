## Why

Task 3.1 of the devops change `canonical-checkout-unborn-head-detection` carried the acceptance
criterion "Update the `canonical-checkout-drift-sweep.sh` entry in `scripts/README.md`". No such
entry existed at the run's base commit `bf64401` -- `scripts/README.md` matched `canonical` only on
an unrelated line about `scripts/claude-hooks/`. A worker cannot update an entry that is not there,
so it invented an 18-line section instead; the mismatch survived three review rounds and ended in a
human decision, `dec-openspec-changes-canonical-checkout-unbo-925c3e395fc2` (resolved 2026-09-20:
"narrow to a minimal entry"). The whole loop -- fan-out, three rounds, a blocked run, a human ruling
-- was spent discovering a fact that was already true of the base tree before a single worker
started.

`conductor/compile.py` is the one context that reads the whole change against the real repo, and it
already refuses a plan for three classes of authoring defect: tasks with no file scope
(`needs_compile`), unordered file collisions (`runplan.unordered_file_collisions`), and requirements
with no task coverage (`req_coverage.find_uncovered_requirements`). It does not read acceptance
criteria against the tree at all: `grep -n "base tree\|precheck\|acceptance" src/worktrail/conductor/compile.py`
returns nothing (confirmed 2026-09-20). Nothing else covers this either -- the nearest active change,
`premise-check-path-needle-shape-filter`, is scoped to triage-time path-needle extraction in
`router/brief_probes.py` / `workqueue/premise_check.py`, not to compile.

An AC that says "update the existing X in `<file>`" is a checkable claim about the base tree: either
`<file>` contains `X` or it does not. Checking it costs one file read at compile time and fails
before fan-out, which is the cheapest possible place for this to fail.

## What Changes

- New `src/worktrail/conductor/ac_targets.py` with `find_missing_ac_targets(spec_dir, repo)`,
  returning one finding per task whose text asks for an update to a **backticked** entity in a
  named repo file that does not contain it.
- The extraction is deliberately narrow, to make a finding mean something: a task only produces a
  finding when its prose pairs an update-verb (`update`, `modify`, `amend`, `extend`, `replace`,
  `rename`, `remove`, `fix`, `correct`) with a backticked needle and a backticked repo-relative file
  path in the same sentence. Prose without backticks, additive phrasing (`add`, `create`,
  `document`), and paths that do not exist in the tree produce nothing.
- `compile.main()` gates on the findings the same way it already gates on scope, ordering and
  requirement coverage: findings are printed to stderr by a new `_print_ac_target_gap_error`, the
  `.compile-ok` marker is withheld, and the exit code is 1 in both plain and `--json` modes.

## Capabilities

### New Capabilities
- `compile-ac-target-precheck`: compile refuses a change whose acceptance criteria tell a worker to
  update something the base tree does not contain.

### Modified Capabilities

## Impact

- `src/worktrail/conductor/ac_targets.py` (new), `src/worktrail/conductor/compile.py` (wiring).
- `tests/conductor/test_ac_targets.py` (new), `tests/conductor/test_compile.py` (gate wiring).
- A change with a phantom-target AC now fails `worktrail-compile` with exit 1 instead of reaching
  fan-out. No effect on a change whose ACs name real targets.
