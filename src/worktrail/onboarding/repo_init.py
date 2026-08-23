"""worktrail-repo-init -- bootstrap or migrate a repo onto the workspace's
repo-standards doctrine (~/rules/CLAUDE.repo.md): an AGENTS.md/CLAUDE.md split,
a dev/prd (or dev/stg/prd) branch model, GitHub rulesets, an OpenSpec scaffold,
and a seeded .worktrail/policy.yaml.

Two subcommands, mirroring devops's ruleset-fleet-rollout.py's propose/apply
split so branch protection and the default-branch change are never made from
an unmerged PR:

  propose  Write the generated files into --repo, which the caller is expected
           to already have checked out as a clean worktree (this tool does not
           create worktrees, commit, push, or open the PR -- that is the
           calling skill's job, same as every other worktrail CLI). Safe to
           re-run; already-present files are left alone -- write-if-absent,
           never regenerate-in-place. `propose`'s JSON/text result carries a
           `drift` list (see `compute_drift`) reporting which already-present,
           worktrail-owned files no longer match what today's template would
           produce; this is report-only and never auto-applied -- the caller
           (a human reading the CLI output, or an agent presenting the list
           via a per-file question) decides whether to delete and regenerate
           each one. Files meant for hand-editing or owned by a third-party
           tool are out of scope for drift (see `compute_drift`'s docstring).
  apply    Run once that PR has merged: create `dev` (and `stg` for a 3-branch
           model) from the current default branch's tip SHA, rename the
           current default branch to `prd`, set `dev` as the new GitHub
           default branch, enable "delete branch on merge", live-apply
           and verify the committed rulesets (same PUT-then-reverify idiom as
           ruleset-fleet-rollout.py), and -- if the auto-merge workflow was
           scaffolded -- idempotently create/update the go:risk-*/
           go:no-automerge labels it depends on.

`propose` deliberately never auto-populates `required_status_checks` from CI
discovery -- it reports discovered CI job display names (from
`.github/workflows/*.yml`) for a human to review, since not every job should
gate every branch (informational jobs, matrix jobs, etc.). A repo with no CI
gets a ruleset with zero required checks, not a copy-pasted list that would
deadlock every future PR. The one scoped exception: when the same `propose`
run newly writes the openspec-validate workflow (see
OPENSPEC_VALIDATE_JOB_NAME) alongside a *fresh* `protect-<branch>.json`, that
one job is seeded as the ruleset's sole required check -- see
`build_ruleset_for_branch` and `patch_ruleset_required_check`.

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

from ..router.policy import POLICY_RELPATH, has_policy_file
from .dependabot_manifest_check_template import (
    DEPENDABOT_MANIFEST_CHECK_PY,
    DEPENDABOT_MANIFEST_CHECK_REQUIREMENTS_TXT,
)
from .rulesets_drift_guard_template import RULESETS_REQUIREMENTS_TXT, RULESETS_SYNC_PY

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


def build_ruleset_for_branch(
    branch: str, branch_model: str, extra_required_status_check: Optional[str] = None,
) -> Dict[str, Any]:
    """branch_model "2" = dev/prd (GGB pattern); "3" = dev/stg/prd (datalena
    pattern, dev is squash + required_linear_history).

    extra_required_status_check, when given, is the sole entry ever placed in
    the generated ruleset's required_status_checks -- callers pass it only
    when generating a *fresh* ruleset file in the same `propose` run that
    also newly writes the openspec-validate workflow (see
    OPENSPEC_VALIDATE_JOB_NAME). Nothing from `state["ci_jobs_discovered"]`
    is ever passed in here; `propose` still deliberately never
    auto-populates required_status_checks from CI discovery otherwise."""
    checks = [extra_required_status_check] if extra_required_status_check else []
    if branch == "dev":
        return build_ruleset(
            "protect-dev", "dev", ["squash"], checks, linear_history=(branch_model == "3"))
    if branch == "stg":
        return build_ruleset("protect-stg", "stg", ["merge"], checks)
    if branch == "prd":
        return build_ruleset("protect-prd", "prd", ["merge"], checks)
    raise ValueError(f"unknown branch {branch!r}")


def _ruleset_structural_view(ruleset: Dict[str, Any]) -> Dict[str, Any]:
    """`ruleset` with any `required_status_checks` rule removed entirely, so
    drift detection can compare merge methods, review-thread resolution, and
    linear-history policy independent of the required-check list -- operators
    are expected to grow that list over time via `discover_ci_checks()`'s own
    human-review flow, so its presence/contents is never drift."""
    view = json.loads(json.dumps(ruleset))
    view["rules"] = [
        r for r in view.get("rules", []) if r.get("type") != "required_status_checks"
    ]
    return view


def patch_ruleset_required_check(path: Path, job_name: str) -> bool:
    """In-place patch for an *existing* `protect-<branch>.json`: locate (or
    create) the `required_status_checks` rule and append
    `{"context": job_name}` only if no entry with that context is already
    present. Everything else in the file -- other rules, key order, other
    required_status_checks entries -- is left untouched. Returns True if the
    file was changed (and thus rewritten), False on a no-op."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.setdefault("rules", [])
    rsc_rule = next((r for r in rules if r.get("type") == "required_status_checks"), None)
    if rsc_rule is None:
        rsc_rule = {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": False,
                "do_not_enforce_on_create": False,
                "required_status_checks": [],
            },
        }
        rules.append(rsc_rule)
    checks = rsc_rule.setdefault("parameters", {}).setdefault("required_status_checks", [])
    if any(c.get("context") == job_name for c in checks):
        return False
    checks.append({"context": job_name})
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


