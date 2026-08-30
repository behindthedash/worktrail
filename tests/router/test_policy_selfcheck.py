#!/usr/bin/env python3
"""Tests for policy_selfcheck.py. Run: python3 -m pytest test_policy_selfcheck.py -q"""

import tempfile
import unittest
from pathlib import Path

from worktrail.router.policy_selfcheck import check_repo, discover_repo_names, sweep


def _git_repo(root: Path, name: str, policy_text: str) -> Path:
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    worktrail_dir = repo / ".worktrail"
    worktrail_dir.mkdir(parents=True)
    (worktrail_dir / "policy.yaml").write_text(policy_text)
    return repo


_DATALENA_POLICY = (
    "# go conductor / parallel-orchestrator policy for datalena.\n"
    'pre_pr_cmd: "PYTHONPATH=. ${DATALENA_VENV:-/home/briank/projects/datalena/.venv}/bin/python -m pytest tests/ -q"\n'
    'integrate_smoke_cmd: "PYTHONPATH=. ${DATALENA_VENV:-/home/briank/projects/datalena/.venv}/bin/python -m pytest tests/ -q"\n'
    "base_branch: dev\n"
    "automerge:\n"
    "  enabled: false\n"
    "  max_risk: low\n"
)

_CLEAN_POLICY = (
    "# go conductor / parallel-orchestrator policy for aperi.\n"
    'pre_pr_cmd: "uv run pytest"\n'
    "base_branch: main\n"
    "automerge:\n"
    "  enabled: false\n"
    "  max_risk: low\n"
)

_SELF_REFERENTIAL_POLICY = (
    "# /go per-repo policy for this repo.\n"
    'pre_pr_cmd: "npm test"\n'
    "automerge:\n"
    "  enabled: false\n"
    "  max_risk: low\n"
)


class TestCheckRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_copy_pasted_policy_flags_all_three_signals(self):
        repo = _git_repo(self.tmp, "aperi-clone", _DATALENA_POLICY)
        _git_repo(self.tmp, "datalena", _DATALENA_POLICY)
        names = discover_repo_names(self.tmp)
        result = check_repo(repo, names)
        signals = {f["signal"] for f in result["findings"]}
        self.assertEqual(signals, {"header-mismatch", "sibling-path", "sibling-envvar"})

    def test_clean_policy_has_no_findings(self):
        repo = _git_repo(self.tmp, "aperi", _CLEAN_POLICY)
        _git_repo(self.tmp, "datalena", _DATALENA_POLICY)
        names = discover_repo_names(self.tmp)
        result = check_repo(repo, names)
        self.assertEqual(result["findings"], [])

    def test_self_referential_header_not_flagged(self):
        repo = _git_repo(self.tmp, "developer-kit", _SELF_REFERENTIAL_POLICY)
        names = discover_repo_names(self.tmp)
        result = check_repo(repo, names)
        self.assertEqual(result["findings"], [])

    def test_missing_policy_file_yields_no_findings(self):
        repo = self.tmp / "no-policy"
        (repo / ".git").mkdir(parents=True)
        result = check_repo(repo, [])
        self.assertIsNone(result["source"])
        self.assertEqual(result["findings"], [])

    def test_findings_are_deduplicated(self):
        repo = _git_repo(self.tmp, "aperi-clone", _DATALENA_POLICY)
        _git_repo(self.tmp, "datalena", _DATALENA_POLICY)
        names = discover_repo_names(self.tmp)
        result = check_repo(repo, names)
        keyed = [(f["signal"], f["detail"]) for f in result["findings"]]
        self.assertEqual(len(keyed), len(set(keyed)))

    def test_missing_relative_cd_target_is_flagged(self):
        repo = _git_repo(
            self.tmp,
            "developer-kit",
            'pre_pr_cmd: "(cd plugins/renamed-away/scripts && python3 -m pytest .)"\n',
        )
        result = check_repo(repo, discover_repo_names(self.tmp))
        self.assertIn("missing-command-path", {f["signal"] for f in result["findings"]})

    def test_existing_relative_cd_target_is_clean(self):
        repo = _git_repo(
            self.tmp,
            "developer-kit",
            'pre_pr_cmd: "(cd plugins/current/scripts && python3 -m pytest .)"\n',
        )
        (repo / "plugins" / "current" / "scripts").mkdir(parents=True)
        result = check_repo(repo, discover_repo_names(self.tmp))
        self.assertNotIn(
            "missing-command-path", {f["signal"] for f in result["findings"]}
        )


class TestSweep(unittest.TestCase):
    def test_sweep_flags_only_contaminated_repo(self):
        tmp = Path(tempfile.mkdtemp())
        _git_repo(tmp, "aperi-clone", _DATALENA_POLICY)
        _git_repo(tmp, "datalena", _DATALENA_POLICY)
        _git_repo(tmp, "aperi", _CLEAN_POLICY)
        results = sweep(tmp)
        flagged = {r["repo"] for r in results if r["findings"]}
        self.assertEqual(flagged, {"aperi-clone"})

    def test_sweep_skips_repos_without_policy_file(self):
        tmp = Path(tempfile.mkdtemp())
        repo = tmp / "no-policy"
        (repo / ".git").mkdir(parents=True)
        results = sweep(tmp)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
