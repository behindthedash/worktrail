## Context

`verify.py`'s `Verifier` class gates every orchestrator-opened PR pre-merge:
it polls `gh pr view --json statusCheckRollup` and classifies the rollup with
`classify_checks()` before allowing `auto_merge()` to proceed. Three bugs in
this pre-merge path already shipped and were fixed (PR #165 `--repo` flag,
PR #164 Projects-classic GraphQL, PR #207 premature CI-green) — each found
only by a human noticing a bad merge after the fact, because the hermetic
test suite mocks `gh` output and cannot exercise real API edge cases.

A second, independent piece of infrastructure already does a similar
fleet-wide sweep: `reconcile_pr_labels.py` discovers every repo with
`docs/specs/go-policy.yaml` under a `--repos-root`, lists PRs per repo via
`gh pr list`, and self-heals label drift. That module is the direct
architectural precedent for this change — same repo-discovery function
(`discover_managed_repos`), same `gh` CLI dependency posture (fail-open, "no
guessing" on `gh` errors), same `--repo`/`--repos-root`/`--json` CLI shape.

Dashboard integration has its own precedent: `router/dashboard.py` already
folds a separate machine-local JSON cache (`~/.go/agent-capacity.json`, via
`agent_capacity.gate_snapshot()`) into its `capacity` output field, additive
to the dashboard's other fields. This proposal's `postmerge_check_failures`
field follows the same shape: a small standalone module owns its own cache
file and a pure "read cache, return summary" function; `dashboard.py` imports
and calls it, exactly like `capacity`.

## Goals / Non-Goals

**Goals:**
- Detect, fleet-wide, any merged PR whose required checks later reported a
  failure — the general shape of the PR #207 bug class, not just that one bug.
- Reuse `verify.py`'s existing `classify_checks()` unmodified, so the
  pre-merge gate and this post-merge audit can never silently diverge on what
  counts as a required-check failure.
- Keep sweep cost bounded regardless of how many PRs a repo merges, via a
  persisted per-repo incremental marker.
- Surface findings through the existing `/go` dashboard operators already
  check, not a new tool they have to remember to run.

**Non-Goals:**
- Automated remediation (revert, re-run, comment, label) of a flagged PR —
  detection only. A post-merge check failure needs human judgment (was it a
  flaky check, a real regression already fixed forward, or a genuine
  verify.py gap?); auto-acting on it risks exactly the kind of blind
  correction the reconciliation-loop guidance in this workspace's own
  `AGENTS.md` warns against for anything beyond additive label repair.
- Real-time/webhook-driven detection. A periodic sweep (cron or CI schedule)
  is sufficient — this closes a "nobody noticed" gap, not a latency gap.
- Auditing PRs merged before this change ships. The incremental marker starts
  from a bounded first-run lookback window (see Decisions), not full history.

## Decisions

**Reuse `classify_checks()` directly, not a re-derived copy.**
`src/worktrail/router/audit_postmerge.py` imports
`from ..orchestrator.verify import classify_checks` and calls it on the same
`statusCheckRollup` shape `verify.py` itself consumes. Alternative considered:
write a separate simpler classifier tailored to post-merge — rejected,
because the whole point of this audit is to never again let the audit and
the live gate disagree about GitHub API behavior.

**New standalone module + console script, not an extension of
`reconcile_pr_labels.py`.** The two sweeps share repo-discovery
(`discover_managed_repos`, imported not duplicated) but differ in what they
scan (open PRs missing labels vs. merged PRs with failing checks) and what
they do with findings (mutate labels vs. read-only report). Folding them into
one script would conflate two different failure classes and two different
response postures (repair vs. detect). Alternative considered: add a
`--postmerge` mode flag to `reconcile_pr_labels.py` — rejected as scope creep
on an already-focused module; a new `[project.scripts]` entry
(`worktrail-audit-postmerge`) keeps each script's `--help` honest about what
it does.

**Per-repo incremental marker stored as a small JSON file under
`~/.go/postmerge-audit-state/<repo-name>.json`** (`{"last_swept_at":
"<ISO8601>"}`), overridable via `--state-dir` / `GO_POSTMERGE_AUDIT_STATE`
env var — mirrors `agent_capacity.py`'s own cache-path convention
(`~/.go/agent-capacity.json`, `GO_AGENT_CAPACITY_CACHE`). Kept separate from
`~/.go/runs/<repo>/*.yaml` run records: run records are per-dispatch audit
trail (one file per GO invocation), not sweep cursor state, and mixing sweep
state into that directory would make `run_record.py prune`'s retention logic
have to special-case a file shape it doesn't otherwise touch. Alternative
considered: store the marker in the repo itself (a dotfile under
`docs/specs/`) — rejected, since sweep state is machine-local operational
telemetry, not a durable project artifact any consuming repo's own commit
history should carry (same reasoning `~/.go/runs` already uses for run
records, per this module's own docstring precedent).

**First-run marker defaults to a bounded lookback window (7 days), not full
history.** A repo swept for the first time queries `gh pr list --state
merged --search "merged:>=<now-7d>"` rather than every PR the repo has ever
merged — bounds worst-case first-sweep cost and avoids flooding the dashboard
with historical noise for bugs that (if real) are almost certainly already
long since noticed and fixed. `--lookback-days` is a CLI override for an
operator who explicitly wants a deeper first sweep.

**Per-repo PR cap per sweep (`--max-prs`, default 50).** Bounds worst-case
`gh` API call volume (one `gh pr view` call per candidate merged PR) so a
repo with an unusually high merge volume in one window can't make a single
sweep invocation run away. A repo that legitimately exceeds the cap in one
window is simply picked up more completely on the *next* sweep — the marker
still only advances past PRs actually checked, per the "sweep failure leaves
marker unchanged" requirement extended to "partial sweep advances the marker
only past the PRs it actually processed."

**Dashboard integration mirrors `capacity`, not a live query.**
`dashboard.py` calls a pure `audit_postmerge.dashboard_snapshot(state_dir)`
function that reads the flagged-PR portion of the state files and returns a
summary dict — it does not itself invoke `gh` or run a sweep. The dashboard
render path must stay fast and side-effect-free (it already runs on every
bare `/go`); triggering a live fleet-wide `gh` sweep from a dashboard render
would violate that. Sweeps run only via the standalone console script
(cron/CI-scheduled), same separation `agent_capacity.py`'s gate cache already
establishes between "write path" (a dispatch capacity-gate event) and "read
path" (dashboard render).

## Risks / Trade-offs

- [Risk] `gh` rate limits under fleet-wide sweeps with many repos × many
  merged PRs → [Mitigation] per-repo `--max-prs` cap; incremental marker
  means steady-state sweeps only touch PRs merged since the last run, not
  full history every time.
- [Risk] A flaky/re-run check reports failure post-merge for reasons
  unrelated to the PR #207 bug class (e.g. an infra blip on a re-run) →
  [Mitigation] explicitly a non-goal to auto-triage; the dashboard entry
  carries enough context (repo, PR, failing check names, merge time) for a
  human to distinguish flake from real gap. Future refinement (e.g.
  suppressing known-flaky check names) is out of scope for this change.
- [Risk] Marker file corruption or deletion causes a full-history rescan →
  [Mitigation] same bounded first-run lookback window applies on marker-miss
  as on first-ever sweep — a missing/corrupt marker degrades to "treat as
  first run," never to unbounded history.
- [Risk] New module drifts from `classify_checks()` if `verify.py`'s
  classifier signature changes later → [Mitigation] direct import (not a
  vendored copy) means a signature change is a hard import error caught by
  the existing test suite, not a silent behavioral drift.

## Migration Plan

No migration — this is a purely additive new capability. Deployment is:
1. Ship `audit_postmerge.py` + console script + dashboard field (this
   change's `tasks.md`).
2. Each consuming repo opts into scheduling the sweep (cron or a CI
   workflow calling `worktrail-audit-postmerge --repo "$PWD"`) at its own
   pace — no repo is forced onto a schedule by this change landing.
3. Rollback is deleting the scheduled trigger; the dashboard field degrades
   to always-empty with no sweep state present, which is already a defined,
   tested state (see spec's "Dashboard rendered with no flagged PRs"
   scenario).

## Open Questions

- Should the sweep also be wired into this repo's own CI (a
  `postmerge-reconciliation-audit.yml` workflow, mirroring
  `rulesets_drift_guard.yml`'s scheduled pattern) as a reference deployment,
  or left entirely to `tasks.md`/operator discretion? Leaning toward: yes,
  add a scheduled workflow for the worktrail repo itself as a working
  reference other consuming repos can copy — captured as a task, not
  resolved here.