# --------------------------------------------------------------------------
# Auto-merge workflow
# --------------------------------------------------------------------------

AUTOMERGE_WORKFLOW_RELPATH = ".github/workflows/worktrail-auto-merge.yml"

# --------------------------------------------------------------------------
# Rulesets drift guard workflow
# --------------------------------------------------------------------------

RULESETS_DRIFT_GUARD_WORKFLOW_RELPATH = ".github/workflows/rulesets_drift_guard.yml"
RULESETS_SCRIPT_DIR_RELPATH = "scripts/ci/rulesets"
RULESETS_SYNC_SCRIPT_RELPATH = f"{RULESETS_SCRIPT_DIR_RELPATH}/rulesets_sync.py"
RULESETS_REQUIREMENTS_RELPATH = f"{RULESETS_SCRIPT_DIR_RELPATH}/requirements.txt"

# --------------------------------------------------------------------------
# Dependabot manifest check
# --------------------------------------------------------------------------

DEPENDABOT_CHECK_WORKFLOW_RELPATH = ".github/workflows/dependabot_manifest_check.yml"
DEPENDABOT_CHECK_SCRIPT_DIR_RELPATH = "scripts/ci/dependabot"
DEPENDABOT_CHECK_SCRIPT_RELPATH = f"{DEPENDABOT_CHECK_SCRIPT_DIR_RELPATH}/test_dependabot_config.py"
DEPENDABOT_CHECK_REQUIREMENTS_RELPATH = f"{DEPENDABOT_CHECK_SCRIPT_DIR_RELPATH}/requirements.txt"
DEPENDABOT_CHECK_JOB_NAME = "Dependabot manifest check"


def build_dependabot_manifest_check_workflow(branches: List[str]) -> str:
    """A "CI: Dependabot Manifest Check" workflow targeting the repo's actual
    branch model, running the vendored `test_dependabot_config.py`
    (dependabot_manifest_check_template) against `.github/dependabot.yml`.

    Deliberately has no `paths` filter (design D4): a broken entry can be
    introduced without ever touching `dependabot.yml` itself -- moving or
    deleting the manifest file an existing entry's `directory` points at is
    enough to silently stop Dependabot-Updates for that entry, and that
    diff would never trigger a `paths`-filtered run. Running unconditionally
    on every pull request is the only way to catch that case.

    The check only reads files already present in the checkout -- it makes
    no GitHub API calls, so unlike `build_rulesets_drift_guard_workflow` it
    needs no minted token and no elevated permissions beyond the default
    `permissions: contents: read`."""
    branches_yaml = "[" + ", ".join(branches) + "]"
    return f'''\
name: "CI: Dependabot Manifest Check"

on:
  pull_request:
    branches: {branches_yaml}
  workflow_dispatch: {{}}

concurrency:
  group: dependabot-manifest-check-${{{{ github.event.pull_request.number || github.ref }}}}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  dependabot-manifest-check:
    name: {DEPENDABOT_CHECK_JOB_NAME}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7

      - name: Setup Python
        uses: actions/setup-python@v7
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: python -m pip install -r {DEPENDABOT_CHECK_REQUIREMENTS_RELPATH}

      - name: Check dependabot.yml manifests
        run: python {DEPENDABOT_CHECK_SCRIPT_RELPATH}
'''


