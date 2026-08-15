#!/usr/bin/env python3
"""Definition-of-Done verification drift guard.

A task's `dod-checks:` frontmatter (see `taskformats/devkit/schema.py`'s
`FIELD_SCHEMA`) lets a task author declare a small set of deterministic
checks — file existence, a grep pattern, or a shell command — that must hold
before the task's `status` may legitimately read `completed`. Nothing
previously verified that those checks actually ran and passed: a task file
could claim `status: completed` with fabricated or stale Acceptance
Criteria / Definition of Done checkboxes and nothing would catch it.

This is the deterministic backstop, wired into `pre_pr_gate.py` the same way
`check_clarification_integrity.py` is: scoped to task files **changed in the
current diff only** (task files that completed before this check existed
must not fail every future PR's gate).

Exit code: 0 if no changed, completed task file with `dod-checks` fails any
check, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from worktrail.taskformats.devkit import schema
from worktrail.taskformats.devkit.schema import is_task_file, read_task_file

CANDIDATE_BASE_REFS = ("origin/main", "origin/master", "main", "master")


def run_check(repo: Path, check: dict) -> str | None:
    """Run one `dod-checks` entry. Return a failure string, or None on pass.

    An unrecognized `type` or a check missing its required keys is itself a
    failure — never a silent pass.
    """
    check_type = check.get("type")

    if check_type == "file_exists":
        path = check.get("path")
        if not path:
            return f"malformed file_exists check (missing 'path'): {check}"
        if not (repo / path).exists():
            return f"file_exists check failed: {path} does not exist"
        return None

    if check_type == "file_tracked":
        path = check.get("path")
        if not path:
            return f"malformed file_tracked check (missing 'path'): {check}"
        if not (repo / path).exists():
            return f"file_tracked check failed: {path} does not exist"
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            cwd=str(repo), capture_output=True, text=True,
        )
        if result.returncode != 0:
            return f"file_tracked check failed: {path} is not tracked by git"
        return None

    if check_type == "ac_checkboxes_complete":
        task_path = check.get("task_path")
        if not task_path:
            return f"malformed ac_checkboxes_complete check (missing 'task_path'): {check}"
        _frontmatter, error, body = read_task_file(repo / task_path)
        if error:
            return f"ac_checkboxes_complete check failed: {task_path} could not be read ({error})"
        if not schema._all_checkboxes_checked(body, sections=("Acceptance Criteria",)):
            return f"ac_checkboxes_complete check failed: {task_path} has unchecked Acceptance Criteria checkboxes"
        return None

    if check_type == "grep":
        path = check.get("path")
        pattern = check.get("pattern")
        if not path or not pattern:
            return f"malformed grep check (missing 'path' and/or 'pattern'): {check}"
        full_path = repo / path
        if not full_path.is_file():
            return f"grep check failed: {path} does not exist"
        text = full_path.read_text(encoding="utf-8", errors="replace")
        if not re.search(pattern, text):
            return f"grep check failed: {path} does not match pattern {pattern!r}"
        return None

    if check_type == "command":
        cmd = check.get("cmd")
        if not cmd:
            return f"malformed command check (missing 'cmd'): {check}"
        result = subprocess.run(["bash", "-c", cmd], cwd=str(repo))
        if result.returncode != 0:
            return f"command check failed (exit {result.returncode}): {cmd}"
        return None

    return f"unrecognized dod-checks type: {check_type!r}"


def derive_dod_checks(frontmatter: dict, body: str, task_relpath: str) -> list[dict]:
    """Derive a `dod-checks` list when a task declares none.

    Always includes one `ac_checkboxes_complete` check against the task file
    itself; for each path in frontmatter `files:` (if present), adds a
    `file_tracked` check and a `no_stub_markers` check for that path.
    """
    checks: list[dict] = [
        {"type": "ac_checkboxes_complete", "task_path": task_relpath},
    ]
    for path in frontmatter.get("files") or []:
        checks.append({"type": "file_tracked", "path": path})
        checks.append({"type": "no_stub_markers", "path": path})
    return checks


def check_task_file(repo: Path, task_path: Path) -> list[str]:
    """Run every `dod-checks` entry for one task file. Empty list means pass,
    not-completed, no `dod-checks` declared and derivation yields nothing, or
    an explicit `dod-checks` that all pass."""
    frontmatter, error, body = read_task_file(task_path)
    if error or not frontmatter:
        return []
    if frontmatter.get("status") != "completed":
        return []
    checks = frontmatter.get("dod-checks")
    if not checks:
        task_relpath = str(task_path.resolve().relative_to(repo.resolve()))
        checks = derive_dod_checks(frontmatter, body, task_relpath)
        if not checks:
            return []

    failures: list[str] = []
    for check in checks:
        failure = run_check(repo, check)
        if failure:
            failures.append(failure)
    return failures


def check_changed_specs(repo: Path, changed_paths: list[str]) -> list[str]:
    """Return failure messages across every changed devkit task file under
    docs/specs/."""
    failures: list[str] = []
    for relpath in changed_paths:
        if not relpath.startswith("docs/specs/") or not is_task_file(relpath):
            continue
        full_path = repo / relpath
        if not full_path.is_file():
            continue
        for failure in check_task_file(repo, full_path):
            failures.append(f"{relpath}: {failure}")
    return failures


def _resolve_base_ref(repo: Path, configured: str | None) -> str | None:
    candidates = (f"origin/{configured}", configured) if configured else CANDIDATE_BASE_REFS
    for ref in candidates:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=str(repo), capture_output=True, text=True,
        )
        if result.returncode == 0:
            return ref
    return None


def _changed_paths_via_git(repo: Path, configured_base: str | None) -> list[str]:
    base_ref = _resolve_base_ref(repo, configured_base)
    if base_ref is None:
        return []
    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", base_ref],
        cwd=str(repo), capture_output=True, text=True,
    )
    if merge_base.returncode != 0:
        return []
    diff = subprocess.run(
        ["git", "diff", "--name-only", merge_base.stdout.strip(), "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if diff.returncode != 0:
        return []
    return [line for line in diff.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", default=".", help="worktree root (default: cwd)")
    parser.add_argument(
        "--base-branch", default=None,
        help="base branch to diff against (default: try origin/main, origin/master, main, master)",
    )
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    changed = _changed_paths_via_git(repo, args.base_branch)
    failures = check_changed_specs(repo, changed)

    if failures:
        print(f"FAIL: {len(failures)} DoD-verification issue(s) in changed task files")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("DoD verification guard: no drift detected in changed task files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
