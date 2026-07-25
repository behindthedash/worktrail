#!/usr/bin/env python3
"""Spec sync drift guard.

Shared SDD tooling invoked by pre_pr_gate.py against any consuming repo's
docs/specs/ tree (see the caller for exact wiring). Originated in
gracefully-giving-back to catch one specific staleness pattern seen after its
PR #546 (feature shipped) merged without a follow-up sync: every TASK-*.md
under a spec's tasks/ directory reported status: completed, but the spec's
own task-plan summary table and parent-spec Status header still described a
pre-implementation state (see PR #553, the docs-only fix, spec 026's
decision-log.md, and handoff brief 20260706-201500-ggb-spec-sync-drift-guard).
Ported into developer-kit (handoff brief
20260707-203826-generalize-spec-sync-guard-to-developer-kit) so every repo
consuming the developer-kit-specs plugin inherits the same check.

Scope (deliberately narrow):

  Check A -- task-plan summary drift
    For specs whose "*--tasks.md" summary uses the CURRENT template (a
    markdown table with a "Task"/"Task ID" column and a "Status" column whose
    values are drawn from the task frontmatter status vocabulary), every
    listed task's summary status must equal that task's TASK-*.md frontmatter
    status. Older specs use a different "Task Index" table (checkbox-style
    "[ ]"/"[x]" Status column) that was never meant to mirror the lifecycle
    vocabulary -- those are recognized and skipped, not flagged, so this
    check does not create a wall of false positives against pre-existing
    specs that predate the current template.

  Check B -- parent-spec Status header drift
    Once every task under a spec is terminal (completed/superseded/optional),
    the parent spec's "**Status**:" header must not still read one of a small
    set of known pre-implementation values (Draft, Ready for Implementation,
    Planned, Proposed, In Review, In Progress). Anything else (including
    legitimate non-standard values like "Backfill") passes -- this is a
    disallow-list, not an allow-list, specifically so backfill specs and
    project-specific status wording are not flagged.

Both checks are gated so they only fire once a spec's own tasks show it is
fully done; specs with any task still pending/in_progress/implemented/reviewed
are treated as in-progress work and skipped entirely.

Exit code: 0 if no spec fails either check, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TASK_STATUS_VOCAB = {
    "pending",
    "in_progress",
    "implemented",
    "reviewed",
    "completed",
    "optional",
    "blocked",
    "escalated",
    "superseded",
}
TERMINAL_STATUSES = {"completed", "superseded", "optional"}

# Parent-spec Status header values that mean "not actually done yet". A
# disallow-list (not an allow-list) so project-specific/backfill status
# wording (e.g. "Backfill") is never flagged.
STALE_PARENT_STATUSES = {
    "draft",
    "ready for implementation",
    "planned",
    "proposed",
    "in review",
    "in progress",
}

# Filenames excluded when looking for the parent-spec markdown file at the
# top level of a spec directory.
AUX_FILENAMES = {"data-model.md", "decision-log.md", "traceability-matrix.md", "technical-plan.md"}


def find_task_statuses(tasks_dir: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for tf in sorted(tasks_dir.glob("TASK-*.md")):
        text = tf.read_text(encoding="utf-8", errors="replace")
        fm_id = re.search(r"^id:\s*(\S+)", text, re.M)
        fm_status = re.search(r"^status:\s*(\S+)", text, re.M)
        if fm_id and fm_status:
            statuses[fm_id.group(1)] = fm_status.group(1).strip()
    return statuses


def parse_summary_table(text: str) -> dict[str, str] | None:
    """Return {TASK-id: status} from the first table with Task + Status
    columns, or None if no such table is found."""
    lines = text.splitlines()
    header_idx = None
    cols: list[str] | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        lowered = stripped.lower()
        if "status" not in lowered or "task" not in lowered:
            continue
        cells = [c.strip().lower() for c in stripped.strip("|").split("|")]
        if "status" in cells and ("task" in cells or "task id" in cells):
            header_idx = i
            cols = cells
            break
    if header_idx is None or cols is None:
        return None

    status_col = cols.index("status")
    task_col = cols.index("task") if "task" in cols else cols.index("task id")

    result: dict[str, str] = {}
    for line in lines[header_idx + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if stripped == "":
                continue
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) <= max(status_col, task_col):
            continue
        task_match = re.search(r"TASK-\d+", cells[task_col])
        if not task_match:
            continue
        result[task_match.group(0)] = cells[status_col].strip().lower()
    return result


def find_parent_spec(spec_dir: Path) -> Path | None:
    candidates = [
        p
        for p in spec_dir.glob("*.md")
        if p.name not in AUX_FILENAMES and not p.name.endswith("--tasks.md") and not p.name.endswith("--technical-plan.md")
    ]
    if not candidates:
        return None
    # Multiple dated revisions of the same spec (e.g. a later rewrite) ->
    # the lexicographically latest filename is the current one (date-prefixed
    # naming sorts chronologically).
    return sorted(candidates)[-1]


def parent_spec_status(parent_spec: Path) -> str | None:
    text = parent_spec.read_text(encoding="utf-8", errors="replace")
    # Accept both "**Status**: X" and "**Status:** X" (colon inside vs.
    # outside the closing bold markers) -- both are equally common across
    # consuming repos' spec corpora.
    m = re.search(r"^\*\*Status(?:\*\*:|:\*\*)\s*(.+?)\s*$", text, re.M)
    return m.group(1).strip() if m else None


def check_spec(spec_dir: Path) -> list[str]:
    """Return a list of failure messages for this spec (empty = pass/skip)."""
    tasks_dir = spec_dir / "tasks"
    if not tasks_dir.is_dir():
        return []

    task_statuses = find_task_statuses(tasks_dir)
    if not task_statuses:
        return []

    all_terminal = all(s in TERMINAL_STATUSES for s in task_statuses.values())
    failures: list[str] = []

    # Check A: task-plan summary vs task frontmatter.
    summary_candidates = sorted(spec_dir.glob("*--tasks.md"))
    if summary_candidates:
        summary_path = summary_candidates[-1]
        table = parse_summary_table(summary_path.read_text(encoding="utf-8", errors="replace"))
        if table is not None and all(v in TASK_STATUS_VOCAB for v in table.values()):
            for task_id, fm_status in task_statuses.items():
                table_status = table.get(task_id)
                if table_status is not None and table_status != fm_status:
                    failures.append(
                        f"{summary_path.name}: {task_id} shows status '{table_status}' "
                        f"but tasks/{task_id}.md frontmatter says '{fm_status}'"
                    )

    # Check B: parent-spec Status header vs completeness.
    if all_terminal:
        parent_spec = find_parent_spec(spec_dir)
        if parent_spec is not None:
            status = parent_spec_status(parent_spec)
            if status is not None and status.strip().lower() in STALE_PARENT_STATUSES:
                failures.append(
                    f"{parent_spec.name}: all tasks are terminal ({', '.join(sorted(set(task_statuses.values())))}) "
                    f"but parent spec Status is still '{status}'"
                )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--specs-root",
        default="docs/specs",
        help="Path to the docs/specs directory (default: docs/specs, relative to cwd)",
    )
    parser.add_argument("--spec", default=None, help="Check only this spec folder name (e.g. 026-authenticated-feedback-capture)")
    args = parser.parse_args()

    specs_root = Path(args.specs_root)
    if not specs_root.is_dir():
        print(f"error: specs root not found: {specs_root}", file=sys.stderr)
        return 2

    spec_dirs = sorted(
        d for d in specs_root.iterdir() if d.is_dir() and re.match(r"^\d+-", d.name)
    )
    if args.spec:
        spec_dirs = [d for d in spec_dirs if d.name == args.spec]
        if not spec_dirs:
            print(f"error: spec not found under {specs_root}: {args.spec}", file=sys.stderr)
            return 2

    total_failures = 0
    checked = 0
    for spec_dir in spec_dirs:
        failures = check_spec(spec_dir)
        if failures:
            checked += 1
            total_failures += len(failures)
            print(f"FAIL {spec_dir.name}")
            for f in failures:
                print(f"  - {f}")
        # PASS/SKIP specs are silent by default to keep output focused on
        # actionable drift; nothing else to report.

    if total_failures:
        print(f"\n{total_failures} drift issue(s) across {checked} spec(s). Run /developer-kit-specs:specs.sync on the affected spec(s) to fix.")
        return 1

    print(f"spec sync guard: no drift detected across {len(spec_dirs)} spec(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
