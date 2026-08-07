## Context

`dashboard.py` already has an established cross-repo detector pattern for
"signal that requires a human/agent glance, not a hard gate": `policy_selfcheck.py`,
`automerge_selfcheck.py`, and `policy_drift_selfcheck.py` each expose a
`check_repo(repo) -> {"repo", "path", "findings": [...]}`, a `sweep(repos_root)`,
and a `main()` CLI with `--repo`/`--repos-root`/`--json` and an exit-0-clean /
exit-1-flagged convention. `scan_repos()` calls each `check_repo()` per candidate
repo and stores the result as a `*_findings` list on that repo's row;
`render_dashboard()` turns a non-empty findings list into one capped, "→ review"
summary line (see `policy_flags`/`automerge_flags`/`drift_flags` in
`render_dashboard()`).

Separately, `dashboard.py` already reads per-spec run journals
(`<repo>-worktrees/run-<spec_id>.json`) in `_journal_verify_pending()` for a
different purpose: detecting groups whose PR hasn't landed after
`integrate_complete`. That function treats every non-`MERGED` state the same way
(folds `QUARANTINED` into a generic `verify-pending` stage with a generic "resume
full-real" next action) and only looks at the *current* spec being stage-detected,
never sweeps every journal file in a repo's worktrees directory.

Journal group records (`journal["groups"][name]`) carry only
`{"pr_url", "head_branch", "state"}` — no per-group timestamp. Per-task entries
in `journal["entries"]` carry `started_at`/`ended_at` (epoch seconds) but are
keyed by `task`, not `group` — a task's group membership is a coordinator-time
concept (`plan_groups()`'s dependency-connected components) that is not persisted
back onto the task or the journal, so reconstructing exact "which task blocked
this group" attribution from a journal alone would require re-running
`plan_groups()` against the live task DAG. That is out of scope here (see
Non-Goals) — this change surfaces that a group is quarantined and how stale that
quarantine is, not a full task-level failure narrative.

## Goals / Non-Goals

**Goals:**
- Make every `QUARANTINED` group entry across a repo's run journals visible on
  the `/go` dashboard without anyone manually grepping `run-*.json` files.
- Surface, per quarantined group: which spec it belongs to, the group name, how
  long it has sat quarantined, and whether it ever reached the PR stage (a group
  quarantined before a PR was opened vs. one quarantined after — e.g. a failed
  smoke test before push vs. a red/blocked PR — are different recovery paths).
- Follow the exact existing `automerge_selfcheck.py`/`policy_drift_selfcheck.py`
  detector shape so the new module composes with `scan_repos()` and
  `render_dashboard()` with no new rendering primitives.

**Non-Goals:**
- Reconstructing per-task failure detail ("blocking task X failed twice, sibling
  task Y already review-passed") from journal `entries[]`. That needs a
  task→group mapping journals don't persist; scoped out to keep this change a
  single, reliably-derivable signal. A future change can add task-level detail
  once group membership is persisted (see Open Questions).
- Any auto-remediation of a quarantined group (retry, auto-close, auto-notify).
  This is detection/visibility only, matching the passive-detector posture of
  every sibling `*_selfcheck.py` module.
- Changing `_journal_verify_pending()`'s existing `verify-pending` stage
  behavior for the *currently-detected* spec — that behavior is unchanged;
  this change adds a separate, repo-wide sweep alongside it.

## Decisions

- **New module, not an extension of `_journal_verify_pending()`.**
  `_journal_verify_pending()` is scoped to one spec dir at stage-detection time
  and answers a different question (is there PR-landing work left to resume).
  A repo-wide sweep across every `run-*.json` in `<repo>-worktrees/` is a
  different shape of check — exactly what the existing `*_selfcheck.py` modules
  already do for other repo-wide signals. Reusing that shape keeps
  `scan_repos()`/`render_dashboard()` wiring identical to three existing call
  sites instead of inventing a fourth pattern.
- **Age = journal file mtime, not a persisted per-group timestamp.**
  Group records have no timestamp field. Adding one would require changing
  `_write_group_journal()`'s schema (and everything that reads
  `journal["groups"][name]`) for every existing and archived journal. mtime is
  already the freshness signal `_journal_verify_pending()`'s sibling module
  `check_repo_freshness.py` uses elsewhere in this file, and integrate.py only
  rewrites a journal when some group's own state changes — so mtime already
  approximates "time since last group-state transition" without a schema
  change. Trade-off: mtime reflects the *last* write to the whole journal file,
  not necessarily the specific group's own last transition, if multiple groups
  in the same run change state at different times before finishing. Accepted:
  bounded imprecision on a visibility signal, not a gate.
- **PR-state summary is `pr_url` presence, not a live GitHub lookup.**
  `check_repo()` (like every sibling selfcheck) does local file inspection only,
  no network calls — matching `check_repo_freshness.py`'s explicit "No git, no
  network" default posture (opt-in only via dashboard.py's `--check-freshness`
  flag for its own git-based check). A quarantined group whose `pr_url` is
  non-empty already had a PR opened before quarantine (e.g. smoke-cmd or CI
  failure after push); an empty `pr_url` means it never reached that stage
  (e.g. integration-branch merge conflict). This is derivable from the journal
  alone and needs no `gh` call.

## Risks / Trade-offs

- [Risk] mtime-derived age can overstate or understate a specific group's own
  quarantine age when a journal holds multiple groups with different transition
  times. → Mitigation: documented in the module docstring and this design; the
  signal is advisory (matches every sibling `*_selfcheck.py` posture), not a
  hard number relied on for automated action.
- [Risk] A quarantined group whose spec has since been fully abandoned (spec
  folder deleted, journal orphaned) would still surface as a finding forever.
  → Mitigation: out of scope for this change (matches today's behavior of
  `_journal_verify_pending()`, which has the same characteristic for a single
  spec); a future cleanup pass can add an orphan check the same way
  `gitnexus`'s own orphan sweep works, if this proves to be a real problem in
  practice.
- [Risk] Scanning every `run-*.json` under `<repo>-worktrees/` on every
  dashboard render adds I/O. → Mitigation: these are small JSON files already
  read one-at-a-time elsewhere in this module; a glob + per-file read stays
  well within the existing `scan_repos()` per-repo cost (which already reads
  three other sibling-module signals per repo).

## Migration Plan

No data migration — this is a pure read-side addition. Rollout is a normal PR:
new module + wiring + tests, merged behind this repo's existing CI gate
(`PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m
worktrail.orchestrator.orchestrate check`). No flag needed; the new finding
list is simply empty (and renders nothing) for any repo with no quarantined
groups, so existing dashboard output is unchanged until a real quarantine
exists.

## Open Questions

- Should group→task attribution (the "blocking task X failed twice" detail from
  the discovery brief) be added later by persisting `plan_groups()`'s group
  membership onto the journal at write time, rather than reconstructed after
  the fact? Deferred — worth revisiting only if the count+age+PR-state signal
  proves insufficient in practice for triage.
