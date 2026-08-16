# One-off `/go` path vs orchestrator group-PR path — gate parity audit

Follow-up to brief `20260815-134144` (fixed in PR #424/#439: the four deterministic drift
checks now run on every orchestrator group PR via `pre_pr_gate.py --checks-only`). This audit
enumerates every gate, drift check, policy verdict, and approval requirement reachable on the
one-off `/go` dispatch path and establishes, for each, whether it also reaches
`integrate.py`'s orchestrator group-PR path — by source read, not by re-running a specific
incident.

**Scope of "one-off path":** `worktrail-preflight run` (`pre_pr_gate.py main()`, called from
`worktrail-sdd-workflow` Phase 8 before `gh pr create`) plus `run_record.py finish`'s
code-enforced corrections plus the `gh pr create` PreToolUse hook.

**Scope of "orchestrator path":** `integrate.py`'s per-group `_run_drift_gate()`
(`pre_pr_gate.py --checks-only`) and `_refresh_pr_labels()` (`pre_pr_gate.py --labels-only`),
plus `verify.py`'s post-PR merge sequence, plus the scheduled `reconcile_pr_labels.py` sweep.

## Verified Observations

### Parity matrix

| Gate / check | One-off enforced | Orchestrator enforced | Verdict |
|---|---|---|---|
| `spec_sync_drift` | Yes — `run_drift_checks()`, `pre_pr_gate.py:445` | Yes — same `run_drift_checks()` function, called via `--checks-only` (`pre_pr_gate.py:433-434`) from `integrate.py:_run_drift_gate` (`integrate.py:672-706`, called at `integrate.py:1082`, before push) | **Parity** — identical function, single code path |
| `check_clarification_integrity` | Yes — `run_drift_checks()` | Yes — same, via `--checks-only` | **Parity** |
| `check_dod_failures` | Yes — `run_drift_checks()` | Yes — same, via `--checks-only` | **Parity** |
| `check_req_coverage_failures` | Yes — `run_drift_checks()` | Yes — same, via `--checks-only` | **Parity** |
| `automerge_eligible()` — `require_human_routes` | Yes — `resolve_pr_labels()` with `--route` | Yes — `_refresh_pr_labels()` threads `--route` (`integrate.py:130-131`; fixed by brief `20260731-145729`, PR #90) | **Parity** |
| `automerge_eligible()` — `protected_paths` | Yes — `resolve_pr_labels()` | Yes — `resolve_pr_labels()` computes `changed_paths()` internally regardless of route/gates, so this reaches the orchestrator path unconditionally (confirmed by brief `20260731-145729`'s own analysis, and by direct read of `pre_pr_gate.py:269-302`) | **Parity** |
| `automerge_eligible()` — classifier `gates` (`require_human_approval`, `never_automerge`, `pause_before_merge`) | Yes — `resolve_pr_labels()` with `--gates` | Yes — `_refresh_pr_labels()` threads `--gates` (`integrate.py:122-129`) | **Parity** |
| `required_checks_gate()` (no live required check on base branch) | Yes — inside `resolve_pr_labels()` (`pre_pr_gate.py:301`) | Yes — same function, same call path as the row above | **Parity** |
| `go:risk-*`/`go:no-automerge` label correction (`ensure_pr_risk_label`) | Yes — code-enforced inside `run_record.py finish()` whenever the record carries a `pull_request`, plus `poll_run.py`/`drain.py` for headless spawns | Partial, by a different mechanism — `_refresh_pr_labels()` computes fresh labels at PR-creation time (parity above), and `reconcile_pr_labels.py`'s scheduled sweep (its own docstring: "5th recurrence of the same failure class #74/#80/#82/#128/#137") self-heals drifted labels across **all** open PRs including multi-PR orchestrated runs, keyed by matching the PR URL to its run record's recorded risk/gates/route — `run_record.py`'s own `pull_request` field is normalized to a list form for exactly this reason (`reconcile_pr_labels.py:70-93`, `test_load_run_index_handles_list_form_pull_request`) | **Parity** — different mechanism (synchronous at `finish` vs. scheduled sweep), same eventual coverage; no gap found |
| Review-thread gate (`check_review_threads`) | Yes — code-enforced inside `run_record.py finish()` (`_enforce_review_thread_gate`, blocks on `blocking: true`) | Yes — `orchestrator/verify.py`'s post-PR sequence (`ensure_mergeable → wait_and_fix_ci → resolve_review_threads → ...`, `verify.py:900-950,1399`), same `check_review_threads.check()` call, explicitly citing the same PR #2133 incident `run_record.py`'s docstring cites | **Parity** |
| `no_implementation_without_approval` (Route A) | Yes — `run_record.py finish()` refuses an implementation-completion state for `selected_route == "A"` without a recorded decision | N/A by construction — Route A never reaches `integrate.py` (no implementation, no group PR); the check is keyed on `$RUN.selected_route`, which is set once at dispatch and shared with any downstream orchestrator invocation the same run makes | **Parity** (not applicable, not a gap) |
| Full `pre_pr_cmd` test command (e.g. `pytest -q`) | Yes — runs the resolved `pre_pr_cmd` (falls back to `integrate_smoke_cmd`) | No — the orchestrator runs `integrate_smoke_cmd` instead (a separate, usually cheaper, policy-documented key: `docs/specs/go-policy.yaml`'s own schema distinguishes the two), via `_run_integration_smoke()` | **Parity via a different backstop, not a gap** — `Lint, Test & Build` (which runs the full `pytest -q`) is a `required_status_checks` entry in `.github/rulesets/protect-main.json` for every PR into `main`, one-off or orchestrator-created; a group PR cannot merge without it passing in CI even though the local drift gate does not run it |
| `is_docs_only()` skip | Yes — skips `pre_pr_cmd` entirely when every changed path matches `docs_only_paths` | No skip — `_run_integration_smoke()` always runs `integrate_smoke_cmd` for every group | **Not a gap** — the orchestrator does strictly more work than the one-off path here (never less), which is the safe direction for a skip mechanism |
| `scope_review_failures()` (run-record scope-completeness review) | Yes — `pre_pr_gate.py main()`, gated on `--run`, called from `worktrail-sdd-workflow` Phase 8 before `gh pr create` | **No** — never reached. `_run_drift_gate()` calls `--checks-only`, which returns from `run_drift_checks()` before `scope_review_failures()` is ever reached (`pre_pr_gate.py:433-434`, `436`); `_refresh_pr_labels()` calls `--labels-only`, an even earlier return (`422-431`). Neither orchestrator call site passes `--run` at all. | **Gap — see below** |

### The gap: `scope_review_failures()` has no orchestrator-path equivalent

`scope_review_failures()` answers "did the run record's scope review confirm every requested
outcome was delivered (not blocked, not silently dropped)?" — a `$RUN`-level question, not a
per-diff one. The one-off path enforces it once, synchronously, immediately before the single
`gh pr create` call (`worktrail-sdd-workflow` SKILL.md Phase 8: "Before `gh pr create`
... run the gate ... `--run "$RUN"`").

For orchestrated Route C/D work, that framing does not transfer cleanly: `integrate.py` creates
**multiple** group PRs autonomously, each at its own point in the fan-out, with no single
"before `gh pr create`" moment for the enclosing `$RUN`. Neither `_run_drift_gate()` nor
`_refresh_pr_labels()` passes `--run`, so `scope_review_failures()` is never evaluated for
orchestrated work at all — not once per run, not once per group. `worktrail-sdd-workflow`
SKILL.md's own Phase 8 scope-completeness instructions are written as if a single `gh pr
create` call is imminent, which is not true once the orchestrator has already produced its
own PRs.

This is a genuine, unrecorded skip (no comment, no policy entry, no design note "this
deliberately does not apply to orchestrated runs") — distinguishing it from the `pre_pr_cmd`
vs `integrate_smoke_cmd` and `is_docs_only` rows above, both of which are explicit,
policy-documented, or structurally safe. Filed as a separate brief per this audit's own
deliverable requirement: `20260815-172721-worktrail-orchestrator-group-pr-path` (see Deferred
Work).

### Correction to this brief's own premise

The brief that requested this audit suggested modelling the durable regression check on "the
existing `*_selfcheck.py` family (`policy_selfcheck`, `policy_drift_selfcheck`,
`journal_selfcheck`, `automerge_selfcheck`)," describing them as establishing "the convention
of a cheap deterministic check wired into `pre_pr_gate.py`." Direct read of all five
`*_selfcheck.py` modules (`policy_selfcheck.py`, `policy_drift_selfcheck.py`,
`journal_selfcheck.py`, `automerge_selfcheck.py`, `quarantine_selfcheck.py`,
`dashboard_selfcheck.py`) shows this premise does not hold: every one of them states in its own
module docstring that it is **"a passive detector, not a gate... it never blocks
`pre_pr_gate.py` or the merge gate."** They are wired into `dashboard.py`'s rendering (one-line
review nudges), not into `pre_pr_gate.py` at all. `grep`-confirmed: no `*_selfcheck` import
appears anywhere in `pre_pr_gate.py` or `integrate.py`.

The actually-correct precedent for "a check that mechanically fails when a new gate lacks an
explicit decision" is `tests/router/test_gate_enforcement_coverage.py` — an AST-based registry
test (not a `*_selfcheck.py`-style passive detector) that already closes an adjacent but
distinct gap: whether a `classify.py`-emitted gate *string* has any real enforcement consumer
at all, anywhere. It does not ask whether that consumer's enforcement also reaches the
orchestrator path — that is this audit's deliverable #3, described below.

## Unknowns / Missing Evidence

- Whether any consuming repo (datalena, gracefully-giving-back, etc.) has its own repo-specific
  gates in `docs/specs/go-policy.yaml` that could exhibit the same one-off/orchestrator
  asymmetry shape — out of scope; this audit is Worktrail-repo and mechanism-focused, matching
  the scope of the three motivating instances and of `classify-gate-enforcement-audit.md`.
- Whether a future consuming repo could add a *new* `pre_pr_cmd`-equivalent key beyond
  `pre_pr_cmd`/`integrate_smoke_cmd` that would need its own parity row — not evaluated; the
  policy schema (`policy.py`'s `DEFAULT_POLICY`) is the source of truth for what keys exist
  today and was read for this audit as of `main`@`fec54cc`.

## Hypotheses

None outstanding for the gates enumerated above — each row's verdict is backed by a direct
function/call-site read, not inference. The `scope_review_failures` gap is not a hypothesis; it
is confirmed by the absence of `--run` in both `integrate.py` call sites to `pre_pr_gate.py`.

## Validation Steps

Reproduce any parity-matrix row:

```bash
# Confirm --checks-only and the one-off drift path share one function:
rg -n "def run_drift_checks|run_drift_checks\(" src/worktrail/router/pre_pr_gate.py

# Confirm --route/--gates threading into the orchestrator label refresh:
sed -n '95,141p' src/worktrail/orchestrator/integrate.py

# Confirm scope_review_failures is unreached by either orchestrator call site:
grep -n '"\-\-run"\|"\-\-checks-only"\|"\-\-labels-only"' src/worktrail/orchestrator/integrate.py

# Confirm Lint, Test & Build is a required check for every PR into main:
python3 -c "import json; d=json.load(open('.github/rulesets/protect-main.json')); \
  print([r for r in d['rules'] if r.get('type')=='required_status_checks'])"
```

## Confirmed Root Cause

Not applicable in the usual sense — this is an audit, not a single-defect investigation. The
one confirmed gap (`scope_review_failures` never reached by orchestrated Route C/D work) has a
clear mechanical root cause: `worktrail-sdd-workflow` SKILL.md's Phase 8 scope-completeness
instructions assume a single, imminent `gh pr create` call, an assumption that does not hold
once the orchestrator has already autonomously created its own group PR(s) earlier in the same
route's execution.

## Recommended Fix

See the filed handoff brief (Deferred Work) for the scope-review gap; not fixed inline here
per PR-scope discipline (a different-purpose fix belongs in its own PR, and per the brief's
own dependency note, the durable selfcheck below is the deliverable that matters most).

## Deferred Work

- Handoff brief filed for the `scope_review_failures` orchestrator-path gap — see work queue
  brief `20260815-172721-worktrail-orchestrator-group-pr-path`.

## The durable part: gate-parity regression check

`tests/router/test_pre_pr_gate_parity.py` (added by this PR) closes the pattern this audit
exists to close: it AST-extracts every direct function call inside `pre_pr_gate.py`'s `main()`
that is not itself `run_drift_checks()`/`resolve_pr_labels()` (the two functions already proven
shared with the orchestrator path via `--checks-only`/`--labels-only`), and requires each to
carry an explicit `GATE_PARITY` registry entry classifying it as `"shared"` (proven reachable
from the orchestrator path by a callable, matching `test_gate_enforcement_coverage.py`'s
proof-function convention) or `"exempt"` with a written reason. A future call added directly to
`main()` with no registry entry fails this test immediately — the same AST-registry shape
`test_gate_enforcement_coverage.py` already established for gate-string enforcement, applied
here to gate-*reachability* parity instead. Because it is a `pytest` test (not a
`pre_pr_gate.py`-internal check), it runs on every PR via the `Lint, Test & Build` required
check — the same universal backstop already proven (parity-matrix row above) to cover the full
`pytest -q` suite for orchestrator-created PRs, so this is the correct placement rather than
forcing a new synchronous call into `pre_pr_gate.py` itself.
