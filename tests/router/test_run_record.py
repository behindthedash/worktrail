#!/usr/bin/env python3
"""Tests for run_record.py. Run: python3 test_run_record.py"""
import json
import subprocess
import tempfile
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from worktrail.router.run_record import (
    ALLOWED_AGENTS,
    COMPLETION_STATES,
    _claim_ref,
    _load,
    _load_lock,
    _lock_path,
    main,
)


def _start(tmp, **over):
    argv = ["start", "--repo", over.get("repo", "/tmp/fake-repo"),
            "--request", over.get("request", "fix the receipt date"),
            "--route", over.get("route", "F"),
            "--risk", over.get("risk", "low"),
            "--dir", tmp]
    if "routing_decision" in over and over["routing_decision"] is not None:
        argv += ["--routing-decision", json.dumps(over["routing_decision"])]
    if "gates" in over and over["gates"] is not None:
        argv += ["--gates", over["gates"]]
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

    def test_start_with_gates_persists_comma_split_list(self):
        res = _start(self.tmp, gates="never_automerge,require_human_approval")
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["gates"], ["never_automerge", "require_human_approval"])

    def test_start_with_empty_gates_flag_persists_explicit_empty_list(self):
        res = _start(self.tmp, gates="")
        rec = _load(Path(res["path"]))
        self.assertIn("gates", rec)
        self.assertEqual(rec["gates"], [])

    def test_start_without_gates_flag_omits_field(self):
        res = _start(self.tmp)
        rec = _load(Path(res["path"]))
        self.assertNotIn("gates", rec)

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

    def test_route_a_blocks_implementation_completion_without_decision(self):
        res = _start(self.tmp, route="A")
        for state in ("completed_and_merged", "completed_pr_open",
                      "completed_awaiting_human_approval"):
            with self.assertRaises(SystemExit):
                main(["finish", res["path"], "--status", state])
            self.assertIsNone(_load(Path(res["path"]))["final_status"])

    def test_route_a_allows_implementation_completion_with_recorded_decision(self):
        res = _start(self.tmp, route="A")
        main(["append", res["path"], "decisions",
              "proceeding to Route D per user approval"])
        out = StringIO()
        with patch("sys.stdout", out):
            main(["finish", res["path"], "--status", "completed_pr_open",
                  "--pr", "https://github.com/x/y/pull/2"])
        rec = _load(Path(res["path"]))
        self.assertEqual(rec["final_status"], "completed_pr_open")

    def test_route_a_own_completions_allowed_without_decision(self):
        for state in ("investigation_complete", "planned_ready_for_implementation"):
            res = _start(self.tmp, route="A")
            out = StringIO()
            with patch("sys.stdout", out):
                main(["finish", res["path"], "--status", state])
            self.assertEqual(_load(Path(res["path"]))["final_status"], state)

    def test_non_route_a_unaffected_by_approval_gate(self):
        res = _start(self.tmp, route="F")
        out = StringIO()
        with patch("sys.stdout", out):
            main(["finish", res["path"], "--status", "completed_and_merged"])
        self.assertEqual(_load(Path(res["path"]))["final_status"], "completed_and_merged")

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

    def test_toctou_two_sessions_both_pass_scan_before_either_sets_specification(self):
        """Reproduces the race described in the 2026-08-07 duplicate-orchestrator
        incident report: #active-conflicts-scan is read-only and a session's own
        record is only tagged with `specification` *after* the scan comes back
        clean. Two sessions can each start a run record and each pass the scan
        before either has written its own `specification` field, so both end up
        as non-terminal records targeting the same spec -- the scan alone cannot
        prevent this, only detect it once at least one write has landed.
        """
        session_a = _start(self.tmp, request="session A implement")
        session_b = _start(self.tmp, request="session B implement")

        # Both sessions scan before either has tagged its own record with the
        # spec_id it's about to implement -- this is the exact interleaving
        # that makes the read-only scan insufficient.
        scan_a = _active_conflicts(self.tmp, specification="spec-race",
                                    exclude=session_a["path"])
        scan_b = _active_conflicts(self.tmp, specification="spec-race",
                                    exclude=session_b["path"])
        self.assertEqual(scan_a, [], "session A's scan should see no conflict yet")
        self.assertEqual(scan_b, [], "session B's scan should see no conflict yet")

        # Both proceed to tag their own record with the spec_id, believing
        # they're first -- this is the bug: nothing stopped both from reaching
        # this point.
        main(["set", session_a["path"], "specification", "spec-race"])
        main(["set", session_b["path"], "specification", "spec-race"])

        record_a = _load(Path(session_a["path"]))
        record_b = _load(Path(session_b["path"]))
        self.assertEqual(record_a["specification"], "spec-race")
        self.assertEqual(record_b["specification"], "spec-race")
        self.assertIsNone(record_a["final_status"])
        self.assertIsNone(record_b["final_status"])
        # Two distinct non-terminal runs now target the same spec_id -- the
        # duplicate-orchestrator condition. `claim` (added by this change)
        # closes this gap by making the scan-and-tag one atomic step.


