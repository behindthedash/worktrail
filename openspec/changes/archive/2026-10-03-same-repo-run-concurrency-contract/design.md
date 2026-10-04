## Context

The brief that proposed this change (`20261003-002924-per-spec-lock-allows-concurrent`) left its
central question open: *is per-spec locking the intended contract — in which case the operator
rule "never run concurrent orchestrators on one repo" needs revisiting and the real defect is
resource contention — or is a repo-level guard the right answer?* It also noted that a hard repo
lock would serialize legitimately disjoint-spec work.

Facts the decision rests on:

- Same-spec double dispatch is already covered twice: `RunLock` aborts a second `full-real` on a
  held `(repo, spec)` lock, and `worktrail-run-record claim` plus the agent-invoked
  `#active-conflicts-scan` gate make an unclaimed same-spec race fail fast
  (`docs/specs/research/concurrent-go-dispatch-brief-claim-race.md`).
- Cross-spec collisions are only partly covered: `claim --expected-files` catches overlapping
  file sets at claim time, but nothing looks at two live runs with disjoint file scope.
- The observed cost of the uncovered case is contention plus invisibility: a task's full-suite
  verification was pushed past its 600s foreground cap by concurrent worker load
  (`go-20261002-220652`), and merges interleaving between two runs stale each other's task
  worktrees.
- Memory (`feedback_never_run_concurrent_orchestrators_same_repo`) already instructs operators
  to treat *any* live run in the repo as a hard stop and claims a repo-wide scan
  (`active-conflicts --repo <repo>`) already exists — it does not; the CLI requires
  `--specification`. The rule is doctrine, unenforced by anything a launch path can consult.

## Goals / Non-Goals

**Goals**

- Make a same-repo concurrent launch visible: name the other live run(s) at launch, before any
  worker spawns.
- Make the collision self-limiting: the launching run narrows its fan-out so two same-repo runs
  do not compound worker load.
- Reuse the existing run-record scan's live/stale semantics rather than inventing a second
  liveness rule, and give operators the repo-wide scan the doctrine already assumes.

**Non-Goals**

- A hard repo-level lock. Serializing disjoint-spec work by default is the failure mode the
  brief warned about, and it would contradict `RunLock`'s documented `(repo, spec)` scope.
- Signaling, pausing, or killing an already-running sibling run. Only the launcher can back off.
- Changing drain's worker model, the fix path's claim keys, the `--expected-files` claim check,
  or the per-spec hard stop.
- Cross-machine repo-wide detection (the remote claim ref stays per-spec).

## Decisions

**Per-spec locking stays the exclusive mechanism; the repo-level signal is advisory plus
throttle.** The evidence separates two things the "never concurrent" rule conflated: *who may
run* (exclusivity) and *how much load two runs may jointly consume* (contention). Exclusivity
is already correct per spec; contention is what degraded the run. So the launch path gains a
detection + warning + width cap, and per-spec exclusivity keeps its current hard-stop behavior.
This also resolves the doctrine mismatch without a new policy surface: the operator memory's
"treat any live run as a hard stop" becomes a real, named signal in the launch log plus an
automatic reduction, and the `active-conflicts` CLI learns the repo-wide mode that memory
already claimed it had.

**One halving, not an escalating reduction.** The cap is `max(1, floor(base / 2))` where `base`
is the width the launch would otherwise use (explicit `--max-workers`, else policy
`max_workers`, else plan width under `max_parallel_workers`). Rationale: a second same-repo
launcher taking half the width keeps the aggregate fan-out across the pair at roughly one run's
requested width — the property that bounds the contention the incident showed — while both runs
still make progress. Only the launcher backs off, and only once; the already-running sibling
keeps its width. A count-based or escalating rule would make the throttle's effect depend on
history the launcher cannot observe symmetrically.

**Reuse the live/stale partition; exclude self by specification.** Detection calls the same
`_active_conflicts()` that backs `#active-conflicts-scan`, with no specification filter, so a
record whose worktree is gone and whose files already landed never throttles a later run, and a
malformed record is skipped into `warnings` instead of aborting a launch. The launching run's
own record is identified by `specification` equal to this run's spec id — the same folder-name
key the journal and `RunLock` use, and the key `/go` claims before dispatching `full-real`
(`#active-conflicts-scan` is a hard stop that runs before any worktree or branch exists), so the
record is always claimed by launch time. Same-spec records are deliberately excluded rather than
reported: same-spec exclusivity is `RunLock`'s job and is strictly stronger, and an abandoned
same-spec record is the claim/reconcile flow's business. A record that was never claimed
(`specification: null`) is still reported — conservative, and the warning says which run it is.

**Report on stdout at launch, not in the run journal.** The fan-out width report
(`_resolve_max_workers`'s "fan-out workers: N (reason)") is already the launch-time channel for
this class of notice, and it is printed before any worker spawns; the journal is written around
fan-out and is consumed by resume/dashboard flows whose contracts this change does not need to
touch. No result-dict key is added either — the result shape is consumed by drain/verify and the
warning is a launch-time observation, not a run outcome.

Alternatives rejected: (a) a repo-level lock with an override flag — new policy surface, and
serializes disjoint work by default; (b) a repo-wide hard stop in `full-real` — same problem,
and it would turn a live-but-idle-looking sibling into a blocking condition worse than the
contention it prevents; (c) re-scanning during the run to shrink the pool — no mechanism to
resize a live pool, and the later launcher is the one that can still act.

## Risks / Trade-offs

- [Halving can slow a run that would have been fine] → It engages only when another live run
  holds the same repo, the warning names why, and the alternative is the observed 27m49s
  re-verification plus degraded evidence under load.
- [A dead-but-not-provably-stale record still throttles a launch] → The same conservative
  direction the per-spec hard stop already takes; `reconcile`/`sweep-orphans` close such
  records, and the warning names the run so an operator can see which one is holding width.
- [Detection races a run that launches a moment later] → The later launcher backs off, so any
  overlapping pair still ends with exactly one backed-off run; a truly simultaneous pair may
  briefly exceed the cap. Acceptable: this is back-pressure, not mutual exclusion.
- [The doctrine text and the code could drift again] → The skill reference is updated in the
  same change, so the documented procedure and the launch behavior agree.
