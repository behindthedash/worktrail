#!/usr/bin/env python3
"""Definition-of-Done verification drift guard.

A task's `dod-checks:` frontmatter (see `taskformats/devkit/schema.py`'s
`FIELD_SCHEMA`) lets a task author declare a small set of deterministic
checks — file existence, a grep pattern, or a shell command — that must hold
before the task's `status` may legitimately read `completed`. Nothing
previously verified that those checks actually ran and passed: a task file
could claim `status: completed` with fabricated or stale Acceptance
Criteria / Definition of Done checkboxes and nothing would catch it.

When a `completed` task declares no `dod-checks` at all, `derive_dod_checks`
synthesizes a fallback in its place — an `ac_checkboxes_complete` check
against the task file itself (covering both the `## Acceptance Criteria`
and `## Definition of Done (DoD)` sections, via
`taskformats.devkit.schema.COMPLETION_AUDIT_SECTIONS` — the same section set
`update_status()`'s completed-task drift warning and the fleet-wide
`checkbox_audit.py` sweep already use, so all three agree on what counts as
drift), plus a `file_tracked` and `no_stub_markers` check for each path in
frontmatter `files:` — so an author can't skip verification simply by
omitting `dod-checks`. An explicit `dod-checks` list always wins over
derivation.

This is the deterministic backstop, wired into `pre_pr_gate.py` the same way
`check_clarification_integrity.py` is: scoped by default to task files
**changed in the current diff only** (task files that completed before this
check existed must not fail every future PR's gate). Pass `--all` to instead
audit every devkit task file under `docs/specs/`, regardless of whether it
changed in the current diff — a backlog report, not a gate; it does not
affect `pre_pr_gate.py`'s exit code.

Exit code: 0 if no changed, completed task file (explicit or derived
`dod-checks`) fails any check, 1 otherwise. Under `--all`, exit code reflects
the full audit instead of the diff-scoped check.

Pass `--suggest-remediation` (with either mode) to classify each failure as a
remediation hint instead of a bare failure string: `stale files: metadata`
(the declared path itself moved or was never tracked — a metadata problem,
not a content problem) vs `genuine unmet AC` (the `ac_checkboxes_complete`
check found real unchecked boxes — status should revert from `completed` to
`implemented`). This never changes default-mode output or exit codes; it only
adds a suggestion line per failure.
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

STUB_MARKER_PATTERN = re.compile(r"\b(TODO|FIXME|XXX|NotImplementedError)\b")


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
        if not schema._all_checkboxes_checked(body, sections=schema.COMPLETION_AUDIT_SECTIONS):
            return (
                f"ac_checkboxes_complete check failed: {task_path} has unchecked "
                "Acceptance Criteria / Definition of Done checkboxes"
            )
        return None

    if check_type == "no_stub_markers":
        path = check.get("path")
        if not path:
            return f"malformed no_stub_markers check (missing 'path'): {check}"
        full_path = repo / path
        if not full_path.is_file():
            return f"no_stub_markers check failed: {path} does not exist"
        text = full_path.read_text(encoding="utf-8", errors="replace")
        match = STUB_MARKER_PATTERN.search(text)
        if match:
            return f"no_stub_markers check failed: {path} contains stub marker {match.group(0)!r}"
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

    Always includes one `ac_checkboxes_complete` check (covering both
    Acceptance Criteria and Definition of Done checkboxes) against the task
    file itself; for each path in frontmatter `files:` (if present), adds a
    `file_tracked` check and a `no_stub_markers` check for that path.
    """
    checks: list[dict] = [
        {"type": "ac_checkboxes_complete", "task_path": task_relpath},
    ]
    for path in frontmatter.get("files") or []:
        checks.append({"type": "file_tracked", "path": path})
        checks.append({"type": "no_stub_markers", "path": path})
    return checks


