## Context

The brief (`20261005-094206-auto-drain-repo-exclusion-list`) left four decisions open: the
scope of the exclusion (all sweeps vs only the drain loop vs per-subcommand), whether a repo
can self-exclude through its own policy, the name and shape of the key, and how an unknown
repo name in the list is handled. It also left the implementation shape open ("filter at the
`discover_repo_names` call sites -- or a filtered wrapper around it").

Two structural facts constrain the answer:

- **The drain's repo sweeps and its claim loop reach a repo by different routes.** The sweeps
  enumerate `--repos-root` themselves (`discover_repo_names()`), but the claim loop does not
  enumerate anything: it spawns a fresh `worktrail-go auto` one-shot, and the *spawned
  session* picks the brief (via `dashboard.py --auto` → `auto_pick_brief()`). A drain-side
  filter alone can therefore never stop a claim -- and a filter that lives only in the spawned
  session would leave the drain's own `count_ready_briefs()` disagreeing with it.
- **Substituting the exclusion into `discover_repo_names()` itself is not available.** It is a
  pure function of `repos_root` with many non-drain callers (operator-invoked selfchecks, the
  dashboard scan, `reconcile_pr_labels`), and `tests/router/test_policy_selfcheck.py` drives it
  directly. Hiding a machine-config read inside it would silently change every one of those
  callers and make the function untestable without config isolation.

## Goals / Non-Goals

**Goals:**

- One machine-wide list that keeps a repo out of every unattended path the drain drives.
- A stop that stays honest: a queue of only-excluded briefs must terminate the loop cleanly.
- An explicit repo scope (`--go-repo`, `--auto-repo`) keeps working; config never silently
  overrides what an operator typed.
- Fail loudly on a malformed list; never on an entry that merely matches nothing.

**Non-Goals:**

- Gating operator-invoked selfcheck CLIs, `reconcile_pr_labels`, or the orchestrator's own
  fan-out. A human running a deliberate sweep is not the unattended drain.
- Per-repo self-exclusion (a repo opting itself out through its own policy).
- A CLI flag for the list. It is persistent operator policy, not a per-invocation knob.

## Decisions

**Scope: the drain and the selection it delegates to -- not the router's other sweeps.** The
list governs (1) the drain's own enumerations (six remediation finders +
`repo_sandbox_roots()`), (2) the two pre-passes it drives (seed-backlog, intake-triage), (3)
the ready count the loop consults, and (4) automatic brief selection, because that is where
the drain's claim actually happens. It does not govern `worktrail-policy-selfcheck`,
`worktrail-branch-selfcheck`, `worktrail-dashboard-selfcheck`, `worktrail-automerge-selfcheck`,
`worktrail-policy-drift-selfcheck`, `worktrail-reconcile-pr-labels`, or the orchestrator: those
are either deliberately operator-invoked or scoped by their own arguments, and widening the
list onto them would make "excluded" mean something different per subsystem. The line drawn is
"automatic enumeration and selection", which is also exactly the `release_gate` precedent's
line (`dashboard.py:2380`: auto pick gated, interactive selection unaffected).

**Home and shape: `routing.drain.exclude_repos`, machine-wide only.** It is a drain-behavior
knob in the file that already carries the drain's other run-wide defaults, resolved through
the same `resolve_routing()` mapping (`drain.max_workers`) and validated in the same
loud-failure block (`_validate_routing_drain()`, `policy.py:750`). Entries are repo directory
names -- basenames -- because that is what `discover_repo_names()` returns, what `--go-repo`
matches, and what a brief's `repo:` value reduces to (`Path(repo).name`); accepting paths
would invite a second, path-shaped matching rule that the discovery side cannot check.
A repo cannot self-exclude: a repo-local `routing:` block *replaces* the machine-wide file
wholesale (`_resolve_routing()`, `policy.py:1273`), so honoring a local copy of the key would
mean a repo both un-excludes itself and drops the operator's spawn targeting; the local copy
is ignored with a warning instead. Rejected alternative: a repo-local policy key (the
`release_gate` shape) -- it would put the list in the wrong hands (the operator's list must
work on repos the operator does not edit) and would need N files to hold one machine policy.

