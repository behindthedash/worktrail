#!/usr/bin/env python3
"""Tests for policy_drift_selfcheck.py.
Run: PYTHONPATH=src python3 -m pytest tests/router/test_policy_drift_selfcheck.py -q
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router.policy_drift_selfcheck import (
    check_repo, main, orphaned_test_paths, sweep, tracked_test_files,
)

_LINT_ONLY = '"([ -d node_modules ] || npm ci) && npm run lint && npm run build"'
_PYTEST = '"PYTHONPATH=src pytest -q"'


def _policy(pre_pr_cmd: str, comments: str = "") -> str:
    head = f"# go conductor policy.\n{comments}" if comments else "# go conductor policy.\n"
    return f"{head}pre_pr_cmd: {pre_pr_cmd}\nbase_branch: main\n"


def _repo(root: Path, name: str, policy_text=None, files=None,
          workflows=None) -> Path:
    """A real git repo — `git ls-files` is the module's file source of truth."""
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if policy_text is not None:
        worktrail_dir = repo / ".worktrail"
        worktrail_dir.mkdir(parents=True)
        (worktrail_dir / "policy.yaml").write_text(policy_text)
    for rel in files or []:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# test\n")
    for name_, body in (workflows or {}).items():
        wf = repo / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / name_).write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def _signals(repo: Path):
    return {f["signal"] for f in check_repo(repo)["findings"]}


class TestOrphanedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tests_with_no_runner_anywhere_are_flagged(self):
        repo = _repo(self.tmp, "behindthedash-like", _policy(_LINT_ONLY),
                     files=["ci/scripts/test_release_notes.py"])
        self.assertIn("orphaned-tests", _signals(repo))
        self.assertEqual(orphaned_test_paths(repo),
                         ["ci/scripts/test_release_notes.py"])

    def test_gate_running_tests_is_clean(self):
        repo = _repo(self.tmp, "worktrail-like", _policy(_PYTEST),
                     files=["tests/test_thing.py"])
        self.assertEqual(_signals(repo), set())
        self.assertEqual(orphaned_test_paths(repo), [])

    def test_ci_running_tests_is_clean_even_when_gate_does_not(self):
        repo = _repo(self.tmp, "ci-only", _policy(_LINT_ONLY),
                     files=["tests/test_thing.py"],
                     workflows={"ci.yml": "jobs:\n  t:\n    steps:\n      - run: pytest -q\n"})
        self.assertEqual(_signals(repo), set())
        self.assertEqual(orphaned_test_paths(repo), [])

    def test_no_test_files_is_clean(self):
        repo = _repo(self.tmp, "no-tests", _policy(_LINT_ONLY))
        self.assertEqual(_signals(repo), set())

    def test_repo_without_policy_yields_no_source_and_no_findings(self):
        repo = _repo(self.tmp, "unmanaged", None, files=["tests/test_a.py"])
        result = check_repo(repo)
        self.assertIsNone(result["source"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(orphaned_test_paths(repo), [])

    def test_npm_run_lint_is_not_mistaken_for_a_test_runner(self):
        repo = _repo(self.tmp, "lint-only", _policy('"npm run lint"'),
                     files=["a.test.ts"])
        self.assertIn("orphaned-tests", _signals(repo))

    def test_npm_run_test_variants_count_as_a_runner(self):
        for cmd in ('"npm test"', '"npm run test"', '"npm run test:unit"',
                    '"pnpm test"', '"node --test src/x.test.mjs"'):
            with self.subTest(cmd=cmd):
                repo = _repo(self.tmp / cmd.strip('"').replace(" ", "_").replace(":", "_"),
                             "r", _policy(cmd), files=["a.test.ts"])
                self.assertEqual(orphaned_test_paths(repo), [], cmd)

    def test_vendored_test_files_are_ignored(self):
        repo = _repo(self.tmp, "vendored", _policy(_LINT_ONLY),
                     files=["vendor/pkg/test_x.py", "node_modules/p/a.test.js"])
        self.assertEqual(tracked_test_files(repo), [])
        self.assertEqual(_signals(repo), set())

    def test_recognises_multiple_test_naming_conventions(self):
        repo = _repo(self.tmp, "polyglot", _policy(_LINT_ONLY), files=[
            "test_a.py", "b_test.py", "c.test.ts", "d.spec.tsx",
            "e_test.go", "rules.test.ts", "test_f.mjs",
        ])
        self.assertEqual(len(tracked_test_files(repo)), 7)


class TestStaleClaims(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_test_suite_claim_contradicted_by_test_files(self):
        repo = _repo(self.tmp, "roost-like",
                     _policy(_PYTEST, "# No test suite in this repo.\n"),
                     files=["firestore.rules.test.ts"])
        self.assertIn("stale-claim-no-tests", _signals(repo))

    def test_no_ci_claim_contradicted_by_workflow_files(self):
        repo = _repo(self.tmp, "kudera-like",
                     _policy(_PYTEST, "# This repo has NO CI workflows.\n"),
                     workflows={"ci.yml": "jobs: {}\n"})
        self.assertIn("stale-claim-no-ci", _signals(repo))

    def test_no_lint_claim_contradicted_by_eslint_config(self):
        repo = _repo(self.tmp, "eslint-repo",
                     _policy(_PYTEST, "# No linter in this repo.\n"))
        (repo / ".eslintrc.json").write_text("{}\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        findings = {f["signal"]: f["detail"] for f in check_repo(repo)["findings"]}
        self.assertIn("stale-claim-no-lint", findings)
        self.assertIn(".eslintrc.json", findings["stale-claim-no-lint"])

    def test_no_lint_claim_contradicted_by_package_json_script(self):
        repo = _repo(self.tmp, "pkg-lint",
                     _policy(_PYTEST, "# No lint config here.\n"))
        (repo / "package.json").write_text('{"scripts": {"lint": "eslint ."}}\n')
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        self.assertIn("stale-claim-no-lint", _signals(repo))

    def test_no_lint_claim_contradicted_by_pyproject_tool_table(self):
        repo = _repo(self.tmp, "ruff-repo",
                     _policy(_PYTEST, "# No linter configured.\n"))
        (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        self.assertIn("stale-claim-no-lint", _signals(repo))

    def test_no_lint_claim_matching_reality_is_clean(self):
        repo = _repo(self.tmp, "truly-no-lint",
                     _policy(_PYTEST, "# No linter in this repo.\n"))
        self.assertNotIn("stale-claim-no-lint", _signals(repo))

    def test_lint_config_present_without_a_claim_is_not_flagged(self):
        repo = _repo(self.tmp, "quiet-lint", _policy(_PYTEST))
        (repo / ".eslintrc.json").write_text("{}\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        self.assertEqual(_signals(repo), set())

    def test_claims_matching_reality_are_clean(self):
        repo = _repo(self.tmp, "honest",
                     _policy(_PYTEST, "# No test suite in this repo. NO CI workflows.\n"))
        self.assertEqual(_signals(repo), set())

    def test_claim_text_inside_a_command_is_not_treated_as_a_claim(self):
        # The phrase appears in the command value, not an authored comment.
        repo = _repo(self.tmp, "command-text",
                     '# go conductor policy.\n'
                     'pre_pr_cmd: "echo no test suite && pytest -q"\n',
                     files=["tests/test_a.py"])
        self.assertNotIn("stale-claim-no-tests", _signals(repo))


class TestSweepAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sweep_only_includes_repos_with_a_policy(self):
        _repo(self.tmp, "managed", _policy(_PYTEST))
        _repo(self.tmp, "unmanaged", None, files=["tests/test_a.py"])
        self.assertEqual([r["repo"] for r in sweep(self.tmp)], ["managed"])

    def test_cli_exits_zero_when_clean(self):
        repo = _repo(self.tmp, "clean", _policy(_PYTEST), files=["tests/test_a.py"])
        self.assertEqual(main(["--repo", str(repo)]), 0)

    def test_cli_exits_one_when_flagged(self):
        repo = _repo(self.tmp, "dirty", _policy(_LINT_ONLY), files=["tests/test_a.py"])
        self.assertEqual(main(["--repo", str(repo)]), 1)

    def test_cli_json_shape(self):
        repo = _repo(self.tmp, "dirty", _policy(_LINT_ONLY), files=["tests/test_a.py"])
        self.assertEqual(main(["--repo", str(repo), "--json"]), 1)

    def test_cli_requires_a_target(self):
        with self.assertRaises(SystemExit):
            main([])


if __name__ == "__main__":
    unittest.main()
