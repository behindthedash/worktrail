# Fleet-wide retroactive delivery audit

Source brief: `20260815-131018-fleet-wide-retroactive-delivery-audit`. Focus:
before PR #422 (`detect_unreconciled_evidence`, generalized from a tail-only
check to every `coordinator.DONE` task), a reviewed-PASSED, journal-done
task's commit could be silently excluded from its group's squash-merge PR
with zero orchestrator-side signal — confirmed live in run `full-1786812908`,
where task 1.3 was dropped from PR #419 and only caught by a human hand-run
of the downstream suite (brief `20260815-115257`, restored via PR #420). The
shipped detector requires the task's own worktree to still exist
(`integrate.py`: `if not wt.is_dir(): continue`) and worktrees are torn down
after merge, so it cannot be pointed at history. This audits history instead,
using each run journal's own recorded `head_sha` per task.

## Tool delivered

`worktrail-audit-delivery` (`src/worktrail/router/audit_delivery.py`,
`tests/router/test_audit_delivery.py`, 39 tests). For each repo:

1. Walk every `run-*.json` orchestrator journal under `<repo>-worktrees/`,
   **recursively** — a spec's own `new`/`modify` pipeline run journal lives
   one level down, under `<repo>-worktrees/<slug>-worktrees/run-<slug>.json`,
   not only directly under `<repo>-worktrees/`. A non-recursive scan misses
   most journals, including `run-auto-dod-verification.json` — the exact
   journal this brief itself cites.
2. Per task, take the **last `role: "review"` entry with `report.review_status
   == "PASSED"`** — the same reviewed-PASSED signal task 1.3 satisfied — and
   its `report.head_sha`.
3. `verify_delivery`: `unverifiable` if the commit object is gone from the
   object store (never reported as a drop — absence of proof is never proof
   of a drop); `delivered` if it's an ancestor of `origin/<base>`;
   otherwise a raw not-an-ancestor candidate.
4. Before calling a candidate `confirmed_dropped`, three automated
   false-positive filters run in order, all because every raw candidate
   manually checked during this audit turned out to be one of these, not a
   real drop:
   - `content_delivered_via_rewrite`: the task's content is provably on the
     base branch right now under a **different SHA** (a squash-merge, a
     rebase, a cherry-pick) — either the whole file is byte-identical, or
     (when a sibling task in the same squash group, or a later commit, also
     touched the file) at least 90% of the task's own added non-blank lines
     are still literally present in the file's current content on base.
   - `identifiers_survive_elsewhere`: the task's own file didn't match, but
     every distinctive function/class/const it *defined* is found somewhere
     else in the base tree right now — the module was renamed or reorganized
     during a later implementation pass, same functions, different path.
     Weaker evidence than a content match (a name match, not a content
     match), so it lands in its own `content_delivered_via_reorg` bucket, not
     merged into `content_delivered_via_rewrite`.
   - `shippable_files` / `never_shipped_by_policy`: a task whose only touched
     paths are ones this repo's own artifact policy never commits to the base
     branch in the first place (`**/reviews/*.md` review scratch, OpenSpec
     `.compile-ok` compile markers) — flagging these as dropped would be
     reporting policy-as-usual as a defect.

Run it: `worktrail-audit-delivery --repo worktrail --repo datalena --repo
gracefully-giving-back --repos-root ~/projects --json`.

## Verified Observations

- Ran against `worktrail` (base `origin/main`), `datalena` (base
  `origin/dev`), `gracefully-giving-back` (base `origin/dev`) on 2026-08-30.
  Raw scan: 69 + 75 + 12 = 156 journals, 623 + 504 + 47 = 1,174 reviewed-PASSED
  tasks checked. After all three automated filters: 115 + 55 + 6 = 176 tasks
  remained flagged `confirmed_dropped`; 161 + 263 + 13 = 437 were content-
  verified delivered under a rewritten SHA; 103 + 32 + 3 = 138 were
  identifier-verified delivered under a renamed/reorganized module;
  42 + 16 + 1 = 59 were policy-excluded scratch files; 190 + 142 + 24 = 356
  were unverifiable (commit object no longer in the store). The first
  (two-filter) pass had left 310 in `confirmed_dropped`; adding the
  identifier-survival filter moved another 134 of those into
  `content_delivered_via_reorg`, leaving 176.
- The originally-known incident (run `full-1786812908`, task 1.3,
  `run-auto-dod-verification.json`) is now correctly discovered by the
  recursive journal walk (it was invisible to a non-recursive scan). Its
  `head_sha` (`9b6cbf541fc3b026320f1b40b0047bc8ec3eb789`) is no longer present
  in either repo's object store (`git cat-file -e` fails) — reported
  `unverifiable`, not `confirmed_dropped`, consistent with this tool's
  designed false-negative bias. PR #420's own description independently
  confirms this specific task was restored; this tool cannot re-derive that
  fact from git objects alone once they've been GC'd.
- Manually verified ~30 of the (pre-identifier-filter) 310 raw
  `confirmed_dropped` candidates in detail (spot-checked across all three
  repos, not an exhaustive review) by reading the actual diff/blame history
  for each — 100% traced to a legitimate non-drop, zero real drops found.
  Several of those samples are exactly what the identifier-survival filter
  above now catches automatically (e.g. the `spec_sync_sweep_stale_
  bookkeeping_check.py` rename below); adding that filter is a direct product
  of this manual verification work, not a separate effort. Every one of the
  ~30 traced to one of:
  - A squash-merge combining multiple tasks' edits to the *same* file, where
    the combined file's content has since been edited further (e.g.
    `pyproject.toml` version bumps) — the task's own addition is present but
    below the 90% literal-line-match bar because of later, unrelated
    modifications interleaved in the diff.
  - A follow-up **rename/reorganization** of the exact module the task added
    (e.g. worktrail task `1.1`/`1.2` of
    `run-stale-bookkeeping-sweep-check.json` added
    `spec_sync_sweep_stale_bookkeeping_check.py`; the shipped, currently-live
    module is `spec_sync_sweep_check.py` — same functionality, different
    file name chosen during a later implementation pass).
  - A **regenerated/generated file** whose current content no longer matches
    any single historical commit's version (datalena `079-fe-be-contract-codegen`
    `TASK-002`, `app/src/lib/generated/api-types.ts` — codegen output,
    regenerated repeatedly since).
  - A **trivial low-content file** (e.g. an `__init__.py`) diverging for
    unrelated reasons while the substantive sibling file in the same task
    (`semver.py`) matched cleanly (datalena `063-capability-provider-registry`
    `TASK-063-02`).
