"""worktrail-repo-init -- bootstrap or migrate a repo onto the workspace's
repo-standards doctrine (~/rules/CLAUDE.repo.md): an AGENTS.md/CLAUDE.md split,
a dev/prd (or dev/stg/prd) branch model, GitHub rulesets, an OpenSpec scaffold,
and a seeded docs/specs/worktrail-go-policy.yaml.

Two subcommands, mirroring devops's ruleset-fleet-rollout.py's propose/apply
split so branch protection and the default-branch change are never made from
an unmerged PR:

  propose  Write the generated files into --repo, which the caller is expected
           to already have checked out as a clean worktree (this tool does not
           create worktrees, commit, push, or open the PR -- that is the
           calling skill's job, same as every other worktrail CLI). Safe to
           re-run; already-present files are left alone.
  apply    Run once that PR has merged: create `dev` (and `stg` for a 3-branch
           model) from the current default branch's tip SHA, rename the
           current default branch to `prd`, set `dev` as the new GitHub
           default branch, then live-apply and verify the committed rulesets
           (same PUT-then-reverify idiom as ruleset-fleet-rollout.py).

`propose` deliberately never auto-populates `required_status_checks` -- it
reports discovered CI job display names (from `.github/workflows/*.yml`) for
a human to review, since not every job should gate every branch (informational
jobs, matrix jobs, etc.). A repo with no CI gets a ruleset with zero required
checks, not a copy-pasted list that would deadlock every future PR.

Usage:
  worktrail-repo-init propose --repo /path/to/repo [--branch-model 2|3] [--check] [--json]
  worktrail-repo-init apply --repo /path/to/repo [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

OPENSPEC_PACKAGE = "@fission-ai/openspec@latest"


# --------------------------------------------------------------------------
# AGENTS.md / CLAUDE.md split
# --------------------------------------------------------------------------

def split_claude_md(repo: Path) -> Tuple[bool, Optional[str]]:
    """Move CLAUDE.md's real content into AGENTS.md, replacing CLAUDE.md with
    the `@AGENTS.md` import line (the repo-standards doctrine's single-
    source-of-truth pattern). Returns (changed, warning)."""
    claude_md = repo / "CLAUDE.md"
    agents_md = repo / "AGENTS.md"
    if claude_md.is_file() and claude_md.read_text(encoding="utf-8").strip() == "@AGENTS.md":
        return False, None
    if agents_md.is_file():
        return False, (
            "AGENTS.md already exists and CLAUDE.md is not yet '@AGENTS.md' -- "
            "left both alone, resolve by hand")
    if claude_md.is_file():
        agents_md.write_text(claude_md.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        agents_md.write_text(f"# {resolve_repo_display_name(repo)}\n", encoding="utf-8")
    claude_md.write_text("@AGENTS.md\n", encoding="utf-8")
    return True, None


# --------------------------------------------------------------------------
# CI job discovery (reporting only -- never auto-required)
# --------------------------------------------------------------------------

def discover_ci_checks(repo: Path) -> List[str]:
    """Job display names from `.github/workflows/*.yml` -- a
    `required_status_checks` `context` must be the job's `name:` (falling
    back to its id when `name:` is absent, matching GitHub's own default
    display), never the workflow filename."""
    workflows_dir = repo / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []
    checks: List[str] = []
    for wf in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            name = job.get("name") if isinstance(job, dict) else None
            checks.append(name if isinstance(name, str) and name.strip() else job_id)
    return checks


# --------------------------------------------------------------------------
# Ruleset generation
# --------------------------------------------------------------------------

def _pull_request_rule(allowed_merge_methods: List[str]) -> Dict[str, Any]:
    return {
        "type": "pull_request",
        "parameters": {
            "required_approving_review_count": 0,
            "dismiss_stale_reviews_on_push": False,
            "required_reviewers": [],
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_review_thread_resolution": True,
            "allowed_merge_methods": allowed_merge_methods,
        },
    }


def build_ruleset(
    name: str, branch: str, allowed_merge_methods: List[str],
    required_status_checks: List[str], linear_history: bool = False,
) -> Dict[str, Any]:
    rules: List[Dict[str, Any]] = [
        _pull_request_rule(allowed_merge_methods),
        {"type": "non_fast_forward"},
    ]
    if linear_history:
        rules.append({"type": "required_linear_history"})
    rules.append({"type": "deletion"})
    if required_status_checks:
        rules.append({
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": False,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {"context": c} for c in required_status_checks
                ],
            },
        })
    return {
        "name": name,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
        "rules": rules,
    }


def build_ruleset_for_branch(branch: str, branch_model: str) -> Dict[str, Any]:
    """branch_model "2" = dev/prd (GGB pattern); "3" = dev/stg/prd (datalena
    pattern, dev is squash + required_linear_history)."""
    if branch == "dev":
        return build_ruleset(
            "protect-dev", "dev", ["squash"], [], linear_history=(branch_model == "3"))
    if branch == "stg":
        return build_ruleset("protect-stg", "stg", ["merge"], [])
    if branch == "prd":
        return build_ruleset("protect-prd", "prd", ["merge"], [])
    raise ValueError(f"unknown branch {branch!r}")


# --------------------------------------------------------------------------
# docs/specs/worktrail-go-policy.yaml seed
# --------------------------------------------------------------------------

def default_policy_yaml(repo_name: str) -> str:
    return (
        f"# {repo_name} -- worktrail-go policy.\n"
        "# See worktrail's src/worktrail/router/policy.py DEFAULTS for the full\n"
        "# schema. Every key is optional and defaults to a safe, do-nothing value\n"
        "# until this repo opts in explicitly (e.g. pre_pr_cmd for the pre-PR test\n"
        "# gate, automerge for GitHub-native auto-merge eligibility).\n"
    )


# --------------------------------------------------------------------------
# OpenSpec scaffold
# --------------------------------------------------------------------------

def init_openspec(repo: Path) -> Tuple[bool, Optional[str]]:
    """`openspec init --tools none` -- just the openspec/{config.yaml,specs/,
    changes/} scaffold. Deliberately `--tools none`: worktrail's own plugin
    already bundles the OpenSpec Claude Code integration (commands/opsx/*.md,
    skills/openspec-*), lightly edited from upstream's generated output (see
    this repo's own AGENTS.md) -- running `--tools claude` per onboarded repo
    would generate a second, un-vetted copy that conflicts with it. Returns
    (changed, warning)."""
    if (repo / "openspec" / "config.yaml").is_file():
        return False, None
    proc = _run(
        ["npx", "--yes", OPENSPEC_PACKAGE, "init", "--tools", "none",
         "--no-animation", str(repo)],
    )
    if proc.returncode != 0:
        return False, f"openspec init failed: {proc.stderr.strip() or proc.stdout.strip()}"
    return True, None


# --------------------------------------------------------------------------
# gh / git helpers
# --------------------------------------------------------------------------

def _run(cmd: List[str], **kw: Any) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def resolve_gh_repo(repo: Path) -> Optional[str]:
    p = _run(["git", "-C", str(repo), "remote", "get-url", "origin"])
    if p.returncode != 0:
        return None
    url = p.stdout.strip()
    if "github.com" not in url:
        return None
    return url.rstrip("/").removesuffix(".git").split("github.com", 1)[-1].lstrip(":/")


def resolve_repo_display_name(repo: Path) -> str:
    """The repo's own name (from `owner/repo` on `origin`), not the directory
    basename `--repo` was invoked with -- a worktree checkout conventionally
    lives at `<repo>-worktrees/<branch>/`, so `repo.name` there is the branch
    name, not the repo name."""
    gh_repo = resolve_gh_repo(repo)
    if gh_repo and "/" in gh_repo:
        return gh_repo.split("/", 1)[1]
    return repo.name


def _gh_raw(args: List[str]) -> Optional[str]:
    p = _run(["gh", *args])
    if p.returncode != 0:
        return None
    return p.stdout.strip() or None


def current_default_branch(gh_repo: str) -> Optional[str]:
    return _gh_raw(["api", f"repos/{gh_repo}", "-q", ".default_branch"])


def branch_sha(gh_repo: str, branch: str) -> Optional[str]:
    return _gh_raw(["api", f"repos/{gh_repo}/branches/{branch}", "-q", ".commit.sha"])


def branch_exists(gh_repo: str, branch: str) -> bool:
    return _run(["gh", "api", f"repos/{gh_repo}/branches/{branch}"]).returncode == 0


def create_branch(gh_repo: str, branch: str, sha: str) -> bool:
    p = _run(["gh", "api", "--method", "POST", f"repos/{gh_repo}/git/refs",
              "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha}"])
    return p.returncode == 0


def rename_branch(gh_repo: str, old: str, new: str) -> bool:
    p = _run(["gh", "api", "--method", "POST", f"repos/{gh_repo}/branches/{old}/rename",
              "-f", f"new_name={new}"])
    return p.returncode == 0


def set_default_branch(gh_repo: str, branch: str) -> bool:
    p = _run(["gh", "api", "--method", "PATCH", f"repos/{gh_repo}",
              "-f", f"default_branch={branch}"])
    return p.returncode == 0


def _list_live_rulesets(gh_repo: str) -> List[Dict[str, Any]]:
    p = _run(["gh", "api", f"repos/{gh_repo}/rulesets"])
    if p.returncode != 0:
        return []
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def apply_ruleset(gh_repo: str, ruleset: Dict[str, Any]) -> Tuple[bool, str]:
    """PUT-then-reverify, mirroring ruleset-fleet-rollout.py's live-apply
    idiom: create if no live ruleset shares this name, else update in place,
    then re-fetch to confirm it actually landed."""
    live = _list_live_rulesets(gh_repo)
    existing = next((r for r in live if r.get("name") == ruleset.get("name")), None)
    payload = json.dumps(ruleset)
    if existing:
        action, args = "updated", [
            "api", "--method", "PUT", f"repos/{gh_repo}/rulesets/{existing['id']}",
            "--input", "-",
        ]
    else:
        action, args = "created", [
            "api", "--method", "POST", f"repos/{gh_repo}/rulesets", "--input", "-",
        ]
    p = _run(["gh", *args], input=payload)
    if p.returncode != 0:
        return False, f"{action} FAILED: {p.stderr.strip()}"
    live_after = _list_live_rulesets(gh_repo)
    match = next((r for r in live_after if r.get("name") == ruleset.get("name")), None)
    if not match:
        return False, f"{action} succeeded but re-fetch found no ruleset named {ruleset.get('name')!r}"
    return True, f"{action} and verified live"


# --------------------------------------------------------------------------
# propose
# --------------------------------------------------------------------------

def detect_state(repo: Path) -> Dict[str, Any]:
    claude_md = repo / "CLAUDE.md"
    already_split = (
        claude_md.is_file() and claude_md.read_text(encoding="utf-8").strip() == "@AGENTS.md"
    )
    rulesets_dir = repo / ".github" / "rulesets"
    existing_rulesets = (
        sorted(p.name for p in rulesets_dir.glob("*.json")) if rulesets_dir.is_dir() else []
    )
    policy_exists = (
        (repo / "docs" / "specs" / "worktrail-go-policy.yaml").is_file()
        or (repo / "docs" / "specs" / "go-policy.yaml").is_file()
    )
    return {
        "claude_md_already_split": already_split,
        "agents_md_exists": (repo / "AGENTS.md").is_file(),
        "existing_rulesets": existing_rulesets,
        "policy_file_exists": policy_exists,
        "openspec_initialized": (repo / "openspec" / "config.yaml").is_file(),
        "ci_jobs_discovered": discover_ci_checks(repo),
    }


def cmd_propose(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 1

    state = detect_state(repo)
    if args.check:
        print(json.dumps(state, indent=2) if args.as_json else state)
        return 0

    written: List[str] = []
    skipped: List[str] = []
    warnings: List[str] = []

    changed, warn = split_claude_md(repo)
    if warn:
        warnings.append(warn)
    elif changed:
        written += ["AGENTS.md", "CLAUDE.md"]
    else:
        skipped.append("CLAUDE.md/AGENTS.md (already split)")

    branches = ["dev", "stg", "prd"] if args.branch_model == "3" else ["dev", "prd"]
    rulesets_dir = repo / ".github" / "rulesets"
    for branch in branches:
        path = rulesets_dir / f"protect-{branch}.json"
        if path.is_file():
            skipped.append(str(path.relative_to(repo)))
            continue
        rulesets_dir.mkdir(parents=True, exist_ok=True)
        ruleset = build_ruleset_for_branch(branch, args.branch_model)
        path.write_text(json.dumps(ruleset, indent=2) + "\n", encoding="utf-8")
        written.append(str(path.relative_to(repo)))

    policy_path = repo / "docs" / "specs" / "worktrail-go-policy.yaml"
    if state["policy_file_exists"]:
        skipped.append(f"{policy_path.relative_to(repo)} (or legacy go-policy.yaml already exists)")
    else:
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(default_policy_yaml(resolve_repo_display_name(repo)), encoding="utf-8")
        written.append(str(policy_path.relative_to(repo)))

    if state["openspec_initialized"]:
        skipped.append("openspec/config.yaml (already initialized)")
    else:
        ok, warn = init_openspec(repo)
        if warn:
            warnings.append(warn)
        elif ok:
            written.append("openspec/ (config.yaml, specs/, changes/)")

    result = {
        "repo": str(repo),
        "branch_model": args.branch_model,
        "written": written,
        "skipped": skipped,
        "warnings": warnings,
        "ci_jobs_discovered": state["ci_jobs_discovered"],
    }
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== {repo} (branch model: {args.branch_model}) ===")
        for w in written:
            print(f"  wrote: {w}")
        for s in skipped:
            print(f"  skipped: {s}")
        for w in warnings:
            print(f"  warning: {w}")
        if state["ci_jobs_discovered"]:
            print("  CI jobs discovered (NOT auto-required -- review and add the ones that")
            print("  should gate merges to .github/rulesets/*.json before opening the PR):")
            for c in state["ci_jobs_discovered"]:
                print(f"    - {c}")
        print()
        print("Next: review the diff, commit, push, and open a PR. Run `apply` once it merges.")
    return 0


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------

def cmd_apply(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    rulesets_dir = repo / ".github" / "rulesets"
    ruleset_files = sorted(rulesets_dir.glob("protect-*.json")) if rulesets_dir.is_dir() else []
    declared = {f.stem.removeprefix("protect-") for f in ruleset_files}
    if not {"dev", "prd"} <= declared:
        print(
            f"error: expected protect-dev.json and protect-prd.json under {rulesets_dir} "
            "-- run `propose` first and merge its PR", file=sys.stderr)
        return 1
    branch_model = "3" if "stg" in declared else "2"

    gh_repo = resolve_gh_repo(repo)
    if not gh_repo:
        print("error: could not resolve GitHub owner/repo from `git remote get-url origin`",
              file=sys.stderr)
        return 1

    result: Dict[str, Any] = {
        "repo": gh_repo, "branch_model": branch_model, "branches": {},
        "rulesets": {}, "warnings": [],
    }

    current_default = current_default_branch(gh_repo)
    if current_default is None:
        print(f"error: could not read the current default branch for {gh_repo}", file=sys.stderr)
        return 1

    if current_default in ("dev", "prd"):
        # Idempotent re-run: either the whole migration already succeeded
        # (default is 'dev') or it's mid-way (renamed to 'prd' but the
        # default flip below hasn't landed yet). Either way `current_default`
        # is no longer the pre-migration branch name -- renaming it to 'prd'
        # here would rename 'dev' itself.
        result["warnings"].append(
            f"default branch is already '{current_default}' -- branch setup looks "
            "already done, skipping create/rename")
    else:
        base_sha = branch_sha(gh_repo, current_default)
        if base_sha is None:
            print(f"error: could not read the tip SHA of '{current_default}'", file=sys.stderr)
            return 1
        for branch in (["dev", "stg"] if branch_model == "3" else ["dev"]):
            if branch_exists(gh_repo, branch):
                result["branches"][branch] = "already existed"
                continue
            ok = create_branch(gh_repo, branch, base_sha)
            result["branches"][branch] = "created" if ok else "FAILED to create"
        ok = rename_branch(gh_repo, current_default, "prd")
        result["branches"][current_default] = "renamed to prd" if ok else "FAILED to rename to prd"

    new_default = current_default_branch(gh_repo)
    if new_default == "dev":
        result["default_branch"] = "already dev"
    else:
        ok = set_default_branch(gh_repo, "dev")
        result["default_branch"] = "set to dev" if ok else "FAILED to set to dev"

    rulesets_failed = False
    for f in ruleset_files:
        try:
            ruleset = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result["rulesets"][f.name] = f"FAILED to parse: {exc}"
            rulesets_failed = True
            continue
        ok, detail = apply_ruleset(gh_repo, ruleset)
        result["rulesets"][f.name] = detail
        if not ok:
            rulesets_failed = True

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== {gh_repo} ===")
        for branch, status in result["branches"].items():
            print(f"  branch {branch}: {status}")
        print(f"  default branch: {result['default_branch']}")
        for name, status in result["rulesets"].items():
            print(f"  ruleset {name}: {status}")
        for w in result["warnings"]:
            print(f"  warning: {w}")
        print()
        print("Manual follow-up:")
        print("  - retarget any other open PRs from the old default branch onto dev")
        print("  - local clones: git fetch origin && git switch dev")

    failed = (
        any("FAILED" in str(v) for v in result["branches"].values())
        or "FAILED" in str(result.get("default_branch", ""))
        or rulesets_failed
    )
    return 1 if failed else 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest="command", required=True)

    propose_p = subs.add_parser(
        "propose",
        help="write AGENTS.md/CLAUDE.md, .github/rulesets/*.json, docs/specs/worktrail-go-policy.yaml, "
             "and an OpenSpec scaffold into --repo")
    propose_p.add_argument("--repo", required=True)
    propose_p.add_argument(
        "--branch-model", choices=("2", "3"), default="2",
        help="2 = dev/prd (default -- use unless the repo has a real staging environment "
             "to gate against); 3 = dev/stg/prd")
    propose_p.add_argument("--check", action="store_true", help="report current state only; write nothing")
    propose_p.add_argument("--json", action="store_true", dest="as_json")

    apply_p = subs.add_parser(
        "apply",
        help="after propose's PR merges: create branches, rename to prd, set the default "
             "branch to dev, live-apply and verify the committed rulesets")
    apply_p.add_argument("--repo", required=True)
    apply_p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)
    if args.command == "propose":
        return cmd_propose(args)
    if args.command == "apply":
        return cmd_apply(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
