## Context

`src/worktrail/drain/drain.py` currently hardcodes two structurally identical
sweep functions:

- `resume_quarantined_budget_exhausted` — finds specs with a `QUARANTINED` /
  `budget_exhausted` group via `find_resumable_quarantines` (backed by
  `quarantine_selfcheck.check_repo`), then spawns `worktrail-live full-real`
  per finding.
- `resume_verify_pending` — finds specs in the `verify-pending` stage via
  `find_verify_pending_specs` (backed by `dashboard.scan`), then spawns the
  same `worktrail-live full-real` command per finding.

Both are called twice inside `drain()`: once before the queue-drain loop
starts, once after it ends (only when the loop actually ran an iteration).
Both results feed fixed keys (`resumed_quarantines`, `resumed_verify_pending`)
in the summary dict `drain()` returns.

`dashboard.py`'s `detect_stage()` already classifies a third stalled-but-safe
stage, `stale-bookkeeping` (impl or tail tasks whose `files:` are all
git-tracked on the base branch — the code shipped, but `status:` was never
flipped to `completed`). Its remediation is different in kind from the other
two: no `full-real` re-run, just a `status:` flip + a docs-only PR — this is
already the exact human procedure `worktrail-go`'s interactive `close-stale`
dispatch action documents by hand.

## Goals / Non-Goals

**Goals**
- Make adding a new safe, unattended remediation category a one-line table
  entry, not a new function + two call sites + a new summary key.
- Add the `stale-bookkeeping` remediation as the first beneficiary of the new
  table (it is the concrete example named in the motivating request).
- Preserve every existing public function signature, return shape, and
  summary-dict key `resume_quarantined_budget_exhausted`,
  `resume_verify_pending`, `resumed_quarantines`, `resumed_verify_pending`
  currently have — both have direct test coverage and (for the two
  functions) may be imported by callers outside `drain.py`.

**Non-Goals**
- Do not add `orchestrator-stuck` (`fanout_failed`) to the table. `routes.md`
  §E and `detect_stage()` both document it as unsafe to silently re-launch —
  it stays human-recovery-only.
- Do not add every other `detect_stage()` stage (`ready-to-implement`,
  `needs-tasks`, `needs-clarification`, `unspecd`, `empty`, `sync-pending`,
  non-stale `tail-pending`) to the table. Those are normal open work already
  visible through `worktrail-go auto`'s dashboard-driven claim flow, not
  silent stalls — adding them here would be scope creep beyond the request's
  named "known safe automated remediation" categories.
- Do not change `worktrail-go`'s interactive `close-stale` dispatch action.
  It remains the human-driven equivalent for a single-repo session; this
  change only automates the unattended, `--repos-root`-wide sweep path.

## Decisions

### D1 — `StageRemediation` table shape

```python
@dataclass(frozen=True)
class StageRemediation:
    key: str  # summary-dict key, e.g. "stale_bookkeeping"
    label: str  # log-line prefix, e.g. "close-stale-bookkeeping"
    finder: Callable[[Path, Optional[str]], List[Dict[str, Any]]]
    action: Callable[[Dict[str, Any], str, int, SpawnerT, LogT], Dict[str, Any]]
    # action(finding, agent, timeout, spawner, log) -> one result dict.
    # Raises on failure; the sweep engine catches per-finding so one bad
    # finding never blocks the rest (matches the existing docstring
    # guarantee on both current functions).


REMEDIATION_TABLE: List[StageRemediation] = [
    StageRemediation(
        "quarantined_budget_exhausted",
        "resume-quarantine",
        find_resumable_quarantines,
        _resume_via_full_real,
    ),
    StageRemediation(
        "verify_pending",
        "resume-verify-pending",
        find_verify_pending_specs,
        _resume_via_full_real,
    ),
    StageRemediation(
        "stale_bookkeeping",
        "close-stale-bookkeeping",
        find_stale_bookkeeping_specs,
        close_stale_bookkeeping,
    ),
]
```

`_resume_via_full_real(finding, agent, timeout, spawner, log, *, label)` is
the shared body both `full-real`-resume actions already share verbatim except
for the log-line label; factor it out once, bind `label` per table row via
`functools.partial`.

### D2 — Generic sweep engine