- One genuinely notable near-miss, not a current gap: `gracefully-giving-back`
  run `full-1785578211` (`maintenance-gate-isr`), task `2.1`
  (`admin-chrome-style.tsx` + `layout.tsx`, commit `20aefa34`, "fix(isr):
  preserve admin auth chrome") is **not** an ancestor of `origin/dev`, and the
  squash commit for that run (`6fc09090`, "`base: 1.1`") lists only task 1.1 —
  the same drop shape as the original incident. `admin-chrome-style.tsx` does
  not exist under any name in `origin/dev`'s current tree, and
  `git log --all` shows exactly one commit ever touched that path (the
  dropped one itself). However, the underlying *functionality* — hiding
  `body > header`/`body > footer` chrome on admin ISR pages — is present and
  live today, inline in `origin/dev:src/app/admin/layout.tsx`, delivered by
  three **independent, later, unrelated-looking bugfix commits**
  (`133db629` "preserve public admin chrome", `057fa8c6` "Fix admin
  first-paint chrome handling", `04f942db` "Move admin chrome suppression
  into admin layout"). The task's specific commit and its specific
  architecture (a separate style component) were dropped and never restored
  as such; the problem it solved was independently rediscovered and re-fixed
  through a different implementation. No user-facing gap exists today. No
  remediation PR is needed for current behavior; the noteworthy part is that
  this drop happened and was never formally detected or closed — it was
  quietly papered over by unrelated engineering, exactly the class of risk
  this brief was worried about.

## Unknowns / Missing Evidence

- 512 tasks are `unverifiable` (object evicted from the store, per the
  2026-09-17 re-run below) — including the one originally-known real
  incident. Whether any *other* genuinely dropped task hides among these
  cannot be determined from git objects alone; it would require independently
  cross-referencing each task's spec against current source (the same
  file:line evidence approach used for the admin-chrome-style.tsx near-miss
  above), which remains out of scope for this audit (git-object-based, by
  design).

## Exhaustive re-verification (2026-09-17)

Follow-up to the explicit scope decision below: the deferred exhaustive
per-item review (`worktrail-handoff` brief
`20260830-193057-fleet-wide-retroactive-delivery-audit`) was picked up and
completed. Re-ran `worktrail-audit-delivery --repo worktrail --repo datalena
--repo gracefully-giving-back --repos-root ~/projects --json`: `confirmed_dropped`
had dropped from 176 (2026-08-30) to **146** (86 worktrail, 51 datalena, 9
gracefully-giving-back) as more commits landed on base in the interim.

Every one of the 146 remaining candidates was manually verified — reading
each commit's diff (`git show <head_sha>`) and checking whether its
functional intent is present in base today, per file, via content diff or
identifier/behavior search. **Result: 146/146 non-drops. Zero
`genuinely_missing`, zero `inconclusive`.** This extends the prior ~30-item
sample's 100% false-positive rate to full fleet-wide coverage of every
`confirmed_dropped` candidate whose object still exists.

Two false-positive categories emerged that the tool's existing three
automated filters (content-rewrite, identifier-survival, policy-exclusion)
do not model:

- **Deliberate deletion.** Several candidates were tasks whose own commit
  *removed* a file (e.g. an out-of-scope test flagged in review, or a
  superseded catalog module) — the file's absence on base today is the
  correct, intended end state, not a drop. The automated filters only check
  for *positive* content presence, so they can't recognize "this task's job
  was to delete something."
- **Systemic monorepo restructuring events.** In datalena, a same-day
  Alembic migration-baseline squash (PR #2902, merged 2026-09-17) collapsed
  every prior migration file into one baseline and moved most schema
  definition to `SQLModel.metadata.create_all()`, and an earlier
  `api/app/models.py` → `api/app/models/` package split relocated model
  classes to new files — both legitimately orphan old migration/model file
  paths at fleet scale while the underlying schema/behavior persists. In
  worktrail, one documented refactor (PR #739, "replace heuristic staleness
  guards with a source-read check") retired 4 files that had genuinely
  shipped, accounting for 19 of the 86 worktrail candidates in a single
  cluster; a separate PR (#781) documented that 4 `routing-target-selector`
  tasks were reviewed PASSED but left genuinely incomplete, then redone from
  scratch — the one cluster across all 146 where the original reviewed
  commit's content didn't hold up, though the eventual feature did ship via
  the follow-up fix, so it does not count as a currently-missing gap.

Full per-item verification tables (spec_id/task, head_sha, verdict, evidence)
are preserved as `worktrail-handoff` run-record decisions on run
`go-20260917-170159`; not duplicated here to avoid drift between two copies
of the same evidence.

## Hypotheses

- ~~**Hypothesis:** the true rate of currently-unremediated silent drops
  across all three repos is very low...~~ **Confirmed** by the 2026-09-17
  exhaustive re-verification above: 0 genuine drops across all 146
  `confirmed_dropped` candidates whose object still exists. The only
  remaining unknown is the `unverifiable` bucket (git objects evicted),
  which is out of this audit's reach by design (absence of proof is never
  presented as proof of a drop).

## Validation Steps

Superseded — see "Exhaustive re-verification (2026-09-17)" above. The
per-item procedure this section originally specified is what that pass ran
against all 146 remaining candidates.

## Confirmed Root Cause

Not applicable — this is an audit, not a single-defect investigation. The
root cause of the *original* incident (task 1.3, PR #419) is already recorded
in PR #420's own description and is not re-litigated here.

## Recommended Fix / Scope Decision

No remediation PR opened. Across the full audit (initial ~30-item sample plus
the 2026-09-17 exhaustive re-verification of all 146 remaining candidates),
zero tasks with currently-missing functionality were found; the one near-miss
(admin-chrome-style.tsx) has its functionality already present on
`origin/dev` via later independent commits. `worktrail-audit-delivery` is
delivered as a repeatable console script (deliverable 1) with three layered,
tested false-positive filters (deliverable 3's automatable portion), and its
raw `confirmed_dropped` output for all three repos is the per-repo candidate
list (deliverable 2).

**Scope decision closed:** the exhaustive per-item manual judgment deferred
by the original session (deliverable 3's full scope) is now complete — see
"Exhaustive re-verification (2026-09-17)" above. `worktrail-handoff` brief
`20260830-193057-fleet-wide-retroactive-delivery-audit` is closed
accordingly. A separate, narrower follow-up was captured for the two new
false-positive categories this pass surfaced (deliberate-deletion and
monorepo-restructuring-event detection) as a tool-enhancement item, distinct
in purpose from this audit's own completion.
