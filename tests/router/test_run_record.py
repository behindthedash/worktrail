#!/usr/bin/env python3
"""Tests for run_record.py. Run: python3 test_run_record.py"""
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router.run_record import ALLOWED_AGENTS, COMPLETION_STATES, _load, main


def _start(tmp, **over):
    argv = ["start", "--repo", over.get("repo", "/tmp/fake-repo"),
            "--request", over.get("request", "fix the receipt date"),
            "--route", over.get("route", "F"),
            "--risk", over.get("risk", "low"),
            "--dir", tmp]
    if "routing_decision" in over and over["routing_decision"] is not None:
        argv += ["--routing-decision", json.dumps(over["routing_decision"])]
    out = StringIO()
    with patch("sys.stdout", out):
        rc = main(argv)
    assert rc == 0
    return json.loads(out.getvalue())


def _legacy_record_text():
    return (
        "run_id: go-20260723-000000\n"
        "started_at: 2026-07-23T00:00:00+0000\n"
        "completed_at: null\n"
        "repository: /tmp/fake-repo\n"
        "base_branch: null\n"
        "base_commit: null\n"
        "worktree: null\n"
        "request_summary: legacy request\n"
        "selected_route: F\n"
        "route_reason: null\n"
        "risk_level: low\n"
        "agent: null\n"
        "status: route_selected\n"
        "epic: null\n"
        "feature: null\n"
        "specification: null\n"
        "handoffs_consumed:\n"
        "handoffs_created:\n"
        "skills_loaded:\n"
        "subagents_called:\n"
        "files_changed:\n"
        "tests_run:\n"
        "decisions:\n"
        "assumptions:\n"
        "deferred_work:\n"
        "scope_review:\n"
        "validation_evidence:\n"
        "failure_recovery:\n"
        "interventions:\n"
        "capacity_gate: null\n"
        "pull_request: null\n"
        "merge_decision: null\n"
        "merge_result: null\n"
        "final_status: null\n"
    )


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_start_creates_record_with_required_fields(self):
        res = _start(self.tmp)
        rec = _load(Path(res["path"]))
        for field in ("run_id", "started_at", "repository", "request_summary",
                      "selected_route", "risk_level", "status", "decisions",
                      "deferred_work", "final_status"):
            self.assertIn(field, rec)
        self.assertEqual(rec["selected_route"], "F")
        self.assertEqual(rec["status"], "route_selected")
        self.assertIsNone(rec["final_status"])

    def test_agent_flag_accepted_values(self):
        for agent in ALLOWED_AGENTS:
            argv = ["start", "--repo", "/tmp/fake-repo",
                    "--request", "test agent", "--route", "J",
                    "--risk", "low", "--agent", agent, "--dir", self.tmp]
            out = StringIO()
            with patch("sys.stdout", out):
                rc = main(argv)
            self.assertEqual(rc, 0)
            res = json.loads(out.getvalue())
            rec = _load(Path(res["path"]))
            self.assertEqual(rec["agent"], agent)

    def test_agent_flag_invalid_value_rejected(self):
        with self.assertRaises(SystemExit):
            main(["start", "--repo", "/tmp/fake-repo",
                  "--request", "test", "--route", "J",
                  "--risk", "low", "--agent", "gemini", "--dir", self.tmp])

    def test_agent_flag_omitted_defaults_to_null(self):
        res = _start(self.tmp)
        rec = _load(Path(res["path"]))
        self.assertIsNone(rec["agent"])

    def test_start_with_routing_decision_roundtrips_decision_payload(self):
        routing_decision = {
            "agent_cli": "codex",
            "agent_model": "gpt-5.4-mini",
            "roles": {
                "reviewer": {"agent_cli": "claude", "agent_model": "sonnet"},
                "writer": {"agent_cli": "opencode", "agent_model": None},
            },
            "fallback": [
                {"agent_cli": "codex", "agent_model": None},
                {"agent_cli": "opencode", "agent_model": "safe/model"},
            ],
        }
        res = _start(self.tmp, routing_decision=routing_decision)
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["routing_decision"], routing_decision)
        self.assertEqual(rec["routing_decision"]["agent_cli"], "codex")
        self.assertEqual(rec["routing_decision"]["roles"]["reviewer"]["agent_model"], "sonnet")
        self.assertEqual(rec["routing_decision"]["fallback"][1]["agent_model"], "safe/model")

    def test_start_without_routing_args_keeps_current_shape(self):
        res = _start(self.tmp)
        rec = _load(Path(res["path"]))
        self.assertNotIn("routing_decision", rec)
        self.assertEqual(
            set(rec),
            {
                "run_id", "started_at", "completed_at", "repository", "base_branch",
                "base_commit", "worktree", "request_summary", "selected_route",
                "route_reason", "risk_level", "agent", "status", "epic", "feature",
                "specification", "handoffs_consumed", "handoffs_created", "skills_loaded",
                "subagents_called", "files_changed", "tests_run", "decisions",
                "assumptions", "deferred_work", "scope_review", "validation_evidence",
                "failure_recovery", "interventions", "capacity_gate", "pull_request",
                "merge_decision", "merge_result", "final_status",
            },
        )

    def test_same_second_starts_do_not_overwrite(self):
        first = _start(self.tmp, request="first request")
        second = _start(self.tmp, request="second request")
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(_load(Path(first["path"]))["request_summary"], "first request")
        self.assertEqual(_load(Path(second["path"]))["request_summary"], "second request")

    def test_legacy_record_without_routing_fields_loads_and_renders(self):
        path = Path(self.tmp) / "legacy.yaml"
        path.write_text(_legacy_record_text(), encoding="utf-8")
        rec = _load(path)
        self.assertNotIn("routing_decision", rec)
        main(["set", str(path), "status", "executing"])
        main(["append", str(path), "decisions", "legacy record still renders"])
        reread = _load(path)
        self.assertEqual(reread["status"], "executing")
        self.assertEqual(reread["decisions"], ["legacy record still renders"])

    def test_set_and_append_roundtrip(self):
        res = _start(self.tmp)
        main(["set", res["path"], "status", "executing"])
        main(["append", res["path"], "decisions", "classified F over G: spec contradicted"])
        main(["append", res["path"], "decisions", "single-worker orchestrate"])
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["status"], "executing")
        self.assertEqual(len(rec["decisions"]), 2)

    def test_set_rejects_invalid_phase(self):
        res = _start(self.tmp)
        with self.assertRaises(SystemExit):
            main(["set", res["path"], "status", "almost-done"])

    def test_finish_requires_explicit_completion_state(self):
        res = _start(self.tmp)
        with self.assertRaises(SystemExit):
            main(["finish", res["path"], "--status", "done-ish"])
        out = StringIO()
        with patch("sys.stdout", out):
            main(["finish", res["path"], "--status", "completed_pr_open",
                  "--pr", "https://github.com/x/y/pull/1"])
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["final_status"], "completed_pr_open")
        self.assertEqual(rec["status"], "done")
        self.assertIsNotNone(rec["completed_at"])
        self.assertIn("pull/1", rec["pull_request"])

    def test_all_ten_completion_states_accepted(self):
        self.assertEqual(len(COMPLETION_STATES), 10)
        for state in COMPLETION_STATES:
            res = _start(self.tmp)
            out = StringIO()
            with patch("sys.stdout", out):
                main(["finish", res["path"], "--status", state])
            self.assertEqual(_load(Path(res["path"]))["final_status"], state)

    def test_refuses_to_record_credentials(self):
        res = _start(self.tmp)
        with self.assertRaises(SystemExit):
            main(["append", res["path"], "decisions",
                  "set api_key=sk-live-123456 in env"])

    def test_values_with_special_chars_roundtrip(self):
        res = _start(self.tmp)
        tricky = 'route F: "receipt" shows #wrong date'
        main(["append", res["path"], "decisions", tricky])
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["decisions"][0], tricky)

    def test_intervention_logs_structured_entries(self):
        res = _start(self.tmp)
        main(["intervention", res["path"], "--category", "grounding",
              "--minutes", "12", "--tokens", "4000", "--note", "found existing module"])
        main(["intervention", res["path"], "--category", "quota_wait",
              "--note", "session limit; slept until reset"])
        rec = _load(Path(res["path"]))
        self.assertEqual(len(rec["interventions"]), 2)
        self.assertIn("[grounding]", rec["interventions"][0])
        self.assertIn("found existing module", rec["interventions"][0])
        self.assertIn("[quota_wait]", rec["interventions"][1])

    def test_intervention_rejects_unknown_category(self):
        res = _start(self.tmp)
        with self.assertRaises(SystemExit):
            main(["intervention", res["path"], "--category", "bogus", "--note", "x"])

    def test_capacity_gate_is_structured(self):
        res = _start(self.tmp)
        main([
            "capacity-gate", res["path"],
            "--provider", "claude:sonnet",
            "--provider", "opencode:safe/model",
            "--failure-class", "transport",
            "--retry-after", "2026-07-20T21:00:00+00:00",
            "--note", "all configured providers gated",
        ])
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["capacity_gate"]["status"], "blocked")
        self.assertEqual(rec["capacity_gate"]["providers"], ["claude:sonnet", "opencode:safe/model"])
        self.assertEqual(rec["capacity_gate"]["failure_class"], "transport")

    def test_capacity_gate_handles_chain_exhaustion_same_as_single_provider(self):
        routing_decision = {
            "agent_cli": "claude",
            "agent_model": "sonnet",
            "roles": {},
            "fallback": [
                {"agent_cli": "claude", "agent_model": None},
                {"agent_cli": "codex", "agent_model": None},
                {"agent_cli": "opencode", "agent_model": None},
            ],
        }
        res = _start(self.tmp, routing_decision=routing_decision)
        main([
            "capacity-gate", res["path"],
            "--provider", "claude:sonnet",
            "--provider", "codex:opus",
            "--provider", "opencode:safe/model",
            "--failure-class", "capacity",
            "--note", "all fallback providers exhausted",
        ])
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["status"], "executing")
        self.assertEqual(
            rec["capacity_gate"]["providers"],
            ["claude:sonnet", "codex:opus", "opencode:safe/model"],
        )
        self.assertEqual(rec["routing_decision"], routing_decision)

    def test_scope_review_records_completion_evidence(self):
        res = _start(self.tmp)
        main(["scope-review", res["path"], "--item", "seed handoff",
              "--status", "complete", "--evidence", "go_seed self-test"])
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["scope_review"], ["complete | seed handoff | go_seed self-test"])

    def test_scope_review_requires_detail(self):
        res = _start(self.tmp)
        with self.assertRaises(SystemExit):
            main(["scope-review", res["path"], "--item", "smoke", "--status", "complete"])


