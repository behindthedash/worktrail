## 1. Staleness warning implementation

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, add
      `_resume_quarantine_staleness_warning(repo: Path, base: str, spec_id: str,
      groups: list, groups_journal: dict) -> None` near `_resume_drift_report`
      (~line 1057): for each `g in groups` whose `groups_journal.get(g["name"],
      {}).get("state") == "QUARANTINED"`, resolve that group's task branches
      (`f"{spec_id}/{tid.lower()}"` for `tid in g["tasks"]`), compute the
      maximum `git merge-base <branch> <base>` → `git rev-list --count
      <merge-base>..<base>` drift across those branches (skipping any branch
      that doesn't exist or whose git calls fail — best-effort, never raises),
      and when the max count is non-zero, print one warning line naming the
      group, the commit count, and recommending `--fresh` (see design.md for
      exact message shape).
- [ ] 1.2 In `_pipeline_scheduler`'s resume branch (~line 3378, inside `if
      resume and Path(journal_path).exists():`, immediately after the existing
      `_resume_drift_report(repo, base, spec_id, tasks)` call), call
      `_resume_quarantine_staleness_warning(repo, base, spec_id, groups,
      groups_journal)`. Do not change `_resume_drift_report`'s own call or
      behavior.
- [ ] 1.3 Add `tests/orchestrator/test_live_extras.py` (or a new
      `test_resume_quarantine_staleness.py` alongside the existing
      `tests/orchestrator/test_*.py` files) covering:
      resumed journal with a `QUARANTINED` group whose task branch is N
      commits behind `base` → warning printed naming the group and N;
      `QUARANTINED` group with zero drift → no warning; journal with no
      `QUARANTINED` groups → no warning, `_resume_drift_report`'s existing
      output unaffected; `QUARANTINED` group whose task branch does not exist
      → no warning, no exception raised.

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`; both must be green.
