#!/usr/bin/env python3
"""Requirement/AC-to-task coverage gate.

A spec can declare a requirement or acceptance criterion (`REQ-001`,
`REQ-NR001`, `FR-273`, `AUTHZ-001`, ...) with no task ever claiming it —
the gap only surfaces months later, by hand, if at all. This is the
deterministic backstop: it catches an uncovered identifier at the moment a
diff introduces it.

Two declaration shapes are recognised (see design D1), both anchored at the
start of a line so that a mid-sentence cross-reference is never mistaken for
a declaration:

- the first cell of a markdown table row: ``| REQ-001 | The system SHALL... |``
- the head of a bullet item, followed by a separating colon:
  ``- REQ-NR001: The system SHALL NOT ...``

The identifier pattern itself is structural (uppercase prefix, hyphen,
optional uppercase sub-namespace segment, digit sequence) rather than a
fixed enum of known prefixes — the real corpus uses ~100 distinct prefixes,
and any enum would silently miss most of them.

Coverage is the union of every task file's ``reqs``, ``ac-mapping``, and
``imp-requirements`` frontmatter arrays (D3); no array is privileged.
``reqs`` is read directly from frontmatter, exactly as
``taskformats/devkit/source.py`` already does via its passthrough list —
``FIELD_SCHEMA`` is deliberately left untouched (D6).

Like its siblings (`check_clarification_integrity.py`,
`check_dod_verification.py`), this is wired into `pre_pr_gate.py` and scoped
to spec files **changed in the current diff only**. Unlike those siblings,
scoping to the diff is not enough on its own: a PR touching a spec for an
unrelated reason must not fail on a gap that has existed for months (D2). So
the blocking check additionally compares the spec's declared-identifier set
in the working tree against the same set at the base ref, and enforces
coverage only on identifiers newly declared in this diff. A spec that did
not exist at the base ref (a brand-new spec) has an empty base declaration
set, so everything it declares counts as newly declared — this is
intentional (see design Risks/Trade-offs), not a special case.

If the base ref cannot be resolved at all, the blocking check skips itself
and reports why, rather than either failing the run or silently treating
every declared identifier as new (which would turn the ratchet into
repo-wide enforcement by accident).

Repo-wide enumeration of every uncovered identifier — including pre-existing
gaps the diff-scoped ratchet never touches — is available as an opt-in audit
mode (`--audit`, D5). It is deliberately not part of the blocking path, and
`pre_pr_gate.py` never calls it.

Exit code (this module's own CLI): 0 if no changed spec declares a newly
uncovered identifier (or the base ref is unresolvable, or `--audit` was
passed), 1 otherwise. `pre_pr_gate.py` calls `check_changed_specs()`
directly and maps a non-empty result to its own exit code.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from worktrail.taskformats.devkit.schema import is_task_file, read_task_file

CANDIDATE_BASE_REFS = ("origin/main", "origin/master", "main", "master")

_IDENTIFIER = r"[A-Z]+-[A-Z]*\d+"
IDENTIFIER_RE = re.compile(_IDENTIFIER)
TABLE_ROW_RE = re.compile(r"^\s*\|\s*(" + _IDENTIFIER + r")\s*\|")
BULLET_RE = re.compile(r"^\s*[-*]\s+(" + _IDENTIFIER + r")\s*:")

COVERAGE_FIELDS = ("reqs", "ac-mapping", "imp-requirements")

# Non-declaring artifacts that live alongside the spec document in the same
# directory and must never be mistaken for it.
AUX_SPEC_FILENAMES = {
    "data-model.md",
    "decision-log.md",
    "traceability-matrix.md",
    "technical-plan.md",
    "user-request.md",
    "brainstorming-notes.md",
}


def discover_declared_identifiers(text: str) -> set[str]:
    """Identifiers that anchor a declaring construct (table row or bullet
    head), per D1. A mid-sentence cross-reference matches neither anchor and
    is never counted."""
    declared: set[str] = set()
    for line in text.splitlines():
        match = TABLE_ROW_RE.match(line) or BULLET_RE.match(line)
        if match:
            declared.add(match.group(1))
    return declared


def collect_task_references(spec_dir: Path) -> set[str]:
    """Union of `reqs`/`ac-mapping`/`imp-requirements` across every devkit
    task file under `spec_dir/tasks/` (D3). Empty if the spec has no tasks
    directory."""
    references: set[str] = set()
    tasks_dir = spec_dir / "tasks"
    if not tasks_dir.is_dir():
        return references
    for task_path in sorted(tasks_dir.iterdir()):
        if not task_path.is_file() or not is_task_file(str(task_path)):
            continue
        frontmatter, error, _body = read_task_file(task_path)
        if error or not frontmatter:
            continue
        for field in COVERAGE_FIELDS:
            for value in frontmatter.get(field) or []:
                references.add(str(value))
    return references


def resolve_spec_document(repo: Path, spec_dir: Path) -> Path | None:
    """The spec document a given `docs/specs/<id>/` directory declares
    requirements in. The devkit corpus does not name this file `spec.md`
    consistently (only 13/125 spec dirs across the real corpus do) -- most
    use a dated filename such as `2026-07-18--automation-health-digest.md`.

    Resolution order:
    1. The exact pointer already recorded in any task file's required
       `spec` frontmatter field (D6 does not apply here -- this is a read of
       an existing schema field, not a new one). This is the only fully
       deterministic source, since it is written once at task-creation time
       and never needs inferring.
    2. If the spec has no tasks yet (nothing to point at it), the single
       remaining `*.md` file in the directory once known auxiliary
       artifacts (data-model, decision-log, traceability-matrix,
       technical-plan, user-request, brainstorming-notes, `--tasks.md`,
       `--technical-plan.md`) are excluded. Lexicographically-last-wins
       heuristics are deliberately not used here: on the real corpus that
       rule picks `user-request.md` over the actual spec document for
       084-automation-health-digest, since `u` sorts after the date-prefixed
       spec filename.
    3. `spec.md`, for the corpora that do use that name.
    """
    tasks_dir = spec_dir / "tasks"
    if tasks_dir.is_dir():
        resolved_dir = spec_dir.resolve()
        for task_path in sorted(tasks_dir.iterdir()):
            if not task_path.is_file() or not is_task_file(str(task_path)):
                continue
            frontmatter, error, _body = read_task_file(task_path)
            if error or not frontmatter:
                continue
            spec_field = frontmatter.get("spec")
            if not spec_field:
                continue
            candidate = (repo / str(spec_field)).resolve()
            try:
                candidate.relative_to(resolved_dir)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate

    candidates = [
        p
        for p in spec_dir.glob("*.md")
        if p.name not in AUX_SPEC_FILENAMES
        and not p.name.endswith("--tasks.md")
        and not p.name.endswith("--technical-plan.md")
    ]
    if len(candidates) == 1:
        return candidates[0]

    spec_md = spec_dir / "spec.md"
    if spec_md.is_file():
        return spec_md

    return None


def _git_show(repo: Path, ref: str, relpath: str) -> str | None:
    """Contents of `relpath` at `ref`, or None if it did not exist there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{relpath}"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def check_spec_coverage(repo: Path, spec_relpath: str, base_ref: str) -> list[str]:
    """Coverage failures for one spec.md's newly-declared identifiers only
    (D2's ratchet). A missing base-ref version reads as an empty declared
    set, so a brand-new spec enforces everything it declares."""
    full_path = repo / spec_relpath
    if not full_path.is_file():
        return []

    declared_now = discover_declared_identifiers(
        full_path.read_text(encoding="utf-8", errors="replace")
    )
    base_text = _git_show(repo, base_ref, spec_relpath)
    declared_base = discover_declared_identifiers(base_text) if base_text is not None else set()
    newly_declared = declared_now - declared_base

    covered = collect_task_references(full_path.parent)
    uncovered = sorted(newly_declared - covered)

    return [
        f"{spec_relpath}: newly-declared identifier {identifier} has no task coverage "
        "(reqs/ac-mapping/imp-requirements)"
        for identifier in uncovered
    ]


