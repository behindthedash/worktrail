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
Made repository-owned so every Worktrail consumer can run the same check
without depending on a separate authoring plugin.

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
    Ready to Implement, Planned, Proposed, In Review, In Progress), and must
    not be entirely missing. Anything else (including legitimate
    non-standard values like "Backfill", "Shipped", or "Complete") passes --
    this is a disallow-list, not an allow-list, specifically so backfill
    specs and project-specific status wording are not flagged (verified
    against a fleet-wide survey of real Status header values, 2026-08-13; a
    naive allow-list of e.g. {Implemented, Backfill} would false-positive on
    those and others). A missing header is always flagged once tasks are
    terminal -- unlike an unrecognized-but-present value, there is no
    legitimate reason for a fully-done spec to carry no Status line at all
    (devops PR #184, spec 004-governance-automation).

Both checks are gated so they only fire once a spec's own tasks show it is
fully done; specs with any task still pending/in_progress/implemented/reviewed
are treated as in-progress work and skipped entirely.

  Check C -- files: entries not git-tracked (opt-in via --repo/`repo=`)
    For each TASK-*.md with frontmatter status: completed and kind: impl (the
    default kind), every `files:` entry that looks repo-relative (does not
    start with `~` or `/` and contains no whitespace -- real fleet `files:`
    data also carries `~/bin` deployment paths, `~/.gitnexus` artifact paths,
    and plain non-file descriptions like "crontab (user-level)", none of
    which this check can verify) must be git-tracked at the given repo's
    current index. This check is skipped entirely unless a repo root is
    supplied -- it has no meaning without one.

    Two verified-non-drift shapes recur fleet-wide and previously had no way
    to be silenced: (a) a task's own success-criteria/title says the file was
    *removed* by that task or a later task in the same spec (working as
    designed, e.g. a "remove legacy X" spec), and (b) the file legitimately
    lives in a *different* repo (e.g. a package-extraction spec whose task
    `files:` point at the extracted package's own repo). Rather than teach
    Check C to infer either case automatically -- inferring "removed on
    purpose" from prose is exactly the kind of guess check_spec_sync.py exists
    to avoid, and inferring "lives elsewhere" would need a registry of every
    consuming repo's other repos -- a task can opt a specific entry out
    explicitly via the sibling frontmatter field `files-sync-exempt`, a list
    of `files:` values this check must not verify. An entry not also present
    in `files:` is inert (nothing to exempt), never an error, since a stale
    exemption naming a `files:` entry that was itself edited/removed since is
    not worth failing the gate over. Confirmed against gracefully-giving-back
    spec 20260817-060013-spec-sync-drift (32 findings across specs
    015/018/026/027, all either case (a) or (b)).

--fix rewrites a spec's Status header (Check B's STALE_PARENT_STATUSES case
only) to "Implemented" in place. Check A drift and a missing Status header
are never auto-fixed -- there is no single correct value to synthesize for
either.

Exit code: 0 if no spec fails any check, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from worktrail.taskformats.devkit.schema import read_task_file

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
    "ready to implement",
    "planned",
    "proposed",
    "in review",
    "in progress",
}

# Filenames excluded when looking for the parent-spec markdown file at the
# top level of a spec directory. `user-request.md` is the devkit-format
# verbatim capture artifact that sits beside spec.md in every devkit spec dir;
# since find_parent_spec() takes the lexicographically last candidate, leaving
# it in shadows spec.md and flags a phantom missing-Status drift fleet-wide.
# `brainstorming-notes.md` is the same shadowing hazard: it carries no
# Status header by design and sorts after a dated spec filename (e.g.
# `2026-07-24--foo.md`, since digits sort before lowercase letters in
# ASCII) -- confirmed live against behindthedash spec
# 001-release-notes-self-audit, which false-positived a "no Status header"
# drift on every PR in the repo because of exactly this gap. `e2e-verification-
# notes.md` is the same shadowing hazard again: no Status header by design,
# and its name also sorts after a dated spec filename -- confirmed live
# against gracefully-giving-back spec 027-feedback-capture-package, whose
# real parent spec (2026-07-13--feedback-capture-package.md, Status:
# Implemented) was shadowed by this file on every future PR.
AUX_FILENAMES = {
    "data-model.md",
    "decision-log.md",
    "traceability-matrix.md",
    "technical-plan.md",
    "user-request.md",
    "brainstorming-notes.md",
    "e2e-verification-notes.md",
}


