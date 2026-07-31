# Investigation: are classify.py's bounded-autonomy gates mechanically enforced?

**Triggered by:** work-queue brief `20260731-104555`, a follow-up to
`20260731-104502` (which found `pause_before_merge` is prose-only: PR #74 was
classified `risk:high`, emitted `gates:["pause_before_merge"]`, but no
`go:no-automerge` label was stamped, and the repo's own `CI: Auto-merge on
open` workflow merged it in ~70s with zero human review).

**Question:** do `require_human_approval`, `never_automerge` (protected
operations), and `no_implementation_without_approval` have the same gap, or
are they actually enforced?

## Verified Observations

`classify.py` (`src/worktrail/router/classify.py:377-389`) emits gates purely
as a function of `risk`/`protected`/`route`:

```python
gates: List[str] = []
if protected:
    gates.append("require_human_approval")
    gates.append("never_automerge")
elif risk == "critical":
    gates.append("require_human_approval")
elif risk == "high":
    gates.append("pause_before_merge")
if route == "J":
    gates.append("routing_cassette_required")
if route == "A":
    gates.append("no_implementation_without_approval")
```

### `no_implementation_without_approval` (Route A) — no consumer anywhere

The only other appearances of this string in the repo are:
`src/worktrail/router/cassettes/routing_cassette.json:7` (a test asserting
the classifier *emits* the string for one Route-A scenario) and
`skills/worktrail-go/references/routes.md:36` (prose: "No implementation
without an explicit decision"). No script, `run_record.py` field, dispatch
check, or CI step reads this gate string. Nothing stops an agent from
classifying a request as Route A and proceeding straight to implementation
anyway — this gate is **100% prose, zero code enforcement**, strictly worse
than `pause_before_merge` (which at least has a deterministic-but-unapplied
half-measure, below).

### `require_human_approval` / `never_automerge` — real computation, unenforced application

These two **do** have a genuine deterministic consumer:
`policy.automerge_eligible()` (`src/worktrail/router/policy.py:624-643`)
returns `(False, "protected operation or human-approval gate")` whenever
either string is in `gates`, independent of the `automerge.max_risk` policy
check. This is called by `pre_pr_gate.py --risk --gates`
(`src/worktrail/router/pre_pr_gate.py:261,343`), which prints an
`AUTOMERGE LABELS:` line (`go:risk-<level>`, plus `go:no-automerge` when
ineligible) for the calling agent to pass to `gh pr create --label`.

Worktrail's own `.github/workflows/auto-merge.yml` **is** correctly wired: it
reads the PR's live labels, and `scripts/ci/automerge_eligibility.sh` returns
`eligible=false` when `go:no-automerge` is present — verified directly
against `scripts/ci/test_automerge_eligibility.sh`'s two cases.

The gap is in **application**, not computation:

- `pre_pr_gate.py` only *prints* the labels — module docstring, line 60-62:
  "...so the repo's own auto-merge automation can act on that policy verdict
  as PR metadata **instead of trusting the calling agent to have run Phase 8
  by hand**." But applying the label is still exactly that: the calling
  agent must itself run `gh pr create --label <label>`. Nothing forces this.
- `scripts/ci/automerge_eligibility.sh` is **fail-open** on a missing label:
  `has_no_automerge_label=false` → `eligible=true`, unconditionally. There is
  no way to distinguish "genuinely eligible, no label needed" from "the
  labeling step was simply never run" — this is the exact mechanism that let
  PR #74 through.
- The **one** place labeling is code-enforced (no agent action required) is
  the parallel orchestrator's own group-PR path: `integrate.py`'s
  `_refresh_pr_labels()` calls `pre_pr_gate.py --labels-only` itself right
  before `gh pr create` for every orchestrator-created group PR
  (`src/worktrail/orchestrator/integrate.py:79-122,884`). **Route J (the
  route PR #74 took) does not go through this path** — it is a one-off
  single-task PR, created via the manual Phase 8 prose in
  `skills/worktrail-sdd-workflow/SKILL.md:171-180`, same as routes F/G/H/I
  and any non-grouped Route D/C work.
- `automerge_selfcheck.py` is a separate passive detector for whether *other*
  repos' `auto-merge.yml` wires the label check at all (it caught
  `behindthedash/devops#58` merging despite carrying `go:no-automerge`,
  2026-07-30). Its own docstring: "This is a passive detector, not a gate...
  it never blocks `pre_pr_gate.py` or the merge gate." It is not invoked by
  CI here or in any consuming repo — a human/agent has to run it by hand.

