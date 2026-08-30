#!/usr/bin/env python3
"""Unit tests for check_req_coverage.py.

Run directly (from this directory): python3 test_check_req_coverage.py
Or as part of the go skill's suite: python3 -m pytest . -q

The `AutomationHealthDigestFixtureTests` fixture is derived from the shape of
the real `docs/specs/084-automation-health-digest/` corpus (dated spec
filename, no `spec.md`, requirements table split across sections, a
consecutive-failure-tracking block no task ever claimed) -- the originating
defect this gate exists to catch.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_req_coverage import (
    audit_all_specs,
    check_changed_specs,
    check_spec_coverage,
    collect_task_references,
    discover_declared_identifiers,
)


def _task_file(reqs=(), ac_mapping=(), imp_requirements=()) -> str:
    def _yaml_list(name: str, values) -> str:
        if not values:
            return f"{name}: []\n"
        items = ", ".join(values)
        return f"{name}: [{items}]\n"

    return (
        "---\n"
        "id: TASK-001\n"
        "title: Fixture task\n"
        "spec: docs/specs/fixture/spec.md\n"
        "status: pending\n"
        + _yaml_list("reqs", reqs)
        + _yaml_list("ac-mapping", ac_mapping)
        + _yaml_list("imp-requirements", imp_requirements)
        + "---\n\n"
        "## Acceptance Criteria\n\n- [ ] done\n\n"
        "## Definition of Done (DoD)\n\n- [ ] done\n"
    )


class DiscoverDeclaredIdentifiersTests(unittest.TestCase):
    def test_table_row_shape_is_declared(self) -> None:
        text = "| REQ-001 | The system SHALL do the thing. | Generic |\n"
        self.assertEqual(discover_declared_identifiers(text), {"REQ-001"})

    def test_bullet_shape_is_declared(self) -> None:
        text = "- REQ-NR001: The system SHALL NOT do the other thing.\n"
        self.assertEqual(discover_declared_identifiers(text), {"REQ-NR001"})

    def test_non_req_prefix_recognised(self) -> None:
        text = (
            "| FR-273 | The system SHALL support the feature. | Generic |\n"
            "- AUTHZ-001: The system SHALL NOT bypass the check.\n"
        )
        self.assertEqual(discover_declared_identifiers(text), {"FR-273", "AUTHZ-001"})

    def test_nr_subnamespace_distinct_from_plain_counterpart(self) -> None:
        text = (
            "| REQ-001 | The system SHALL do the thing. | Generic |\n"
            "- REQ-NR001: The system SHALL NOT do the other thing.\n"
        )
        declared = discover_declared_identifiers(text)
        self.assertEqual(declared, {"REQ-001", "REQ-NR001"})
        self.assertNotEqual({"REQ-001"}, {"REQ-NR001"})

    def test_prose_cross_reference_is_not_declared(self) -> None:
        text = (
            "This behavior builds on REQ-001 and must not regress AUTHZ-001,\n"
            "as discussed elsewhere in this document.\n"
        )
        self.assertEqual(discover_declared_identifiers(text), set())

    def test_bullet_without_colon_is_not_declared(self) -> None:
        # A bullet head is only a declaration when followed by the separating
        # colon (D1) -- otherwise it's an ordinary list item that happens to
        # start with an identifier-shaped token.
        text = "- REQ-002 requires further design work.\n"
        self.assertEqual(discover_declared_identifiers(text), set())


class CollectTaskReferencesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec_dir = Path(self.tmp.name) / "docs" / "specs" / "fixture"
        self.spec_dir.mkdir(parents=True)

    def _write_task(self, name: str, content: str) -> None:
        tasks_dir = self.spec_dir / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / name).write_text(content, encoding="utf-8")

    def test_union_of_reqs_ac_mapping_imp_requirements(self) -> None:
        self._write_task(
            "TASK-001.md",
            _task_file(
                reqs=["REQ-001"], ac_mapping=["AC-002"], imp_requirements=["REQ-003"]
            ),
        )
        self.assertEqual(
            collect_task_references(self.spec_dir), {"REQ-001", "AC-002", "REQ-003"}
        )

    def test_coverage_satisfied_via_ac_mapping_alone(self) -> None:
        self._write_task("TASK-001.md", _task_file(ac_mapping=["REQ-010"]))
        self.assertIn("REQ-010", collect_task_references(self.spec_dir))

    def test_spec_with_no_tasks_dir_returns_empty_set(self) -> None:
        self.assertEqual(collect_task_references(self.spec_dir), set())


class GitRepoTestCase(unittest.TestCase):
    """Shared helpers for tests that need a real throwaway git repo."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )

    def _init_repo(self, initial_branch: str = "main") -> str:
        d = tempfile.mkdtemp(prefix="req-coverage-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d], check=False))
        self._git(d, "init", "-q", "-b", initial_branch)
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


class RatchetTests(GitRepoTestCase):
    """check_spec_coverage's newly-declared-only comparison against base_ref (D2)."""

    def test_newly_declared_uncovered_fails(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo,
            "docs/specs/100-fixture/spec.md",
            "| REQ-100 | The system SHALL do the thing. | Generic |\n",
        )
        self._commit(repo, "add spec declaring REQ-100")

        failures = check_spec_coverage(
            Path(repo), "docs/specs/100-fixture/spec.md", "main"
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("REQ-100", failures[0])

    def test_preexisting_uncovered_on_touched_spec_passes(self) -> None:
        repo = self._init_repo()
        self._write(
            repo,
            "docs/specs/200-fixture/spec.md",
            "| REQ-200 | The system SHALL do the thing. | Generic |\n",
        )
        self._commit(repo, "pre-existing spec with an uncovered requirement")

        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo,
            "docs/specs/200-fixture/spec.md",
            "| REQ-200 | The system SHALL do the thing. | Generic |\n"
            "\nSome unrelated prose clarification.\n",
        )
        self._commit(repo, "touch spec for an unrelated reason")

        failures = check_spec_coverage(
            Path(repo), "docs/specs/200-fixture/spec.md", "main"
        )
        self.assertEqual(failures, [])

    def test_requirement_added_together_with_its_coverage_passes(self) -> None:
        repo = self._init_repo()
        self._write(
            repo,
            "docs/specs/300-fixture/spec.md",
            "| REQ-300 | The system SHALL do the thing. | Generic |\n",
        )
        self._commit(repo, "pre-existing spec with an uncovered requirement")

        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo,
            "docs/specs/300-fixture/spec.md",
            "| REQ-300 | The system SHALL do the thing. | Generic |\n"
            "| REQ-301 | The system SHALL do the new thing. | Generic |\n",
        )
        self._write(
            repo,
            "docs/specs/300-fixture/tasks/TASK-001.md",
            _task_file(reqs=["REQ-301"]),
        )
        self._commit(repo, "add REQ-301 with its covering task")

        failures = check_spec_coverage(
            Path(repo), "docs/specs/300-fixture/spec.md", "main"
        )
        self.assertEqual(failures, [])

    def test_brand_new_spec_enforces_every_identifier(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo,
            "docs/specs/400-fixture/spec.md",
            "| REQ-400 | The system SHALL do the thing. | Generic |\n"
            "- REQ-NR001: The system SHALL NOT do the other thing.\n",
        )
        self._commit(repo, "add brand-new spec")

        failures = check_spec_coverage(
            Path(repo), "docs/specs/400-fixture/spec.md", "main"
        )
        self.assertEqual(len(failures), 2)
        joined = "\n".join(failures)
        self.assertIn("REQ-400", joined)
        self.assertIn("REQ-NR001", joined)

    def test_nr_subnamespace_enforced_independently_of_plain_counterpart(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo,
            "docs/specs/500-fixture/spec.md",
            "| REQ-001 | The system SHALL do the thing. | Generic |\n"
            "- REQ-NR001: The system SHALL NOT do the other thing.\n",
        )
        self._write(
            repo,
            "docs/specs/500-fixture/tasks/TASK-001.md",
            _task_file(reqs=["REQ-001"]),
        )
        self._commit(repo, "cover only the plain identifier")

        failures = check_spec_coverage(
            Path(repo), "docs/specs/500-fixture/spec.md", "main"
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("REQ-NR001", failures[0])


class CheckChangedSpecsTests(GitRepoTestCase):
    def test_only_docs_specs_paths_are_checked(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "src/app.py", "| REQ-001 | not a spec | Generic |\n")
        self._commit(repo, "unrelated source change")

        self.assertEqual(check_changed_specs(Path(repo), ["src/app.py"], "main"), [])

    def test_resolves_real_dated_spec_filename_not_spec_md(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        relpath = "docs/specs/084-fixture/2026-07-18--fixture.md"
        self._write(
            repo, relpath, "| REQ-001 | The system SHALL do the thing. | Generic |\n"
        )
        self._commit(repo, "add dated-filename spec")

        failures = check_changed_specs(Path(repo), [relpath], "main")
        self.assertEqual(len(failures), 1)
        self.assertIn(relpath, failures[0])


class BaseRefUnresolvableTests(GitRepoTestCase):
    def test_cli_skips_rather_than_fails_when_base_ref_unresolvable(self) -> None:
        # An initial branch name that collides with none of the candidate
        # base refs (origin/main, origin/master, main, master) and no
        # origin remote at all -- base ref resolution has nothing to find.
        repo = self._init_repo(initial_branch="trunk")
        self._write(
            repo,
            "docs/specs/600-fixture/spec.md",
            "| REQ-600 | The system SHALL do the thing. | Generic |\n",
        )
        self._commit(repo, "add spec")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "worktrail.router.check_req_coverage",
                "--repo",
                repo,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SKIP", result.stdout)


class AuditModeTests(GitRepoTestCase):
    def test_audit_enumerates_preexisting_gaps(self) -> None:
        # audit_all_specs is opt-in, repo-wide, and un-scoped by the ratchet
        # -- a gap that has existed since before any tracked base ref must
        # still surface here, unlike in check_spec_coverage.
        repo = self._init_repo()
        self._write(
            repo,
            "docs/specs/700-fixture/spec.md",
            "| REQ-700 | The system SHALL do the thing. | Generic |\n"
            "| REQ-701 | The system SHALL do the covered thing. | Generic |\n",
        )
        self._write(
            repo,
            "docs/specs/700-fixture/tasks/TASK-001.md",
            _task_file(reqs=["REQ-701"]),
        )
        self._commit(repo, "long-since-merged spec with one uncovered requirement")

        failures = audit_all_specs(Path(repo))
        self.assertEqual(len(failures), 1)
        self.assertIn("REQ-700", failures[0])


AUTOMATION_HEALTH_DIGEST_SPEC = """# Feature Specification: Automation Layer Health Digest

## Requirements

### Loop Status Signals

| ID | Requirement | Type |
|---|---|---|
| REQ-001 | The system SHALL query, for each of the 5 loops' producing workflows, the most recently completed workflow run. | Generic |
| REQ-002 | For loops 2 through 5, the system SHALL determine that loop's status from the conclusion of its most recently completed run. | Generic |
| REQ-003 | WHEN a loop's most recent run conclusion is `success` THEN the system SHALL report that loop's status as healthy. | Event |
| REQ-004 | WHEN a loop's most recent run conclusion is not `success` THEN the system SHALL report that loop's status as needs attention. | Event |
| REQ-005 | IF no completed run can be found for a loop's producing workflow THEN the system SHALL report that loop's status as unknown. | Negative |
| REQ-006 | For loop 1, the system SHALL determine that loop's status from the count of currently open `workflow-failure` issues. | Generic |
| REQ-007 | WHEN loop 1 has zero open `workflow-failure` issues THEN the system SHALL report its status as no active failures. | Event |
| REQ-008 | WHEN loop 1 has one or more open `workflow-failure` issues THEN the system SHALL report its status as N active failure(s). | Event |
| REQ-009 | For loop 4, the system SHALL additionally report open `e2e-heal-escalation` and `a11y-finding` counts. | Generic |
| REQ-010 | For loop 5, the system SHALL additionally report the open `drift-harness-regression` count. | Generic |
| REQ-011 | The system SHALL NOT compute an open-issue-count signal for loop 2 or loop 3. | Negative |
| REQ-012 | For each loop, the system SHALL report the timestamp of the signal used for that loop's status and a human-readable age. | Generic |
| REQ-013 | WHEN loop 4's or loop 5's most recent run is older than 36 hours THEN the system SHALL flag that loop's digest entry as stale. | Event |
| REQ-014 | The system SHALL NOT apply a staleness flag to loops 1, 2, or 3. | Negative |

### Reporting and Persistence

| REQ-015 | The system SHALL render one combined report listing all 5 loops. | Generic |
| REQ-016 | The system SHALL write the combined report to the workflow run's `$GITHUB_STEP_SUMMARY` on every invocation. | Generic |
| REQ-017 | WHEN no auto-managed digest issue currently exists THEN the system SHALL create one. | Event |
| REQ-018 | WHEN that issue already exists THEN the system SHALL update its body rather than opening a new issue. | Event |
| REQ-019 | IF the auto-managed digest issue is currently closed THEN the system SHALL reopen it when updating its body. | Feature |
| REQ-020 | The system SHALL include the digest run's own timestamp in both the step summary and the issue body. | Generic |
| REQ-021 | IF collecting a signal for one loop fails THEN the system SHALL report that loop's status as unknown/query failed and still produce a complete report. | Negative |
| REQ-022 | The system SHALL support both a nightly scheduled trigger and a manual on-demand trigger. | Feature |

### Loop-Level Consecutive-Failure Tracking (Scheduled Workflows)

| REQ-023 | For each producing workflow whose trigger includes a `schedule` event, the system SHALL additionally report that workflow's consecutive-failure count. | Generic |
| REQ-024 | The consecutive-failure count SHALL be the number of immediately preceding completed scheduled-triggered runs whose conclusion was not `success`. | Generic |
| REQ-025 | WHEN any scheduled workflow's consecutive-failure count reaches 2 or more THEN the system SHALL flag that loop's digest entry. | Event |
| REQ-026 | The consecutive-failure count SHALL be maintained in the auto-managed digest issue body as a JSON block so it persists across digest runs. | Generic |
| REQ-027 | IF a scheduled workflow has fewer than 2 completed scheduled-triggered runs in the lookback window THEN the system SHALL report its consecutive-failure count as insufficient data. | Negative |
| REQ-028 | The system MAY include additional scheduled-only workflows beyond the 5 existing loops in this consecutive-failure tracking. | Feature |

### Loop 2 Anchor Telemetry

| REQ-029 | For loop 2, the system SHALL report the count of `invalid_anchor` telemetry events found in that run's job logs. | Generic |
| REQ-030 | The system SHALL retain loop 2's `invalid_anchor` counts from the most recent 14 digest runs as a JSON block. | Generic |

## Non-Requirements

- REQ-NR001: The system SHALL NOT modify, comment on, close, merge, or relabel any issue outside its own auto-managed digest issue.
- REQ-NR002: The system SHALL NOT change any of the 5 existing loops' workflow files, scripts, or configuration.
"""


class AutomationHealthDigestFixtureTests(GitRepoTestCase):
    """Fixture derived from the real docs/specs/084-automation-health-digest/
    shape: a dated spec filename (not spec.md), and task files whose union of
    `reqs` covers every declared identifier except REQ-023..REQ-028 -- the
    consecutive-failure-tracking block no task ever claimed in the originating
    defect."""

    def test_audit_reports_the_known_uncovered_range(self) -> None:
        repo = self._init_repo()
        spec_dir = "docs/specs/084-automation-health-digest"
        self._write(
            repo,
            f"{spec_dir}/2026-07-18--automation-health-digest.md",
            AUTOMATION_HEALTH_DIGEST_SPEC,
        )
        self._write(
            repo,
            f"{spec_dir}/tasks/TASK-001.md",
            _task_file(reqs=[f"REQ-{n:03d}" for n in range(1, 15)] + ["REQ-NR001"]),
        )
        self._write(
            repo,
            f"{spec_dir}/tasks/TASK-002.md",
            _task_file(reqs=[f"REQ-{n:03d}" for n in range(15, 23)] + ["REQ-NR002"]),
        )
        self._write(
            repo,
            f"{spec_dir}/tasks/TASK-003.md",
            _task_file(reqs=["REQ-029", "REQ-030"]),
        )
        self._commit(repo, "084-automation-health-digest corpus fixture")

        failures = audit_all_specs(Path(repo))
        uncovered = {f.split("identifier ")[1] for f in failures}
        self.assertEqual(uncovered, {f"REQ-{n:03d}" for n in range(23, 29)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
