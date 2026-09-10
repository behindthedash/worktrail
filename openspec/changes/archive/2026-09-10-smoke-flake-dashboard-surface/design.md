## Context

See proposal.md — Why. The relevant current state:

- `integrate._record_smoke_flake(journal_path, name, detail)` writes
  `journal["smoke_flakes"][<group name>] = <first-attempt detail>` — a flat `str -> str` map,
  one entry per group, last-write-wins within a run. The journal lives at
  `<repo>-worktrees/run-<spec_id>.json` (`live.journal_path_for`).
- `dashboard.py` already hosts a family of passive per-repo detectors called the same way:
  `policy_selfcheck`, `automerge_selfcheck`, `policy_drift_selfcheck`, `quarantine_selfcheck`,
  and `journal_selfcheck` each expose `check_repo(repo) -> {"findings": [...]}` and are invoked
  once per repo in the collection loop, their results stashed on `repo_info[repo_key]`.
- `render_dashboard()` takes cross-repo aggregates as flat keyword arguments
  (`capacity`, `postmerge_check_failures`, `clusters`) and renders each as one
  `head` / `… +N` / `→ <next action>` line.
- 112 journals currently sit in `worktrail-worktrees/`. Worktree teardown removes the
  worktree directory but not the sibling `run-*.json`, so the journal set only grows.

## Goals / Non-Goals

**Goals:**
- One deterministic, testable aggregation function that the dashboard, the CLI, and the tests
  all read through — no second way to compute the same number.
- Zero risk to dashboard availability: a detector failure degrades to "no section", never to
  a broken dashboard.
- Match the existing detector family's module shape, CLI convention, and render shape closely
  enough that the next reader does not have to learn a new pattern.

**Non-Goals:**
- Per-attempt or historical flake *rate* (flakes ÷ total runs). The journal records only the
  flake, not the denominator; computing a rate would require a new write-side contract, which
  this change explicitly excludes.
- Auto-quarantining, auto-filing a handoff, or otherwise acting on a flaky suite. Detection
  and display only; the operator decides.
- Journal retention/GC. The recency window makes retention a non-issue for this feature
  without taking on deleting anyone's journals.

## Decisions

**A new module rather than extending `journal_selfcheck.py`.**
`journal_selfcheck` is explicitly an *invariant-violation* detector — its docstring frames
every finding as a state-machine bug that caused an incident — and it deliberately skips any
journal whose RunLock is held, because "stranded" means nobody is driving the run. A smoke
flake is neither an invariant violation nor liveness-dependent, and a flake recorded by a
live run is still a flake. Folding it in would require special-casing the liveness skip and
muddying that module's stated posture. A sibling module (`smoke_flake_selfcheck.py`) keeps
both postures clean, and matches the precedent that each detector concern gets its own module
(`quarantine_selfcheck`, `dashboard_selfcheck`, `automerge_selfcheck`).
*Alternative considered:* a new finding `kind` inside `journal_selfcheck.check_repo()` — one
fewer file, but it inherits the live-run skip and the "invariant violation" framing, and its
findings would render in the "🚩 Stranded runs" line, which is the wrong next action.

**Aggregate by suite, count by run, order by count.**
The actionable unit is the suite, not the run: "`base` flaked in 3 runs" tells the operator
what to fix, while "run X had a flake" does not. Counting distinct runs (rather than journal
entries) is well-defined because the journal's map holds at most one entry per suite per run.
Descending count puts the worst offender first without needing a severity concept.

**Recency window on journal mtime, defaulting to 30 days, caller-overridable.**
Without a bound the count is monotonic forever: a suite fixed in March still reads as
"flaked in 9 runs" in September, and the signal decays into noise across an ever-growing
journal set. mtime is the only timestamp available without adding a write-side field, and it
is accurate enough for a display heuristic — a journal's last write is the run that produced
its flakes. 30 days is long enough to span several runs of the same suite and short enough
that a genuine fix clears the line within a month.
*Alternatives considered:* (a) cap at the N most recent journals — simpler, but N runs is not
a fixed span of time, so the window silently narrows as run frequency rises; (b) add an
explicit timestamp to the `smoke_flakes` entry — more precise, but that is a write-contract
change this change is scoped to avoid.

**Report single flakes, classified, rather than muting them.**
The documented signal is recurrence (2+ runs), so `recurring` vs `single` is a first-class
field on every entry. But muting singles outright would hide the first sighting of a suite
that is about to become recurring, and the dashboard's whole problem today is invisibility.
Reporting both with the classification lets the rendered line lead with recurring suites while
still leaving the single ones discoverable, and lets the JSON consumer filter as it likes.

**One cross-repo aggregate parameter on `render_dashboard()`, entries tagged with their repo.**
This mirrors `postmerge_check_failures` (a single cross-repo dict) rather than the per-repo-row
pattern used for worktrees. The rendered line is one line for the whole dashboard in both
single-repo and multi-repo mode, and tagging each entry with its repo name keeps multi-repo
output unambiguous without a second rendering path.

**Wrap the detector call in the dashboard's existing never-break try/except.**
`_capacity_gate_snapshot` is already wrapped in a bare `except Exception` with the comment
"telemetry must never break the dashboard". The smoke-flake snapshot takes the same treatment:
on any exception the aggregate is empty and the section is simply not rendered. Combined with
the detector's own per-journal skip-on-error, a corrupt journal set degrades to silence rather
than to a traceback in the operator's orientation view.

## Risks / Trade-offs

- **mtime is a proxy, not a real event time** (a `touch`, a copy, or a filesystem restore can
  move a journal into or out of the window) → Accept. The consequence is a display line being
  slightly wrong for one journal, never incorrect behavior; the window is overridable when
  precision matters, and the entries name their runs so the operator can check.
- **A flaky suite that flakes under many different spec ids inflates nothing, but a suite
  renamed between runs splits into two entries** → Accept. Group names are stable in practice,
  and splitting is visibly wrong to a reader in a way that silently merging two different
  suites would not be.
- **Scanning 112+ journals on every dashboard render adds I/O** → Each journal is a small JSON
  file already being globbed by `journal_selfcheck` on the same pass, and the window check is
  an `stat()` that short-circuits before any read. If this ever becomes measurable, the mtime
  filter is the natural place to tighten.
- **The line could become noise if many suites flake once each** → The count cap (`head` plus
  `… +N`) bounds the line length the same way every sibling section does, and recurring
  entries sort first, so the visible portion stays the actionable portion.
