## Context

`src/worktrail/drain/drain.py`'s `REMEDIATION_TABLE` (added by
`archive/2026-08-08-drain-remediation-table`) currently has three rows:
`quarantined_budget_exhausted`, `verify_pending`, `stale_bookkeeping`. Each
pairs a `finder(repos_root, go_repo)` with an `action(finding, agent, timeout,
spawner, log)`; `sweep_remediations` iterates the table generically, so a new
row is additive — no change to the engine itself. See proposal.md for the
sync-pending stage's own origin (`dashboard.py`'s `detect_stage()`).

**Reconciling with the prior change's Non-Goals:** that design explicitly
listed `sync-pending` among stages *not* added to the table, reasoning they
are "normal open work already visible through `worktrail-go auto`'s
dashboard-driven claim flow, not silent stalls." That reasoning does not hold
up against the same design's own repeated statement for the other two rows —
`resume_quarantined_budget_exhausted` and `resume_verify_pending`'s
docstrings both say plainly that auto mode "only claims work-queue briefs,
never the 'Ready to implement' specs shown in the dashboard's Active Work
section." A `sync-pending` spec is exactly such a dashboard-only stage, not a
work-queue brief, so it is just as invisible to `worktrail-go auto` as
`verify-pending` was before this table existed. This change treats that
Non-Goal as based on a since-corrected premise, not as a decision this change
must work around.

## Goals / Non-Goals

**Goals**
- Add `sync-pending` as a fourth safe, unattended-recoverable remediation
  category, mirroring `verify_pending`'s finder/action/table-row/summary-key
  shape exactly — this is additive to `REMEDIATION_TABLE`, not a change to
  `sweep_remediations`'s engine.

**Non-Goals**
- Do not touch `dashboard.py` — the `sync-pending` stage already exists
  there (`detect_stage()`); this change only adds a finder that filters for
  it and an action that resolves it.
- Do not revisit `orchestrator-stuck` (`fanout_failed`) — still
  human-recovery-only, unchanged from the prior design.
- Do not extend `find_sync_pending_specs` to the OpenSpec-format spec tree.
  It scans `dashboard.scan(repo_path / "docs" / "specs")` only, the same
  devkit-only scope `find_verify_pending_specs`/`find_stale_bookkeeping_specs`
  already have — this change mirrors that existing, pre-established scope
  rather than fixing it. (Tracked as the same pre-existing gap D4 of the
  prior design already flagged and deferred for `stale-bookkeeping`.)

## Decisions

### New finder — `find_sync_pending_specs`

Mirrors `find_verify_pending_specs` (`drain.py:433`) verbatim except for the
stage filter:

```python
def find_sync_pending_specs(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in rows:
            if row.get("stage") != "sync-pending":
                continue
            spec_id = row.get("id")
            if not spec_id:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            found.append({
                "repo": repo_path, "repo_name": name,
                "spec_id": spec_id, "spec_rel": spec_rel,
            })
    return found
```

### New action — `resume_sync_pending`

Unlike the two `full-real`-resume rows, the remediation for a `sync-pending`
spec is `/opsx:sync <spec_id>`, not another orchestrator run. `drain.py`'s
own `build_command` (`drain.py:147`) cannot be reused here — its `prompt` is
hardcoded to either the router's `PROMPT` constant or `worktrail-go {repo}
auto`, with no way to substitute an arbitrary skill invocation.

The existing spawner contract the other three rows use is
`Callable[[List[str], int], SpawnOutcome]` — no `cwd` parameter — because
`worktrail-live full-real --repo <repo> ...` and `gh`/`git` calls all take
their target path as an explicit argument, never relying on process cwd.
`/opsx:sync` has no such flag (it is a slash-command skill invocation, not a
plain CLI binary), so it must actually run with the target repo as process
cwd. Rather than widen the spawner contract with a new parameter (which
would touch all three existing rows' call sites), wrap the already-installed
`worktrail-skill-dispatch` console script (`router/skill_dispatch.py`) as the
spawned command — its own `main()` threads `--cwd` through to the child
subprocess's `cwd=` internally (verified: `skill_dispatch.py:160-162`), so
the existing no-cwd spawner signature is untouched:

```python
def build_sync_command(agent: str, repo: Path, spec_id: str) -> List[str]:
    """Wraps the installed worktrail-skill-dispatch console script so the
    /opsx:sync child runs with `repo` as its process cwd -- skill_dispatch's
    own build_command() has no way to set process cwd itself (only codex/
    opencode's -C/--dir flags, which claude lacks), and drain.py's spawner
    contract has no cwd parameter for any existing row to widen."""
    return ["worktrail-skill-dispatch", "--agent", agent, "--skill", "opsx:sync",
            "--args", spec_id, "--cwd", str(repo), "--write"]