def _claim(run, **over):
    argv = ["claim", run, "--specification", over.get("specification", "spec-a")]
    out = StringIO()
    with patch("sys.stdout", out):
        rc = main(argv)
    return rc, json.loads(out.getvalue())


class TestClaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_second_claim_on_same_specification_fails_fast(self):
        session_a = _start(self.tmp, request="session A implement")
        session_b = _start(self.tmp, request="session B implement")

        rc_a, out_a = _claim(session_a["path"], specification="spec-race")
        rc_b, out_b = _claim(session_b["path"], specification="spec-race")

        self.assertEqual(rc_a, 0)
        self.assertEqual(out_a["status"], "claimed")
        self.assertEqual(rc_b, 1)
        self.assertEqual(out_b["status"], "already-claimed")
        self.assertEqual(out_b["run_id"], session_a["run_id"])

        record_a = _load(Path(session_a["path"]))
        record_b = _load(Path(session_b["path"]))
        self.assertEqual(record_a["specification"], "spec-race")
        self.assertIsNone(record_b["specification"])

    def test_claim_on_different_specification_does_not_conflict(self):
        session_a = _start(self.tmp, request="session A implement")
        session_b = _start(self.tmp, request="session B implement")

        rc_a, out_a = _claim(session_a["path"], specification="spec-a")
        rc_b, out_b = _claim(session_b["path"], specification="spec-b")

        self.assertEqual(rc_a, 0)
        self.assertEqual(rc_b, 0)
        self.assertEqual(out_a["status"], "claimed")
        self.assertEqual(out_b["status"], "claimed")

    def test_finish_releases_the_claim_so_a_later_session_can_claim(self):
        session_a = _start(self.tmp, request="session A implement")
        _claim(session_a["path"], specification="spec-race")

        out = StringIO()
        with patch("sys.stdout", out):
            main(["finish", session_a["path"], "--status", "completed_pr_open"])

        session_b = _start(self.tmp, request="session B implement")
        rc_b, out_b = _claim(session_b["path"], specification="spec-race")

        self.assertEqual(rc_b, 0)
        self.assertEqual(out_b["status"], "claimed")

    def test_stale_claim_from_a_deleted_run_record_is_reclaimable(self):
        session_a = _start(self.tmp, request="session A implement, then crashes")
        _claim(session_a["path"], specification="spec-race")
        # Start session B's record before deleting A's, so `start`'s
        # same-second collision guard can't hand B the exact freed path A is
        # about to vacate (which would make this an accidental same-run-id
        # coincidence rather than a genuine second session).
        session_b = _start(self.tmp, request="session B implement")
        # Simulate a crashed session: its run record is gone but the lock
        # file it wrote survives on disk.
        Path(session_a["path"]).unlink()

        rc_b, out_b = _claim(session_b["path"], specification="spec-race")

        self.assertEqual(rc_b, 0)
        self.assertEqual(out_b["status"], "claimed")
        self.assertNotEqual(session_a["run_id"], session_b["run_id"])

    def test_claim_still_honors_pre_existing_non_terminal_conflicts(self):
        """A record tagged via plain `set` (not `claim`, e.g. from before this
        primitive existed) has no lock file, but is still a real conflict --
        `claim` must not blindly trust the absence of a lock file.
        """
        legacy = _start(self.tmp, request="legacy set-based session")
        main(["set", legacy["path"], "specification", "spec-race"])

        newcomer = _start(self.tmp, request="new claim-based session")
        rc, out = _claim(newcomer["path"], specification="spec-race")

        self.assertEqual(rc, 1)
        self.assertEqual(out["status"], "conflict")
        record_newcomer = _load(Path(newcomer["path"]))
        self.assertIsNone(record_newcomer["specification"])


@pytest.fixture()
def remote_origin(tmp_path: Path):
    """A local bare git repo standing in for `origin`, plus a working clone.

    Lets `--remote` claim tests exercise real `git push`/`fetch`/`ls-remote`
    against `_push_remote_claim`/`_read_remote_claim`/`_delete_remote_claim`
    with no network dependency and no reliance on a hosted remote's specific
    behavior. Returns `(bare_dir, clone_dir)`; pass `clone_dir` as the
    project repo (`record["repository"]`) the claim functions operate on.
    """
    bare_dir = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(bare_dir)], check=True)

    clone_dir = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(bare_dir), str(clone_dir)], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(clone_dir), "config", "user.name", "Test"], check=True)

    return bare_dir, clone_dir


