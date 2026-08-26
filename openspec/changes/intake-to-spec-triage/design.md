## Context

See proposal.md — Why. Relevant current state, verified 2026-08-26:

- `queue_triage.py` already runs an evaluate/apply loop with `--confirm`-gated verdicts (`keep`, `stale-close`, `needs-update`, `duplicate-of`), repo-grouped evaluator agents, and a `## Triage <date>` dedup window. It clusters nothing itself; `consolidate_cluster.py` clusters brief→brief and emits multi-purpose batch briefs.
- `seed_backlog.py` already converts backlog into execution briefs (`seeded-from:` key, idempotent per key) including Route D `ready-to-implement` seeding behind the repo policy key `allow_seeded_implementation` (default false). `dashboard.py` computes `ready-to-implement` for OpenSpec changes as well as devkit specs.
- `dashboard.py::auto_pick_brief` is the single unattended selection point, with an ordered gate list and a miss log; `drain.py` shells to `worktrail-go auto` per iteration and already runs pre/post sweeps (quarantine resume).
- `router/overlap_check.py`'s `scan()` already extracts `{spec_id, stage, feature_summary}` from an OpenSpec root; `duplicate_brief_detection` already defines the token-overlap coefficient (`OVERLAP_THRESHOLD` 0.45).
- `decisions.pending_decision_envelope()` is the human-decision-queue filing API.
- Neither the seeder nor triage is on cron; the nightly script (`devops` repo) runs `worktrail-drain` only.
- Workspace rules: never commit to a base branch; PRs only. Docs-only PRs are auto-merge eligible under the fleet's docs-only risk cap.

## Goals / Non-Goals

**Goals:**
- One backlog (OpenSpec changes); the queue is intake plus mechanically seeded execution.
- Every new behavior sits behind an existing seam (auto-pick gate, triage verdict, drain sweep, policy key) so the change is additive and reversible per flag.
- Zero migration of existing briefs.

**Non-Goals:**
- Retiring `consolidate_cluster.py` or the dashboard's brief-cluster advisory (they remain advisory; not the fold-target selector).
- Triaging the devkit `docs/specs/NNN-*` format as a fold target (OpenSpec changes only; devkit repos get `propose-change` disabled and fall back to existing verdicts).
- Changing what `worktrail-go auto` does once it has claimed an execution brief.
- Editing the `devops` nightly cron script or any repo's policy values — captured as handoffs.

## Decisions

**D1. Execution reaches specs through seeded briefs, not by drain claiming specs directly.**
The drain keeps claiming queue briefs; the seeder (already built) turns `ready-to-implement` changes into execution briefs. Alternative: teach `worktrail-go auto` to claim a spec directly. Rejected: the queue's POSIX-rename claim is the only atomic single-owner arbiter across agents and machines; specs have no equivalent, and run records, `seeded-from` idempotency, and cross-machine claim guards all hang off brief ids. Reuse wins.

**D2. Brief kind is derived from `seeded-from:`, not a new `kind:` field.**
100 queued briefs need no rewrite; every brief the seeder has ever written is already correctly classified; a hand-captured brief can never accidentally declare itself execution. Alternative: explicit `kind:` frontmatter with a backfill script (pattern exists: `backfill_*`). Rejected as a second source of truth for a fact provenance already encodes.

**D3. Fold targets are ranked brief→active-change with deterministic pre-ranking and an LLM verdict.**
Same shape as the existing triage (deterministic grouping, evidence-required agent verdict, apply fails open to `keep`). The candidate set is bounded (top-K=5) and the evaluator may only fold into a presented candidate; anything else downgrades to `keep`. Alternative: pure token-overlap auto-fold above a threshold. Rejected: a wrong fold silently pollutes a change's scope; the existing `queue-triage` spec already chose evidence + `--confirm` for the same reason.