def resume_sync_pending(
    repos_root: Path,
    go_repo: Optional[str],
    agent: str,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """Resume every sync-pending spec found under `repos_root` by spawning
    `/opsx:sync <spec_id>`. Best-effort: one spec's sync failing does not
    stop the others. Thin wrapper over sweep_remediations, restricted to
    this row's key."""
    return sweep_remediations(
        repos_root, go_repo, agent, timeout, spawner, log,
        keys=["sync_pending"],
    )["sync_pending"]

def _run_sync_pending(
    finding: Dict[str, Any],
    agent: str,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> Dict[str, Any]:
    repo, spec_id = finding["repo"], finding["spec_id"]
    cmd = build_sync_command(agent, repo, spec_id)
    log(f"resume-sync-pending: {finding['repo_name']} {spec_id} -> /opsx:sync")
    outcome = spawner(cmd, timeout)
    log(f"resume-sync-pending result: {finding['repo_name']} {spec_id} "
        f"exit={outcome.exit_code}")
    return {
        "repo": finding["repo_name"], "spec_id": spec_id,
        "exit_code": outcome.exit_code,
    }
```

Result shape (`repo`, `spec_id`, `exit_code`) matches `_resume_via_full_real`'s
existing shape, so `resumed_sync_pending` entries are structurally
interchangeable with `resumed_verify_pending` entries for any caller that
doesn't branch on which row produced them.

### Table row and summary-dict wiring

```python
REMEDIATION_TABLE: List[StageRemediation] = [
    StageRemediation(
        "quarantined_budget_exhausted", "resume-quarantine",
        find_resumable_quarantines,
        functools.partial(_resume_via_full_real, label="resume-quarantine")),
    StageRemediation(
        "verify_pending", "resume-verify-pending",
        find_verify_pending_specs,
        functools.partial(_resume_via_full_real, label="resume-verify-pending")),
    StageRemediation(
        "stale_bookkeeping", "close-stale-bookkeeping",
        find_stale_bookkeeping_specs, close_stale_bookkeeping),
    StageRemediation(
        "sync_pending", "resume-sync-pending",
        find_sync_pending_specs, _run_sync_pending),
]
```

`drain()`'s summary dict gains one line, mirroring the other three:

```python
summary["resumed_sync_pending"] = resumed.get("sync_pending", [])
```

No change to `sweep_remediations`, `drain()`'s two call sites, or any
existing row — the fourth row rides the same generic iterate + per-finding
try/except loop the other three already use.

## Risks / Trade-offs

- **Risk:** `/opsx:sync` needs to run with the target repo as process cwd,
  which the existing no-cwd spawner contract cannot express directly.
  Mitigation: wrap `worktrail-skill-dispatch`, which already threads `--cwd`
  through to the child's actual process cwd — the new action's command list
  is a call to that installed console script, not a raw `claude -p ...`
  invocation, so no spawner-contract change is needed and no new subprocess
  wiring is introduced.
- **Trade-off:** unlike `close_stale_bookkeeping`, this action does not wait
  for or verify the sync's outcome beyond the spawned process's exit code —
  matching the two `full-real`-resume actions' existing best-effort
  contract (spawn, record, move on) rather than `close_stale_bookkeeping`'s
  synchronous PR-open pattern. A sync that starts but does not complete is
  visible on the next sweep like any other repeated finding, not silently
  dropped.

## Migration Plan

No data migration. Purely additive: new finder, new action, new table row,
new summary-dict key. Every existing `REMEDIATION_TABLE` row, `drain()`
call site, and summary-dict key is unchanged.

## Open Questions

None.
