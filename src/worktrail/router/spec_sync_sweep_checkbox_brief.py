#!/usr/bin/env python3
"""spec_sync_sweep_checkbox_brief.py — file one Checkbox Drift Brief per repo
(REQ-CHG-007..011).

A repo's captured checkbox-drift hits (one or more, across one or more task
files) are filed as exactly one Checkbox Drift Brief, never one per affected
task file (REQ-CHG-007). The brief follows the handoff skill's existing
brief document format verbatim (`references/handoff-template.md`), the same
way the parent's `spec_sync_sweep_brief.file_drift_brief()` does, adding
only the `drift-source: checkbox-drift-sweep` marker value, and its body
lists every hit's `path`, `unchecked_count`, `total_count`, and `sections`
(REQ-CHG-008).

Filing is a pure write into the existing deferred-work queue
(`queue_base/queue/`) -- no new surfacing channel, and no git operation
(commit, branch, `gh pr create`) is ever performed against the checked repo
or any other repo (REQ-CHG-009, REQ-CHG-010, REQ-CHG-011). The written file
is validated via `brief_frontmatter.validate_brief()` before this function
returns, matching the parent's own `file_drift_brief()` pre-success
validation convention (see `../contracts/checkbox-drift-brief.event.md`).
This is a new, sibling function -- the parent's `file_drift_brief()` is not
modified by this module.
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

from ..shared.brief_frontmatter import validate_brief

DRIFT_SOURCE = "checkbox-drift-sweep"


def _slug(repo: Path) -> str:
    raw = repo.name or "repo"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "repo"


def _brief_id(repo: Path) -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-checkbox-drift-{_slug(repo)}"


def _render(repo: Path, hits: List[Dict[str, Any]], brief_id: str) -> str:
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    focus = (
        f"Checkbox-completion drift detected in {_slug(repo)} "
        f"across {len(hits)} task file(s)"
    )

    findings = [
        {
            "path": hit.get("path", ""),
            "unchecked_count": hit.get("unchecked_count", 0),
            "total_count": hit.get("total_count", 0),
        }
        for hit in hits
    ]
    findings_yaml = yaml.safe_dump(
        {"drift-findings": findings}, sort_keys=False, default_flow_style=False
    ).rstrip("\n")

    lines = [
        "---",
        f"id: {brief_id}",
        f"created: {created}",
        f"focus: {focus}",
        f"repo: {repo}",
        "remote: null",
        "base-branch: null",
        "status: queued",
        "suggested-skills: []",
        f"drift-source: {DRIFT_SOURCE}",
        *findings_yaml.splitlines(),
        "---",
        "",
        "## Focus",
        "",
        f"{focus}:",
        "",
    ]
    for hit in hits:
        sections = hit.get("sections") or []
        sections_text = ", ".join(sections) if sections else "(none)"
        lines.append(
            f"- **{hit.get('path', '')}**: {hit.get('unchecked_count', 0)}/"
            f"{hit.get('total_count', 0)} unchecked [{sections_text}]"
        )
    lines.append("")
    return "\n".join(lines)


def file_checkbox_drift_brief(
    repo: Path, hits: List[Dict[str, Any]], queue_base: Path
) -> Path:
    """Write exactly one Checkbox Drift Brief for `repo` into `queue_base/queue/`.

    Regardless of how many entries `hits` contains (or how many distinct
    task-file paths they span), exactly one new `.md` file is created. The
    file's `repo` frontmatter equals `str(repo)`, and its body lists every
    hit's `path`, `unchecked_count`, `total_count`, and `sections`. The
    written brief is re-read and validated via
    `brief_frontmatter.validate_brief()` (required=`("id", "status",
    "focus")`) before this function returns; a `ValueError` is raised if
    validation fails. This function performs no git operation against
    `repo` or any other repo.
    """
    queue_dir = queue_base / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    brief_id = _brief_id(repo)
    path = queue_dir / f"{brief_id}.md"
    path.write_text(_render(repo, hits, brief_id), encoding="utf-8")

    ok, reason = validate_brief(path, required=("id", "status", "focus"))
    if not ok:
        raise ValueError(f"written Checkbox Drift Brief failed validation: {reason}")

    return path
