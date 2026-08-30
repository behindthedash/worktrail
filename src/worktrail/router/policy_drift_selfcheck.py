#!/usr/bin/env python3
"""policy_drift_selfcheck.py — worktrail-go-policy.yaml rationale-vs-reality drift detector.

`docs/specs/worktrail-go-policy.yaml` justifies each repo's pre-PR gate in freeform
rationale comments, and those comments encode claims about repository reality
("This repo has NO CI workflows", "No test suite in this repo"). Reality moves
— tests get added, CI gets added — and the comments do not. `policy.py` parses
each key in isolation and cannot see this: the value read is a perfectly valid
command, just one whose stated justification is no longer true.

The consequential half of that drift is not cosmetic. A repo can end up with
real, git-tracked test files that **nothing runs** — not `pre_pr_cmd`, not any
CI workflow — while the policy comment still explains why no gate is needed.
Verified 2026-07-31 across the 15 repos in this workspace that have a policy
file: `behindthedash` (3 orphaned pytest files under `ci/scripts/release_notes/`)
and `roost-radar` (orphaned `firestore.rules.test.ts`), both shipping that way
for weeks. See docs/specs/research/go-policy-drift-guard.md.

Sibling to `policy_selfcheck.py` (cross-repo copy-paste contamination) — same
inputs, different question. That module asks "is this policy file another
repo's?"; this one asks "does this policy file still describe *this* repo?"

Posture: passive detector for the dashboard, matching `policy_selfcheck.py` and
`automerge_selfcheck.py` — `check_repo()` never blocks anything. The one
deliberate exception is `orphaned_test_paths()`, which `pre_pr_gate.py` calls
to print a non-blocking WARNING at PR time; it still never changes an exit code.

Signals (see `check_repo`):
  - orphaned-tests: the repo has git-tracked test files, but neither
    `pre_pr_cmd`/`integrate_smoke_cmd` nor any `.github/workflows/*.yml`
    invokes a recognised test runner. The functional gap.
  - stale-claim-no-tests: the policy's comments assert there is no test suite,
    but test files exist. Cosmetic drift.
  - stale-claim-no-lint: the policy's comments assert there is no lint
    configuration, but one exists. Cosmetic drift.
  - stale-claim-no-ci: the policy's comments assert there are no CI workflows,
    but workflow files exist. Cosmetic drift.

Deliberate limits (precision is worth more than recall here — an advisory that
cries wolf gets ignored, and then it may as well not exist):
  - Reachability is undecidable in general: `pre_pr_cmd` is an arbitrary shell
    string, so proving `npm test` reaches a given file means running it. This
    asks the weaker, honest question — does *any* recognised runner appear? —
    which under-detects a repo whose gate runs some tests but not the orphaned
    ones. That under-detection is what keeps false positives at zero.
  - CI is matched on whole-file text, not just `run:` steps, which biases
    toward assuming CI does run tests.
  - A workflow that runs tests only through a composite/marketplace action with
    no shell command is not seen.
  - Only repos that have a `worktrail-go-policy.yaml` are considered; a repo with tests
    and no policy at all is a different (and larger) question.

Usage:
  policy_drift_selfcheck.py --repo /path/to/repo [--json]
  policy_drift_selfcheck.py --repos-root ~/projects [--json]   # sweep every repo
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .policy import policy_file_path
from .policy_selfcheck import discover_repo_names

# Only these two keys can run a test suite; `worktree_bootstrap_cmd` installs
# dependencies and must not count as enforcement.
_COMMAND_KEYS = ("pre_pr_cmd", "integrate_smoke_cmd")

_WORKFLOWS_RELPATH = Path(".github") / "workflows"

# Test-file naming conventions, matched against git-tracked repo-relative
# POSIX paths. Tracked-only means gitignored build output and node_modules are
# excluded for free, without maintaining an ignore list.
_TEST_FILE_RES = tuple(
    re.compile(p)
    for p in (
        r"(?:^|/)test_[^/]+\.py$",
        r"(?:^|/)[^/]+_test\.py$",
        r"\.test\.(?:ts|tsx|js|jsx|mjs|cjs)$",
        r"\.spec\.(?:ts|tsx|js|jsx|mjs|cjs)$",
        r"(?:^|/)test_[^/]+\.(?:mjs|cjs|js|ts)$",
        r"(?:^|/)[^/]+_test\.go$",
        r"(?:^|/)[^/]+_test\.rb$",
        r"(?:^|/)[^/]+Test\.java$",
    )
)

# Vendored trees can be tracked, unlike node_modules; their tests aren't ours.
_IGNORED_PARTS = frozenset({"node_modules", "vendor", "third_party", ".venv", "venv"})

# Recognised test-runner invocations. Deliberately requires a runner *verb*:
# `npm run lint` must not match, `npm run test:unit` must.
_RUNNER_RE = re.compile(
    r"\b(?:"
    r"pytest"
    r"|jest"
    r"|vitest"
    r"|mocha"
    r"|phpunit"
    r"|rspec"
    r"|playwright\s+test"
    r"|node\s+--test"
    r"|go\s+test"
    r"|cargo\s+test"
    r"|dotnet\s+test"
    r"|(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?test"
    r"|(?:python|python3)\s+-m\s+(?:pytest|unittest)"
    r"|tox"
    r"|nox"
    r")\b",
    re.IGNORECASE,
)

# Narrow, high-precision absence claims. Freeform prose will always be matched
# only partially, so these must never be the sole signal a repo is judged on.
_NO_TESTS_RES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bno\s+test\s+suite\b",
        r"\bno\s+tests?\s+in\s+this\s+repo\b",
        r"\bno\s+test\s+or\s+standalone\b",
    )
)
_NO_CI_RES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bno\s+ci\s+workflows?\b",
        r"\bno\s+ci\s+pipelines?\b",
    )
)
_NO_LINT_RES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bno\s+lint(?:ing)?\s+(?:config(?:uration)?|setup|script)\b",
        r"\bno\s+linter\b",
        r"\bno\s+eslint\b",
    )
)

# Lint configuration, by filename. A repo-root `package.json` with a `lint`
# script and a pyproject `[tool.<linter>]` table are handled separately.
_LINT_CONFIG_NAMES = frozenset(
    {
        ".eslintrc",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.json",
        ".eslintrc.yml",
        ".eslintrc.yaml",
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "eslint.config.ts",
        "biome.json",
        "biome.jsonc",
        ".oxlintrc.json",
        "ruff.toml",
        ".ruff.toml",
        ".flake8",
        ".pylintrc",
        "pylintrc",
        "tslint.json",
        ".golangci.yml",
        ".golangci.yaml",
        ".golangci.toml",
        ".rubocop.yml",
        ".credo.exs",
        ".stylelintrc",
        ".stylelintrc.json",
    }
)
_PYPROJECT_LINT_RE = re.compile(
    r"^\[tool\.(?:ruff|flake8|pylint|black|isort)\b", re.MULTILINE
)
_PKG_LINT_SCRIPT_RE = re.compile(r'"lint[^"]*"\s*:')


def _is_test_path(rel: str) -> bool:
    if _IGNORED_PARTS & set(rel.split("/")):
        return False
    return any(r.search(rel) for r in _TEST_FILE_RES)


def tracked_test_files(repo: Path) -> list[str]:
    """Git-tracked test files in `repo`, as repo-relative POSIX paths.

    One `git ls-files` beats globbing: it is a single subprocess, and it can't
    wander into node_modules or gitignored build output.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return sorted(p for p in proc.stdout.split("\0") if p and _is_test_path(p))


