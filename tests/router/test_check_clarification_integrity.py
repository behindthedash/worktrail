#!/usr/bin/env python3
"""Unit tests for check_clarification_integrity.py.

Run directly (from this directory): python3 test_check_clarification_integrity.py
Or as part of the go skill's suite: python3 -m pytest . -q

Reproduces the exact anti-pattern the Decision Classification rule in
specs.spec-check.md (PR #428) forbids: an owner-escalated
[NEEDS CLARIFICATION] marker resolved by inference from the locked schema's
shape, then the spec still declaring "Open Questions: None outstanding".
Motivating case: behindthedash/datalena PR #1934 (spec 098), flagged by an
owner audit after two markers were resolved this way and reported clean.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_clarification_integrity import (
    check_changed_specs, check_text,
)

CLEAN_SPEC = """# Functional Specification: Fixture

## Clarifications

### Session 2026-07-24

- Q: What payment providers should be supported? → A: Stripe + PayPal [agent-resolved]

## Open Questions

None outstanding.
"""

INFERRED_AND_DECLARED_CLEAN_SPEC = """# Functional Specification: Fixture

## Clarifications

### Session 2026-07-24

- Q: Must workspace slugs be globally unique? → A: Yes, globally unique — the
  locked schema has no representation for org-scoped slug namespaces [agent-resolved]

## Open Questions

Open Questions: None outstanding
"""

DECISION_NEEDED_SPEC = """# Functional Specification: Fixture

## Clarifications

None resolved this session.

## Open Questions

1 of 1 [NEEDS CLARIFICATION] markers require an owner decision before this
spec is considered clean: workspace slug uniqueness.
"""

INFERENCE_WITHOUT_ALL_CLEAR_SPEC = """# Functional Specification: Fixture

## Clarifications

- Q: Must workspace slugs be globally unique? → A: Yes — cannot be expressed
  otherwise given the locked schema [agent-resolved]

## Open Questions

Decision needed: see above.
"""


class CheckTextTests(unittest.TestCase):
    def test_clean_spec_passes(self) -> None:
        self.assertEqual(check_text(CLEAN_SPEC), [])

    def test_inferred_and_declared_clean_fails(self) -> None:
        failures = check_text(INFERRED_AND_DECLARED_CLEAN_SPEC)
        self.assertEqual(len(failures), 1)
        self.assertIn("no representation for", failures[0])

    def test_decision_needed_without_all_clear_passes(self) -> None:
        self.assertEqual(check_text(DECISION_NEEDED_SPEC), [])

    def test_inference_language_without_all_clear_line_passes(self) -> None:
        # Inference language alone isn't the failure -- only its co-occurrence
        # with a declared-clean line is.
        self.assertEqual(check_text(INFERENCE_WITHOUT_ALL_CLEAR_SPEC), [])

    def test_resolved_from_locked_schema_structure_variant(self) -> None:
        text = (
            "## Clarifications\n\n- Q: x? -> A: y, resolved from the locked "
            "schema's own structure [agent-resolved]\n\n"
            "## Open Questions\n\nnothing outstanding\n"
        )
        failures = check_text(text)
        self.assertEqual(len(failures), 1)


class CheckChangedSpecsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _write(self, relpath: str, content: str) -> None:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_only_docs_specs_md_paths_are_checked(self) -> None:
        self._write("src/app.py", "cannot be expressed\nnone outstanding\n")
        self.assertEqual(check_changed_specs(self.repo, ["src/app.py"]), [])

    def test_changed_spec_with_anti_pattern_fails(self) -> None:
        self._write(
            "docs/specs/098-fixture/spec.md", INFERRED_AND_DECLARED_CLEAN_SPEC
        )
        failures = check_changed_specs(
            self.repo, ["docs/specs/098-fixture/spec.md"]
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("docs/specs/098-fixture/spec.md", failures[0])

    def test_unchanged_spec_is_not_scanned(self) -> None:
        # File exists on disk but isn't in the changed-paths list -- must be
        # ignored, since pre-existing merged specs are out of scope for this
        # check (see handoff brief 20260724-161249-fleet-spec-inference-...).
        self._write(
            "docs/specs/098-fixture/spec.md", INFERRED_AND_DECLARED_CLEAN_SPEC
        )
        self.assertEqual(check_changed_specs(self.repo, []), [])

    def test_clean_changed_spec_passes(self) -> None:
        self._write("docs/specs/099-fixture/spec.md", CLEAN_SPEC)
        self.assertEqual(
            check_changed_specs(self.repo, ["docs/specs/099-fixture/spec.md"]), []
        )


class MainCliGitIntegrationTests(unittest.TestCase):
    """Exercises main()'s own git-diff resolution against a real throwaway repo."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self) -> str:
        d = tempfile.mkdtemp(prefix="clarify-")
        self._git(d, "init", "-q", "-b", "main")
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        (Path(d) / "README.md").write_text("x\n", encoding="utf-8")
        self._git(d, "add", ".")
        self._git(d, "commit", "-q", "-m", "base")
        return d

    def _write(self, repo: str, relpath: str, content: str) -> None:
        path = Path(repo) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _commit(self, repo: str, message: str) -> None:
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", message)

    def test_cli_fails_on_newly_added_inferred_spec(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo, "docs/specs/098-fixture/spec.md", INFERRED_AND_DECLARED_CLEAN_SPEC
        )
        self._commit(repo, "add spec")

        result = subprocess.run(
            [sys.executable, "-m", "worktrail.router.check_clarification_integrity",
             "--repo", repo], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/specs/098-fixture/spec.md", result.stdout)

    def test_cli_passes_on_clean_changed_spec(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/099-fixture/spec.md", CLEAN_SPEC)
        self._commit(repo, "add spec")

        result = subprocess.run(
            [sys.executable, "-m", "worktrail.router.check_clarification_integrity",
             "--repo", repo], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cli_ignores_pre_existing_inferred_spec_not_in_diff(self) -> None:
        repo = self._init_repo()
        self._write(
            repo, "docs/specs/098-fixture/spec.md", INFERRED_AND_DECLARED_CLEAN_SPEC
        )
        self._commit(repo, "pre-existing spec on main")
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/098-fixture/unrelated.md", "unrelated change\n")
        self._commit(repo, "unrelated change")

        result = subprocess.run(
            [sys.executable, "-m", "worktrail.router.check_clarification_integrity",
             "--repo", repo], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
