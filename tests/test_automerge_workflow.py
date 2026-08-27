"""auto-merge.yml triggers on pull_request labeled/unlabeled alongside
opened/reopened/ready_for_review with no workflow-level concurrency group at
all, so overlapping runs for the same PR (e.g. two `gh pr create --label`
flags fire two separate `labeled` events) race the "Check automerge
eligibility" -> "Arm"/"Disarm" steps against each other with no serialization.
Whichever run's arm/disarm step executes last wins, regardless of which read
the PR's current (correct) label state -- the same class of stuck-state bug
the version-bump check's own concurrency group once guarded against for its
own labeled/unlabeled trigger (`tests/test_release_metadata_workflow.py`;
that check no longer listens for labeled/unlabeled at all, since release
intent there is now read from the diff instead), with an even sharper edge
here because there was no concurrency group whatsoever to build on."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-merge.yml"


def _workflow() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if "on" not in wf and True in wf:
        wf["on"] = wf.pop(True)  # PyYAML YAML 1.1 resolver folds `on` to True
    return wf


def test_workflow_declares_a_concurrency_group():
    """A `labeled`/`unlabeled` event must not be able to interleave with an
    `opened`/`reopened`/`ready_for_review` run for the same PR with no
    coordination at all."""
    wf = _workflow()
    assert "concurrency" in wf, (
        "auto-merge.yml has no concurrency group -- overlapping "
        "opened/labeled/unlabeled runs for the same PR can race the "
        "arm/disarm steps against each other"
    )
    assert wf["concurrency"]["cancel-in-progress"] is True


def test_concurrency_group_keys_on_pr_action_and_label():
    """The group must include the PR number (so different PRs never share a
    group), the triggering action (so a `labeled` run never cancels an
    in-flight `opened`/`synchronize` run for the same PR -- the exact
    stuck-check incident the version-bump check's own group once guarded
    against, PRs #344-#346), and the label name (so two labels applied in the
    same PR creation -- two separate `labeled` events -- don't share one
    `-labeled` group and cancel each other, PR #393)."""
    wf = _workflow()
    group = wf["concurrency"]["group"]
    assert "github.event.pull_request.number" in group
    assert "github.event.action" in group
    assert "github.event.label.name" in group
