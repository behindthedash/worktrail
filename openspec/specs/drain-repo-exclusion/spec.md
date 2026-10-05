# drain-repo-exclusion Specification

## Purpose

Give the operator one machine-wide list -- `routing.drain.exclude_repos` in the routing file --
naming repositories the machine's unattended work must not touch, so an excluded repo is swept
by no drain remediation, granted no sandbox write root, seeded and triaged by no drain
pre-pass, counted ready by no queue view the drain consults, and picked by no automatic
selection -- while capture, triage-by-hand, explicit claims, and an explicitly named repo
(`--go-repo`, `--auto-repo`) keep working exactly as before.

## Requirements

### Requirement: The machine-wide routing file declares repos excluded from unattended draining

The machine-wide routing file (`worktrail_home()/routing.yaml`, or `WORKTRAIL_ROUTING_FILE`
when set) SHALL accept `routing.drain.exclude_repos`: a list whose every entry is a repository
directory name -- a basename exactly as `discover_repo_names()` reports it, never a path.
The resolved routing table's `drain` mapping (the same mapping `drain.max_workers` resolves
through) SHALL carry the validated list under `exclude_repos` in declared order when the key
is present, and consumers SHALL treat a missing `exclude_repos` key -- including the empty
`drain` mapping `resolve_routing()` returns when no routing block is configured at all -- as
an empty list. The drain SHALL read that resolved value, and automatic selection SHALL read
the same validated value through the shared machine-wide read, so neither consumer parses the
raw file on its own. A value that is not a list of non-empty strings SHALL raise
`OperatorConfigError` naming the key, matching the drain block's existing loud-failure
treatment of a malformed `max_workers`; it SHALL never be silently dropped or coerced.

The list SHALL be read from the machine-wide file only. A repo-local `routing:` block
replaces the machine-wide file wholesale for spawn targeting, so honoring its own
`drain.exclude_repos` would let a governed repo un-exclude itself; a non-empty repo-local
`routing:` block that declares the key SHALL instead be ignored with a warning naming the key
as machine-wide only.

#### Scenario: The key is absent

- **WHEN** the machine-wide routing file declares no `drain.exclude_repos` (or no `drain:`
  block at all)
- **THEN** a missing `exclude_repos` key resolves as an empty list and nothing is excluded

#### Scenario: The key is declared

- **WHEN** the machine-wide routing file declares
  `drain: {exclude_repos: [career-teleprompt, continuum]}`
- **THEN** the resolved `drain` mapping carries those two names in declared order

#### Scenario: A malformed value fails loud

- **WHEN** `drain.exclude_repos` is a bare string such as `continuum`, a list containing a
  non-string, or a list containing an empty or whitespace-only entry
- **THEN** loading the policy raises `OperatorConfigError` naming `routing.drain.exclude_repos`,
  and `worktrail-drain` exits 2 without starting a run

#### Scenario: A repo-local copy of the key is inert and warned

- **WHEN** a repo's local `routing:` block declares `drain.exclude_repos`
- **THEN** that block's copy does not exclude anything, and policy loading emits a warning
  naming `drain.exclude_repos` as machine-wide only

### Requirement: Drain repo sweeps skip excluded repos

Every repo enumeration the drain performs for its own sweeps SHALL drop excluded names before
any finding is produced, so an excluded repo yields no remediation finding, no agent spawn, no
pull request, and no branch or worktree mutation. This covers the six `REMEDIATION_TABLE`
finders -- resumable quarantines, verify-pending specs, sync-pending specs, stale bookkeeping,
complete OpenSpec changes, and stale branches -- and `repo_sandbox_roots()`, which SHALL not
add an excluded repo's `.git` or its `<name>-worktrees` sibling to any spawned one-shot's
writable sandbox roots. Names not in the list SHALL be swept exactly as before, and the
`--go-repo` restriction SHALL keep applying alongside the list -- except where the
explicit-scope rule below makes an explicitly named repo win over it.

#### Scenario: An excluded repo's resumable quarantine is not swept

- **WHEN** a repo carrying a resumable quarantine group is excluded and the drain's pre-loop
  sweep runs
- **THEN** no finding is returned for that repo, no remediation agent is spawned for it, and
  every other repo's findings are remediated unchanged

#### Scenario: An excluded repo's stale branch is not pruned

- **WHEN** an excluded repo has a branch a stale-branch sweep would otherwise prune
- **THEN** the sweep returns no finding for that repo, the branch is left untouched, and no
  pull request is opened for it

#### Scenario: An excluded repo is no writable sandbox root

- **WHEN** the drain builds a one-shot's sandbox args with an exclusion list covering a repo
  under `--repos-root`
- **THEN** neither that repo's `.git` nor its `<name>-worktrees` sibling appears in the
  sandbox's writable roots, while every non-excluded repo's roots are unchanged

#### Scenario: An unlisted repo is swept as before

- **WHEN** the exclusion list names repos other than the one carrying a finding
- **THEN** the finding is returned and remediated exactly as before this requirement

### Requirement: The drain's seeding and triage pre-passes skip excluded repos

The drain's seed-backlog pre-pass SHALL not scan an excluded repo, so no needs-tasks,
ready-to-implement, or epic-gap brief is ever seeded for one -- its specs never become queue
briefs. The drain's intake-triage pre-pass SHALL not evaluate or apply verdicts for an
excluded repo's briefs: no evaluator agent is spawned against that repo group, no fold or
propose pull request is opened for it, and its intake briefs stay untouched in the queue
(neither triaged, released, nor rewritten). Every non-excluded repo group SHALL be processed
exactly as before, and the pre-passes stay best-effort: one repo's exclusion changes nothing
about their never-abort failure discipline. The `--seed-backlog --dry-run` preview SHALL
likewise count no excluded repo's specs.

