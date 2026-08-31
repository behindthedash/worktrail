#!/usr/bin/env python3
"""spec_sync_sweep_stale_bookkeeping_brief.py — file one Stale Bookkeeping
Drift Brief per repo.

A repo's captured stale-bookkeeping findings (one or more) are filed as
exactly one Drift Brief, never one per finding, mirroring
`spec_sync_sweep_checkbox_brief.py`'s structure. Filing is a pure write into
the existing deferred-work queue (`queue_base/queue/`) -- no git operation
is ever performed against the checked repo or any other repo. The written
file is validated via `brief_frontmatter.validate_brief()` before this
function returns.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import yaml

from ..shared.brief_frontmatter import validate_brief

DRIFT_SOURCE = "stale-bookkeeping-sweep"


def _slug(repo: Path) -> str:
    raw = repo.name or "repo"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "repo"


def _brief_id(repo: Path) -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005
    return f"{timestamp}-stale-bookkeeping-{_slug(repo)}"


def _render(repo: Path, findings: list[dict[str, Any]], brief_id: str) -> str:
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    focus = (
        f"Stale bookkeeping drift detected in {_slug(repo)} "
        f"across {len(findings)} finding(s)"
    )

    drift_findings = [
        {
            "format": finding.get("format", ""),
            "spec_id": finding.get("spec_id", ""),
            "task_id": finding.get("task_id", ""),
            "next_action": finding.get("next_action", ""),
            "files": finding.get("files", []),
        }
        for finding in findings
    ]
    findings_yaml = yaml.safe_dump(
        {"drift-findings": drift_findings}, sort_keys=False, default_flow_style=False
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
    for finding in findings:
        lines.append(
            f"- **{finding.get('spec_id', '')}** / {finding.get('task_id', '')}"
            f" ({finding.get('format', '')}): {finding.get('next_action', '')}"
        )
    lines.append("")
    return "\n".join(lines)


def file_stale_bookkeeping_brief(
    repo: Path, findings: list[dict[str, Any]], queue_base: Path
) -> Path:
    """Write exactly one Stale Bookkeeping Drift Brief for `repo` into
    `queue_base/queue/`.

    Regardless of how many entries `findings` contains, exactly one new
    `.md` file is created. The file's `repo` frontmatter equals `str(repo)`,
    and its body lists every finding's spec/change id, task id, and
    `next_action`. The written brief is re-read and validated via
    `brief_frontmatter.validate_brief()` (required=`("id", "status",
    "focus")`) before this function returns; a `ValueError` is raised if
    validation fails. This function performs no git operation against
    `repo` or any other repo.
    """
    queue_dir = queue_base / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    brief_id = _brief_id(repo)
    path = queue_dir / f"{brief_id}.md"
    path.write_text(_render(repo, findings, brief_id), encoding="utf-8")

    ok, reason = validate_brief(path, required=("id", "status", "focus"))
    if not ok:
        raise ValueError(
            f"written Stale Bookkeeping Drift Brief failed validation: {reason}"
        )

    return path
