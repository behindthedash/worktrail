#!/usr/bin/env python3
"""automerge_selfcheck.py — cross-repo auto-merge label-gate detector.

`routes.md`'s "Automerge labels" contract makes `go:no-automerge` binding by
relying on each repo's own auto-merge automation
(`.github/workflows/auto-merge.yml` or equivalent) to check the label before
running `gh pr merge --auto` — `pre_pr_gate.py` only *applies* the label, it
never enforces it. Nothing previously verified that a repo's workflow, often
copy-pasted from a sibling, actually implements that check. Verified live
2026-07-30: devops's `auto-merge.yml` merged PR behindthedash/devops#58
within seconds despite the PR carrying `go:no-automerge`, because the
workflow's `gh pr merge --auto` step has no `if:` condition and the file
never references the label at all.

This is a passive detector, not a gate: it flags signals for a human/agent to
judge, matching `policy_selfcheck.py`'s own posture. It never blocks
`pre_pr_gate.py` or the merge gate.

Scope: only `run:` steps that shell out to `gh pr merge ... --auto` are
inspected (the only mechanism every repo in this workspace uses to arm
auto-merge). A composite/marketplace action that arms auto-merge without a
`gh pr merge` shell command would not be seen by this detector.

Signals (see `check_workflow_file`):
  - unconditional-automerge: a step runs `gh pr merge --auto` with no `if:`
    condition on that step, and the workflow file never references
    `go:no-automerge` anywhere — no gate exists at all.
  - unguarded-merge-step: a step runs `gh pr merge --auto` with no `if:`
    condition on that step, but the file *does* reference `go:no-automerge`
    elsewhere (e.g. a sibling eligibility-check step) — the label is checked
    but never wired to the merge step itself.

Usage:
  automerge_selfcheck.py --repo /path/to/repo [--json]
  automerge_selfcheck.py --repos-root ~/projects [--json]   # sweep every repo
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .policy_selfcheck import discover_repo_names

_WORKFLOWS_RELDIR = ".github/workflows"
_MERGE_RUN_RE = re.compile(r"\bgh\s+pr\s+merge\b")
_AUTO_FLAG_RE = re.compile(r"--auto\b")
_NO_AUTOMERGE_RE = re.compile(r"no-automerge", re.IGNORECASE)


def _step_run_text(step: Any) -> str:
    run = step.get("run") if isinstance(step, dict) else None
    return run if isinstance(run, str) else ""


def find_automerge_steps(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every step across all jobs whose `run:` shells out to `gh pr merge --auto`."""
    found: List[Dict[str, Any]] = []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return found
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            text = _step_run_text(step)
            if _MERGE_RUN_RE.search(text) and _AUTO_FLAG_RE.search(text):
                found.append({"job": job_id, "step": step})
    return found


def check_workflow_file(path: Path) -> Dict[str, Any]:
    """Findings for one workflow file. Empty `findings` = clean or not applicable."""
    result: Dict[str, Any] = {"path": str(path), "has_automerge": False, "findings": []}
    try:
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - a malformed/foreign workflow must not break the sweep
        return result
    if not isinstance(doc, dict):
        return result

    merge_steps = find_automerge_steps(doc)
    if not merge_steps:
        return result
    result["has_automerge"] = True

    references_label = bool(_NO_AUTOMERGE_RE.search(text))
    for entry in merge_steps:
        step = entry["step"]
        if isinstance(step, dict) and step.get("if"):
            continue  # gated on some condition -- clean
        name = (step.get("name") if isinstance(step, dict) else None) or entry["job"]
        if references_label:
            signal = "unguarded-merge-step"
            detail = (
                f"job '{entry['job']}' step '{name}' runs gh pr merge --auto with no "
                "if: condition, though go:no-automerge is referenced elsewhere in the file"
            )
        else:
            signal = "unconditional-automerge"
            detail = (
                f"job '{entry['job']}' step '{name}' runs gh pr merge --auto with no "
                "if: condition and the file never references go:no-automerge"
            )
        result["findings"].append({"signal": signal, "detail": detail})
    return result


def check_repo(repo: Path) -> Dict[str, Any]:
    """Findings for every workflow file in one repo. Empty `findings` = clean."""
    repo = Path(repo)
    result: Dict[str, Any] = {
        "repo": repo.name, "path": str(repo), "has_automerge": False,
        "sources": [], "findings": [],
    }
    wf_dir = repo / _WORKFLOWS_RELDIR
    if not wf_dir.is_dir():
        return result
    for wf_path in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        file_result = check_workflow_file(wf_path)
        if not file_result["has_automerge"]:
            continue
        result["has_automerge"] = True
        result["sources"].append(file_result["path"])
        result["findings"].extend(file_result["findings"])
    return result


def sweep(repos_root: Path) -> List[Dict[str, Any]]:
    """check_repo() for every repo under `repos_root` that has an auto-merge workflow."""
    names = discover_repo_names(repos_root)
    results = []
    for name in names:
        r = check_repo(repos_root / name)
        if r["has_automerge"]:
            results.append(r)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="single repo to check")
    p.add_argument("--repos-root", help="sweep every repo under this directory")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not args.repo and not args.repos_root:
        p.error("one of --repo or --repos-root is required")

    if args.repos_root:
        root = Path(args.repos_root).expanduser()
        if args.repo:
            results = [check_repo(Path(args.repo).expanduser())]
        else:
            results = sweep(root)
    else:
        results = [check_repo(Path(args.repo).expanduser())]

    flagged = [r for r in results if r["findings"]]
    if args.json:
        print(json.dumps({"results": results, "flagged": len(flagged)}, indent=2))
    else:
        if not flagged:
            print(f"automerge_selfcheck: {len(results)} repo(s) checked, no unguarded auto-merge signals")
        for r in flagged:
            print(f"{r['repo']}:")
            for f in r["findings"]:
                print(f"  [{f['signal']}] {f['detail']}")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
