#!/usr/bin/env python3
"""Regression coverage for the tail-PR fan-out dedup fix.

Reproduces the incident from handoff brief 20260816-191733: when a base
integration group quarantines, `detect_unreconciled_evidence` flags every DONE
task at once (not just DAG leaves), and pre-fix `reconcile_unreconciled_tail_evidence`
opened one full-CI PR per finding -- 10 PRs for a 10-task DAG when only the 2
leaf tasks' PRs were needed, the other 8 being byte-identical-prefix subsets of
a leaf's own cumulative branch.

Run: python3 test_tail_pr_dedup.py
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(
    0, __import__("os").path.dirname(__import__("os").path.abspath(__file__))
)

from typing import ClassVar

from worktrail.orchestrator import integrate

# The exact DAG from the incident: 1.1<-1.2<-1.3<-1.4<-1.5<-2.1<-2.2<-2.3<-2.4,
# plus 3.1 depending only on 1.5 (a second independent leaf).
INCIDENT_TASKS = [
    {"id": "1.1", "deps": []},
    {"id": "1.2", "deps": ["1.1"]},
    {"id": "1.3", "deps": ["1.2"]},
    {"id": "1.4", "deps": ["1.3"]},
    {"id": "1.5", "deps": ["1.4"]},
    {"id": "2.1", "deps": ["1.5"]},
    {"id": "2.2", "deps": ["2.1"]},
    {"id": "2.3", "deps": ["2.2"]},
    {"id": "2.4", "deps": ["2.3"]},
    {"id": "3.1", "deps": ["1.5"]},
]
INCIDENT_BY_ID = {t["id"]: t for t in INCIDENT_TASKS}


def _finding(task_id: str) -> dict:
    return {"task": task_id, "head_sha": "deadbeef", "worktree": f"/tmp/{task_id}"}


class TailDependencyClosureTest(unittest.TestCase):
    def test_closure_walks_full_chain(self):
        self.assertEqual(
            integrate._tail_dependency_closure("2.4", INCIDENT_BY_ID),
            {"1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3"},
        )

    def test_closure_of_root_is_empty(self):
        self.assertEqual(
            integrate._tail_dependency_closure("1.1", INCIDENT_BY_ID), set()
        )


class TailSupersededByMapTest(unittest.TestCase):
    def test_incident_dag_only_two_leaves_survive(self):
        findings = [_finding(t["id"]) for t in INCIDENT_TASKS]
        superseded_by = integrate._tail_superseded_by_map(findings, INCIDENT_BY_ID)

        # Every non-leaf task is superseded by *some* qualifying descendant.
        self.assertEqual(
            set(superseded_by.keys()),
            {"1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3"},
        )
        # Leaves never appear as superseded.
        self.assertNotIn("2.4", superseded_by)
        self.assertNotIn("3.1", superseded_by)
        # Every recorded descendant is itself an unsuperseded finding task id.
        finding_ids = {f["task"] for f in findings}
        for descendant in superseded_by.values():
            self.assertIn(descendant, finding_ids)

    def test_single_finding_has_no_supersession(self):
        superseded_by = integrate._tail_superseded_by_map(
            [_finding("2.4")], INCIDENT_BY_ID
        )
        self.assertEqual(superseded_by, {})

    def test_two_independent_findings_supersede_nothing(self):
        # 2.4 and 3.1 share no ancestor/descendant relationship with each other
        # directly in this finding set (only via 1.5, which isn't a finding here).
        superseded_by = integrate._tail_superseded_by_map(
            [_finding("2.4"), _finding("3.1")], INCIDENT_BY_ID
        )
        self.assertEqual(superseded_by, {})

    def test_shared_ancestor_superseded_by_either_descendant(self):
        # 1.5 is an ancestor of both 2.4 and 3.1; with all three as findings,
        # 1.5 must be superseded by one of them (deterministic pick).
        findings = [_finding("1.5"), _finding("2.4"), _finding("3.1")]
        superseded_by = integrate._tail_superseded_by_map(findings, INCIDENT_BY_ID)
        self.assertEqual(set(superseded_by.keys()), {"1.5"})
        self.assertIn(superseded_by["1.5"], {"2.4", "3.1"})


class ReconcileDedupIntegrationTest(unittest.TestCase):
    """`reconcile_unreconciled_tail_evidence` over the full incident DAG."""

    def test_only_leaf_findings_call_integrate_one(self):
        findings = [_finding(t["id"]) for t in INCIDENT_TASKS]
        called_with = []
        checkbox_tasks_by_leaf = {}

        def fake_integrate_one(g, *_args, **_kwargs):
            called_with.append(g["tasks"][0])
            # Merge/deliverable scope must stay the single leaf task id --
            # only the checkbox-write scope (asserted below) widens to include
            # superseded ancestors.
            self.assertEqual(g["tasks"], [g["tasks"][0]])
            checkbox_tasks_by_leaf[g["tasks"][0]] = g["checkbox_tasks"]

        with (
            unittest.mock.patch.object(
                integrate, "integrate_one", side_effect=fake_integrate_one
            ),
            unittest.mock.patch.object(
                integrate, "_read_group_journal_record", return_value={}
            ),
            unittest.mock.patch.object(
                integrate, "_close_superseded_tail_pr"
            ) as close_mock,
        ):
            result = integrate.reconcile_unreconciled_tail_evidence(
                findings,
                Path("/fake/repo"),
                "spec-1",
                INCIDENT_TASKS,
                "origin",
                "run-1",
                "main",
                None,
            )

        self.assertEqual(sorted(called_with), ["2.4", "3.1"])

        by_task = {r["task"]: r for r in result}
        for task_id in ("1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3"):
            self.assertEqual(by_task[task_id]["reconcile_state"], "superseded")
            self.assertEqual(by_task[task_id]["reconcile_pr_url"], "")
            # Always cites an actual leaf (one that gets a real PR below), never
            # a non-leaf intermediate that is itself superseded.
            self.assertIn(by_task[task_id]["reconcile_superseded_by"], {"2.4", "3.1"})
        self.assertEqual(close_mock.call_count, 8)

        # Regression (brief 20260830-005718 / PR #841): the leaf's own worktree
        # is stacked on its FULL dependency chain, so its cumulative branch
        # carries every superseded ancestor's commits too. `checkbox_tasks` is
        # what `_write_group_task_status` reads to decide which checkboxes to
        # tick -- if it names only the leaf, every superseded ancestor's
        # checkbox never gets ticked anywhere even though its code landed in
        # this exact merge (22 of 25 tasks lost live on run
        # go-20260829-205653 / worktrail PR #841).
        self.assertEqual(
            set(checkbox_tasks_by_leaf["2.4"]),
            {"1.1", "1.2", "1.3", "1.4", "1.5", "2.1", "2.2", "2.3", "2.4"},
            "leaf 2.4's checkbox_tasks must also tick every ancestor its "
            "cumulative branch carries, not just its own task id",
        )
        self.assertEqual(
            set(checkbox_tasks_by_leaf["3.1"]),
            {"3.1"},
            "3.1's only ancestor dependency (1.5) is superseded by 2.4, not "
            "3.1, so 3.1's own checkbox_tasks must not double-claim it",
        )

    def test_input_findings_list_not_mutated(self):
        findings = [_finding(t["id"]) for t in INCIDENT_TASKS]
        original = [dict(f) for f in findings]

        with (
            unittest.mock.patch.object(integrate, "integrate_one", return_value=None),
            unittest.mock.patch.object(
                integrate, "_read_group_journal_record", return_value={}
            ),
            unittest.mock.patch.object(integrate, "_close_superseded_tail_pr"),
        ):
            integrate.reconcile_unreconciled_tail_evidence(
                findings,
                Path("/fake/repo"),
                "spec-1",
                INCIDENT_TASKS,
                "origin",
                "run-1",
                "main",
                None,
            )

        self.assertEqual(findings, original)


class ReconcileVerifySeamTest(unittest.TestCase):
    """`reconcile_unreconciled_tail_evidence`'s post-`integrate_one` verify step."""

    SINGLE_TASK: ClassVar = [{"id": "1.1", "deps": []}]

    def test_open_state_runs_verify_one_and_reflects_merged_outcome(self):
        findings = [_finding("1.1")]
        journal_reads = [
            {
                "state": "OPEN",
                "head_branch": "run-1/tail-1.1",
                "pr_url": "https://pr/1",
            },
            {
                "state": "OPEN",
                "head_branch": "run-1/tail-1.1",
                "pr_url": "https://pr/1",
            },
            {
                "state": "MERGED",
                "head_branch": "run-1/tail-1.1",
                "pr_url": "https://pr/1",
            },
        ]
        verify_calls = []

        class FakeVerifier:
            def verify_one(
                self,
                group,
                group_branch,
                delivered,
                merged,
                quarantined,
                lock,
                **kwargs,
            ):
                verify_calls.append((group["name"], group_branch))
                merged.append(group["name"])

        write_calls = []

        def fake_write(
            journal_path,
            name,
            pr_url,
            head_branch,
            state,
            quarantine_reason="",
            quarantine_detail="",
        ):
            write_calls.append((name, state))

        with (
            unittest.mock.patch.object(integrate, "integrate_one", return_value=None),
            unittest.mock.patch.object(
                integrate, "_read_group_journal_record", side_effect=journal_reads
            ),
            unittest.mock.patch.object(
                integrate, "_write_group_journal", side_effect=fake_write
            ),
        ):
            result = integrate.reconcile_unreconciled_tail_evidence(
                findings,
                Path("/fake/repo"),
                "spec-1",
                self.SINGLE_TASK,
                "origin",
                "run-1",
                "main",
                None,
                make_verifier=lambda: FakeVerifier(),
            )

        self.assertEqual(verify_calls, [("tail-1.1", "run-1/tail-1.1")])
        self.assertEqual(write_calls, [("tail-1.1", "MERGED")])
        self.assertEqual(result[0]["reconcile_state"], "merged")
        self.assertEqual(result[0]["reconcile_pr_url"], "https://pr/1")

    def test_verify_one_exception_quarantines_only_that_finding(self):
        findings = [_finding("1.1")]
        journal_reads = [
            {"state": None},
            {
                "state": "OPEN",
                "head_branch": "run-1/tail-1.1",
                "pr_url": "https://pr/1",
            },
        ]

        class ExplodingVerifier:
            def verify_one(self, *args, **kwargs):
                raise RuntimeError("boom")

        with (
            unittest.mock.patch.object(integrate, "integrate_one", return_value=None),
            unittest.mock.patch.object(
                integrate, "_read_group_journal_record", side_effect=journal_reads
            ),
        ):
            result = integrate.reconcile_unreconciled_tail_evidence(
                findings,
                Path("/fake/repo"),
                "spec-1",
                self.SINGLE_TASK,
                "origin",
                "run-1",
                "main",
                None,
                make_verifier=lambda: ExplodingVerifier(),
            )

        self.assertEqual(result[0]["reconcile_state"], "quarantined")
        self.assertEqual(result[0]["reconcile_pr_url"], "")


