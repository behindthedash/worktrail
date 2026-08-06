#!/usr/bin/env python3
"""Post-hoc `go:risk-*` PR label correction, shared across every headless
one-shot spawn -- not just drain.py's queue-drain loop.

A spawned agent process (`claude -p` / `codex exec` / `opencode run`) issues
its own `gh pr create` -- a raw subprocess call, never reachable by the
Claude Code PreToolUse label-enforcement hook (behindthedash/devops#71):
Codex/OpenCode have no equivalent hook mechanism at all, and even a headless
`claude -p` session is not guaranteed to load the interactive hook config.
If that PR skips Phase 8's labeling step, `automerge_eligibility.sh`'s
fail-closed check (worktrail PR #107) stalls a PR that should have been
eligible -- see docs/specs/research/classify-gate-enforcement-audit.md and
docs/specs/research/go-dispatch-one-shot-pr-label-gap.md.

Originally defined only in drain/drain.py (worktrail PR #128), which applies
it after its own spawned one-shot exits. Extracted here so go's own Phase 7
headless-dispatch poll-exit path (router/poll_run.py) and the CLI entrypoint
below (invoked from sdd-workflow's own Phase 8, covering interactive Claude
and Codex in-session dispatch, which never go through poll_run.py at all)
can apply the identical correction.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .run_record import _load as load_run_record


def _current_pr_labels(repo: str, pr_url: str) -> Optional[List[str]]:
    """Live label names on a PR, or None if the `gh` call fails or is
    unparseable (never guess from stale/absent data)."""
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "labels"],
        capture_output=True, text=True, cwd=repo, timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return [label.get("name", "") for label in data.get("labels", [])]


def ensure_pr_risk_label(repo: Optional[str], pr_url: Optional[str],
                         risk_level: Optional[str]) -> Optional[str]:
    """Add a go:risk-<level> label to a PR that carries none.

    This is a minimal, safe correction, not a re-run of `automerge_eligible()`:
    the run record does not persist `gates`, so full eligibility can't be
    reconstructed here. It only ADDS `go:risk-<risk_level>` when the PR
    carries NO go:risk-* label at all, using the risk already recorded at
    Phase 6 start; it never touches go:no-automerge, which an agent may have
    legitimately added itself and must never be silently removed.
    """
    if not repo or not pr_url or not risk_level:
        return None
    labels = _current_pr_labels(repo, pr_url)
    if labels is None or any(label.startswith("go:risk-") for label in labels):
        return None
    new_label = f"go:risk-{risk_level}"
    result = subprocess.run(
        ["gh", "pr", "edit", pr_url, "--add-label", new_label],
        capture_output=True, text=True, cwd=repo, timeout=30,
    )
    return new_label if result.returncode == 0 else None


def main(argv=None) -> int:
    """CLI entrypoint: apply the correction using a run record's own
    `repository` / `pull_request` / `risk_level` fields -- the same fields
    Phase 6 already recorded, no new plumbing required.

    Usage: worktrail-ensure-pr-label --run /path/to/run.yaml

    Intended for sdd-workflow's own Phase 8, immediately after
    `run_record.py finish --pr <url> ...`, so interactive and Codex
    in-session dispatch (which never spawn a subprocess for poll_run.py to
    observe) get the same correction poll_run.py applies for headless
    Claude/OpenCode workers. A no-op (prints `{"applied": null}`) when there
    is no PR, no recorded risk level, or the PR already carries a
    go:risk-* label.
    """
    parser = argparse.ArgumentParser(
        description="Apply the go:risk-* PR label post-hoc correction from a run record.")
    parser.add_argument("--run", required=True, help="Path to run record YAML file")
    args = parser.parse_args(argv)
    record = load_run_record(Path(args.run))
    pr_url = record.get("pull_request")
    applied = (ensure_pr_risk_label(record.get("repository"), pr_url, record.get("risk_level"))
               if pr_url else None)
    print(json.dumps({"applied": applied}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
