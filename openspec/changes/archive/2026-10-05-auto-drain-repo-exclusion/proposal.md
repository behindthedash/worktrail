## Why

The unattended drain has no way to leave a repo alone. Every repo-scoped thing it does starts
from `discover_repo_names(repos_root)` (`src/worktrail/router/policy_selfcheck.py:67` --
"every immediate subdirectory of `repos_root` that is a git repo"), and every discovered repo
is swept: the six `REMEDIATION_TABLE` finders (`src/worktrail/drain/drain.py:819,855,890,926,
967,1336`), the codex sandbox's writable roots (`repo_sandbox_roots()`, `:304`), the
seed-backlog pre-pass (`src/worktrail/workqueue/seed_backlog.py:92,131,187`), and the
intake-triage pre-pass, which fans one evaluator agent out per repo group and can open fold/
propose pull requests. The claim loop is repo-blind in the same way: `worktrail-go auto` picks
the oldest eligible brief whatever repo it names, and the drain counts those briefs as ready
so long as they are not blocked or deferred (`count_ready_briefs()`, `drain.py:343`).

The only repo-scoping knobs today point the other way: `--go-repo` restricts a run to *one*
repo (`drain.py:2958`) and `--repos-root` changes the sweep root (`drain.py:3022`). Neither
expresses "run across the root, except these repos", which is what an operator needs for a
repo they are hand-editing, keeping private, or deliberately parking -- the requester's list
being `career-teleprompt`, `continuum`, and `when-truth-becomes-optional`.

The machine-wide routing file is the natural home: it already carries the `drain:` block
(`drain.max_workers`, `docs/config/routing.yaml.example:242`, validated by
`_validate_routing_drain()`, `src/worktrail/router/policy.py:750`, and resolved through
`resolve_routing()`, `policy.py:1304`), and the drain already loads it for exactly this kind of
run-wide setting (`machine_wide_routing()`, `drain.py:553`). The closest existing precedent for
gating unattended scheduling by repo is `release_gate`: repo policy sets it, `auto_pick_brief()`
(`src/worktrail/router/dashboard.py:2282`) skips that repo's non-blocker briefs with the
structured reason `release-gate:<name>` (`:2380`), and interactive selection is unaffected.
(Work-queue brief `20261005-094206-auto-drain-repo-exclusion-list`.)

## What Changes

- **`routing.drain.exclude_repos` joins the machine-wide routing file's `drain:` block**: a
  list of repository directory names (basenames, exactly as `discover_repo_names()` reports
  them). `resolve_routing()`'s `drain` mapping carries the list under `exclude_repos`, empty
  when the key is absent; a value that is
  not a list of non-empty strings raises `OperatorConfigError` the way a malformed
  `max_workers` already does. The list resolves from the machine-wide file only -- a
  repo-local `routing:` block's copy is ignored with a warning, since a repo that could
  un-exclude itself would be no operator gate.
- **Every drain repo sweep skips excluded repos**: the six remediation finders and
  `repo_sandbox_roots()` drop excluded names during discovery, so no finding, agent spawn,
  PR, branch or worktree mutation, and no sandbox write root is ever produced for one.
- **The drain's seed-backlog and intake-triage pre-passes skip excluded repos**: no brief is
  seeded for one, and no evaluator is spawned for (or verdict applied to) its intake briefs.
- **Excluded repos' briefs are not ready and are never automatically picked**: the drain's
  ready count ignores them (so a queue holding only excluded briefs stops `queue_empty`
  instead of burning a one-shot on a `no_pick`), and automatic selection skips them with the
  structured reason `repo-excluded`, alongside the existing skip reasons. A manual claim, an
  interactive route, and a repo named explicitly (`--go-repo R`, `--auto-repo R`) all keep
  reaching them -- an explicit scope beats the list for that run, and the drain logs the
  override.
- **The drain reports what it applied**: the resolved list is logged before the first
  iteration (under `--dry-run` too), and an entry matching no repo under `--repos-root` is
  reported as inert rather than failing the run -- the same machine-wide list is used against
  different roots.
- The operator docs follow: `docs/config/routing.yaml.example`, the `worktrail-routing-config`
  cookbook, `worktrail-go`'s drain reference, and the auto-mode skip-reason list.

## Capabilities

### New Capabilities

- `drain-repo-exclusion`: the `routing.drain.exclude_repos` key (shape, validation,
  machine-wide-only resolution), the drain-side footprint (sweeps, sandbox roots, seed-backlog
  and intake-triage pre-passes, ready count), the `repo-excluded` automatic-selection skip
  reason, explicit-scope precedence, and the run-log reporting of what was applied.

### Modified Capabilities

_None._

## Impact

- `src/worktrail/router/policy.py` -- `_validate_routing_drain()`'s shape check,
  `resolve_routing()`'s `drain` mapping, and the machine-wide-only read the pick path and the
  drain share.
- `src/worktrail/drain/drain.py` -- `DrainConfig`, main()'s resolution and reporting, the
  discovery sites (`repo_sandbox_roots()` and the six finders), `sweep_remediations()`'s
  finder channel, `count_ready_briefs()`, and the seed-backlog/intake-triage pre-pass calls.
- `src/worktrail/workqueue/seed_backlog.py` -- the three finders and `seed_backlog()` gain the
  exclusion list.
- `src/worktrail/workqueue/queue_triage.py` -- `inventory()` filters excluded repo groups and
  `evaluate` accepts the list for the drain's pre-pass.
- `src/worktrail/router/dashboard.py` -- `auto_pick_brief()`'s new skip reason and its
  machine-wide list read.
- `docs/config/routing.yaml.example`, `skills/worktrail-routing-config/SKILL.md`,
  `skills/worktrail-go/references/drain.md`, `skills/worktrail-go/references/auto-mode.md`.
- Tests: `tests/router/test_policy.py`, `tests/drain/test_drain.py`,
  `tests/workqueue/test_seed_backlog.py`, `tests/workqueue/test_queue_triage_inventory.py`,
  `tests/workqueue/test_queue_triage.py`, `tests/router/test_dashboard.py`.
- Not in scope: operator-invoked selfcheck CLIs (`worktrail-policy-selfcheck`,
  `worktrail-branch-selfcheck`, `worktrail-dashboard-selfcheck`, `worktrail-automerge-selfcheck`,
  `worktrail-policy-drift-selfcheck`, `worktrail-reconcile-pr-labels`) and the orchestrator's
  own fan-out keep enumerating every repo under `--repos-root`; a deliberate operator sweep is
  not the unattended drain. The interactive dashboard's rendered counts are unchanged.
