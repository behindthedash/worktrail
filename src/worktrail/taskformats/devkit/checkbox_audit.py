"""Fleet-wide audit for TASK-*.md files whose ``status: completed``
frontmatter isn't backed by fully-checked body checkboxes.

``schema.py``'s ``update_status()`` only warns about this drift on the next
Write|Edit of a given task file. Tasks that already drifted into
``status: completed`` with unticked checkboxes before that fix, and that
nobody edits again, never surface the warning. This script scans historical
task files directly so the drift can be found without a manual spec audit.

Reuses ``schema.py``'s ``read_task_file`` / ``_all_checkboxes_checked`` logic
directly (both live in this same package) so the audit and the hook can
never disagree on what counts as drift.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .schema import (
    COMPLETION_AUDIT_SECTIONS,
    _all_checkboxes_checked,
    _extract_sections,
    read_task_file,
)

UNCHECKED_RE = re.compile(r"- \[ \]")
CHECKED_RE = re.compile(r"- \[x\]")
HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)
RECONCILIATION_NOTE_RE = re.compile(r"\s*-\s+Reconciliation note:")


class Hit:
    def __init__(self, path, unchecked_count, total_count, sections):
        self.path = path
        self.unchecked_count = unchecked_count
        self.total_count = total_count
        self.sections = sections


def _is_reconciled(text: str, match_end: int) -> bool:
    """Whether the unchecked box ending at ``match_end`` is immediately followed by
    a line matching the "Reconciliation note:" convention (PR #669) -- meaning it
    was individually verified and deliberately left unchecked with cited evidence,
    not genuine drift.
    """
    next_newline = text.find("\n", match_end)
    if next_newline == -1:
        return False
    line_end = text.find("\n", next_newline + 1)
    next_line = text[next_newline + 1 : line_end if line_end != -1 else len(text)]
    return RECONCILIATION_NOTE_RE.match(next_line) is not None


def _unreconciled_unchecked_matches(text: str) -> list:
    """Unchecked-checkbox matches in ``text``, excluding ones with a Reconciliation note."""
    return [m for m in UNCHECKED_RE.finditer(text) if not _is_reconciled(text, m.end())]


def _unchecked_sections(body: str) -> list:
    """Return the ``##``/``###`` headings that contain >=1 unreconciled unchecked box,
    in order."""
    headings = [(m.start(), m.group(2).strip()) for m in HEADING_RE.finditer(body)]
    sections: list = []
    for m in _unreconciled_unchecked_matches(body):
        pos = m.start()
        heading = "(no heading)"
        for h_start, h_text in headings:
            if h_start <= pos:
                heading = h_text
            else:
                break
        if heading not in sections:
            sections.append(heading)
    return sections


def audit_repo(repo: Path) -> list[Hit]:
    """Scan ``docs/specs/**/tasks/TASK-*.md`` under ``repo`` for status:completed drift.

    The recursive ``**`` covers both spec-owned tasks (docs/specs/<id>/tasks/)
    and change-spec-owned tasks (docs/specs/<id>/changes/<slug>/tasks/,
    TASK-CHG-*.md) — both are real task files subject to the same lifecycle
    hook. Point-in-time snapshots under reviews/ are a different naming shape
    (TASK-*-review.md, not inside a tasks/ dir) and are excluded by the glob.
    """
    hits: list[Hit] = []
    for task_path in sorted(repo.glob("docs/specs/**/tasks/TASK-*.md")):
        frontmatter, error, body = read_task_file(task_path)
        if error or frontmatter is None:
            continue
        if frontmatter.get("status") != "completed":
            continue
        if _all_checkboxes_checked(body, sections=COMPLETION_AUDIT_SECTIONS):
            continue
        scoped_text = _extract_sections(body, COMPLETION_AUDIT_SECTIONS)
        unchecked = len(_unreconciled_unchecked_matches(scoped_text))
        if unchecked == 0:
            continue
        checked = len(CHECKED_RE.findall(scoped_text))
        hits.append(
            Hit(
                path=task_path,
                unchecked_count=unchecked,
                total_count=unchecked + checked,
                sections=_unchecked_sections(scoped_text),
            )
        )
    return hits


def format_report(repo: Path, hits: list) -> str:
    if not hits:
        return f"{repo}: no drift found."
    lines = [f"{repo}: {len(hits)} task file(s) with status:completed drift"]
    for hit in hits:
        rel = hit.path.relative_to(repo)
        sections = ", ".join(hit.sections) if hit.sections else "(none)"
        lines.append(
            f"  - {rel}: {hit.unchecked_count}/{hit.total_count} unchecked [{sections}]"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        required=True,
        help="Repo root to scan (docs/specs/**/tasks/TASK-*.md); repeatable.",
    )
    args = parser.parse_args(argv)

    any_hits = False
    for repo_str in args.repos:
        repo = Path(repo_str).expanduser().resolve()
        hits = audit_repo(repo)
        print(format_report(repo, hits))
        if hits:
            any_hits = True

    return 1 if any_hits else 0


if __name__ == "__main__":
    sys.exit(main())