def check_changed_specs(repo: Path, changed_paths: list[str], base_ref: str) -> list[str]:
    """Return failure messages across every changed spec document under
    docs/specs/*/, enforcing only identifiers newly declared relative to
    base_ref. The spec document's filename is resolved per spec directory
    (see `resolve_spec_document`) rather than assumed to be `spec.md`."""
    failures: list[str] = []
    for relpath in changed_paths:
        match = re.match(r"^docs/specs/([^/]+)/", relpath)
        if not match:
            continue
        spec_dir = repo / "docs" / "specs" / match.group(1)
        spec_file = resolve_spec_document(repo, spec_dir)
        if spec_file is None or str(spec_file.relative_to(repo)) != relpath:
            continue
        failures.extend(check_spec_coverage(repo, relpath, base_ref))
    return failures


def audit_all_specs(repo: Path) -> list[str]:
    """Repo-wide enumeration of every uncovered declared identifier,
    including pre-existing gaps the ratchet never enforces. Opt-in only
    (D5) — never called from the blocking gate path."""
    specs_root = repo / "docs" / "specs"
    if not specs_root.is_dir():
        return []
    failures: list[str] = []
    for spec_dir in sorted(d for d in specs_root.iterdir() if d.is_dir()):
        spec_file = resolve_spec_document(repo, spec_dir)
        if spec_file is None:
            continue
        declared = discover_declared_identifiers(
            spec_file.read_text(encoding="utf-8", errors="replace")
        )
        covered = collect_task_references(spec_dir)
        uncovered = sorted(declared - covered)
        relpath = str(spec_file.relative_to(repo))
        failures.extend(
            f"{relpath}: uncovered identifier {identifier}" for identifier in uncovered
        )
    return failures


def _candidate_base_refs(configured: str | None) -> tuple[str, ...]:
    return (f"origin/{configured}", configured) if configured else CANDIDATE_BASE_REFS


def _resolve_base_ref(repo: Path, configured: str | None) -> str | None:
    candidates = _candidate_base_refs(configured)
    for ref in candidates:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=str(repo), capture_output=True, text=True,
        )
        if result.returncode == 0:
            return ref
    return None


def _changed_paths_via_git(repo: Path, base_ref: str) -> list[str]:
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
        "--audit", action="store_true",
        help="repo-wide enumeration of every uncovered identifier, including pre-existing "
             "gaps; opt-in only, never part of the blocking gate",
    )
    args = parser.parse_args()

    repo = Path(args.repo).resolve()

    if args.audit:
        failures = audit_all_specs(repo)
        if failures:
            print(f"AUDIT: {len(failures)} uncovered identifier(s) across docs/specs/")
            for failure in failures:
                print(f"  - {failure}")
            return 0
        print("req/AC coverage audit: no uncovered identifiers found.")
        return 0

    base_ref = _resolve_base_ref(repo, args.base_branch)
    if base_ref is None:
        tried = _candidate_base_refs(args.base_branch)
        print(
            "req/AC coverage guard: SKIP — no resolvable base ref (tried "
            f"{', '.join(tried)}); cannot determine newly-declared identifiers."
        )
        return 0

    changed = _changed_paths_via_git(repo, base_ref)
    failures = check_changed_specs(repo, changed, base_ref)

    if failures:
        print(f"FAIL: {len(failures)} req/AC coverage issue(s) in changed spec files")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("req/AC coverage guard: no uncovered newly-declared identifiers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