_AUTOMERGE_WORKFLOW = '''\
name: "CI: Auto-merge on open"
on:
  pull_request:
    types: [opened, reopened, ready_for_review, labeled, unlabeled]

# A `labeled` run must never cancel an in-flight opened/reopened/ready_for_review
# run for the same PR -- a cancelled run of a required check stays on the head
# SHA and blocks the merge/auto-merge even after a newer run succeeds. The label
# name is included too: a PR opened with two `gh pr create --label` flags fires
# two separate `labeled` events that would otherwise share one group and cancel
# each other.
concurrency:
  group: worktrail-auto-merge-${{ github.event.pull_request.number }}-${{ github.event.action }}-${{ github.event.label.name || '' }}
  cancel-in-progress: true

jobs:
  auto-merge:
    # Never attempt auto-merge on a draft PR -- GitHub rejects it.
    if: ${{ !github.event.pull_request.draft }}
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: write
    steps:
      - name: Check automerge eligibility
        id: check-automerge
        run: |
          labels=$(gh pr view "${{ github.event.pull_request.number }}" \\
            --json labels --jq '.labels[].name' -R "${{ github.repository }}")
          eligible="false"
          if echo "$labels" | grep -qE "^go:risk-(low|medium)$" \\
              && ! echo "$labels" | grep -q "^go:no-automerge$"; then
            eligible="true"
          fi
          echo "eligible=$eligible" >> "$GITHUB_OUTPUT"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      # Native GitHub auto-merge persists once armed regardless of later label
      # changes -- disarming on every ineligible re-check is what actually
      # stops a merge that an earlier, more permissive trigger already armed.
      # `|| true` because disarming a PR that was never armed is a no-op.
      - name: Disarm on ineligible
        if: steps.check-automerge.outputs.eligible != 'true'
        run: gh pr merge --disable-auto "${{ github.event.pull_request.number }}" -R "${{ github.repository }}" || true
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Arm auto-merge
        if: steps.check-automerge.outputs.eligible == 'true'
        run: |
          if [ "${{ github.event.pull_request.base.ref }}" = "dev" ]; then
            gh pr merge --auto --squash "${{ github.event.pull_request.number }}" -R "${{ github.repository }}"
          else
            gh pr merge --auto --merge "${{ github.event.pull_request.number }}" -R "${{ github.repository }}"
          fi
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
'''


def build_automerge_workflow() -> str:
    """A portable, drop-in "CI: Auto-merge on open" workflow: arms/disarms
    GitHub-native auto-merge based on the go:risk-low/go:risk-medium /
    go:no-automerge labels worktrail-go itself applies (policy.py's
    automerge_labels()). Matches the convention already used by
    datalena/GGB/worktrail's own .github/workflows/auto-merge.yml, but
    inlined here with no external ci/scripts/automerge_eligibility.sh
    dependency, and picks squash vs merge the same way build_ruleset_for_branch
    does (dev -> squash, everything else -> merge).

    Inert until something applies a go:risk-* label -- a repo not using
    worktrail-go's classifier for its PRs never has this fire."""
    return _AUTOMERGE_WORKFLOW


# Colors/descriptions match the live label sets already in use on
# behindthedash/gracefully-giving-back and behindthedash/datalena (checked via
# `gh label list --search "go:"` -- both repos agree on this scheme).
AUTOMERGE_LABELS: List[Dict[str, str]] = [
    {"name": "go:risk-low", "color": "0e8a16", "description": "GO v2: low risk tier"},
    {"name": "go:risk-medium", "color": "fbca04", "description": "GO v2: medium risk tier"},
    {"name": "go:risk-high", "color": "d93f0b", "description": "GO v2: high risk tier"},
    {"name": "go:risk-critical", "color": "b60205", "description": "GO v2: critical risk tier"},
    {"name": "go:no-automerge", "color": "5319e7", "description": "GO v2: not eligible for auto-merge"},
]


def ensure_automerge_labels(gh_repo: str) -> Dict[str, str]:
    """Idempotently create (or update in place) the go:risk-*/go:no-automerge
    labels the generated auto-merge workflow (build_automerge_workflow())
    depends on. `gh label create --force` create-or-updates in a single call,
    so unlike apply_ruleset()'s PUT-then-reverify there is no separate
    existence check needed here.

    Without these labels pre-existing, ensure_pr_risk_label()/
    ensure_pr_no_automerge_label() (router/pr_labels.py) log a stderr warning
    and silently no-op on a freshly onboarded repo -- PRs never get labeled at
    all until an operator notices and creates the labels by hand."""
    result: Dict[str, str] = {}
    for label in AUTOMERGE_LABELS:
        p = _run([
            "gh", "label", "create", label["name"], "--force",
            "--color", label["color"], "--description", label["description"],
            "--repo", gh_repo,
        ])
        result[label["name"]] = "ok" if p.returncode == 0 else f"FAILED: {p.stderr.strip()}"
    return result