def _active_conflicts(tmp, **over):
    argv = ["active-conflicts",
            "--dir", tmp,
            "--repo", over.get("repo", "/tmp/fake-repo"),
            "--specification", over.get("specification", "spec-a")]
    if "exclude" in over:
        argv += ["--exclude", over["exclude"]]
    out = StringIO()
    with patch("sys.stdout", out):
        rc = main(argv)
    assert rc == 0
    return json.loads(out.getvalue())


class TestActiveConflicts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_returns_non_terminal_record_and_excludes_finished_one(self):
        active = _start(self.tmp, request="active run")
        main(["set", active["path"], "specification", "spec-a"])
        finished = _start(self.tmp, request="finished run")
        main(["set", finished["path"], "specification", "spec-a"])
        out = StringIO()
        with patch("sys.stdout", out):
            main(["finish", finished["path"], "--status", "completed_pr_open"])

        results = _active_conflicts(self.tmp, specification="spec-a")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["run_id"], active["run_id"])
        for field in ("run_id", "path", "started_at", "request_summary", "agent"):
            self.assertIn(field, results[0])

    def test_no_match_for_different_specification(self):
        other = _start(self.tmp, request="unrelated run")
        main(["set", other["path"], "specification", "spec-b"])

        results = _active_conflicts(self.tmp, specification="spec-a")

        self.assertEqual(results, [])

    def test_exclude_omits_callers_own_record(self):
        mine = _start(self.tmp, request="my run")
        main(["set", mine["path"], "specification", "spec-a"])

        results = _active_conflicts(self.tmp, specification="spec-a", exclude=mine["path"])

        self.assertEqual(results, [])

    def test_missing_run_record_directory_returns_empty_list(self):
        empty_dir = tempfile.mkdtemp()

        results = _active_conflicts(empty_dir, repo="/tmp/never-seen-repo",
                                     specification="spec-a")

        self.assertEqual(results, [])

    def test_scan_is_read_only(self):
        res = _start(self.tmp, request="untouched run")
        main(["set", res["path"], "specification", "spec-a"])
        before = Path(res["path"]).read_text(encoding="utf-8")

        _active_conflicts(self.tmp, specification="spec-a")

        after = Path(res["path"]).read_text(encoding="utf-8")
        self.assertEqual(before, after)


