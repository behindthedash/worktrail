#!/usr/bin/env python3
"""Queue triage: repo-scoped dedup/staleness evaluation of the work queue."""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Dict, List, Tuple

from ..shared.brief_frontmatter import read_frontmatter, split_frontmatter
from .work_queue import queue_dir

NO_REPO_KEY = "__none__"

_TRIAGE_HEADING_RE = re.compile(r"^##\s+Triage\s+(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)

# Codifies the 2026-07-31 pilot's lessons (see design.md's "Evaluator prompt
# template" decision): repo-fetch-first, a bounded per-brief tool-call budget,
# a memory check before raising a false alarm, and fail-open-to-`keep` on any
# undecidable case. One spawn per repo group, so `{repo}`/`{briefs}` describe
# a whole group, not a single brief. Kept as a module-level constant (not a
# file) so `tests/workqueue/test_queue_triage.py` can assert on it directly,
# matching how `drain.py` keeps `PROMPT` as an importable constant.
EVALUATOR_PROMPT_TEMPLATE = """\
You are triaging work-queue briefs for the repo group `{repo}` for staleness \
and duplication. Evaluate ONLY the briefs listed below; do not scan the queue \
for others.

Briefs in this group:
{briefs}

Step 1 — repo check (do this first, before judging any brief):
Run `gh repo view --json isArchived,name -- {repo}` (skip this step if `{repo}` \
is `{no_repo_key}` — these briefs are cross-cutting and have no target repo). \
If the repo is confirmed archived or renamed away, every brief in this group is \
`stale-close` on that fact alone — no further per-brief evidence is required. \
If the check fails or is inconclusive (network error, ambiguous name, etc.), \
proceed to step 2 for every brief as normal.

Step 2 — per-brief evaluation:
For each brief above, spend at most 3-4 tool calls (e.g. `git log`, `gh pr list \
--search`, `grep`) confirming or refuting the brief's premise. Cite the specific \
PR, commit, or file you found as evidence — a verdict without cited evidence is \
invalid.

Step 3 — memory check before raising an alarm:
Before flagging anything you observe as a live operational concern, check \
{memory_index} for whether it already documents the same state as expected or \
known. If it does, that is not new evidence of staleness or a problem — treat \
it as confirming the brief's premise rather than refuting it.

Step 4 — fail open:
If evidence is inconclusive after steps 1-3, do not guess: verdict `keep` and \
record what you checked and why it was inconclusive as the evidence.

For each brief, output one JSON object with exactly these fields:
{{"brief_id": "...", "verdict": "keep|stale-close|needs-update|duplicate-of", \
"duplicate_of": "<brief-id or null>", "evidence": "<cited PR/commit/file, or \
why inconclusive for a fail-open keep>", "confidence": "high|medium|low"}}
"""


def group_queue_by_repo() -> Dict[str, List[Path]]:
    """Group every brief in `queue_dir()` by its frontmatter `repo:` value.

    A brief with no `repo` field, or a null/empty one, collapses into the
    single `"__none__"` group so callers always have exactly one bucket for
    repo-less briefs instead of needing to special-case `None`.
    """
    groups: Dict[str, List[Path]] = {}
    d = queue_dir()
    if not d.is_dir():
        return groups
    for path in sorted(f for f in d.iterdir() if f.is_file() and f.suffix == ".md"):
        fm = read_frontmatter(path)
        repo = fm.get("repo")
        key = repo.strip() if isinstance(repo, str) and repo.strip() else NO_REPO_KEY
        groups.setdefault(key, []).append(path)
    return groups


def is_recently_triaged(path: Path, within_days: int) -> bool:
    """True if `path`'s most recent ``## Triage <ISO date>`` section is within `within_days`.

    Lenient like the rest of this module's date handling (`work_queue._is_not_yet_due`,
    `_recently_released_info`): an unreadable file, a body with no `## Triage` section, or
    every such section carrying an unparsable date all fall through to False rather than
    raising, since a dedup check that can't confirm recency must not block a brief from
    being evaluated.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    _, body = split_frontmatter(content)

    dates: List[datetime.date] = []
    for raw in _TRIAGE_HEADING_RE.findall(body):
        try:
            dates.append(datetime.date.fromisoformat(raw))
        except ValueError:
            continue
    if not dates:
        return False

    most_recent = max(dates)
    age_days = (datetime.date.today() - most_recent).days
    return age_days <= within_days


def inventory(within_days: int) -> Tuple[Dict[str, List[Path]], List[Path]]:
    """Compose `group_queue_by_repo()` + `is_recently_triaged()` into an evaluation set.

    Briefs whose most recent `## Triage` section falls within `within_days` fail the
    dedup check and are excluded from the returned groups (so 2.x never re-evaluates
    them) but collected into `skipped` for report visibility. A group left empty by
    filtering is dropped entirely rather than kept as an empty bucket.
    """
    skipped: List[Path] = []
    groups: Dict[str, List[Path]] = {}
    for key, paths in group_queue_by_repo().items():
        kept: List[Path] = []
        for path in paths:
            if is_recently_triaged(path, within_days):
                skipped.append(path)
            else:
                kept.append(path)
        if kept:
            groups[key] = kept
    return groups, skipped