def build_rulesets_drift_guard_workflow(branches: List[str]) -> str:
    """A "CI: Rulesets Drift Guard" workflow targeting the repo's actual
    branch model: `--check`s committed `.github/rulesets/*.json` against
    live GitHub rulesets on a PR touching them (plus a weekly schedule, to
    catch out-of-band UI edits) and `--apply`s them on push to a protected
    branch, using vendored `rulesets_sync.py` (rulesets_drift_guard_template).

    Reading/writing live rulesets needs repository Administration
    read/write, a GitHub App installation permission that cannot be granted
    to the default GITHUB_TOKEN via the workflow-level `permissions:` block
    -- so this mints a token from the same fleet-wide App already used for
    release-notes automation (`vars.RELEASE_NOTES_APP_ID` /
    `secrets.RELEASE_NOTES_APP_PRIVATE_KEY`), matching this repo's own
    .github/workflows/rulesets_drift_guard.yml. A repo where that App is not
    yet installed/configured must not fail CI over it: the token-mint step
    and every rulesets-check/apply step are gated on
    `vars.RELEASE_NOTES_APP_ID` (respectively the minted token) being
    non-empty, so they report `skipped`, not `failure`."""
    branches_yaml = "[" + ", ".join(branches) + "]"
    return f'''\
name: 'CI: Rulesets Drift Guard'

on:
  pull_request:
    branches: {branches_yaml}
    paths:
      - '.github/rulesets/**'
      - '{RULESETS_DRIFT_GUARD_WORKFLOW_RELPATH}'
      - '{RULESETS_SCRIPT_DIR_RELPATH}/**'
  push:
    branches: {branches_yaml}
    paths:
      - '.github/rulesets/**'
  workflow_dispatch: {{}}
  schedule:
    # Weekly drift check to catch out-of-band GitHub UI edits.
    - cron: '0 9 * * 1'

concurrency:
  group: rulesets-drift-guard-${{{{ github.event.pull_request.number || github.ref }}}}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  rulesets-check:
    name: Rulesets drift check
    runs-on: ubuntu-latest
    env:
      RULESETS_APP_ID: ${{{{ vars.RELEASE_NOTES_APP_ID }}}}
      RULESETS_APP_PRIVATE_KEY: ${{{{ secrets.RELEASE_NOTES_APP_PRIVATE_KEY }}}}
    steps:
      - uses: actions/checkout@v7

      - name: Setup Python
        uses: actions/setup-python@v7
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: python -m pip install -r {RULESETS_SCRIPT_DIR_RELPATH}/requirements.txt

      # Reading/writing live rulesets requires repository Administration,
      # a GitHub App installation permission -- it cannot be granted to the
      # default GITHUB_TOKEN via the workflow-level `permissions:` block.
      # Mint a token from the fleet-wide App already installed for bot
      # automation instead.
      - name: Generate rulesets-check bot token
        id: app-token
        if: ${{{{ env.RULESETS_APP_ID != '' && env.RULESETS_APP_PRIVATE_KEY != '' }}}}
        uses: actions/create-github-app-token@v3
        with:
          client-id: ${{{{ env.RULESETS_APP_ID }}}}
          private-key: ${{{{ env.RULESETS_APP_PRIVATE_KEY }}}}

      - name: Check committed rulesets against live GitHub rulesets
        if: ${{{{ github.event_name != 'push' && steps.app-token.outputs.token != '' }}}}
        env:
          GITHUB_TOKEN: ${{{{ steps.app-token.outputs.token }}}}
        run: python {RULESETS_SCRIPT_DIR_RELPATH}/rulesets_sync.py --check

      - name: Apply committed rulesets to live GitHub rulesets
        if: ${{{{ github.event_name == 'push' && steps.app-token.outputs.token != '' }}}}
        env:
          GITHUB_TOKEN: ${{{{ steps.app-token.outputs.token }}}}
        run: python {RULESETS_SCRIPT_DIR_RELPATH}/rulesets_sync.py --apply

      - name: Note skipped run due to missing App credentials
        if: ${{{{ always() && (env.RULESETS_APP_ID == '' || env.RULESETS_APP_PRIVATE_KEY == '') }}}}
        run: echo "::notice::Rulesets drift guard skipped -- install the release-notes GitHub App and set vars.RELEASE_NOTES_APP_ID / secrets.RELEASE_NOTES_APP_PRIVATE_KEY to enable it."
'''


# --------------------------------------------------------------------------
# OpenSpec validate workflow
# --------------------------------------------------------------------------

OPENSPEC_VALIDATE_WORKFLOW_RELPATH = ".github/workflows/worktrail-openspec-validate.yml"

# _OPENSPEC_VALIDATE_WORKFLOW sets no job-level `name:` on `openspec-validate`,
# so GitHub's own default display -- and discover_ci_checks()'s name-or-id
# fallback -- both resolve to the job id itself. Keeping that string as a
# constant here, instead of re-typing it at each required_status_checks call
# site, is what keeps discover_ci_checks() and the ruleset entry from ever
# drifting apart.
OPENSPEC_VALIDATE_JOB_NAME = "openspec-validate"