class CloseSupersededTailPrTest(unittest.TestCase):
    def test_noop_when_journal_has_no_open_record(self):
        with (
            unittest.mock.patch.object(
                integrate,
                "_read_group_journal_record",
                return_value={"state": "QUARANTINED"},
            ),
            unittest.mock.patch(
                "worktrail.orchestrator.integrate.subprocess.run"
            ) as run_mock,
        ):
            integrate._close_superseded_tail_pr(
                Path("/fake/repo"), "origin", "tail-1.1", None
            )
        run_mock.assert_not_called()

    def test_closes_pr_and_cancels_nonterminal_runs_when_open(self):
        journal_record = {"state": "OPEN", "head_branch": "run-1/tail-1.1"}
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            result = unittest.mock.Mock()
            result.returncode = 0
            if cmd[:2] == ["gh", "run"] and cmd[2] == "list":
                result.stdout = (
                    '[{"databaseId": 111, "status": "completed"}, '
                    '{"databaseId": 222, "status": "in_progress"}]'
                )
            else:
                result.stdout = ""
            result.stderr = ""
            return result

        with (
            unittest.mock.patch.object(
                integrate, "_read_group_journal_record", return_value=journal_record
            ),
            unittest.mock.patch.object(
                integrate,
                "_git",
                return_value=unittest.mock.Mock(
                    stdout="https://github.com/acme/repo.git\n"
                ),
            ),
            unittest.mock.patch(
                "worktrail.orchestrator.integrate.subprocess.run", side_effect=fake_run
            ),
        ):
            integrate._close_superseded_tail_pr(
                Path("/fake/repo"), "origin", "tail-1.1", "/fake/journal.json"
            )

        close_calls = [c for c in calls if c[:2] == ["gh", "pr"]]
        list_calls = [c for c in calls if c[:3] == ["gh", "run", "list"]]
        cancel_calls = [c for c in calls if c[:3] == ["gh", "run", "cancel"]]

        self.assertEqual(len(close_calls), 1)
        self.assertIn("run-1/tail-1.1", close_calls[0])
        self.assertEqual(len(list_calls), 1)
        # Only the non-terminal run (222) gets cancelled; the completed one (111) does not.
        self.assertEqual(len(cancel_calls), 1)
        self.assertIn("222", cancel_calls[0])
        self.assertNotIn("111", " ".join(str(c) for c in cancel_calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