### Aside: dead documentation reference

`docs/specs/research/go-policy-integrity-audit.md` is referenced six times
across the codebase (`policy.py` comments/warnings, `live.py`, a test
docstring in `test_live_extras.py`) as the source of a "2026-07
key-vs-consumer audit" of `protected_paths`/`require_human_routes`/
`automerge.enabled`. `git log --all -- "*go-policy-integrity-audit*"` returns
zero commits — the file was never committed. Not in scope to fix here, but
worth flagging: it's a broken pointer surfaced to repo authors in a live
warning message (`policy.py:614,618`).

## Unknowns / Missing Evidence

- Whether any *consuming* repo (datalena, gracefully-giving-back, etc.) has
  ever hit this exact gap on a `require_human_approval`/`never_automerge`
  classification — not checked; out of scope (this audit is Worktrail-repo
  and mechanism-focused, per the brief).
- Whether `automerge_preflight.required_checks_gate()` (referenced in
  `pre_pr_gate.py`'s docstring as a second `go:no-automerge` trigger, for
  "base branch has no live GitHub-side required check") has the same
  agent-must-apply-the-label gap — almost certainly yes, by the same code
  path, but not independently traced line-by-line.

## Hypotheses

None remaining — root cause is confirmed by direct code reading (not
inference): see below.

## Confirmed Root Cause

`require_human_approval` and `never_automerge` share the **identical**
structural gap as `pause_before_merge`: the risk/gate → label decision is
computed correctly and deterministically in code
(`policy.automerge_eligible()`), and worktrail's own CI correctly consumes
the label if present — but *stamping* the label onto a specific PR is an
agent-executed, unenforced step for every PR-producing route except the
parallel orchestrator's own group-PR integration path. Any one-off/manual
route (which is exactly what Route J is) can silently skip Phase 8's
labeling instructions with no error, no test failure, and no CI block — and
the receiving check (`automerge_eligibility.sh`) fails *open* on a missing
label, so a skipped label is indistinguishable from a genuinely-eligible PR.
`no_implementation_without_approval` has no code consumer at all, making it
worse than the other three (fully prose, no deterministic half-measure).

This confirms the brief's hypothesis: a protected/destructive-classified
change (`never_automerge`) or a critical-risk change (`require_human_approval`)
taking a one-off PR route can auto-merge unreviewed today, for exactly the
same reason PR #74 did.

## Recommended Next Route

**Route J (fix), as a separate follow-up** — not folded into this audit.
The fix requires a design decision between at least two shapes (mirroring
the two options already proposed in the still-queued sibling brief
`20260731-104502` for `pause_before_merge`, which apply identically to all
label-driven gates):

1. Make `pre_pr_gate.py --run` itself call `gh pr create --label`/`gh pr edit
   --add-label` when a PR number is available, removing the agent-executed
   step entirely (matches the orchestrator's own `integrate.py` pattern).
2. Make `scripts/ci/automerge_eligibility.sh` fail **closed** instead of open
   — require an explicit `go:risk-*` label to be present at all before
   allowing auto-merge, rather than treating "no label" as "eligible."

Both are non-trivial (option 2 is a behavior change for every consuming repo
running this workflow; option 1 needs a PR-number handoff into `pre_pr_gate.py`
that doesn't exist today) and deserve their own scoped PR and testing, not a
bundled fix inside this audit. `no_implementation_without_approval` likely
needs a third, unrelated mechanism (a `run_record.py` field blocking a
`finish` on an implementation-completion state when the originating route was
A and no recorded decision exists) — different code path, different fix.

Completion: `investigation_complete`.
