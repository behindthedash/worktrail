"""Vendored copy of devops's canonical `scripts/ci/rulesets/{rulesets_sync.py,requirements.txt}`.

`rulesets_sync.py` guards against a repo's committed `.github/rulesets/*.json`
drifting from what GitHub actually enforces live (see the script's own
docstring for the "why"). worktrail-repo-init scaffolds this pair of files
into a target repo verbatim -- as string constants here rather than a
packaged data file, matching how `repo_init.py` already vendors
`_AUTOMERGE_WORKFLOW` as an inline template rather than reading it off disk,
so the CLI has no runtime dependency on package-data resolution.

Source of truth: behindthedash/devops, `scripts/ci/rulesets/`. Update these
constants by hand if that script changes upstream -- there is no automated
sync back to devops.
"""

from __future__ import annotations

RULESETS_SYNC_PY = '''\
#!/usr/bin/env python3
"""Guard against committed rulesets drifting from what GitHub actually enforces.

`.github/rulesets/*.json` is treated as the source of truth for branch
protection, but GitHub does NOT auto-apply edits to those files — a ruleset
must be pushed to the live repo separately via the rulesets API. Nothing
previously caught the gap between a merged JSON change and the live ruleset
it was supposed to update: datalena's rulesets have needed manual apply before
(the stg promotion gate rollout), and the same gap blocked a bot push in
gracefully-giving-back, where this script originated.

Usage::

    python scripts/ci/rulesets/rulesets_sync.py --check
    python scripts/ci/rulesets/rulesets_sync.py --apply

``--check`` exits non-zero if any committed ruleset differs from its live
counterpart (use as a CI guard); ``--apply`` pushes the committed JSON over
the live ruleset, creating it first if no live ruleset of that name exists yet.
"""
import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional

import requests

RULESETS_DIR = pathlib.Path(".github/rulesets")
API_BASE = "https://api.github.com"


def gh_api(path: str, token: str, method: str = "GET", payload: Optional[dict] = None) -> Any:
    url = f"{API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "devops-rulesets-sync",
    }
    resp = requests.request(method, url, headers=headers, json=payload)
    if resp.status_code >= 300:
        raise RuntimeError(
            f"GitHub API {method} {path} failed: {resp.status_code} {resp.text}"
        )
    return resp.json() if resp.text else {}


def resolve_token(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    import os

    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def resolve_repo(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    import os

    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    out = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True
    )
    url = out.stdout.strip()
    # git@github.com:owner/repo.git or https://github.com/owner/repo.git
    tail = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    return tail


def canon_conditions(cond: Dict[str, Any]) -> Dict[str, Any]:
    ref = cond.get("ref_name", {})
    return {
        "ref_name": {
            "include": sorted(ref.get("include", [])),
            "exclude": sorted(ref.get("exclude", [])),
        }
    }


def canon_bypass_actors(actors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = [
        {
            "actor_id": a.get("actor_id"),
            "actor_type": a.get("actor_type"),
            "bypass_mode": a.get("bypass_mode"),
        }
        for a in actors
    ]
    return sorted(normalized, key=lambda a: (a["actor_type"] or "", a["actor_id"] or 0))


def canon_rule_params(rule_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if rule_type == "required_status_checks":
        return {
            "strict_required_status_checks_policy": params.get(
                "strict_required_status_checks_policy", False
            ),
            "do_not_enforce_on_create": params.get("do_not_enforce_on_create", False),
            "required_status_checks": sorted(
                c.get("context") for c in params.get("required_status_checks", [])
            ),
        }
    if rule_type == "pull_request":
        return {
            "required_approving_review_count": params.get(
                "required_approving_review_count", 0
            ),
            "dismiss_stale_reviews_on_push": params.get(
                "dismiss_stale_reviews_on_push", False
            ),
            "require_code_owner_review": params.get("require_code_owner_review", False),
            "require_last_push_approval": params.get("require_last_push_approval", False),
            "required_review_thread_resolution": params.get(
                "required_review_thread_resolution", False
            ),
            "allowed_merge_methods": sorted(params.get("allowed_merge_methods", [])),
        }
    return params or {}


def canon_rules(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {r["type"]: canon_rule_params(r["type"], r.get("parameters", {})) for r in rules}


def canonicalize(ruleset: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a ruleset (local file or API response) to the writable fields
    that matter for drift comparison, in an order-independent shape."""
    return {
        "name": ruleset.get("name"),
        "target": ruleset.get("target"),
        "enforcement": ruleset.get("enforcement"),
        "conditions": canon_conditions(ruleset.get("conditions", {})),
        "bypass_actors": canon_bypass_actors(ruleset.get("bypass_actors", [])),
        "rules": canon_rules(ruleset.get("rules", [])),
    }


def find_live_ruleset(name: str, live_rulesets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for rs in live_rulesets:
        if rs.get("name") == name:
            return rs
    return None


def load_local_files(rulesets_dir: pathlib.Path) -> List[pathlib.Path]:
    return sorted(rulesets_dir.glob("*.json"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="exit non-zero if any ruleset has drifted"
    )
    mode.add_argument(
        "--apply", action="store_true", help="push committed rulesets over the live ones"
    )
    ap.add_argument("--repo", default=None, help="owner/repo (default: detect from env/git remote)")
    ap.add_argument("--token", default=None, help="GitHub token (default: $GITHUB_TOKEN or `gh auth token`)")
    ap.add_argument("--dir", default=str(RULESETS_DIR), help="directory of committed ruleset JSON files")
    args = ap.parse_args(argv)

    rulesets_dir = pathlib.Path(args.dir)
    files = load_local_files(rulesets_dir)
    if not files:
        print(f"no ruleset files found in {rulesets_dir}")
        return 0

    token = resolve_token(args.token)
    repo = resolve_repo(args.repo)

    live_rulesets = gh_api(f"/repos/{repo}/rulesets", token)

    drifted: List[str] = []
    missing: List[str] = []
    for path in files:
        local = json.loads(path.read_text())
        name = local.get("name") or path.stem
        live = find_live_ruleset(name, live_rulesets)
        if live is None:
            missing.append(name)
            if args.apply:
                body = {k: v for k, v in local.items() if k not in ("id", "_id")}
                gh_api(f"/repos/{repo}/rulesets", token, method="POST", payload=body)
                print(f"created: {name}")
            else:
                print(f"MISSING LIVE: '{name}' has no live ruleset on {repo}", file=sys.stderr)
            continue

        live_full = gh_api(f"/repos/{repo}/rulesets/{live['id']}", token)
        local_canon = canonicalize(local)
        live_canon = canonicalize(live_full)

        if local_canon != live_canon:
            drifted.append(name)
            if args.apply:
                body = {k: v for k, v in local.items() if k not in ("id", "_id")}
                gh_api(f"/repos/{repo}/rulesets/{live['id']}", token, method="PUT", payload=body)
                print(f"synced: {name}")
            else:
                local_json = json.dumps(local_canon, indent=2, sort_keys=True)
                live_json = json.dumps(live_canon, indent=2, sort_keys=True)
                print(f"DRIFT: '{name}' committed != live", file=sys.stderr)
                print(f"  committed: {local_json}", file=sys.stderr)
                print(f"  live:      {live_json}", file=sys.stderr)
        elif args.apply:
            print(f"already in sync: {name}")

    if args.check:
        if drifted or missing:
            names = ", ".join(drifted + [f"{n} (missing live)" for n in missing])
            print(f"DRIFT: {names}", file=sys.stderr)
            print("Run the same command with --apply to realign, or apply the "
                  "ruleset by hand if this is the first time it's being created.",
                  file=sys.stderr)
            return 1
        print(f"in sync: all {len(files)} ruleset file(s) match live")

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

RULESETS_REQUIREMENTS_TXT = """\
requests>=2.34.2
pytest>=8.3.0
"""