def _prune(tmp, **over):
    argv = ["prune", "--dir", tmp]
    if "repo" in over:
        argv += ["--repo", over["repo"]]
    argv += ["--keep-count", str(over.get("keep_count", 50))]
    argv += ["--keep-days", str(over.get("keep_days", 30))]
    if over.get("dry_run"):
        argv.append("--dry-run")
    out = StringIO()
    with patch("sys.stdout", out):
        rc = main(argv)
    assert rc == 0
    return json.loads(out.getvalue())


class TestPrune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _age(self, path, days_ago):
        ts = (
            f"2020-01-{max(1, 28 - days_ago):02d}T00:00:00+0000"
            if days_ago < 27 else "2020-01-01T00:00:00+0000"
        )
        main(["set", path, "started_at", ts])
        main(["finish", path, "--status", "completed_pr_open"])

    def test_keeps_only_keep_count_most_recent_by_default(self):
        paths = [_start(self.tmp, request=f"run {i}")["path"] for i in range(5)]
        for i, p in enumerate(paths):
            self._age(p, days_ago=i)  # run 0 newest, run 4 oldest

        result = _prune(self.tmp, keep_count=2, keep_days=0)

        repo = result["repos"][0]
        self.assertEqual(repo["kept"], 2)
        self.assertEqual(len(repo["pruned"]), 3)
        for p in paths[:2]:
            self.assertTrue(Path(p).exists())
        for p in paths[2:]:
            self.assertFalse(Path(p).exists())

    def test_keep_days_overrides_keep_count_for_recent_records(self):
        paths = [_start(self.tmp, request=f"run {i}")["path"] for i in range(3)]
        for p in paths:
            main(["finish", p, "--status", "completed_pr_open"])  # started_at stays "now"

        result = _prune(self.tmp, keep_count=0, keep_days=30)

        repo = result["repos"][0]
        self.assertEqual(repo["kept"], 3)
        self.assertEqual(repo["pruned"], [])
        for p in paths:
            self.assertTrue(Path(p).exists())

    def test_never_prunes_a_non_terminal_run(self):
        active = _start(self.tmp, request="still running")["path"]
        main(["set", active, "started_at", "2020-01-01T00:00:00+0000"])

        result = _prune(self.tmp, keep_count=0, keep_days=0)

        self.assertEqual(result["repos"][0]["pruned"], [])
        self.assertTrue(Path(active).exists())

    def test_dry_run_reports_without_deleting(self):
        paths = [_start(self.tmp, request=f"run {i}")["path"] for i in range(3)]
        for i, p in enumerate(paths):
            self._age(p, days_ago=i)

        result = _prune(self.tmp, keep_count=1, keep_days=0, dry_run=True)

        self.assertEqual(len(result["repos"][0]["pruned"]), 2)
        for p in paths:
            self.assertTrue(Path(p).exists())

    def test_repo_filter_only_prunes_matching_repo_dir(self):
        mine = _start(self.tmp, repo="/tmp/repo-a", request="mine")["path"]
        other = _start(self.tmp, repo="/tmp/repo-b", request="other")["path"]
        for p in (mine, other):
            self._age(p, days_ago=90)

        result = _prune(self.tmp, repo="/tmp/repo-a", keep_count=0, keep_days=0)

        self.assertEqual(len(result["repos"]), 1)
        self.assertEqual(result["repos"][0]["repo"], "repo-a")
        self.assertFalse(Path(mine).exists())
        self.assertTrue(Path(other).exists())

    def test_missing_dir_returns_empty_repos_list(self):
        empty = tempfile.mkdtemp()
        result = _prune(Path(empty, "does-not-exist").as_posix())
        self.assertEqual(result, {"repos": []})

    def test_rejects_negative_keep_values(self):
        with self.assertRaises(SystemExit):
            main(["prune", "--dir", self.tmp, "--keep-count", "-1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