_OPENSPEC_VALIDATE_WORKFLOW = '''\
name: "CI: OpenSpec validate"
on:
  pull_request:
    paths:
      - "openspec/**"

jobs:
  openspec-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # Unpinned @latest: OpenSpec has no stable major yet and this workflow
      # is meant to be a portable drop-in with no repo-local lockfile to keep
      # current -- the tradeoff is a validate rule that can shift under a repo
      # without warning if OpenSpec ships a breaking release, in exchange for
      # never going stale on an old CLI version.
      - name: openspec validate --all --strict
        run: npx --yes @fission-ai/openspec@latest validate --all --strict
'''


def build_openspec_validate_workflow() -> str:
    """A portable, drop-in "CI: OpenSpec validate" workflow: paths-filtered
    to `openspec/**` so it only runs on PRs that touch the spec tree, running
    `openspec validate --all --strict` via the same unpinned
    `@fission-ai/openspec@latest` CLI this module's own `init_openspec()`
    uses (see OPENSPEC_PACKAGE) -- kept as an inline literal here rather than
    templated from that constant so the workflow stays copy-pasteable on its
    own, matching build_automerge_workflow()'s shape."""
    return _OPENSPEC_VALIDATE_WORKFLOW


# --------------------------------------------------------------------------
# .worktrail/policy.yaml seed
# --------------------------------------------------------------------------

def default_policy_yaml(repo_name: str, *, enable_aspens: bool = False) -> str:
    header = (
        f"# {repo_name} -- worktrail-go policy.\n"
        "# See worktrail's src/worktrail/router/policy.py DEFAULTS for the full\n"
        "# schema. Every key is optional and defaults to a safe, do-nothing value\n"
        "# until this repo opts in explicitly (e.g. pre_pr_cmd for the pre-PR test\n"
        "# gate, automerge for GitHub-native auto-merge eligibility).\n"
    )
    if not enable_aspens:
        return header
    return header + "add_ons:\n  aspens: {}\n"


# --------------------------------------------------------------------------
# Aspens add-on (opt-in)
# --------------------------------------------------------------------------

def enable_aspens(repo: Path) -> Tuple[bool, Optional[str]]:
    """Opt a repo into the `aspens` add-on at bootstrap time: install the CLI
    if needed and run its one-time `aspens doc init`
    (`AspensAddOn.install()`/`.configure()`), so a freshly onboarded repo
    doesn't wait for its first orchestrated task to get configured.
    Deliberately never runs `AddOn.run()` (`aspens doc sync`) -- that's a
    per-task concern (`worktrail.addons.runner`'s stage-and-commit path), not
    a bootstrap one.

    Returns (configured, warning). `configured` is True only if
    `.aspens.json` now exists -- `AspensAddOn.configure()` swallows subprocess
    failures silently (best-effort priming, matching its own posture), so
    file existence is the only reliable postcondition available here."""
    if (repo / ".aspens.json").is_file():
        return False, None
    from ..addons.aspens import AspensAddOn
    from ..addons.runner import ADDON_TIMEOUT_DEFAULT, AddOnContext

    ctx = AddOnContext(worktree=repo, repo=repo, config={}, timeout=ADDON_TIMEOUT_DEFAULT)
    addon = AspensAddOn()
    addon.install(ctx)
    addon.configure(ctx)
    if (repo / ".aspens.json").is_file():
        return True, None
    return False, (
        "aspens doc init did not produce .aspens.json -- the aspens CLI may not be "
        "installed/reachable; run `aspens doc init` by hand once it is")


# --------------------------------------------------------------------------
# GitNexus add-on (opt-in)
# --------------------------------------------------------------------------

def enable_gitnexus(repo: Path) -> Tuple[bool, Optional[str]]:
    """Opt a repo into a GitNexus index at bootstrap time: run
    `gitnexus analyze --embeddings --index-only` so a freshly onboarded repo
    doesn't wait for its first orchestrated task to get indexed.
    `--index-only` deliberately skips AGENTS.md/skills file injection --
    bootstrap only wants the index, not a second copy of files this repo
    already manages.

    Returns (configured, warning). `configured` is True only if `.gitnexus/`
    now exists -- the subprocess call is swallowed on timeout or launch
    failure (best-effort indexing, matching `enable_aspens`'s posture), so
    directory existence, not the return code, is the only reliable
    postcondition available here."""
    if (repo / ".gitnexus").is_dir():
        return False, None
    try:
        _run(["gitnexus", "analyze", "--embeddings", "--index-only", str(repo)])
    except (subprocess.TimeoutExpired, OSError):
        pass
    if (repo / ".gitnexus").is_dir():
        return True, None
    return False, (
        "gitnexus analyze did not produce .gitnexus/ -- the gitnexus CLI may not be "
        "installed/reachable; run `gitnexus analyze --embeddings --index-only` by hand "
        "once it is")


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


def get_delete_branch_on_merge(gh_repo: str) -> Optional[bool]:
    raw = _gh_raw(["api", f"repos/{gh_repo}", "-q", ".delete_branch_on_merge"])
    if raw is None:
        return None
    return raw.strip().lower() == "true"


