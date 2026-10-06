"""Repair retained task branches and clear their quarantine journal state.

This module deliberately performs all git repair before rewriting the run
journal.  A failed repair therefore leaves the resume state untouched.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from worktrail.orchestrator import dispatch, live, progress, resume_group, worktree
from worktrail.router import quarantine_selfcheck
from worktrail.router.journal_selfcheck import _runlock_held


class RecoveryRefused(RuntimeError):
    """A recovery precondition was not met; the journal must remain unchanged."""


def _terminal_failure(entry: dict[str, Any]) -> bool:
    return (
        not entry.get("event")
        and (entry.get("report") or {}).get("terminal_status")
        in live._NON_RETRYABLE_TERMINAL
    )


def _clear_task_entries(
    journal: dict[str, Any], task_ids: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply clear-task's guardrails and cascade to an in-memory journal.

    This is intentionally side-effect free except for the supplied in-memory
    dictionary.  The command layer owns the single eventual journal write.
    """
    entries = journal.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    targets = set(task_ids)
    for entry in entries:
        if entry.get("event") or entry.get("task") not in targets:
            continue
        report = entry.get("report") or {}
        completed = report.get("terminal_status") == "done" or (
            entry.get("role") == dispatch.ROLE_CLEANUP
            and report.get("status") != "failed"
            and report.get("terminal_status") not in live._NON_RETRYABLE_TERMINAL
        )
        if completed:
            raise RecoveryRefused(
                f"{entry.get('task')} has a success/completion entry "
                f"(role {entry.get('role')!r}); clearing it would discard completed work"
            )

    uncleared = sorted(
        task_id
        for task_id in targets
        if not any(
            _terminal_failure(entry) and entry.get("task") == task_id
            for entry in entries
        )
    )
    if uncleared:
        raise RecoveryRefused(
            "no failed/escalated journal entries for "
            f"{', '.join(uncleared)}; nothing to clear"
        )

    cleared = set(targets)
    removed = {
        id(entry)
        for entry in entries
        if _terminal_failure(entry) and entry.get("task") in targets
    }
    changed = True
    while changed:
        changed = False
        for entry in entries:
            if id(entry) in removed or not _terminal_failure(entry):
                continue
            if entry.get("role") != "dependency-gate":
                continue
            if cleared.intersection(live._gate_blockers(entry)):
                removed.add(id(entry))
                cleared.add(entry.get("task"))
                changed = True

    direct = [
        entry
        for entry in entries
        if id(entry) in removed and entry.get("task") in targets
    ]
    cascaded = [
        entry
        for entry in entries
        if id(entry) in removed and entry.get("task") not in targets
    ]
    journal["entries"] = [entry for entry in entries if id(entry) not in removed]
    return direct, cascaded