```python
def sweep_remediations(
    repos_root, go_repo, agent, timeout, spawner, log
) -> Dict[str, List[Dict[str, Any]]]:
    results: Dict[str, List[Dict[str, Any]]] = {}
    for remediation in REMEDIATION_TABLE:
        applied = []
        for finding in remediation.finder(repos_root, go_repo):
            try:
                applied.append(
                    remediation.action(finding, agent, timeout, spawner, log)
                )
            except Exception as exc:  # noqa: BLE001 — one finding must not
                # block the rest of the sweep
                log(
                    f"{remediation.label} error: "
                    f"{finding.get('repo_name')} {finding.get('spec_id')}: {exc}"
                )
        results[remediation.key] = applied
    return results
```

`drain()` calls `sweep_remediations` at both existing call sites (pre-loop,
post-loop-when-iteration-ran) instead of the two separate function calls, and
merges the two passes' dicts key-by-key (same merge semantics the current
`resumed_quarantines +=` / `resumed_verify_pending +=` already have).

### D3 — Backward-compatible public functions

```python
def resume_quarantined_budget_exhausted(
    repos_root, go_repo, agent, timeout, spawner, log
) -> List[Dict[str, Any]]:
    """Unchanged public signature/behavior — now a thin call into the shared
    sweep engine. See REMEDIATION_TABLE."""
    return sweep_remediations(repos_root, go_repo, agent, timeout, spawner, log).get(
        "quarantined_budget_exhausted", []
    )


def resume_verify_pending(
    repos_root, go_repo, agent, timeout, spawner, log
) -> List[Dict[str, Any]]:
    """Unchanged public signature/behavior — now a thin call into the shared
    sweep engine. See REMEDIATION_TABLE."""
    return sweep_remediations(repos_root, go_repo, agent, timeout, spawner, log).get(
        "verify_pending", []
    )
```

**Caution for the implementing task:** a naive version of D3 above calls
`sweep_remediations` (which sweeps *all three* stages) just to extract one
key, silently re-running the *other* two stages' finders and actions as a
side effect every time either single-purpose function is called — a real
behavior change existing callers (including `drain()` itself, if D2 is
wired incorrectly) must not hit. Resolve this by giving `sweep_remediations`
an optional `keys: Optional[Iterable[str]] = None` filter parameter (default:
every table row) that restricts which `REMEDIATION_TABLE` rows run; D3's two
wrappers each pass their own single key. `drain()` itself calls
`sweep_remediations` with no filter (all three).

### D4 — `stale-bookkeeping` finder

```python
def find_stale_bookkeeping_specs(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair currently in the `stale-bookkeeping` stage,
    across every repo under `repos_root` (or just `go_repo`). Carries the
    stale task ids `detect_stage()` already computed so the action does not
    re-derive the git-tracked-file check."""
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in rows:
            if row.get("stage") != "stale-bookkeeping":
                continue
            spec_id = row.get("id")
            stale_ids = row.get("stale_task_ids") or []
            if not spec_id or not stale_ids:
                continue
            found.append(
                {
                    "repo": repo_path,
                    "repo_name": name,
                    "spec_id": spec_id,
                    "stale_task_ids": stale_ids,
                }
            )
    return found
```

This requires `detect_stage()`'s `stale-bookkeeping` branch (dashboard.py,
the `elif stale_ids:` arm around line 883) to add `stale_task_ids=stale_ids`
to the `info.update(...)` call it already makes — additive, does not change
`next_action`'s existing formatted string or any other field. `dashboard.scan`
already returns `detect_stage()`'s full info dict per row (verify by reading
`scan()`'s implementation before assuming), so no signature change is needed
there.

**Known pre-existing gap this change does not need to fix:** `dashboard.scan`
is only called against `docs/specs` — an OpenSpec-format repo's
`stale-bookkeeping` specs are invisible to this finder the same way
`find_verify_pending_specs` already misses them (both pre-date this change).
Out of scope here; flag as a candidate follow-up, do not silently expand this
change's scope to fix it.

### D5 — `stale-bookkeeping` action: status flip + docs-only PR

```python
def close_stale_bookkeeping(finding, agent, timeout, spawner, log) -> Dict[str, Any]:
```

