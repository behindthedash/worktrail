#!/usr/bin/env python3
"""Clarification-inference drift guard.

Backs the Decision Classification rule `specs.spec-check` gained in PR #428
(`plugins/developer-kit-specs/commands/specs.spec-check.md`): an owner-escalated
`[NEEDS CLARIFICATION]` marker (design selection, security/authz, cross-tenant
contention, or justified only by an absent schema field) must never be
silently resolved and reported clean. That fix lives entirely in the
command's Markdown operating procedure — nothing executes to verify an agent
actually followed it, so a headless or careless run can still infer-and-
declare-clean exactly as before.

This is the deterministic backstop, wired into `pre_pr_gate.py` the same way
`check_spec_sync.py` is. Unlike `check_spec_sync.py` (which scans the whole
`docs/specs/` tree unconditionally), this check is deliberately scoped to
spec files **changed in the current diff only**: specs that already merged
under the pre-PR#428 behavior may legitimately carry this pattern (see
handoff brief 20260724-161249-fleet-spec-inference-exposure-audit, which
audits that pre-existing exposure separately) and must not fail every future
PR's gate. Only newly-touched or newly-created spec files are checked.

Signature (deliberately narrow, matches its sibling): a spec file containing
inference-flavored language ("no representation for", "resolved from the
locked directive/schema('s) (own) structure", "cannot be expressed") together
with an "Open Questions: None outstanding" (or equivalent all-clear) line —
the exact co-occurrence the Decision Classification rule forbids.

Exit code: 0 if no changed spec file matches the anti-pattern, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

INFERENCE_PATTERNS = [
    re.compile(r"no representation for", re.IGNORECASE),
    re.compile(
        r"resolved from the locked (?:directive|schema)(?:'s)?\s*(?:own\s+)?structure",
        re.IGNORECASE,
    ),
    re.compile(r"cannot be expressed", re.IGNORECASE),
]

ALL_CLEAR_PATTERNS = [
    re.compile(r"open questions:\s*none outstanding", re.IGNORECASE),
    re.compile(r"nothing outstanding", re.IGNORECASE),
    re.compile(r"none outstanding", re.IGNORECASE),
]

CANDIDATE_BASE_REFS = ("origin/main", "origin/master", "main", "master")


def check_text(text: str) -> list[str]:
    """Return failure descriptions for one spec file's text (empty = pass)."""
    inference_hit = None
    for pattern in INFERENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            inference_hit = match.group(0)
            break
    if inference_hit is None:
        return []

    all_clear_hit = None
    for pattern in ALL_CLEAR_PATTERNS:
        match = pattern.search(text)
        if match:
            all_clear_hit = match.group(0)
            break
    if all_clear_hit is None:
        return []

    return [
        f"inference-based Clarifications resolution ('{inference_hit}') co-occurs "
        f"with an all-clear line ('{all_clear_hit}') — see Decision Classification "
        "in specs.spec-check.md; an owner-escalated marker must be reported as "
        "Decision needed, not None outstanding"
    ]


def check_changed_specs(repo: Path, changed_paths: list[str]) -> list[str]:
    """Return failure messages across every changed docs/specs/*/*.md file."""
    failures: list[str] = []
    for relpath in changed_paths:
        if not relpath.startswith("docs/specs/") or not relpath.endswith(".md"):
            continue
        full_path = repo / relpath
        if not full_path.is_file():
            continue
        text = full_path.read_text(encoding="utf-8", errors="replace")
        for failure in check_text(text):
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
        print(f"FAIL: {len(failures)} clarification-integrity issue(s) in changed spec files")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("clarification integrity guard: no drift detected in changed spec files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
