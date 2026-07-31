#!/usr/bin/env python3
"""Tests for automerge_selfcheck.py. Run: python3 -m pytest test_automerge_selfcheck.py -q"""
import tempfile
import unittest
from pathlib import Path

from worktrail.router.automerge_selfcheck import check_repo, check_workflow_file, sweep


def _git_repo(root: Path, name: str, workflow_text: str,
              workflow_name: str = "auto-merge.yml") -> Path:
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / workflow_name).write_text(workflow_text)
    return repo


_UNCONDITIONAL = """\
name: 'CI: Auto-merge on open'
on:
  pull_request:
    types: [opened, reopened, ready_for_review]
jobs:
  auto-merge:
    if: ${{ !github.event.pull_request.draft }}
    runs-on: ubuntu-latest
    steps:
      - run: gh pr merge --auto --squash "${{ github.event.pull_request.number }}" -R "${{ github.repository }}"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""

_GATED = """\
name: 'CI: Auto-merge on open'
on:
  pull_request:
    types: [opened, reopened, ready_for_review]
jobs:
  auto-merge:
    if: ${{ !github.event.pull_request.draft }}
    runs-on: ubuntu-latest
    steps:
      - name: Check automerge eligibility
        id: check-automerge
        run: |
          labels=$(gh pr view "${{ github.event.pull_request.number }}" --json labels)
          echo "eligible=true" >> "$GITHUB_OUTPUT"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - run: gh pr merge --auto --squash "${{ github.event.pull_request.number }}" -R "${{ github.repository }}"
        if: steps.check-automerge.outputs.eligible == 'true'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""

_UNGUARDED_BUT_REFERENCED = """\
name: 'CI: Auto-merge on open'
on:
  pull_request:
    types: [opened, reopened, ready_for_review]
jobs:
  auto-merge:
    runs-on: ubuntu-latest
    steps:
      - name: Check automerge eligibility
        run: |
          echo "checking go:no-automerge label"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - run: gh pr merge --auto --squash "${{ github.event.pull_request.number }}" -R "${{ github.repository }}"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""

_NO_AUTOMERGE_STEP = """\
name: 'CI: Lint'
on:
  pull_request:
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: npm run lint
"""


class TestCheckWorkflowFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _write(self, text: str) -> Path:
        path = self.tmp / "auto-merge.yml"
        path.write_text(text)
        return path

    def test_unconditional_merge_step_is_flagged(self):
        result = check_workflow_file(self._write(_UNCONDITIONAL))
        self.assertTrue(result["has_automerge"])
        self.assertEqual([f["signal"] for f in result["findings"]], ["unconditional-automerge"])

    def test_gated_merge_step_is_clean(self):
        result = check_workflow_file(self._write(_GATED))
        self.assertTrue(result["has_automerge"])
        self.assertEqual(result["findings"], [])

    def test_referenced_but_unwired_label_is_flagged_distinctly(self):
        result = check_workflow_file(self._write(_UNGUARDED_BUT_REFERENCED))
        self.assertEqual([f["signal"] for f in result["findings"]], ["unguarded-merge-step"])

    def test_workflow_without_automerge_step_has_no_findings(self):
        result = check_workflow_file(self._write(_NO_AUTOMERGE_STEP))
        self.assertFalse(result["has_automerge"])
        self.assertEqual(result["findings"], [])

    def test_missing_file_yields_no_findings(self):
        result = check_workflow_file(self.tmp / "does-not-exist.yml")
        self.assertFalse(result["has_automerge"])
        self.assertEqual(result["findings"], [])

    def test_malformed_yaml_does_not_crash(self):
        path = self.tmp / "auto-merge.yml"
        path.write_text("jobs: [this is not: valid: yaml")
        result = check_workflow_file(path)
        self.assertEqual(result["findings"], [])


class TestCheckRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_unguarded_repo_is_flagged(self):
        repo = _git_repo(self.tmp, "devops", _UNCONDITIONAL)
        result = check_repo(repo)
        self.assertTrue(result["has_automerge"])
        self.assertEqual([f["signal"] for f in result["findings"]], ["unconditional-automerge"])

    def test_gated_repo_is_clean(self):
        repo = _git_repo(self.tmp, "worktrail", _GATED)
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])

    def test_repo_without_workflows_dir_is_clean(self):
        repo = self.tmp / "no-workflows"
        (repo / ".git").mkdir(parents=True)
        result = check_repo(repo)
        self.assertFalse(result["has_automerge"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["sources"], [])


class TestSweep(unittest.TestCase):
    def test_sweep_flags_only_unguarded_repo(self):
        tmp = Path(tempfile.mkdtemp())
        _git_repo(tmp, "devops", _UNCONDITIONAL)
        _git_repo(tmp, "worktrail", _GATED)
        _git_repo(tmp, "no-automerge-repo", _NO_AUTOMERGE_STEP)
        results = sweep(tmp)
        checked = {r["repo"] for r in results}
        flagged = {r["repo"] for r in results if r["findings"]}
        self.assertEqual(checked, {"devops", "worktrail"})
        self.assertEqual(flagged, {"devops"})

    def test_sweep_skips_repos_without_git(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "not-a-repo").mkdir()
        results = sweep(tmp)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