def set_delete_branch_on_merge(gh_repo: str) -> bool:
    p = _run(["gh", "api", "--method", "PATCH", f"repos/{gh_repo}",
              "-f", "delete_branch_on_merge=true"])
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


def _gh_json_names(args: List[str]) -> List[str]:
    p = _run(["gh", *args])
    if p.returncode != 0:
        return []
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item.get("name") for item in data if isinstance(item, dict)]


def app_credentials_configured(gh_repo: str) -> bool:
    """True only if both `RELEASE_NOTES_APP_ID` (a repo variable) and
    `RELEASE_NOTES_APP_PRIVATE_KEY` (a repo secret) are present -- the two
    credentials the rulesets drift-guard workflow's App-token mint step needs."""
    variable_names = _gh_json_names(["variable", "list", "--json", "name", "-R", gh_repo])
    secret_names = _gh_json_names(["secret", "list", "--json", "name", "-R", gh_repo])
    return "RELEASE_NOTES_APP_ID" in variable_names and "RELEASE_NOTES_APP_PRIVATE_KEY" in secret_names


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
    policy_exists = has_policy_file(repo)
    return {
        "claude_md_already_split": already_split,
        "agents_md_exists": (repo / "AGENTS.md").is_file(),
        "existing_rulesets": existing_rulesets,
        "policy_file_exists": policy_exists,
        "openspec_initialized": (repo / "openspec" / "config.yaml").is_file(),
        "automerge_workflow_exists": (repo / AUTOMERGE_WORKFLOW_RELPATH).is_file(),
        "rulesets_drift_guard_exists": (
            repo / RULESETS_DRIFT_GUARD_WORKFLOW_RELPATH
        ).is_file(),
        "rulesets_sync_script_exists": (repo / RULESETS_SYNC_SCRIPT_RELPATH).is_file(),
        "rulesets_requirements_exists": (repo / RULESETS_REQUIREMENTS_RELPATH).is_file(),
        "openspec_validate_workflow_exists": (repo / OPENSPEC_VALIDATE_WORKFLOW_RELPATH).is_file(),
        "ci_jobs_discovered": discover_ci_checks(repo),
    }


def _content_drift(repo: Path, relpath: str, current_content: str) -> Optional[Dict[str, str]]:
    on_disk = (repo / relpath).read_text(encoding="utf-8")
    if on_disk == current_content:
        return None
    return {
        "path": relpath,
        "detail": "content differs from what today's template would generate -- delete "
                   "the file and re-run propose to regenerate it",
    }