def _policy_text(repo: Path) -> str | None:
    src = policy_file_path(repo)
    if not src.is_file():
        return None
    return src.read_text(encoding="utf-8", errors="replace")


def _comment_lines(text: str) -> str:
    """Only `#` comment lines — prose claims live there, commands don't.

    Scanning the whole file would let a command that happens to contain
    "no test" read as an authored claim.
    """
    return "\n".join(
        line.strip() for line in text.splitlines() if line.strip().startswith("#")
    )


def _command_values(text: str) -> str:
    vals = []
    for key in _COMMAND_KEYS:
        m = re.search(rf"^{key}:\s*(.+)$", text, re.MULTILINE)
        if m:
            vals.append(m.group(1))
    return "\n".join(vals)


def workflow_files(repo: Path) -> list[Path]:
    wf = repo / _WORKFLOWS_RELPATH
    if not wf.is_dir():
        return []
    return sorted(
        p for p in wf.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def lint_config_evidence(repo: Path) -> str | None:
    """The first piece of lint configuration found, or None.

    Returns the evidence itself rather than a bool so the finding can name what
    contradicted the claim — "there is no lint config" is much easier to act on
    when the reply is "`.eslintrc.json` exists".
    """
    repo = Path(repo)
    for name in sorted(_LINT_CONFIG_NAMES):
        if (repo / name).is_file():
            return name
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            if _PKG_LINT_SCRIPT_RE.search(
                pkg.read_text(encoding="utf-8", errors="replace")
            ):
                return "package.json lint script"
        except OSError:
            pass
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            m = _PYPROJECT_LINT_RE.search(
                pyproject.read_text(encoding="utf-8", errors="replace")
            )
            if m:
                return f"pyproject.toml {m.group(0).strip()}]"
        except OSError:
            pass
    return None


def _ci_runs_tests(repo: Path) -> bool:
    for f in workflow_files(repo):
        try:
            if _RUNNER_RE.search(f.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def orphaned_test_paths(repo: Path) -> list[str]:
    """Test files that no configured gate and no CI workflow appears to run.

    Empty when the repo is clean, has no policy file, or has no test files.
    This is the one entry point `pre_pr_gate.py` calls, so it stays cheap and
    free of prose parsing.
    """
    repo = Path(repo)
    text = _policy_text(repo)
    if text is None:
        return []
    tests = tracked_test_files(repo)
    if not tests:
        return []
    if _RUNNER_RE.search(_command_values(text)):
        return []
    if _ci_runs_tests(repo):
        return []
    return tests


def check_repo(repo: Path) -> dict[str, Any]:
    """Findings for one repo's worktrail-go-policy.yaml. Empty `findings` = clean."""
    repo = Path(repo)
    result: dict[str, Any] = {
        "repo": repo.name,
        "path": str(repo),
        "source": None,
        "findings": [],
    }
    text = _policy_text(repo)
    if text is None:
        return result
    result["source"] = str(policy_file_path(repo))
    findings = result["findings"]

    tests = tracked_test_files(repo)
    workflows = workflow_files(repo)
    gate_runs_tests = bool(_RUNNER_RE.search(_command_values(text)))
    ci_runs_tests = _ci_runs_tests(repo)
    comments = _comment_lines(text)

    if tests and not gate_runs_tests and not ci_runs_tests:
        shown = ", ".join(tests[:3])
        more = f" (+{len(tests) - 3} more)" if len(tests) > 3 else ""
        findings.append(
            {
                "signal": "orphaned-tests",
                "detail": (
                    f"{len(tests)} git-tracked test file(s) run by no test runner in "
                    f"pre_pr_cmd/integrate_smoke_cmd and none in .github/workflows: "
                    f"{shown}{more}"
                ),
            }
        )

    if tests and any(r.search(comments) for r in _NO_TESTS_RES):
        findings.append(
            {
                "signal": "stale-claim-no-tests",
                "detail": (
                    f"policy comments claim there is no test suite, but "
                    f"{len(tests)} test file(s) exist"
                ),
            }
        )

    if any(r.search(comments) for r in _NO_LINT_RES):
        lint = lint_config_evidence(repo)
        if lint is not None:
            findings.append(
                {
                    "signal": "stale-claim-no-lint",
                    "detail": (
                        f"policy comments claim there is no lint configuration, but "
                        f"{lint} exists"
                    ),
                }
            )

    if workflows and any(r.search(comments) for r in _NO_CI_RES):
        findings.append(
            {
                "signal": "stale-claim-no-ci",
                "detail": (
                    f"policy comments claim there are no CI workflows, but "
                    f"{len(workflows)} workflow file(s) exist: "
                    + ", ".join(f.name for f in workflows[:3])
                ),
            }
        )

    return result


def sweep(repos_root: Path) -> list[dict[str, Any]]:
    """check_repo() for every repo under `repos_root` that has a policy file."""
    results = []
    for name in discover_repo_names(repos_root):
        r = check_repo(repos_root / name)
        if r["source"] is not None:
            results.append(r)
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="single repo to check")
    p.add_argument("--repos-root", help="sweep every repo under this directory")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not args.repo and not args.repos_root:
        p.error("one of --repo or --repos-root is required")

    if args.repo:
        results = [check_repo(Path(args.repo).expanduser())]
    else:
        results = sweep(Path(args.repos_root).expanduser())

    flagged = [r for r in results if r["findings"]]
    if args.json:
        print(json.dumps({"results": results, "flagged": len(flagged)}, indent=2))
    else:
        if not flagged:
            print(
                f"policy_drift_selfcheck: {len(results)} repo(s) checked, no drift signals"
            )
        for r in flagged:
            print(f"{r['repo']}:")
            for f in r["findings"]:
                print(f"  [{f['signal']}] {f['detail']}")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
