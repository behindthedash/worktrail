#!/usr/bin/env python3
"""policy_selfcheck.py — cross-repo copy-paste detector for worktrail-go-policy.yaml.

`docs/specs/worktrail-go-policy.yaml` is a per-repo trust boundary (automerge, pre-PR
gate command, base branch). The documented way to write one is "copy a
sibling repo's file as a template" (see PR #246), which risks a
half-customized file surviving review: a repo's own policy embedding another
repo's absolute paths, venv env vars, or header comment. `policy.py` parses
and validates each key in isolation and cannot see this — the value read is a
perfectly valid string, just one that quietly names the wrong repo.

This is a passive detector, not a gate: it flags signals for a human/agent to
judge, matching `policy.py`'s own `unknown_keys` warning posture. It never
blocks `pre_pr_gate.py` or the merge gate.

Signals (see `check_repo`):
  - header-mismatch: the leading comment block's "policy for <name>" phrase
    names a repo other than the one this file lives in.
  - sibling-path: `pre_pr_cmd`/`integrate_smoke_cmd` embeds an absolute
    `~/projects/<other-repo>/` path fragment.
  - sibling-envvar: those same commands reference a `<OTHER_REPO>_VENV`-style
    shell variable naming a sibling repo.

Usage:
  policy_selfcheck.py --repo /path/to/repo [--repos-root ~/projects] [--json]
  policy_selfcheck.py --repos-root ~/projects [--json]   # sweep every repo
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .policy import policy_file_path

# Commands are read from these two keys only — the only free-text policy
# values that plausibly embed another repo's paths/env vars.
_COMMAND_KEYS = ("pre_pr_cmd", "integrate_smoke_cmd")
_RELATIVE_CD_RE = re.compile(
    r"(?:^|[(&|;])\s*cd\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s;&|)]+))"
)

_HEADER_FOR_RE = re.compile(r"policy\s+for\s+([A-Za-z0-9._-]+)", re.IGNORECASE)
_PROJECTS_PATH_RE = re.compile(r"/projects/([A-Za-z0-9._-]+)/")
_VENV_VAR_RE = re.compile(r"\$\{?([A-Z0-9_]+)_VENV\b")

# Self-referential wording ("policy for this repo") is not a repo name and
# must never be compared against `own` — it would always mismatch.
_HEADER_STOPWORDS = {"this", "the", "our", "your", "my", "that"}


def _norm(name: str) -> str:
    """Normalize a repo/comment name for comparison (case + separators)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _names_match(candidate: str, repo_name: str) -> bool:
    a, b = _norm(candidate), _norm(repo_name)
    # Allow "briankudera" to match "briankudera.com", "reponame-worktrees", etc.
    return a == b or a.startswith(b) or b.startswith(a)


def discover_repo_names(repos_root: Path) -> List[str]:
    """Every immediate subdirectory of `repos_root` that is a git repo."""
    if not repos_root.is_dir():
        return []
    return sorted(
        p.name for p in repos_root.iterdir()
        if p.is_dir() and (p / ".git").exists()
    )


def _header_block(text: str) -> str:
    lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            lines.append(stripped)
            continue
        if stripped == "":
            continue
        break  # first non-comment, non-blank line ends the header
    return "\n".join(lines)


def check_repo(repo: Path, sibling_names: List[str]) -> Dict[str, Any]:
    """Findings for one repo's worktrail-go-policy.yaml. Empty `findings` = clean."""
    repo = Path(repo)
    src = policy_file_path(repo)
    result: Dict[str, Any] = {"repo": repo.name, "path": str(repo),
                              "source": None, "findings": []}
    if not src.is_file():
        return result
    result["source"] = str(src)
    text = src.read_text(encoding="utf-8")
    own = repo.name
    findings = result["findings"]

    header = _header_block(text)
    m = _HEADER_FOR_RE.search(header)
    if m and m.group(1).lower() not in _HEADER_STOPWORDS and not _names_match(m.group(1), own):
        findings.append({
            "signal": "header-mismatch",
            "detail": f"header comment says \"policy for {m.group(1)}\" but this repo is '{own}'",
        })

    others = [s for s in sibling_names if s != own and _norm(s) != _norm(own)]
    seen = set()
    for key in _COMMAND_KEYS:
        m = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
        if not m:
            continue
        value = m.group(1)
        for cd_match in _RELATIVE_CD_RE.finditer(value):
            target = next((part for part in cd_match.groups() if part), "")
            if not target or target.startswith(("/", "$", "~")):
                continue
            dedup_key = ("missing-command-path", key, target)
            if dedup_key in seen or (repo / target).is_dir():
                continue
            seen.add(dedup_key)
            findings.append({
                "signal": "missing-command-path",
                "detail": f"{key} changes into missing repo-relative directory '{target}'",
            })
        for path_match in _PROJECTS_PATH_RE.finditer(value):
            named = path_match.group(1)
            dedup_key = ("sibling-path", key, _norm(named))
            if dedup_key in seen or not any(_names_match(named, sib) for sib in others):
                continue
            seen.add(dedup_key)
            findings.append({
                "signal": "sibling-path",
                "detail": f"{key} embeds path for sibling repo '{named}', not '{own}'",
            })
        for var_match in _VENV_VAR_RE.finditer(value):
            var_repo = var_match.group(1)
            dedup_key = ("sibling-envvar", key, _norm(var_repo))
            if dedup_key in seen or not any(_names_match(var_repo, sib) for sib in others):
                continue
            seen.add(dedup_key)
            findings.append({
                "signal": "sibling-envvar",
                "detail": f"{key} references ${{{var_repo}_VENV}}, naming sibling repo not '{own}'",
            })
    return result


def sweep(repos_root: Path) -> List[Dict[str, Any]]:
    """check_repo() for every repo under `repos_root` that has a policy file."""
    names = discover_repo_names(repos_root)
    results = []
    for name in names:
        r = check_repo(repos_root / name, names)
        if r["source"] is not None:
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
            names = discover_repo_names(root)
            results = [check_repo(Path(args.repo).expanduser(), names)]
        else:
            results = sweep(root)
    else:
        results = [check_repo(Path(args.repo).expanduser(), [])]

    flagged = [r for r in results if r["findings"]]
    if args.json:
        print(json.dumps({"results": results, "flagged": len(flagged)}, indent=2))
    else:
        if not flagged:
            print(f"policy_selfcheck: {len(results)} repo(s) checked, no cross-repo signals")
        for r in flagged:
            print(f"{r['repo']}:")
            for f in r["findings"]:
                print(f"  [{f['signal']}] {f['detail']}")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
