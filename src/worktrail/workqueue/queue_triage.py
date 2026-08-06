#!/usr/bin/env python3
"""Queue triage: repo-scoped dedup/staleness evaluation of the work queue."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..shared.brief_frontmatter import read_frontmatter
from .work_queue import queue_dir

NO_REPO_KEY = "__none__"


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