#### Scenario: No brief is seeded for an excluded repo

- **WHEN** the seed-backlog pre-pass runs with a needs-tasks spec in an excluded repo and
  another in a non-excluded repo
- **THEN** only the non-excluded repo's spec becomes a queue brief, and the excluded repo's
  spec is untouched

#### Scenario: No evaluator is spawned for an excluded repo's intake briefs

- **WHEN** `--intake-triage` runs with queued intake briefs for an excluded repo and for a
  non-excluded repo
- **THEN** an evaluator is spawned only for the non-excluded repo's group, the excluded
  repo's briefs remain in the queue unmodified, and the applied verdicts cover only the
  evaluated group

#### Scenario: The dry-run preview counts no excluded repo

- **WHEN** `--seed-backlog --dry-run` runs against a root whose needs-tasks specs all live in
  excluded repos
- **THEN** the preview reports nothing would be seeded

### Requirement: A brief for an excluded repo is not ready and is never automatically picked

A queued brief whose `repo:` frontmatter resolves by basename to an excluded name SHALL not
count toward the drain loop's ready count, whether the value is an absolute path, a bare name,
or an `owner/name` form. A queue whose only briefs are for excluded repos SHALL therefore stop
the drain with the existing `queue_empty` reason and spawn no iteration, rather than spending
a session that can only report `no_pick`.

Automatic selection SHALL skip such a brief with the structured skip reason `repo-excluded`
and SHALL never return it as the pick, so a fresh `worktrail-go auto` session (the drain's own
or any other) leaves it alone while a non-excluded brief in the same queue is still picked.
The reason's leading token carries no colon, so `log_auto_pick_miss()` aggregates it as its own
coarse bucket, not under an existing one. Interactive selection is untouched: nothing in this
requirement refuses or alters a manual claim, an interactive route, or an explicitly named
repo, all of which are governed by the next requirement.

#### Scenario: Only excluded briefs stop the drain cleanly

- **WHEN** the queue holds only briefs whose repos are excluded
- **THEN** the drain's ready count is zero, it stops with the `queue_empty` reason, and no
  one-shot is spawned

#### Scenario: Automatic selection skips with the repo-excluded reason

- **WHEN** automatic selection evaluates a queued brief whose repo is excluded
- **THEN** the brief is recorded as skipped with reason `repo-excluded` and is never the pick

#### Scenario: Every repo shape matches by basename

- **WHEN** briefs carry `repo:` values of `/home/operator/projects/continuum`, `continuum`,
  and `acme/continuum`, and `continuum` is excluded
- **THEN** all three briefs are skipped with reason `repo-excluded`

#### Scenario: A non-excluded brief is still picked

- **WHEN** the same queue also holds a brief whose repo is not excluded
- **THEN** automatic selection returns that brief as the pick

### Requirement: An explicit repo scope overrides the exclusion list

The exclusion list SHALL govern automatic enumeration and selection only; a repo named
explicitly on the command line SHALL win over it. `worktrail-drain --go-repo R` with `R`
excluded SHALL sweep and drain `R` for that run -- the same way the flag already restricts a
run to one repo -- and SHALL log that the explicit flag overrides the exclusion, rather than
refusing to start or skipping `R`. Likewise `worktrail-dashboard --auto --auto-repo R` SHALL
pick `R`'s briefs and record no `repo-excluded` skip for them, because the explicit filter
names the scope the operator asked for.

#### Scenario: An excluded repo named by --go-repo is drained anyway

- **WHEN** `--go-repo continuum` is passed and `continuum` is in `drain.exclude_repos`
- **THEN** the run sweeps and drains `continuum`'s repos and briefs, and the drain log states
  that the explicit `--go-repo` overrode the exclusion list

#### Scenario: An excluded repo named by --auto-repo is still picked

- **WHEN** automatic selection runs with `--auto-repo continuum` and `continuum` is excluded
- **THEN** its briefs are eligible to be the pick and none is skipped as `repo-excluded`

#### Scenario: An explicit claim of an excluded repo's brief still succeeds

- **WHEN** an operator claims an excluded repo's queued brief by hand
- **THEN** the claim succeeds exactly as it does for any other brief

### Requirement: The drain reports the applied exclusion list

When the resolved exclusion list is non-empty, the drain SHALL report it before its first
iteration -- including under `--dry-run` -- so the run log states what was excluded rather
than leaving its absence to be inferred. When `--repos-root` exists, an entry that matches no
repo directory there SHALL be reported as unmatched: the entry is inert (it excludes nothing),
and it SHALL NOT be fatal or change the run's exit status, because the same machine-wide list
is used against different repo roots. A missing `--repos-root` stays the existing no-op and
produces no unmatched-entry noise.

#### Scenario: The run log names the resolved list

- **WHEN** a drain starts with a non-empty exclusion list
- **THEN** its log names the excluded repos before the first iteration, and `--dry-run`
  reports them too

#### Scenario: An entry matching no repo is inert and reported

- **WHEN** the list names a repo with no directory under the run's `--repos-root`
- **THEN** the log reports that entry as matching no repo, nothing is excluded beyond the
  matching entries, and the drain proceeds and exits exactly as it would without the entry

#### Scenario: An absent repos-root is not an unmatched report

- **WHEN** `--repos-root` names a path that does not exist
- **THEN** the drain behaves as the existing no-op and reports no unmatched entries
