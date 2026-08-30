#!/usr/bin/env python3
"""spec_sync_sweep_brief.py — file one Drift Brief per repo (REQ-010..014).

A repo's captured spec-sync drift findings (one or more, across one or more
specs) are filed as exactly one Drift Brief, never one per affected spec
(REQ-010). The brief follows the handoff skill's existing brief document
format verbatim (`references/handoff-template.md`), adding only the
`drift-source: spec-sync-sweep` marker field, and its body lists every
finding's `spec` identifier and `reason` (REQ-011).

Filing is a pure write into the existing deferred-work queue
(`queue_base/queue/`) -- no new surfacing channel, and no git operation
(commit, branch, `gh pr create`) is ever performed against the checked repo
or any other repo (REQ-012, REQ-013, REQ-014). The written file is validated
via `brief_frontmatter.validate_brief()` before this function returns,
matching `work_queue.py`'s own pre-success validation convention (see
`contracts/drift-brief.event.md`).
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from ..shared.brief_frontmatter import validate_brief

DRIFT_SOURCE = "spec-sync-sweep"


def _slug(repo: Path) -> str:
    raw = repo.name or "repo"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "repo"


def _brief_id(repo: Path) -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005
    return f"{timestamp}-spec-sync-drift-{_slug(repo)}"


def _render(repo: Path, findings: list[dict[str, str]], brief_id: str) -> str:
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    spec_count = len({f.get("spec", "") for f in findings})
    focus = (
        f"Spec-sync drift detected in {repo} "
        f"({len(findings)} finding(s) across {spec_count} spec(s))"
    )

    lines = [
        "---",
        f"id: {brief_id}",
        f"created: {created}",
        f"focus: {focus}",
        f"repo: {repo}",
        "remote: null",
        "status: queued",
        f"drift-source: {DRIFT_SOURCE}",
        "---",
        "",
        "## Focus",
        "",
        focus,
        "",
        "## Findings",
        "",
    ]
    for finding in findings:
        lines.append(f"- `{finding.get('spec', '')}`: {finding.get('reason', '')}")
    lines.append("")
    return "\n".join(lines)


def file_drift_brief(
    repo: Path, findings: list[dict[str, str]], queue_base: Path
) -> Path:
    """Write exactly one Drift Brief for `repo` into `queue_base/queue/`.

    Regardless of how many entries `findings` contains (or how many
    distinct `spec` values they span), exactly one new `.md` file is
    created. The file's `repo` frontmatter equals `str(repo)`, and its body
    lists every finding's `spec` identifier and `reason`. The written brief
    is re-read and validated via `brief_frontmatter.validate_brief()`
    (required=`("id", "status", "focus")`) before this function returns;
    a `ValueError` is raised if validation fails. This function performs no
    git operation against `repo` or any other repo.
    """
    queue_dir = queue_base / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    brief_id = _brief_id(repo)
    path = queue_dir / f"{brief_id}.md"
    path.write_text(_render(repo, findings, brief_id), encoding="utf-8")

    ok, reason = validate_brief(path, required=("id", "status", "focus"))
    if not ok:
        raise ValueError(f"written Drift Brief failed validation: {reason}")

    return path