class TestRemoteClaim:
    def test_first_claim_pushes_ref_and_records_remote_true(self, remote_origin, tmp_path):
        bare_dir, clone_dir = remote_origin
        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        res = _start(str(run_dir), repo=str(clone_dir), request="remote claim session")

        out = StringIO()
        with patch("sys.stdout", out):
            rc = main(["claim", res["path"], "--specification", "spec-remote", "--remote"])
        result = json.loads(out.getvalue())

        assert rc == 0
        assert result["status"] == "claimed"

        ref = _claim_ref("spec-remote")
        pushed = subprocess.run(
            ["git", "-C", str(bare_dir), "rev-parse", "--verify", ref],
            capture_output=True, text=True,
        )
        assert pushed.returncode == 0
        assert pushed.stdout.strip() != ""

        lock = _load_lock(_lock_path(Path(res["path"]), "spec-remote"))
        assert lock["remote"] is True
        assert lock["run_id"] == res["run_id"]

    def test_second_claim_while_first_is_fresh_fails_with_remote_scope_and_no_local_lock(
        self, remote_origin, tmp_path
    ):
        """Two machines never share a local run-record dir, so this uses two
        separate `--dir` roots (session A's, session B's) both pointing their
        `repository` field at the same clone -- session B's local lock
        acquisition succeeds (no visibility into session A's local lock),
        so it's the remote push conflict alone that must reject it.
        """
        bare_dir, clone_dir = remote_origin
        run_dir_a = tmp_path / "runs-a"
        run_dir_a.mkdir()
        run_dir_b = tmp_path / "runs-b"
        run_dir_b.mkdir()
        res_a = _start(str(run_dir_a), repo=str(clone_dir), request="session A remote claim")
        res_b = _start(str(run_dir_b), repo=str(clone_dir), request="session B remote claim")

        out_a = StringIO()
        with patch("sys.stdout", out_a):
            rc_a = main(["claim", res_a["path"], "--specification", "spec-remote-second", "--remote"])
        result_a = json.loads(out_a.getvalue())
        assert rc_a == 0
        assert result_a["status"] == "claimed"

        out_b = StringIO()
        with patch("sys.stdout", out_b):
            rc_b = main(["claim", res_b["path"], "--specification", "spec-remote-second", "--remote"])
        result_b = json.loads(out_b.getvalue())

        assert rc_b == 1
        assert result_b["status"] == "already-claimed"
        assert result_b["scope"] == "remote"

        lock_path_b = _lock_path(Path(res_b["path"]), "spec-remote-second")
        assert not lock_path_b.exists()
        assert _load(Path(res_b["path"]))["specification"] is None

    def test_second_claim_after_ttl_expiry_succeeds_via_stale_reclaim(self, remote_origin, tmp_path):
        """First claim publishes with `--remote-ttl-seconds 0`, so it's stale
        the instant a second reader checks it. A short sleep crosses the
        claim payload's second-precision `claimed_at` boundary, guaranteeing
        the freshness check (`age <= ttl_seconds`) sees `age > 0` rather than
        racing an exact-same-second read that would still count as fresh.
        """
        bare_dir, clone_dir = remote_origin
        run_dir_a = tmp_path / "runs-a"
        run_dir_a.mkdir()
        run_dir_b = tmp_path / "runs-b"
        run_dir_b.mkdir()
        res_a = _start(str(run_dir_a), repo=str(clone_dir), request="session A remote claim")
        res_b = _start(str(run_dir_b), repo=str(clone_dir), request="session B remote claim")

        out_a = StringIO()
        with patch("sys.stdout", out_a):
            rc_a = main([
                "claim", res_a["path"], "--specification", "spec-remote-stale", "--remote",
                "--remote-ttl-seconds", "0",
            ])
        result_a = json.loads(out_a.getvalue())
        assert rc_a == 0
        assert result_a["status"] == "claimed"

        time.sleep(1.1)

        out_b = StringIO()
        with patch("sys.stdout", out_b):
            rc_b = main(["claim", res_b["path"], "--specification", "spec-remote-stale", "--remote"])
        result_b = json.loads(out_b.getvalue())

        assert rc_b == 0
        assert result_b["status"] == "claimed"

        ref = _claim_ref("spec-remote-stale")
        pushed = subprocess.run(
            ["git", "-C", str(bare_dir), "rev-parse", "--verify", ref],
            capture_output=True, text=True,
        )
        assert pushed.returncode == 0

        lock_b = _load_lock(_lock_path(Path(res_b["path"]), "spec-remote-stale"))
        assert lock_b["remote"] is True
        assert lock_b["run_id"] == res_b["run_id"]
        assert _load(Path(res_b["path"]))["specification"] == "spec-remote-stale"


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