**D4. Fold/propose land via branch + PR, and the brief closes only after the PR exists.**
Honors the no-base-branch-commits rule and gives a review surface for the LLM's spec edits. Docs-only, so eligible for existing auto-merge. Fail-closed ordering: worktree → edit → `openspec validate` → commit → push → open PR via `gh` → `done(triaged-to=..., note=PR url)`. Any earlier failure leaves the brief untouched and reports the branch for manual recovery. Alternative: write straight to the base checkout. Rejected by doctrine.

**D5. Fold content shape is append-only.**
`proposal.md` gets `## Folded from <brief-id>` (focus text + capture date); `tasks.md` gets a new numbered group with the brief's actionable lines as unchecked tasks. Append-only keeps the diff reviewable and never disturbs checked tasks or the change's existing requirements. Requirement/spec deltas are *not* synthesized on fold — that stays a human/`opsx:update` step when the folded tasks turn out to need spec changes.

**D6. `work-directly` is a verdict that promotes the brief, not a bypass of the gate.**
Stamping `seeded-from: triage:<date>:direct` reuses the exact mechanism the seeder uses, so auto-pick, idempotency, and reporting all work unchanged. Evidence must cite a reproduction (test/check/command); the evaluator prompt says so and `apply` enforces it by regex on the evidence field, downgrading otherwise.

**D7. WIP cap defaults off and only throttles `propose-change`.**
`max_active_changes: 0` means no behavior change for any repo until an operator sets it. Over cap, intake is held (`keep` + note listing fold candidates) rather than filed as a decision — filing 30 decisions would move the pile again. Folding is unaffected because folding reduces sprawl.

**D8. `needs-decision` uses the existing decision queue; brief stays queued.**
Reuses `pending_decision_envelope` and the existing "awaiting decision" skip in auto-pick and triage. Decision answer (a repo) is written back to the brief's `repo:` by the existing decision-apply path, which re-arms triage.

**D9. Interactive `worktrail-go <intake-id>` runs single-brief triage.**
Consistent with "never worked": a human naming an intake brief gets the fold/propose choice, with the same apply code path. Execution briefs behave exactly as today.

**D10. Drain pre-passes are flags on `worktrail-drain`, not a new cron entry.**
Matches the existing sweep pattern in `drain.py`, keeps one nightly process and one lock, and the summary/digest already have a home for per-stage blocks. The flags default off so the change is deployable before the `devops` script is updated.

## Risks / Trade-offs

- [Evaluator over-folds loosely related briefs into a big change] → top-K candidates only, evidence required, PR review surface, and the dashboard's existing spec-overlap advisory still runs.
- [Fold PR closed unmerged leaves the brief `done` with a dangling `triaged-to:`] → closure note carries the PR URL; add a `triaged-to` check to the existing stale-bookkeeping detection (`openspec-stale-bookkeeping-detection`) as a follow-up if it materializes; not in this change.
- [Proposed changes are LLM-authored specs of variable quality] → `openspec validate` gate, PR review, and the WIP cap keep volume bounded; a bad proposal is a docs-only PR to close.
- [Pre-pass agent cost per night] → repo-grouped evaluators (existing), 25-day `## Triage` dedup window (existing), and the pre-pass runs inside the drain's existing budget accounting.
- [Repos with devkit-format specs have no fold/propose target] → evaluator receives an empty candidate list and `propose-change` is disabled for that repo; existing verdicts still apply.
- [`work-directly` regex is a weak evidence check] → it is a floor, not the gate: the brief still goes through auto-pick's other gates and a Route F run with its own verification.

## Migration Plan

1. Ship this change; all flags/keys default off — behavior identical until enabled.
2. Enable on the nightly drain by adding `--intake-triage --seed-backlog` in the `devops` nightly script (handoff).
3. Per repo, set `allow_seeded_implementation: true` where the drain should implement ready changes, and choose `max_active_changes` (handoff; datalena needs a conscious number given 49 active changes).
4. Rollback: remove the flags from the nightly script; already-folded PRs remain reviewable docs PRs.