def find_task_statuses(tasks_dir: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for tf in sorted(tasks_dir.glob("TASK-*.md")):
        text = tf.read_text(encoding="utf-8", errors="replace")
        fm_id = re.search(r"^id:\s*(\S+)", text, re.MULTILINE)
        fm_status = re.search(r"^status:\s*(\S+)", text, re.MULTILINE)
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
        if p.name not in AUX_FILENAMES
        and not p.name.endswith("--tasks.md")
        and not p.name.endswith("--technical-plan.md")
    ]
    if not candidates:
        return None
    # Prefer candidates that actually carry a Status header: any sibling
    # auxiliary document without one (an inventory, a notes file, ...) cannot
    # be the parent spec, whatever its filename sorts as. AUX_FILENAMES only
    # covers the known names; datalena spec 099-recursive-organization-model
    # (2026-08-22) was shadowed by `org-units-dependency-inventory.md`, a
    # fourth instance of the same hazard. Fall back to every candidate only
    # when none has a header, so a genuinely missing Status header is still
    # flagged by Check B.
    with_status = [p for p in candidates if parent_spec_status(p) is not None]
    pool = with_status or candidates
    # Multiple dated revisions of the same spec (e.g. a later rewrite) ->
    # the lexicographically latest filename is the current one (date-prefixed
    # naming sorts chronologically).
    return max(pool)