def _ruleset_drift(repo: Path, branch: str, branch_model: str) -> Optional[Dict[str, str]]:
    relpath = f".github/rulesets/protect-{branch}.json"
    try:
        on_disk = json.loads((repo / relpath).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    baseline = build_ruleset_for_branch(branch, branch_model)
    if _ruleset_structural_view(on_disk) == _ruleset_structural_view(baseline):
        return None
    return {
        "path": relpath,
        "detail": "ruleset structure (merge methods, review-thread resolution, linear "
                   "history) differs from today's template -- required_status_checks is "
                   "intentionally excluded from this comparison since operators are "
                   "expected to grow it over time; review the rest by hand",
    }


def compute_drift(
    repo: Path, state: Dict[str, Any], branches: List[str], branch_model: str,
) -> List[Dict[str, str]]:
    """Report-only: files `propose` owns and skips when already present,
    whose on-disk content no longer matches what today's generator would
    produce. Never auto-applied and `propose` never regenerates a file on
    its own account of this -- a human (or an agent on their behalf,
    presenting this list via a per-file question) decides whether to
    upgrade. Only worktrail-owned templates are checked; files meant for
    hand-editing (`.worktrail/policy.yaml`, `CLAUDE.md`/`AGENTS.md`) and
    third-party-tool state (`openspec/`, `.aspens.json`, `.gitnexus/`) are
    out of scope -- there is no single "current" content to diff them
    against."""
    drift: List[Dict[str, str]] = []
    for branch in branches:
        if f"protect-{branch}.json" in state["existing_rulesets"]:
            found = _ruleset_drift(repo, branch, branch_model)
            if found:
                drift.append(found)
    if state["rulesets_sync_script_exists"]:
        found = _content_drift(repo, RULESETS_SYNC_SCRIPT_RELPATH, RULESETS_SYNC_PY)
        if found:
            drift.append(found)
    if state["rulesets_requirements_exists"]:
        found = _content_drift(repo, RULESETS_REQUIREMENTS_RELPATH, RULESETS_REQUIREMENTS_TXT)
        if found:
            drift.append(found)
    if state["rulesets_drift_guard_exists"]:
        found = _content_drift(
            repo, RULESETS_DRIFT_GUARD_WORKFLOW_RELPATH,
            build_rulesets_drift_guard_workflow(branches))
        if found:
            drift.append(found)
    if state["automerge_workflow_exists"]:
        found = _content_drift(repo, AUTOMERGE_WORKFLOW_RELPATH, build_automerge_workflow())
        if found:
            drift.append(found)
    if state["openspec_validate_workflow_exists"]:
        found = _content_drift(
            repo, OPENSPEC_VALIDATE_WORKFLOW_RELPATH, build_openspec_validate_workflow())
        if found:
            drift.append(found)
    return drift


def cmd_propose(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 1

    state = detect_state(repo)
    if args.check:
        check_branches = ["dev", "stg", "prd"] if args.branch_model == "3" else ["dev", "prd"]
        check_result = dict(state, drift=compute_drift(repo, state, check_branches, args.branch_model))
        print(json.dumps(check_result, indent=2) if args.as_json else check_result)
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
    openspec_validate_newly_written = not state["openspec_validate_workflow_exists"]
    required_check_configured = False
    for branch in branches:
        path = rulesets_dir / f"protect-{branch}.json"
        if path.is_file():
            if openspec_validate_newly_written and patch_ruleset_required_check(
                path, OPENSPEC_VALIDATE_JOB_NAME
            ):
                written.append(
                    f"{path.relative_to(repo)} (patched: added "
                    f"{OPENSPEC_VALIDATE_JOB_NAME} to required_status_checks)")
                required_check_configured = True
            else:
                skipped.append(str(path.relative_to(repo)))
            continue
        rulesets_dir.mkdir(parents=True, exist_ok=True)
        extra_check = OPENSPEC_VALIDATE_JOB_NAME if openspec_validate_newly_written else None
        if extra_check:
            required_check_configured = True
        ruleset = build_ruleset_for_branch(branch, args.branch_model, extra_check)
        path.write_text(json.dumps(ruleset, indent=2) + "\n", encoding="utf-8")
        written.append(str(path.relative_to(repo)))

    sync_script_path = repo / RULESETS_SYNC_SCRIPT_RELPATH
    if state["rulesets_sync_script_exists"]:
        skipped.append(f"{RULESETS_SYNC_SCRIPT_RELPATH} (already exists)")
    else:
        sync_script_path.parent.mkdir(parents=True, exist_ok=True)
        sync_script_path.write_text(RULESETS_SYNC_PY, encoding="utf-8")
        written.append(str(sync_script_path.relative_to(repo)))

    requirements_path = repo / RULESETS_REQUIREMENTS_RELPATH
    if state["rulesets_requirements_exists"]:
        skipped.append(f"{RULESETS_REQUIREMENTS_RELPATH} (already exists)")
    else:
        requirements_path.parent.mkdir(parents=True, exist_ok=True)
        requirements_path.write_text(RULESETS_REQUIREMENTS_TXT, encoding="utf-8")
        written.append(str(requirements_path.relative_to(repo)))

    drift_guard_path = repo / RULESETS_DRIFT_GUARD_WORKFLOW_RELPATH
    if state["rulesets_drift_guard_exists"]:
        skipped.append(f"{RULESETS_DRIFT_GUARD_WORKFLOW_RELPATH} (already exists)")
    else:
        drift_guard_path.parent.mkdir(parents=True, exist_ok=True)
        drift_guard_path.write_text(
            build_rulesets_drift_guard_workflow(branches), encoding="utf-8")
        written.append(str(drift_guard_path.relative_to(repo)))

    policy_path = repo / POLICY_RELPATH
    if state["policy_file_exists"]:
        skipped.append(f"{POLICY_RELPATH} (or a legacy policy filename) already exists")
    else:
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(
            default_policy_yaml(resolve_repo_display_name(repo), enable_aspens=args.with_aspens),
            encoding="utf-8")
        written.append(str(policy_path.relative_to(repo)))

    if args.with_aspens:
        configured, warn = enable_aspens(repo)
        if warn:
            warnings.append(warn)
        elif configured:
            written.append(".aspens.json (aspens doc init)")
        else:
            skipped.append(".aspens.json (already configured)")

    if args.with_gitnexus:
        indexed, warn = enable_gitnexus(repo)
        if warn:
            warnings.append(warn)
        elif indexed:
            written.append(".gitnexus/ (gitnexus analyze)")
        else:
            skipped.append(".gitnexus/ (already indexed)")

    if state["openspec_initialized"]:
        skipped.append("openspec/config.yaml (already initialized)")
    else:
        ok, warn = init_openspec(repo)
        if warn:
            warnings.append(warn)
        elif ok:
            written.append("openspec/ (config.yaml, specs/, changes/)")

    automerge_path = repo / AUTOMERGE_WORKFLOW_RELPATH
    if state["automerge_workflow_exists"]:
        skipped.append(f"{AUTOMERGE_WORKFLOW_RELPATH} (already exists)")
    else:
        automerge_path.parent.mkdir(parents=True, exist_ok=True)
        automerge_path.write_text(build_automerge_workflow(), encoding="utf-8")
        written.append(str(automerge_path.relative_to(repo)))
        if not state["ci_jobs_discovered"] and not required_check_configured:
            warnings.append(
                "No CI jobs discovered and no required_status_checks configured -- the "
                "auto-merge workflow just written will merge any go:risk-low/medium-labeled "
                "PR with nothing else to gate it. Add required checks to "
                ".github/rulesets/*.json before applying risk labels to real PRs.")

    openspec_validate_path = repo / OPENSPEC_VALIDATE_WORKFLOW_RELPATH
    if state["openspec_validate_workflow_exists"]:
        skipped.append(f"{OPENSPEC_VALIDATE_WORKFLOW_RELPATH} (already exists)")
    else:
        openspec_validate_path.parent.mkdir(parents=True, exist_ok=True)
        openspec_validate_path.write_text(build_openspec_validate_workflow(), encoding="utf-8")
        written.append(str(openspec_validate_path.relative_to(repo)))

    drift = compute_drift(repo, state, branches, args.branch_model)

    result = {
        "repo": str(repo),
        "branch_model": args.branch_model,
        "written": written,
        "skipped": skipped,
        "warnings": warnings,
        "ci_jobs_discovered": state["ci_jobs_discovered"],
        "drift": drift,
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
        if drift:
            print("  Drift found (skipped files that no longer match today's template --")
            print("  never auto-upgraded; review each and decide whether to regenerate):")
            for d in drift:
                print(f"    - {d['path']}: {d['detail']}")
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

    if get_delete_branch_on_merge(gh_repo):
        result["delete_branch_on_merge"] = "already enabled"
    else:
        ok = set_delete_branch_on_merge(gh_repo)
        result["delete_branch_on_merge"] = "enabled" if ok else "FAILED to enable"

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

    labels_failed = False
    if (repo / AUTOMERGE_WORKFLOW_RELPATH).is_file():
        result["labels"] = ensure_automerge_labels(gh_repo)
        labels_failed = any("FAILED" in status for status in result["labels"].values())

    if not app_credentials_configured(gh_repo):
        result["warnings"].append(
            "rulesets drift-guard workflow will skip -- install the release-notes GitHub App "
            "on this repo and set the RELEASE_NOTES_APP_ID variable / "
            "RELEASE_NOTES_APP_PRIVATE_KEY secret to enable it")

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== {gh_repo} ===")
        for branch, status in result["branches"].items():
            print(f"  branch {branch}: {status}")
        print(f"  default branch: {result['default_branch']}")
        print(f"  delete branch on merge: {result['delete_branch_on_merge']}")
        for name, status in result["rulesets"].items():
            print(f"  ruleset {name}: {status}")
        for name, status in result.get("labels", {}).items():
            print(f"  label {name}: {status}")
        for w in result["warnings"]:
            print(f"  warning: {w}")
        print()
        print("Manual follow-up:")
        print("  - retarget any other open PRs from the old default branch onto dev")
        print("  - local clones: git fetch origin && git switch dev")

    failed = (
        any("FAILED" in str(v) for v in result["branches"].values())
        or "FAILED" in str(result.get("default_branch", ""))
        or "FAILED" in str(result.get("delete_branch_on_merge", ""))
        or rulesets_failed
        or labels_failed
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
        help="write AGENTS.md/CLAUDE.md, .github/rulesets/*.json, .worktrail/policy.yaml, "
             "an OpenSpec scaffold, and an auto-merge workflow into --repo")
    propose_p.add_argument("--repo", required=True)
    propose_p.add_argument(
        "--branch-model", choices=("2", "3"), default="2",
        help="2 = dev/prd (default -- use unless the repo has a real staging environment "
             "to gate against); 3 = dev/stg/prd")
    propose_p.add_argument("--check", action="store_true", help="report current state only; write nothing")
    propose_p.add_argument(
        "--with-aspens", action="store_true",
        help="opt into the aspens skill-doc-sync add-on: declares add_ons.aspens in the "
             "seeded policy file and runs `aspens doc init` now instead of waiting for the "
             "repo's first orchestrated task")
    propose_p.add_argument(
        "--with-gitnexus", action="store_true",
        help="opt into bootstrap GitNexus indexing: runs `gitnexus analyze --embeddings "
             "--index-only` now instead of waiting for the repo's first orchestrated task")
    propose_p.add_argument("--json", action="store_true", dest="as_json")

    apply_p = subs.add_parser(
        "apply",
        help="after propose's PR merges: create branches, rename to prd, set the default "
             "branch to dev, enable delete-branch-on-merge, live-apply and verify the "
             "committed rulesets")
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