**A machine-wide read helper on the policy side, one filter helper on the drain side.** The
pick path (`dashboard.py`, inside the spawned session) and the drain process each resolve the
list through a small `policy.py` helper that reads the machine-wide routing file directly
(never `_resolve_routing()`'s repo-local branch). The drain then filters discovery through one
drain-side helper -- the "filtered wrapper around `discover_repo_names()`" the brief suggested
-- which the drain's six finders and `repo_sandbox_roots()` call in place of their current
`discover_repo_names()` + `go_repo` pair, so the rule lives in exactly one function and each
call site gets *shorter*, not longer. The finder signature carries the list explicitly
(`finder(repos_root, go_repo, exclude_repos)`), matching how `go_repo` already threads through
`StageRemediation`; the alternative -- filtering findings inside `sweep_remediations()` --
was rejected because it lets the sweep still *scan* excluded repos (network `gh` calls per
repo in the branch sweep) and gives the sandbox-root and seed-backlog paths no shared rule.

**Explicit scope wins; the list governs only what is automatic.** `--go-repo R` and
`--auto-repo R` name the scope the operator asked for, so an excluded `R` is still swept,
drained, and picked, and the drain logs the override. This follows the drain's established
CLI-over-config precedence (`drain-operator-config`: explicit flags win entirely) and avoids
a refusal that would break an existing per-repo cron script on an unrelated config edit.
Both halves are needed for the rule to hold: the drain normalizes its list once at the run
boundary (dropping the explicitly named repo), and `auto_pick_brief()` treats its explicit
`repo_filter` the same way -- otherwise the spawned session would skip the very brief the
explicit `--go-repo` run exists to claim.

**Termination: the ready count consumes the same list as the pick.** Without this, a queue
holding only excluded briefs keeps `ready_count > 0`; the drain spawns a one-shot, the session
picks nothing, and `decide()` stops with `no_pick` -- one wasted headless session and a
misleading stop reason. With the count filtered, the loop stops at the top with `queue_empty`
and spawns nothing. Both sides match a brief's `repo:` by basename, so a value written as an
absolute path, a bare name, or `owner/name` behaves identically.

**An unmatched entry is inert and reported, never fatal.** The same machine-wide list is used
with different `--repos-root` values (`~/projects` in production, tmp roots in tests), so an
entry with no matching directory is a normal state, not a config error -- failing on it would
make one operator's list un-runnable under any narrower root. It is reported once, in the
drain's log, when the root exists; a missing root stays the existing no-op with no noise.

**Posture split: fail loud on shape, best-effort on read.** A malformed `exclude_repos` raises
`OperatorConfigError` (the drain block's existing treatment of stated operator intent), and
`worktrail-drain` exits 2 before any spawn -- the drain's startup `load_policy()` surfaces it.
The pick path inside a spawned session treats an unreadable or malformed file as "no
exclusions" and warns, matching `_load_dashboard_policy()`'s established best-effort posture
in that file (`dashboard.py:439`): the loud gate for this config already ran in the drain, and
a hard failure there would break the interactive front door for a config problem the drain
reports properly.

## Risks / Trade-offs

- **A stale per-repo cron with `--go-repo R` keeps touching an excluded `R`.** Accepted: the
  override is logged on every such run, and the alternative (refusing to start) converts an
  unrelated routing edit into a broken cron.
- **The pick path reads the routing file on every `--auto` call.** One YAML read per pick,
  against a drain iteration measured in minutes; no caching is introduced, so an operator's
  edit is honored by the next pick -- the same freshness property `_load_dashboard_policy()`
  already has.
- **Excluded repos still appear in the interactive dashboard's counts** (ready briefs, backlog
  rows). Accepted: the dashboard is the human view, and a human seeing an excluded repo's
  backlog is not unattended work touching it.
- **`repo-excluded` becomes a new miss-log bucket** alongside the existing skip reasons; the
  reason deliberately carries no colon, so `log_auto_pick_miss()`'s `:`-split aggregation
  treats it as its own coarse category rather than folding it into `blocked` or another
  bucket.