def _check_task_file_pairs(repo: Path, task_path: Path) -> list[tuple[dict, str]]:
    """Run every `dod-checks` entry for one task file, returning `(check,
    failure)` pairs for entries that failed. Empty list means pass,
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

    pairs: list[tuple[dict, str]] = []
    for check in checks:
        failure = run_check(repo, check)
        if failure:
            pairs.append((check, failure))
    return pairs


def check_task_file(repo: Path, task_path: Path) -> list[str]:
    """Run every `dod-checks` entry for one task file. Empty list means pass,
    not-completed, no `dod-checks` declared and derivation yields nothing, or
    an explicit `dod-checks` that all pass."""
    return [failure for _check, failure in _check_task_file_pairs(repo, task_path)]


STALE_PATH_CHECK_TYPES = frozenset({"file_exists", "file_tracked"})


def _find_candidate_paths(repo: Path, basename: str, *, limit: int = 5) -> list[str]:
    """Search git-tracked files in `repo` for other paths with the given
    basename — a candidate corrected path for a 'stale files: metadata'
    classification. Returns [] if `repo` is not a git checkout or nothing
    matches."""
    result = subprocess.run(
        ["git", "ls-files", f"*/{basename}", basename],
        cwd=str(repo), capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    return sorted({line for line in result.stdout.splitlines() if line.strip()})[:limit]


def classify_failure(repo: Path, check: dict, failure: str) -> dict:
    """Classify one `dod-checks` failure as a remediation hint.

    `stale-files-metadata`: the declared `path` doesn't exist/isn't tracked
    at its declared location — a metadata problem, not a content problem.
    Suggests the verified-correct path when exactly one git-tracked file
    elsewhere in the repo shares its basename.

    `genuine-unmet-ac`: an `ac_checkboxes_complete` check failed — the task's
    Acceptance Criteria / DoD boxes are actually unchecked. Suggests
    reverting `status: completed` to `status: implemented`.

    `unclassified`: no confident remediation signal (a `grep`/`command`
    check, an unrecognized/malformed check, or a `no_stub_markers` failure
    caused by an actual stub marker rather than a missing path).
    """
    check_type = check.get("type")
    is_stale_path_check = check_type in STALE_PATH_CHECK_TYPES or (
        check_type == "no_stub_markers" and "does not exist" in failure
    )

    if is_stale_path_check:
        path = check.get("path")
        if path and (repo / path).exists():
            suggestion = (
                f"'{path}' exists on disk but is not tracked by git — "
                f"run `git add {path}`"
            )
        else:
            candidates = _find_candidate_paths(repo, Path(path).name) if path else []
            if len(candidates) == 1:
                suggestion = f"path moved — update the declared path to '{candidates[0]}'"
            elif candidates:
                suggestion = (
                    "path moved — ambiguous candidates found: "
                    + ", ".join(candidates)
                )
            else:
                suggestion = "no candidate found elsewhere in the repo — verify by hand"
        return {"classification": "stale-files-metadata", "suggestion": suggestion,
                "check": check, "failure": failure}

    if check_type == "ac_checkboxes_complete":
        task_path = check.get("task_path")
        return {
            "classification": "genuine-unmet-ac",
            "suggestion": f"flip status: completed -> implemented in {task_path}",
            "check": check,
            "failure": failure,
        }

    return {"classification": "unclassified", "suggestion": None,
            "check": check, "failure": failure}


def check_task_file_with_hints(repo: Path, task_path: Path) -> list[dict]:
    """Like `check_task_file`, but returns a remediation hint per failure
    instead of a bare string."""
    return [
        classify_failure(repo, check, failure)
        for check, failure in _check_task_file_pairs(repo, task_path)
    ]


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


def audit_all_specs(repo: Path) -> list[str]:
    """Return failure messages across every devkit task file under
    docs/specs/, regardless of whether it changed in the current diff."""
    failures: list[str] = []
    for full_path in sorted((repo / "docs" / "specs").rglob("TASK-*.md")):
        relpath = str(full_path.relative_to(repo))
        if not is_task_file(relpath):
            continue
        for failure in check_task_file(repo, full_path):
            failures.append(f"{relpath}: {failure}")
    return failures


def check_changed_specs_with_hints(repo: Path, changed_paths: list[str]) -> list[dict]:
    """Like `check_changed_specs`, but each entry is a remediation hint dict
    (with a `task` key added) instead of a bare failure string."""
    hints: list[dict] = []
    for relpath in changed_paths:
        if not relpath.startswith("docs/specs/") or not is_task_file(relpath):
            continue
        full_path = repo / relpath
        if not full_path.is_file():
            continue
        for hint in check_task_file_with_hints(repo, full_path):
            hints.append({**hint, "task": relpath})
    return hints


def audit_all_specs_with_hints(repo: Path) -> list[dict]:
    """Like `audit_all_specs`, but each entry is a remediation hint dict
    (with a `task` key added) instead of a bare failure string."""
    hints: list[dict] = []
    for full_path in sorted((repo / "docs" / "specs").rglob("TASK-*.md")):
        relpath = str(full_path.relative_to(repo))
        if not is_task_file(relpath):
            continue
        for hint in check_task_file_with_hints(repo, full_path):
            hints.append({**hint, "task": relpath})
    return hints


_CLASSIFICATION_LABELS = {
    "stale-files-metadata": "stale files: metadata",
    "genuine-unmet-ac": "genuine unmet AC",
    "unclassified": "unclassified",
}


def format_remediation_hint(hint: dict) -> str:
    """Render one remediation-hint dict as a display line."""
    label = _CLASSIFICATION_LABELS[hint["classification"]]
    if hint["suggestion"]:
        return f"{label} — {hint['suggestion']}"
    return label


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
    parser.add_argument(
        "--all", action="store_true",
        help="audit every task file under docs/specs/, not just those changed in the current diff",
    )
    parser.add_argument(
        "--suggest-remediation", action="store_true",
        help=(
            "classify each failure as 'stale files: metadata' (a declared path is "
            "missing/untracked — suggests the verified-correct path when found "
            "elsewhere in the repo) or 'genuine unmet AC' (checkboxes are actually "
            "unchecked — suggests reverting status: completed -> implemented), "
            "instead of a bare failure string"
        ),
    )
    args = parser.parse_args()

    repo = Path(args.repo).resolve()

    if args.all:
        if args.suggest_remediation:
            hints = audit_all_specs_with_hints(repo)
            if hints:
                print(f"FAIL: {len(hints)} DoD-verification issue(s) in audit report")
                for hint in hints:
                    print(f"  - {hint['task']}: {hint['failure']}")
                    print(f"      remediation: {format_remediation_hint(hint)}")
                return 1
            print("DoD verification audit: no drift detected across docs/specs/.")
            return 0
        failures = audit_all_specs(repo)
        if failures:
            print(f"FAIL: {len(failures)} DoD-verification issue(s) in audit report")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print("DoD verification audit: no drift detected across docs/specs/.")
        return 0

    changed = _changed_paths_via_git(repo, args.base_branch)

    if args.suggest_remediation:
        hints = check_changed_specs_with_hints(repo, changed)
        if hints:
            print(f"FAIL: {len(hints)} DoD-verification issue(s) in changed task files")
            for hint in hints:
                print(f"  - {hint['task']}: {hint['failure']}")
                print(f"      remediation: {format_remediation_hint(hint)}")
            return 1
        print("DoD verification guard: no drift detected in changed task files.")
        return 0

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
