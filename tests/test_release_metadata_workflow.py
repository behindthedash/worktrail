"""Release intent is read directly from the diff -- whether pyproject.toml's
`version` line changed -- not from a label. An ordinary feature/fix PR that
never touches pyproject.toml's version must pass unconditionally; a PR that
does change it declares release intent and its metadata (semver validity,
version increase, .codex-plugin/plugin.json sync) is validated. This guards
the committed workflow's trigger wiring and the check script's evaluation so
the label-dependent, race-prone gate (PR #307's stale-check incident; the
`has-exempt-label=false` failures on already go:no-version-bump-labeled PRs
#726/#727) can't return."""

import json
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release_metadata_check.yml"
RULESET = REPO_ROOT / ".github" / "rulesets" / "protect-main.json"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "ci" / "release_metadata_check.sh"


def _workflow() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if "on" not in wf and True in wf:
        wf["on"] = wf.pop(True)  # PyYAML YAML 1.1 resolver folds `on` to True
    return wf


def test_pull_request_trigger_has_no_label_events():
    """Release intent is diff-derived, not label-derived, so there is nothing
    for a labeled/unlabeled event to re-evaluate -- the async label-visibility
    race (PR #307, PRs #726/#727) cannot recur because this workflow no
    longer listens for label events at all."""
    wf = _workflow()
    types = wf["on"]["pull_request"]["types"]
    assert "labeled" not in types
    assert "unlabeled" not in types
    assert set(types) == {"opened", "synchronize", "reopened"}


def test_trigger_declares_only_pull_request():
    wf = _workflow()
    assert set(wf["on"]) == {"pull_request"}
    assert set(wf["on"]["pull_request"]) <= {"branches", "types"}


def test_ruleset_requires_the_release_metadata_check():
    """The ruleset must keep "Release metadata check" required -- the
    always-running gate that validates any PR declaring release intent."""
    ruleset = json.loads(RULESET.read_text(encoding="utf-8"))
    contexts = []
    for rule in ruleset["rules"]:
        if rule["type"] == "required_status_checks":
            contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert "Release metadata check" in contexts
    assert "Version bump check" not in contexts


def test_ordinary_pr_passes_without_any_version_change():
    """A PR that never touches pyproject.toml's version is not a release PR
    and must pass unconditionally -- no label, no src/worktrail/** gate."""
    result = _run_check(version_diff="", plugin_version="0.8.2")
    assert result["is_release"] == "false"
    assert result["pass"] == "true"


def test_valid_release_bump_passes():
    result = _run_check(
        version_diff='-version = "0.8.2"\n+version = "0.8.3"\n',
        plugin_version="0.8.3",
    )
    assert result["is_release"] == "true"
    assert result["pass"] == "true"


def test_release_bump_fails_when_version_decreases():
    result = _run_check(
        version_diff='-version = "0.8.3"\n+version = "0.8.2"\n',
        plugin_version="0.8.2",
    )
    assert result["is_release"] == "true"
    assert result["pass"] == "false"


def test_release_bump_fails_when_plugin_manifest_out_of_sync():
    result = _run_check(
        version_diff='-version = "0.8.2"\n+version = "0.8.3"\n',
        plugin_version="0.8.2",
    )
    assert result["is_release"] == "true"
    assert result["pass"] == "false"


def test_release_bump_fails_on_non_semver_version():
    result = _run_check(
        version_diff='-version = "0.8.2"\n+version = "0.8.3-rc1"\n',
        plugin_version="0.8.3-rc1",
    )
    assert result["is_release"] == "true"
    assert result["pass"] == "false"


def _run_check(version_diff: str, plugin_version: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        diff = Path(tmp) / "diff.txt"
        plugin_manifest = Path(tmp) / "plugin.json"
        out = Path(tmp) / "out.txt"
        diff.write_text(version_diff)
        plugin_manifest.write_text(json.dumps({"version": plugin_version}))
        subprocess.run(
            [
                "bash",
                str(CHECK_SCRIPT),
                "--version-diff",
                str(diff),
                "--plugin-manifest",
                str(plugin_manifest),
                "--github-output",
                str(out),
            ],
            check=True,
            cwd=REPO_ROOT,
        )
        parsed = {}
        for line in out.read_text().splitlines():
            key, _, value = line.partition("=")
            parsed[key] = value
        return parsed