@contextlib.contextmanager
def _recovery_worktree(repo: Path, branch: str):
    """Check out an existing retained branch without moving its ref."""
    parent = repo.parent / f"{repo.name}-recover"
    path = parent / f"{branch.replace('/', '-')}-{uuid.uuid4().hex[:8]}"
    result = live._git(repo, "worktree", "add", "-f", str(path), branch, check=False)
    if result.returncode != 0:
        raise RecoveryRefused(
            f"could not create recovery worktree for {branch}: {result.stderr.strip()}"
        )
    try:
        yield path
    finally:
        live._git(repo, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def _repair_branch(repo: Path, branch: str, base_ref: str, dry_run: bool) -> str:
    """Merge ``base_ref`` into a retained branch and return its outcome text."""
    checkouts = live._worktree_checkouts_on_branch(repo, branch)
    for checkout in checkouts:
        if live._git(checkout, "status", "--porcelain", check=False).stdout.strip():
            raise RecoveryRefused(
                f"retained branch {branch} has uncommitted changes at {checkout}"
            )

    if (
        live._git(
            repo, "merge-base", "--is-ancestor", base_ref, branch, check=False
        ).returncode
        == 0
    ):
        return "already current"
    if dry_run:
        return f"would merge {base_ref}"

    manager = (
        contextlib.nullcontext(checkouts[0])
        if checkouts
        else _recovery_worktree(repo, branch)
    )
    with manager as checkout:
        merged = live._git(checkout, "merge", "--no-edit", base_ref, check=False)
        if merged.returncode != 0:
            conflicts = live._git(
                checkout, "diff", "--name-only", "--diff-filter=U", check=False
            ).stdout.splitlines()
            live._git(checkout, "merge", "--abort", check=False)
            paths = ", ".join(conflicts) or "(no conflicted paths reported)"
            raise RecoveryRefused(f"merge conflict repairing {branch}: {paths}")
        commit = live._git(checkout, "rev-parse", "HEAD", check=False).stdout.strip()
    return f"merged {commit}"


def recover(
    repo: Path,
    spec_rel: str,
    group_names: list[str],
    task_names: list[str],
    *,
    base: str = "dev",
    remote: str = "origin",
    dry_run: bool = False,
) -> int:
    """Repair selected retained branches, then atomically clear the journal."""
    repo = Path(repo).resolve()
    if not group_names and not task_names:
        print("recover: name at least one --group or --tasks task ID")
        return 1
    journal_path = live.journal_path_for(repo, spec_rel)
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"recover: cannot read run journal {journal_path}: {exc}")
        return 1
    if not isinstance(journal, dict):
        print(f"recover: run journal {journal_path} is not a JSON object")
        return 1
    if _runlock_held(journal_path.with_suffix(".lock")):
        print(f"recover: a live run holds the run lock for {journal_path}; not editing")
        return 1

    selected_groups, problems = resume_group.select_groups(journal, group_names, False)
    selected_tasks: list[str] = []
    for group in selected_groups:
        task_ids = quarantine_selfcheck.group_task_ids(
            repo, journal_path.stem.removeprefix("run-"), group
        )
        if task_ids is None:
            problems.append(f"group {group!r}: no cached RunPlan task resolution")
            continue
        selected_tasks.extend(task_ids)
    if problems:
        print("recover: " + "; ".join(problems))
        return 1
    selected_tasks.extend(task_names)
    selected_tasks = list(dict.fromkeys(selected_tasks))

    after = copy.deepcopy(journal)
    try:
        direct, cascaded = _clear_task_entries(after, selected_tasks)
    except RecoveryRefused as exc:
        print(f"recover: refusing -- {exc}. Journal left unchanged.")
        return 1

    base_ref = live._live_base_ref(repo, remote, base)
    if base_ref is None:
        print(f"recover: cannot resolve live base {remote}/{base} or {base}")
        return 1
    branches = {
        task_id: worktree.task_branch(journal_path.stem.removeprefix("run-"), task_id)
        for task_id in selected_tasks
    }
    for branch in branches.values():
        for checkout in live._worktree_checkouts_on_branch(repo, branch):
            if live._git(checkout, "status", "--porcelain", check=False).stdout.strip():
                print(
                    f"recover: refusing -- retained branch {branch} has uncommitted "
                    f"changes at {checkout}. Journal left unchanged."
                )
                return 1
    outcomes: list[tuple[str, str]] = []
    try:
        for task_id in selected_tasks:
            branch = branches[task_id]
            if not live._branch_exists(repo, branch):
                outcomes.append((task_id, "no retained branch"))
                continue
            outcomes.append((task_id, _repair_branch(repo, branch, base_ref, dry_run)))
    except RecoveryRefused as exc:
        print(f"recover: refusing -- {exc}. Journal left unchanged.")
        return 1

    if selected_groups:
        resume_group.clear_groups(after, selected_groups)
    if dry_run:
        for task_id, outcome in outcomes:
            print(f"recover: {task_id}: {outcome}")
        print(
            f"recover: would clear entries for {', '.join(sorted({e.get('task') for e in direct}))}"
        )
        if selected_groups:
            print(f"recover: would clear groups: {', '.join(selected_groups)}")
        print("recover: dry-run; no merge or journal write performed")
        return 0

    if _runlock_held(journal_path.with_suffix(".lock")):
        print(f"recover: a live run holds the run lock for {journal_path}; not editing")
        return 1
    progress.atomic_write_text(
        journal_path, json.dumps(after, indent=2, sort_keys=True) + "\n"
    )
    for task_id, outcome in outcomes:
        print(f"recover: {task_id}: {outcome}")
    print(
        f"recover: cleared entries for {', '.join(sorted({e.get('task') for e in direct}))}"
    )
    if cascaded:
        print(
            f"recover: cleared cascaded entries for {', '.join(sorted({e.get('task') for e in cascaded}))}"
        )
    if selected_groups:
        print(f"recover: cleared groups: {', '.join(selected_groups)}")
    print(
        f"next: worktrail-live full-real --repo {repo} --spec {spec_rel} "
        f"--remote {remote} --base {base} --resume"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--tasks", default="", help="comma-separated task IDs")
    parser.add_argument("--base", default="dev")
    parser.add_argument("--remote", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    return recover(
        repo,
        args.spec,
        args.group,
        [task.strip() for task in args.tasks.split(",") if task.strip()],
        base=args.base,
        remote=args.remote or live._default_remote(repo),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
