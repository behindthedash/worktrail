## Context

See `proposal.md` - Why. Two building blocks already exist and are being composed, not
extended:

- `dashboard.py`'s `_pending_impl_stale()` / `_pending_openspec_stale()`, reachable through the
  public `dashboard.scan(specs_root)` entry point, which `seed_backlog.py` already calls the
  same way (`dashboard.scan(repo_path / "docs" / "specs")`). `scan()` internally infers
  `repo_root` from `specs_root` and, when it resolves, also walks `repo_root / "openspec" /
  "changes"` via `_safe_detect_openspec` — so one `scan()` call already covers both the devkit
  (`docs/specs/`) and OpenSpec (`openspec/changes/`) formats for a repo, with no per-format
  branching needed at the call site.
- `spec_sync_sweep.py`'s existing two-check composition: `discover_repos_with_specs()` (gated
  on a repo having `docs/specs/`), per-repo try/except isolation
  (`spec_sync_sweep_checkbox_check.py`'s shape), `spec_sync_sweep_dedup.find_unresolved_drift_brief(repo,
  queue_base, drift_source=...)`, and a brief-filing module that writes one Markdown file into
  `queue_base/queue/` and validates it with `brief_frontmatter.validate_brief()`.

## Goals / Non-Goals

**Goals:**
- Reuse `dashboard.scan()` as-is; the new check module contains no git subprocess calls and no
  stale/freshness comparison logic of its own.
- Match the checkbox-drift-sweep addition's shape closely enough that a reader who understands
  that addition understands this one without new concepts (per-repo check module → per-repo
  brief module → three-way independent wiring in `run_sweep()`).
- Preserve the sweep's existing independence and error-isolation guarantees (REQ-CHG-005 for
  the original two checks) across all three checks.

**Non-Goals:**
- Auto-flipping a stale task's status to `completed`, or opening a closing PR. That remains the
  interactive `close-stale` dispatch action's job (`worktrail-go`'s Phase 2 dispatch); this
  sweep only files a brief, exactly like the existing spec-sync-drift and checkbox-drift checks
  file briefs rather than fixing anything themselves.
- Broadening repo discovery beyond `discover_repos_with_specs()`'s existing `docs/specs/`-gated
  set. A repo with only an `openspec/` tree and no `docs/specs/` directory is out of scope for
  this change — see Open Questions.
- Any change to `_pending_impl_stale`, `_pending_openspec_stale`, `dashboard.scan()`, or the
  RunPlan cache they read.

## Decisions

**Reuse `dashboard.scan()`, not `_pending_impl_stale`/`_pending_openspec_stale` directly.**
Calling `scan()` gets the check module the same enumeration (spec dirs under `docs/specs/`,
change dirs under `openspec/changes/`), the same per-format dispatch, and the same
`stage: "stale-bookkeeping"` / `stale_task_ids` output shape the interactive dashboard already
relies on — with zero new code duplicating that enumeration. The alternative (calling
`_pending_impl_stale`/`_pending_openspec_stale` per spec/change directly) would require the
check module to re-implement spec-dir and change-dir discovery itself, duplicating logic
`scan()` already owns, for no behavioral gain. `scan()` is also already an established reuse
point for exactly this kind of cross-cutting consumer (`seed_backlog.py`'s
`find_epic_gaps`-adjacent code calls it the same way).

**Findings carry the `stage`/`next_action`/`stale_task_ids` fields `scan()` already computes,
not a re-derived file list.** For a devkit spec row, `scan()` doesn't expose the underlying
`files:` per stale task id (only the aggregate `tasks` counts dict); for an OpenSpec change row,
`scan()`'s `tasks` field is the raw loaded task list, whose `files` key is only populated when
the task declares it inline (`openspec-task-file-declaration`) — a task relying on the
RunPlan-cache path (`_pending_openspec_stale`'s own internal merge) has no file list surfaced
back into that row. Re-deriving the merged list ourselves would mean duplicating
`_pending_openspec_stale`'s `runplan.apply_to_tasks` call, which the proposal explicitly rules
out. The check module therefore reports, per stale task: `spec_id`/`change_id`, `format`
(`"devkit"` or `"openspec"`), `task_id`, `next_action` (the already-computed evidence string,
e.g. "files already merged on base branch"), and `files` when the task's own loaded record
happens to carry a non-empty `files` list (best-effort, not guaranteed). This keeps the finding
shape honest about what data is actually available without new git calls.

**Discovery stays `discover_repos_with_specs()` (docs/specs-gated), same as the other two
checks.** `run_sweep()` already calls this once per run and shares the resulting repo list
across all three checks; introducing a second, broader discovery function (e.g. one that also
matches `openspec/`-only repos with no `docs/specs/`) would make the three checks scan different
repo sets within the same run, breaking the "same discovered repos, three independent checks"
model the proposal and spec both describe. Any repo in this workspace that uses OpenSpec has
so far also kept (or started from) a `docs/specs/` tree, so this is not expected to leave real
repos uncovered; if it does, broadening discovery is a separate, easy follow-up change.

**Error isolation mirrors `check_repo_checkbox_drift`'s shape exactly:** the entire per-repo
`scan()` call and finding-extraction runs inside one `try/except Exception`, returning
`{"repo": str(repo), "findings": [...], "error": None}` on success or `{"repo": ..., "findings":
[], "error": str(exc)}` on failure. This is on top of `scan()`'s own internal per-spec/per-change
isolation (`_safe_detect_stage`, `_safe_detect_openspec` already never raise) — belt-and-suspenders,
matching the existing checks' isolation philosophy rather than trusting only one layer.

**Brief content mirrors `spec_sync_sweep_checkbox_brief.py`'s template shape** (YAML frontmatter
with `drift-source: stale-bookkeeping-sweep`, a `## Focus` body section, one line per finding),
substituting checkbox-specific fields (`path`/`unchecked_count`/`total_count`/`sections`) for
stale-bookkeeping fields (`format`/`spec_id or change_id`/`task_id`/`next_action`/`files`).
Written via the same `queue_base / "queue"` path and validated with the same
`brief_frontmatter.validate_brief(path, required=("id", "status", "focus"))` call before
returning, so it participates in the existing claim/dedup machinery with no changes there.

