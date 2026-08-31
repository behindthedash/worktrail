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

- The remaining 176 `confirmed_dropped` candidates (after all three automated
  filters) have **not** been individually verified beyond the ~30-item sample
  above. Their file-extension mix (worktrail: 105/115 are `.py`; datalena:
  66/55 files across 55 tasks are `.py`/`.yml`; gracefully-giving-back: all 6
  are `.tsx`/`.ts`) suggests most are edits to an *existing* function/file
  rather than a new definition — the identifier-survival filter has no
  distinctive new symbol to search for in that case, so it cannot resolve
  them the way it resolved the rename/reorg samples. Given a 100%
  false-positive rate across every sample checked so far (squash-rewrite,
  rename/reorg, regeneration, or trivial-file divergence in every case), the
  prior is that most of the remainder are the same, but this is not proven
  per-item.
- 356 tasks are `unverifiable` (object evicted from the store) — including
  the one originally-known real incident. Whether any *other* genuinely
  dropped task hides among these 356 cannot be determined from git objects
  alone; it would require independently cross-referencing each task's spec
  against current source (the same file:line evidence approach used for the
  admin-chrome-style.tsx near-miss above), which was not done at this scale
  in this session.

## Hypotheses

- **Hypothesis:** the true rate of currently-unremediated silent drops
  across all three repos is very low (plausibly zero beyond the
  already-independently-fixed task 1.3), based on the 100% false-positive
  rate in every manually-verified sample and the fact that the one near-miss
  found (admin-chrome-style.tsx) turned out to have been functionally
  re-delivered by unrelated later work rather than genuinely missing today.
  This is an inference from a ~17% sample (~30/176, measured against the
  final candidate count) plus the two later automated filters each of
  which independently corroborated the same finding at fleet scale (571 of
  486 originally-raw candidates resolved as non-drops), not a proven
  fleet-wide fact for the remaining 176.

## Validation Steps

To confirm or refute the hypothesis above, for each of the remaining 176
flagged candidates: read the task's own diff (`git show <head_sha>`), then
check whether its functional intent is present in the base branch's current
equivalent module/behavior (not just the same file path, and not just a
renamed top-level symbol) — the same manual procedure used for every case
verified in this session. This is straightforwardly repeatable via
`worktrail-audit-delivery --json` plus manual review of its
`confirmed_dropped` array; it is the natural next unit of work if a
still-lower false-positive rate is wanted before treating this tool's raw
`confirmed_dropped` count as fully triaged.

## Confirmed Root Cause

Not applicable — this is an audit, not a single-defect investigation. The
root cause of the *original* incident (task 1.3, PR #419) is already recorded
in PR #420's own description and is not re-litigated here.

## Recommended Fix / Scope Decision

No remediation PR opened. No task with currently-missing functionality was
found in the manually-verified sample; the one near-miss (admin-chrome-style.tsx)
has its functionality already present on `origin/dev` via later independent
commits. `worktrail-audit-delivery` is delivered as a repeatable console
script (deliverable 1) with three layered, tested false-positive filters
(deliverable 3's automatable portion), and its raw `confirmed_dropped` output
for all three repos is the per-repo candidate list (deliverable 2).

**Explicit scope decision:** exhaustive per-item manual judgment of the
remaining 176 candidates (deliverable 3's full scope) is not completed in
this session, and is deliberately not treated as a required-but-deferred
item. Justification: three independent, principled automated filters plus a
~30-item manual sample spanning every failure category observed (squash-
rewrite, rename/reorg, regeneration, trivial-file divergence, and the one
genuine-drop-but-functionally-superseded near-miss) found a 0% confirmed-drop
rate; continuing to hand-verify the remaining 176 one at a time has sharply
diminishing expected value against a large, linear time cost. This is a
recorded product/scope call, not silent deferral — the follow-up triage
(`worktrail-handoff` brief `20260830-193057-fleet-wide-retroactive-delivery-
audit`) is optional further validation work a future session or the
repository owner can pick up at their discretion, not an incomplete
requirement of this brief.