Mirrors the human procedure `worktrail-go`'s `close-stale` dispatch action
already documents (routes.md's dispatch table): flip each stale task's
`status:` to `completed` via the existing
`taskformats/devkit/schema.set_status_completed(task_file)` (already used by
`taskformats/devkit/source.py` for the identical transition), then commit and
open a docs-only PR — **do not** invoke the orchestrator.

Git mechanics: this is not a spec-owned change (no `openspec/changes/<id>/`
or `docs/specs/<id>/changes/<slug>/` artifact is being authored), so it does
not fit the `new`/`modify` pipelines' spec-worktree setup. Use the same
direct-branch pattern Route F already uses for unspecced-code fixes
(`subagent-prompts.md#fix-branch-worktree-setup` /
`#fix-branch-worktree-teardown`): a short-lived worktree off the target
repo's base branch, commit the flipped `TASK-*.md` file(s), push, `gh pr
create` with the `go:risk-low` label (a status-only flip of already-shipped
work is inherently low risk — no code changes), then tear the worktree down
once the PR is open (do not wait for merge; this mirrors how the two
`full-real`-resume actions do not block on their spawned run finishing
either — they spawn and record the outcome, not much different in spirit).
Do not run `pre_pr_gate.py`'s full test-suite gate for this docs-only PR —
`integrate_smoke_cmd`/`pre_pr_cmd` may not even apply to a pure YAML-frontmatter
change in an arbitrary consumer repo; a docs-only PR is exempt the same way
Route C's own spec-only PR is (see `routes.md` §C's push step).

Return shape mirrors the two full-real actions' result dicts for
`sweep_remediations`/`resumed_stale_bookkeeping` consistency:

```python
{"repo": finding["repo_name"], "spec_id": finding["spec_id"],
 "task_ids": finding["stale_task_ids"], "pr_url": <url or None>}
```

If `gh pr create` fails, the action raises (caught by the sweep engine's
per-finding `try/except`, per D2) rather than silently reporting success with
no PR.

### D6 — `drain()` wiring and summary dict

```python
resumed: Dict[str, List[Dict[str, Any]]] = {}
...
if config.repos_root is not None and not config.dry_run:
    resumed = sweep_remediations(
        config.repos_root,
        config.go_repo,
        active_agent,
        config.iteration_timeout,
        spawner,
        log,
    )
...
if config.repos_root is not None and not config.dry_run and state.iteration > 0:
    post = sweep_remediations(
        config.repos_root,
        config.go_repo,
        active_agent,
        config.iteration_timeout,
        spawner,
        log,
    )
    for k, v in post.items():
        resumed.setdefault(k, []).extend(v)
...
summary["resumed_quarantines"] = resumed.get("quarantined_budget_exhausted", [])
summary["resumed_verify_pending"] = resumed.get("verify_pending", [])
summary["resumed_stale_bookkeeping"] = resumed.get("stale_bookkeeping", [])
```

Existing summary-dict consumers (tests, any external reader) keep working
unmodified; the new key is strictly additive.

## Risks / Trade-offs

- **Risk:** `close_stale_bookkeeping` opens a PR without waiting for CI/merge,
  unlike the `full-real`-resume actions which spawn a full pipeline run.
  Mitigation: this is a pure status-flip on already-shipped code — the actual
  risk surface is "did we correctly verify the files are shipped," which
  `_pending_impl_stale`/`_pending_tail_stale` already gate before the stage
  is even reported as `stale-bookkeeping`; this change trusts that existing
  gate rather than re-deriving it.
- **Trade-off:** D3's per-key filter on `sweep_remediations` adds a small
  parameter surface instead of leaving the two legacy functions as fully
  independent code paths. Chosen because duplicating the loop body again to
  avoid the parameter would recreate exactly the "near-identical function"
  problem this change exists to close.

## Migration Plan

No data migration. Existing callers of `resume_quarantined_budget_exhausted`
and `resume_verify_pending` are source-compatible (same signature, same
return shape). `drain()`'s summary dict is additive-only. No behavior change
for the two existing remediation categories beyond the shared log-label
factoring (log assertions in existing tests check label substrings, not full
line text — verified against `tests/drain/test_drain.py` before this design
was written).

## Open Questions

None — the two ambiguous points (naive-D3 double-sweep risk, and whether to
also fix `find_verify_pending_specs`'s OpenSpec-format blind spot) are
resolved above (D3's filter parameter; D4 explicitly defers the OpenSpec gap
as out of scope).
