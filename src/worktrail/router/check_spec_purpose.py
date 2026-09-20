#!/usr/bin/env python3
"""Capability-spec Purpose drift guard.

`openspec archive` writes a new `openspec/specs/<capability>/spec.md` with a
placeholder Purpose (`TBD - created by archiving change ...`). Nothing ever
forced anyone back to replace it, so capabilities accumulate requirements with
no statement of what the capability is for — the one line a later reader needs
to decide whether a change belongs in that spec at all.

This is the deterministic backstop, wired into `pre_pr_gate.py` the same way
`check_clarification_integrity.py` is. Like that check (and unlike
`check_spec_sync.py`, which scans the whole tree unconditionally), it is
deliberately scoped to capability specs **changed in the current diff only**:
specs that merged before this guard existed may still carry a placeholder and
must not fail every future PR's gate. Only newly-touched or newly-created
capability specs are checked, so the tree converges as specs are edited rather
than blocking unrelated work.

Only `openspec/specs/<capability>/spec.md` is in scope. `openspec/changes/**`
delta specs carry requirement deltas, not a capability Purpose, and
`docs/specs/**` is the older devkit format with its own contract.

Exit code: 0 if every changed capability spec states a real Purpose, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

SPEC_PATH_RE = re.compile(r"^openspec/specs/([^/]+)/spec\.md$")

CANDIDATE_BASE_REFS = ("origin/main", "origin/master", "main", "master")


def _purpose_body(text: str) -> str | None:
    """Return the `## Purpose` section body, or None if there is no such heading."""
    lines = text.splitlines()
    body: list[str] | None = None
    for line in lines:
        if body is None:
            if line.strip().lower() == "## purpose":
                body = []
            continue
        if line.startswith("## "):
            break
        body.append(line)
    if body is None:
        return None
    return "\n".join(body)


def check_text(capability: str, text: str) -> list[str]:
    """Return failure descriptions for one capability spec's text (empty = pass)."""
    body = _purpose_body(text)
    if body is None:
        return [f"{capability}: no `## Purpose` section"]

    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if not first:
        return [f"{capability}: `## Purpose` section is empty"]
    if first.lower().startswith("tbd"):
        return [
            f"{capability}: `## Purpose` is still the archive placeholder ({first!r})"
        ]
    return []


def check_changed_specs(repo: Path, changed_paths: list[str]) -> list[str]:
    """Return failure messages across every changed openspec capability spec."""
    failures: list[str] = []
    for relpath in changed_paths:
        match = SPEC_PATH_RE.match(relpath)
        if not match:
            continue
        full_path = repo / relpath
        if not full_path.is_file():
            continue
        text = full_path.read_text(encoding="utf-8", errors="replace")
        failures.extend(check_text(match.group(1), text))
    return failures


def _resolve_base_ref(repo: Path, configured: str | None) -> str | None:
    candidates = (
        (f"origin/{configured}", configured) if configured else CANDIDATE_BASE_REFS
    )
    for ref in candidates:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            check=False,
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return ref
    return None


def _changed_paths_via_git(repo: Path, base_branch: str | None) -> list[str]:
    base_ref = _resolve_base_ref(repo, base_branch)
    if base_ref is None:
        return []
    merge_base = subprocess.run(
        ["git", "merge-base", base_ref, "HEAD"],
        check=False,
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if merge_base.returncode != 0:
        return []
    diff = subprocess.run(
        ["git", "diff", "--name-only", merge_base.stdout.strip(), "HEAD"],
        check=False,
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        return []
    return [line for line in diff.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", default=".", help="worktree root (default: cwd)")
    parser.add_argument(
        "--base-branch",
        default=None,
        help="base branch to diff against (default: try origin/main, origin/master, main, master)",
    )
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    changed = _changed_paths_via_git(repo, args.base_branch)
    failures = check_changed_specs(repo, changed)

    if failures:
        print(f"FAIL: {len(failures)} changed capability spec(s) with no Purpose")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("spec purpose guard: every changed capability spec states a Purpose.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