## Risks / Trade-offs

- **[Risk]** `dashboard.scan()` computes far more than stale-bookkeeping alone (delta drift,
  journal-verify-pending, sync-pending, feature summaries) for every spec/change in a repo, so
  this check pays a heavier per-repo cost than a narrowly-scoped stale-only query would.
  → **Mitigation**: acceptable for a weekly sweep (same cost profile the interactive `/go` scan
  already pays per repo); if sweep runtime becomes a problem, a future change can add a
  `probe_stale`-only, lighter entry point to `dashboard.py` without touching this check's own
  interface — deferred rather than pre-optimized here.
- **[Risk]** A repo with only `openspec/` and no `docs/specs/` never enters
  `discover_repos_with_specs()`'s result set, so this check silently never runs for it (same gap
  the other two checks already have today).
  → **Mitigation**: matches existing sweep behavior exactly (not a regression this change
  introduces); flagged as an Open Question below rather than silently fixed as a "while you're
  here" discovery-widening change.
- **[Risk]** Reporting `next_action`/`stale_task_ids` instead of raw file evidence makes the
  brief's OpenSpec-format findings slightly less self-contained (a reader may need to open the
  change's `tasks.md` to see which files were checked) when the task didn't declare `files:`
  inline.
  → **Mitigation**: the brief still names the exact task id and change id, which is enough to
  navigate to the authoritative source (`tasks.md` or the RunPlan cache) rather than trusting a
  second, possibly-stale copy of file evidence baked into the brief itself.

## Open Questions

- Should `discover_repos_with_specs()` be broadened (here or in a follow-up) to also match
  `openspec/`-only repos with no `docs/specs/` directory? Deferred: no repo in this workspace is
  currently known to be OpenSpec-only, and widening discovery is a small, independent change
  that can be made later without touching this check's own interface or spec.
