## Why

The work queue has become a second backlog that competes with, and starves, the spec backlog: as of 2026-08-26 there are 100 queued briefs (58 of them brief-to-brief "consolidated" batches merging unrelated purposes, 36 with `repo: null`) while datalena alone carries 49 active OpenSpec changes, the top 15 with zero tasks done. Unattended drains only ever claim work-queue briefs (`drain.py`, `seed_backlog.py` docstrings), so handoff briefs get worked directly and specs never get reached. The queue needs to become an **intake** channel whose items are consumed into specs, and execution needs to be initiated only from specs.

## What Changes

- Introduce a provenance-derived **brief kind**: a brief carrying `seeded-from:` is an *execution* brief; every other queued brief (handoff captures, consolidated batches) is an *intake* brief.
- Unattended auto-pick (`worktrail-go auto`, hence the nightly drain) SHALL never claim an intake brief; only execution briefs are dispatched for implementation.
- `worktrail-go <intake-brief-id>` triages that brief (fold/propose) instead of implementing it.
- Extend the `queue-triage` verdict set with `fold-into-change`, `propose-change`, `work-directly`, and `needs-decision`, each with evidence and an explicit target. `apply --confirm` executes fold/propose as a docs-only branch + PR in the target repo, then closes the brief with `triaged-to:` and the PR URL (fail-closed: no PR, no closure).
- Rank a brief's candidate targets brief→**active change** (not brief→brief) using the existing OpenSpec root scan and focus-overlap coefficient, so triage merges into existing changes before proposing new ones.
- Add a per-repo WIP cap (`max_active_changes`, default off) that downgrades `propose-change` to `keep` + triage note when a repo is over cap, so intake pressure pushes toward draining specs instead of creating change #50.
- `work-directly` is the bounded escape hatch for a verified small defect: it converts the intake brief into an execution brief (`seeded-from: triage:<date>:direct`) rather than bypassing the gate.
- `needs-decision` files a pending-decision envelope in the human decision queue (for `repo: null` and ambiguous briefs) and leaves the brief queued.
- `worktrail-drain` gains `--intake-triage` and `--seed-backlog` pre-passes that run before the first drain iteration and are reported in the drain summary, closing the loop intake → spec → seeded execution brief → drain.

## Capabilities

### New Capabilities
- `intake-triage`: brief-kind derivation, intake exclusion from unattended dispatch, brief→change candidate ranking, fold/propose/work-directly/needs-decision apply semantics, per-repo WIP cap, and the drain pre-passes.

### Modified Capabilities
- `queue-triage`: the verdict set grows from `{keep, stale-close, needs-update, duplicate-of}` to also include `fold-into-change`, `propose-change`, `work-directly`, `needs-decision`; the `apply` step gains the corresponding `--confirm`-gated actions and dangling-target handling for them.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (verdict types, evaluator prompt, candidate ranking, apply actions)
- `src/worktrail/router/dashboard.py::auto_pick_brief` (new `intake-untriaged` skip reason) and the `worktrail-go <brief-id>` claim path in the router
- `src/worktrail/drain/drain.py` (pre-pass flags + summary fields), `src/worktrail/workqueue/seed_backlog.py` (invoked as a pre-pass)
- `src/worktrail/router/policy.py` (`max_active_changes` key, default 0)
- `src/worktrail/workqueue/work_queue.py` (`triaged-to:` frontmatter on `done`)
- Reuses `spec_overlap` scan for OpenSpec roots and `decisions.pending_decision_envelope`.
- Operator follow-up outside this repo: the nightly cron script in `devops` (`~/bin/worktrail-drain-nightly.sh`) must pass the new flags; datalena must choose a `max_active_changes` value consciously (49 active changes today). Captured as handoffs, not done here.
- No change to brief file format for existing briefs: kind is derived, no migration of the 100 queued briefs.
