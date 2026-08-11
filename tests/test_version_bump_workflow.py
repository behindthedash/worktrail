"""The version-bump CI must re-run when the go:no-version-bump exemption label
changes, or a PR applying it after open stays red until a no-op push (PR #307
needed an empty "chore: retrigger CI after version exemption" commit for
exactly this). This guards the committed workflow's trigger wiring and label
evaluation so the exemption can never again silently require a manual
re-trigger."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "version_bump_check.yml"
RULESET = REPO_ROOT / ".github" / "rulesets" / "protect-main.json"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "ci" / "version_bump_check.sh"


def _workflow() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if "on" not in wf and True in wf:
        wf["on"] = wf.pop(True)  # PyYAML YAML 1.1 resolver folds `on` to True
    return wf


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_pull_request_trigger_reruns_on_label_changes():
    """Applying or removing go:no-version-bump must trigger a fresh run.

    The check reads the PR's *current* labels at run time (via `gh pr view`),
    so a label-triggered run evaluates the post-change state: the label present
    passes, the label removed re-fails when a bump is required (fail-closed)."""
    wf = _workflow()
    types = wf["on"]["pull_request"]["types"]
    assert "labeled" in types
    assert "unlabeled" in types
    assert {"opened", "synchronize", "reopened"} <= set(types)


def test_trigger_never_opens_a_non_label_spam_vector():
    """The rerun must ride pull_request `labeled`/`unlabeled` events only --
    never an unconditional trigger (like `issue_comment`, `workflow_dispatch`,
    or a bare `pull_request` without explicit types) that a non-label actor
    could fire at will. The `on:` block may only declare pull_request."""
    wf = _workflow()
    assert set(wf["on"]) == {"pull_request"}
    assert set(wf["on"]["pull_request"]) <= {"branches", "types"}


def test_check_evaluates_the_current_exemption_label():
    """The workflow must read the live label set, not a stale static value, so
    label present/absent (and repeated label changes) are all evaluated against
    the PR's current state."""
    text = _workflow_text()
    assert 'gh pr view "${{ github.event.pull_request.number }}"' in text
    assert "--json labels" in text
    assert "^go:no-version-bump$" in text


def test_ruleset_requires_the_version_bump_check():
    """The ruleset must keep "Version bump check" required -- the fail-closed
    enforcement point this label-triggered rerun feeds into."""
    ruleset = json.loads(RULESET.read_text(encoding="utf-8"))
    contexts = []
    for rule in ruleset["rules"]:
        if rule["type"] == "required_status_checks":
            contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert "Version bump check" in contexts


def test_check_script_reports_pass_when_exempt_true():
    """The shell check still passes when the exemption label is present -- the
    rerun triggered by `labeled` must go green, not stay red."""
    result = _run_check(
        changed_files="src/worktrail/orchestrator/dispatch.py\n",
        version_diff="",
        has_exempt_label="true",
    )
    assert result["pass"] == "true"
    assert result["exempt"] == "true"


def test_check_script_reports_fail_when_exempt_absent():
    """Removing the exemption label (unlabeled) re-fails the check for an
    unbumped src/ change -- fail-closed preserved through label changes."""
    result = _run_check(
        changed_files="src/worktrail/orchestrator/dispatch.py\n",
        version_diff="",
        has_exempt_label="false",
    )
    assert result["pass"] == "false"
    assert result["exempt"] == "false"


def test_check_script_tolerates_repeated_label_changes():
    """Repeated label churn (apply/remove/apply) is evaluated deterministically
    against the current state each time -- no accumulation, no memory."""
    for has_exempt in ("true", "false", "true"):
        result = _run_check(
            changed_files="src/worktrail/orchestrator/dispatch.py\n",
            version_diff="",
            has_exempt_label=has_exempt,
        )
        assert result["pass"] == has_exempt


def _run_check(changed_files: str, version_diff: str, has_exempt_label: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        changed = Path(tmp) / "changed.txt"
        diff = Path(tmp) / "diff.txt"
        out = Path(tmp) / "out.txt"
        changed.write_text(changed_files)
        diff.write_text(version_diff)
        subprocess.run(
            [
                "bash",
                str(CHECK_SCRIPT),
                "--changed-files",
                str(changed),
                "--version-diff",
                str(diff),
                "--has-exempt-label",
                has_exempt_label,
                "--github-output",
                str(out),
            ],
            check=True,
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        )
        parsed = {}
        for line in out.read_text().splitlines():
            key, _, value = line.partition("=")
            parsed[key] = value
        return parsed