def parent_spec_status(parent_spec: Path) -> str | None:
    text = parent_spec.read_text(encoding="utf-8", errors="replace")
    # Accept both "**Status**: X" and "**Status:** X" (colon inside vs.
    # outside the closing bold markers) -- both are equally common across
    # consuming repos' spec corpora.
    m = re.search(r"^\*\*Status(?:\*\*:|:\*\*)\s*(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _rewrite_parent_status(parent_spec: Path, new_status: str) -> bool:
    """Rewrite the parent spec's Status header value to `new_status` in place,
    preserving whichever of the "**Status**:"/"**Status:**" conventions the
    file already uses. Returns True if the file changed."""
    text = parent_spec.read_text(encoding="utf-8", errors="replace")
    new_text, n = re.subn(
        r"(^\*\*Status(?:\*\*:|:\*\*)\s*).+?\s*$",
        lambda m: m.group(1) + new_status,
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n and new_text != text:
        parent_spec.write_text(new_text, encoding="utf-8")
        return True
    return False


def _git_tracked(repo: Path, paths: list[str]) -> set[str]:
    """The subset of `paths` git tracks at `repo`'s current index. One batched
    `git ls-files` call rather than one subprocess per path (mirrors
    dashboard.py's `_git_tracked`). On any git failure, returns `paths` itself
    -- "cannot confirm tracking" is treated conservatively as tracked, so a
    transient git problem never manufactures a false files-tracked failure."""
    if not paths:
        return set()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--"] + paths,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return set(paths)
    if result.returncode != 0:
        return set(paths)
    return {p for p in result.stdout.split("\0") if p}


def _entry_is_tracked(entry: str, tracked: set[str]) -> bool:
    """Whether `entry` counts as git-tracked given `tracked`, the set of file
    paths `_git_tracked` reported. A plain file entry must appear verbatim.
    A directory-style entry (trailing slash) counts as tracked when at least
    one reported path falls under it -- `git ls-files` expands a directory
    pathspec into the individual file paths beneath it and never echoes back
    the literal directory string itself, so an exact-match check against a
    directory entry always misses even when its contents are fully tracked."""
    if entry in tracked:
        return True
    if entry.endswith("/"):
        return any(p.startswith(entry) for p in tracked)
    return False


def _looks_repo_relative(entry: str) -> bool:
    """True for `files:` entries this check can verify: repo-relative source
    paths. False for `~`-prefixed deployment paths, absolute paths, and
    plain non-file descriptions (e.g. "crontab (user-level)") -- real fleet
    `files:` data carries all of these, and naively checking every entry
    against git would false-positive on them fleet-wide."""
    return not (entry.startswith(("~", "/")) or any(ch.isspace() for ch in entry))


def check_spec(spec_dir: Path, repo: Path | None = None) -> list[str]:
    """Return a list of failure messages for this spec (empty = pass/skip).

    `repo` is optional and enables Check C (files-tracked): when omitted,
    Check C is skipped entirely, matching prior behavior exactly."""
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
        table = parse_summary_table(
            summary_path.read_text(encoding="utf-8", errors="replace")
        )
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
            terminal_summary = ", ".join(sorted(set(task_statuses.values())))
            if status is None:
                failures.append(
                    f"{parent_spec.name}: all tasks are terminal ({terminal_summary}) "
                    f"but parent spec has no Status header"
                )
            elif status.strip().lower() in STALE_PARENT_STATUSES:
                failures.append(
                    f"{parent_spec.name}: all tasks are terminal ({terminal_summary}) "
                    f"but parent spec Status is still '{status}'"
                )

    # Check C: files: entries for completed impl tasks not git-tracked on `repo`.
    if repo is not None:
        candidates: list[tuple[str, str]] = []
        for tf in sorted(tasks_dir.glob("TASK-*.md")):
            frontmatter, error, _body = read_task_file(tf)
            if error or not isinstance(frontmatter, dict):
                continue
            if frontmatter.get("status") != "completed":
                continue
            if frontmatter.get("kind", "impl") != "impl":
                continue
            exempt = {
                e
                for e in (frontmatter.get("files-sync-exempt") or [])
                if isinstance(e, str)
            }
            for entry in frontmatter.get("files") or []:
                if (
                    isinstance(entry, str)
                    and _looks_repo_relative(entry)
                    and entry not in exempt
                ):
                    candidates.append((tf.name, entry))
        if candidates:
            tracked = _git_tracked(repo, [entry for _, entry in candidates])
            for tf_name, entry in candidates:
                if not _entry_is_tracked(entry, tracked):
                    failures.append(
                        f"{tf_name}: files: entry '{entry}' is not git-tracked on {repo}"
                    )

    return failures


def fix_spec(spec_dir: Path) -> list[str]:
    """Auto-fix mode for Check B's STALE_PARENT_STATUSES case only: when every
    task is terminal and the parent spec's Status header is a disallow-listed
    pre-implementation value, rewrite it to 'Implemented' in place. Returns
    messages describing what was fixed (empty if nothing was fixed).

    Never touches Check A (task-plan summary drift) or Check B's missing-header
    case -- neither has a single correct value this function can synthesize,
    so both stay report-only even under --fix."""
    tasks_dir = spec_dir / "tasks"
    if not tasks_dir.is_dir():
        return []

    task_statuses = find_task_statuses(tasks_dir)
    if not task_statuses or not all(
        s in TERMINAL_STATUSES for s in task_statuses.values()
    ):
        return []

    parent_spec = find_parent_spec(spec_dir)
    if parent_spec is None:
        return []

    status = parent_spec_status(parent_spec)
    if status is None or status.strip().lower() not in STALE_PARENT_STATUSES:
        return []

    if _rewrite_parent_status(parent_spec, "Implemented"):
        return [
            f"{parent_spec.name}: Status header updated '{status}' -> 'Implemented'"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--specs-root",
        default="docs/specs",
        help="Path to the docs/specs directory (default: docs/specs, relative to cwd)",
    )
    parser.add_argument(
        "--spec",
        default=None,
        help="Check only this spec folder name (e.g. 026-authenticated-feedback-capture)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Repo root to verify files: entries are git-tracked against (enables Check C). "
        "Omit to skip Check C entirely.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Rewrite each spec's stale parent Status header to 'Implemented' (Check B's "
        "STALE_PARENT_STATUSES case only). Check A drift and a missing Status header are "
        "never auto-fixed and still reported afterward.",
    )
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
            print(
                f"error: spec not found under {specs_root}: {args.spec}",
                file=sys.stderr,
            )
            return 2

    repo = Path(args.repo) if args.repo else None

    total_fixed = 0
    total_failures = 0
    checked = 0
    for spec_dir in spec_dirs:
        if args.fix:
            for msg in fix_spec(spec_dir):
                print(f"FIXED {spec_dir.name}: {msg}")
                total_fixed += 1
        failures = check_spec(spec_dir, repo=repo)
        if failures:
            checked += 1
            total_failures += len(failures)
            print(f"FAIL {spec_dir.name}")
            for f in failures:
                print(f"  - {f}")
        # PASS/SKIP specs are silent by default to keep output focused on
        # actionable drift; nothing else to report.

    if total_fixed:
        print(f"\n{total_fixed} spec(s) auto-fixed.")

    if total_failures:
        print(
            f"\n{total_failures} drift issue(s) across {checked} spec(s). Run worktrail-check-spec-sync --fix on the affected spec(s) to fix what's fixable, or fix the rest by hand."
        )
        return 1

    print(f"spec sync guard: no drift detected across {len(spec_dirs)} spec(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
