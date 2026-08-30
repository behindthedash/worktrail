#!/usr/bin/env python3
"""Extra coverage for dispatch.py: group prompts, parse_report_back error paths,
transition edge cases."""

import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import dispatch
from worktrail.orchestrator.dispatch import (
    DecisionDispatchError,
    validate_resolved_decision_input,
)
from worktrail.workqueue import decisions as decisions_mod


def _ctx(**kw):
    base = {
        "spec_id": "004-test",
        "spec_folder": "docs/specs/004-test/",
        "worktree_path": "/tmp/wt/004-test",
        "branch": "task/TASK-001",
        "base_commit": "abc1234",
        "group_branch": "run-001/base",
        "base_branch": "main",
    }
    base.update(kw)
    return base


def _group(**kw):
    base = {
        "name": "base",
        "tasks": ["TASK-001", "TASK-002"],
        "reqs": ["REQ-001"],
        "depends_on": [],
    }
    base.update(kw)
    return base


class BuildWorkerPromptTests(unittest.TestCase):
    def test_unknown_role_raises(self):
        task = {"id": "TASK-001", "files": ["src/a.ts"]}
        with self.assertRaises(ValueError) as cm:
            dispatch.build_worker_prompt("bad-role", task, _ctx())
        self.assertIn("bad-role", str(cm.exception))

    def test_worker_prompt_explains_gitnexus_worktree_boundary(self):
        task = {"id": "TASK-001", "files": ["src/a.ts"]}
        prompt = dispatch.build_worker_prompt(dispatch.ROLE_IMPLEMENT, task, _ctx())
        self.assertIn("generated worktree normally has no GitNexus index", prompt)
        self.assertIn("the worktree wins", prompt)
        self.assertIn("do not search for or create", prompt)
        self.assertIn("GitNexus capability preflight: unavailable", prompt)


class BuildGroupPromptTests(unittest.TestCase):
    def test_unknown_group_role_raises(self):
        with self.assertRaises(ValueError) as cm:
            dispatch.build_group_prompt("bad-role", _group(), _ctx())
        self.assertIn("bad-role", str(cm.exception))

    def test_resolve_prompt_contains_group_name(self):
        prompt = dispatch.build_group_prompt(
            dispatch.ROLE_RESOLVE, _group(name="feature-2"), _ctx()
        )
        self.assertIn("feature-2", prompt)
        self.assertIn("CONFLICTING", prompt)

    def test_resolve_prompt_includes_merge_instructions(self):
        prompt = dispatch.build_group_prompt(dispatch.ROLE_RESOLVE, _group(), _ctx())
        self.assertIn("git fetch", prompt)
        self.assertIn("git push", prompt)

    def test_ci_fix_prompt_contains_failing_checks(self):
        ctx = _ctx(
            failing_checks="lint, typecheck",
            failure_log="Error: type mismatch at line 42",
        )
        prompt = dispatch.build_group_prompt(dispatch.ROLE_CI_FIX, _group(), ctx)
        self.assertIn("lint, typecheck", prompt)
        self.assertIn("type mismatch at line 42", prompt)

    def test_ci_fix_prompt_default_no_log(self):
        prompt = dispatch.build_group_prompt(dispatch.ROLE_CI_FIX, _group(), _ctx())
        self.assertIn("(no log captured)", prompt)

    def test_group_prompt_ends_with_json_contract(self):
        for role in (dispatch.ROLE_RESOLVE, dispatch.ROLE_CI_FIX):
            prompt = dispatch.build_group_prompt(role, _group(), _ctx())
            self.assertIn("```json", prompt)
            self.assertIn("status", prompt)

    def test_group_prompt_hard_rules_present(self):
        for role in (dispatch.ROLE_RESOLVE, dispatch.ROLE_CI_FIX):
            prompt = dispatch.build_group_prompt(role, _group(), _ctx())
            self.assertIn("Hard rules", prompt)

    def test_group_prompts_explain_gitnexus_worktree_boundary(self):
        for role in (
            dispatch.ROLE_RESOLVE,
            dispatch.ROLE_ASSEMBLY_RESOLVE,
            dispatch.ROLE_CI_FIX,
        ):
            prompt = dispatch.build_group_prompt(role, _group(), _ctx())
            self.assertIn("generated worktree normally has no GitNexus index", prompt)
            self.assertIn("the worktree wins", prompt)
            self.assertIn("GitNexus capability preflight: unavailable", prompt)

    def test_group_prompt_forbids_hand_rolled_wait_loop(self):
        """Both group-level briefs must forbid hand-rolling a background-wait loop.

        Guards against the lessons.md failure where a spawned ci-fix worker
        improvised an unbounded `while true; ... sleep` loop after pushing. The
        orchestrator owns CI waiting (verify.py bounded poll), not the worker.
        """
        for role in (dispatch.ROLE_RESOLVE, dispatch.ROLE_CI_FIX):
            prompt = dispatch.build_group_prompt(role, _group(), _ctx())
            self.assertIn("hand-roll a background-wait loop", prompt)
            self.assertIn("report back", prompt)

    def test_group_prompt_forbids_harness_backgrounding(self):
        """Every group-level brief must forbid harness-native backgrounding too.

        Brief 20260821-182348: a resolve worker hit "no report-back JSON block
        found" during integrate (skipping the merge-conflict resolution for
        stop-hook-deferred-work-flag/1.1) after backgrounding work the same way
        the 1.4/implement worker did. The shell-idiom rule above does not name
        that affordance, so all three group roles must carry the explicit one.
        """
        for role in (
            dispatch.ROLE_RESOLVE,
            dispatch.ROLE_ASSEMBLY_RESOLVE,
            dispatch.ROLE_CI_FIX,
        ):
            prompt = dispatch.build_group_prompt(role, _group(), _ctx())
            self.assertIn("run_in_background", prompt, f"role={role}")
            self.assertIn("single headless turn", prompt, f"role={role}")
            self.assertIn("FOREGROUND", prompt, f"role={role}")

    def test_group_prompt_forbids_self_merge_and_workflow_edits(self):
        """Every group-level brief must forbid enabling merge and editing CI workflows.

        Guards against handoff 20260701-204500: a spawned ci-fix worker ran
        `gh pr merge --auto` on its own initiative, hit "Protected branch rules
        not configured" on the orchestrator's ephemeral base branch, and then
        "fixed" that by patching the repo's real `.github/workflows/auto-merge.yml`
        as an unreviewed side effect of an unrelated task. Enabling merge is the
        orchestrator's job (verify.py auto_merge()); workers must never do it or
        touch shared CI config themselves.
        """
        for role in (
            dispatch.ROLE_RESOLVE,
            dispatch.ROLE_ASSEMBLY_RESOLVE,
            dispatch.ROLE_CI_FIX,
        ):
            ctx = _ctx(conflicting_branch="task/TASK-002")
            prompt = dispatch.build_group_prompt(role, _group(), ctx)
            self.assertIn("gh pr merge", prompt)
            self.assertIn(".github/workflows/**", prompt)


class BuildStackConflictPromptTests(unittest.TestCase):
    def _prompt(self, **kw):
        task = kw.pop("task", {"id": "TASK-003", "files": ["src/a.ts"]})
        spec_id = kw.pop("spec_id", "004-test")
        conflicting_branch = kw.pop("conflicting_branch", "task/TASK-002")
        worktree_path = kw.pop("worktree_path", "/tmp/wt/004-test/TASK-003")
        return dispatch.build_stack_conflict_prompt(
            spec_id, task, conflicting_branch, worktree_path
        )

    def test_renders_task_id(self):
        prompt = self._prompt(task={"id": "TASK-003", "files": []})
        self.assertIn("TASK-003", prompt)

    def test_renders_conflicting_branch(self):
        prompt = self._prompt(conflicting_branch="task/TASK-002")
        self.assertIn("task/TASK-002", prompt)

    def test_renders_worktree_path(self):
        prompt = self._prompt(worktree_path="/tmp/wt/004-test/TASK-003")
        self.assertIn("/tmp/wt/004-test/TASK-003", prompt)

    def test_forbids_push_or_pr(self):
        prompt = self._prompt()
        self.assertIn("Do NOT push or open a PR", prompt)
        self.assertNotIn("git push", prompt)
        self.assertNotIn("gh pr create", prompt)

    def test_explains_gitnexus_worktree_boundary(self):
        prompt = self._prompt()
        self.assertIn("generated worktree normally has no GitNexus index", prompt)
        self.assertIn("the worktree wins", prompt)

    def test_includes_merge_conflict_resolution_steps(self):
        prompt = self._prompt()
        self.assertIn("git diff --name-only --diff-filter=U", prompt)
        self.assertIn("git merge --continue", prompt)

    def test_forbids_hand_rolled_wait_loop(self):
        prompt = self._prompt()
        self.assertIn("hand-roll a background-wait loop", prompt)

    def test_forbids_harness_backgrounding(self):
        prompt = self._prompt()
        self.assertIn("run_in_background", prompt)
        self.assertIn("single headless turn", prompt)
        self.assertIn("FOREGROUND", prompt)

    def test_ends_with_json_contract(self):
        prompt = self._prompt(task={"id": "TASK-003", "files": []})
        self.assertIn("```json", prompt)
        self.assertIn('"task": "TASK-003"', prompt)
        self.assertIn(f'"step": "{dispatch.ROLE_ASSEMBLY_RESOLVE}"', prompt)


class ParseReportBackTests(unittest.TestCase):
    def _ok(self, **kw):
        base = {
            "task": "T-1",
            "step": "implement",
            "status": "success",
            "head_sha": "abc",
        }
        base.update(kw)
        import json

        return "```json\n" + json.dumps(base) + "\n```"

    def test_fallback_no_fenced_block(self):
        import json

        payload = {"task": "T-1", "step": "implement", "status": "success"}
        text = "some preamble " + json.dumps(payload) + " some tail"
        r = dispatch.parse_report_back(text)
        self.assertEqual(r["task"], "T-1")

    def test_no_json_at_all_raises(self):
        with self.assertRaises(ValueError) as cm:
            dispatch.parse_report_back("no json here whatsoever")
        self.assertIn("no report-back JSON block found", str(cm.exception))

    def test_malformed_json_raises(self):
        with self.assertRaises(ValueError) as cm:
            dispatch.parse_report_back("```json\n{bad json\n```")
        self.assertIn("invalid", str(cm.exception))

    def test_missing_required_fields_raises(self):
        import json

        text = "```json\n" + json.dumps({"task": "T-1"}) + "\n```"
        with self.assertRaises(ValueError) as cm:
            dispatch.parse_report_back(text)
        self.assertIn("missing required fields", str(cm.exception))

    def test_bad_status_raises(self):
        import json

        payload = {"task": "T-1", "step": "implement", "status": "unknown"}
        text = "```json\n" + json.dumps(payload) + "\n```"
        with self.assertRaises(ValueError) as cm:
            dispatch.parse_report_back(text)
        self.assertIn("bad status", str(cm.exception))

    def test_fallback_trailing_metadata_after_worker_json(self):
        """Regression: claude CLI appends session metadata directly after worker output.

        rfind+rfind builds blob = worker_json + trailing chars; json.loads raises
        JSONDecodeError "Extra data". raw_decode must parse only the first JSON object
        and return the worker report successfully.
        """
        import json

        report = {"task": "TASK-004", "step": "implement", "status": "success"}
        # Simulate the metadata suffix appended by claude -p with no separator.
        # rfind("{") picks position 0 (worker's { is the only { in the text here);
        # rfind("}") picks the final } — blob = worker_json + trailing.
        trailing = '","costUSD":0.8781132,"contextWindow":200000,"uuid":"f14a7cc5-abc"}'
        text = json.dumps(report) + trailing
        r = dispatch.parse_report_back(text)
        self.assertEqual(r["task"], "TASK-004")
        self.assertEqual(r["status"], "success")


class DispatchCLITests(unittest.TestCase):
    def test_demo_exits_zero(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = dispatch.main(["demo"])
        self.assertEqual(rc, 0)
        self.assertIn("IMPLEMENT", buf.getvalue())
        self.assertIn("REVIEW", buf.getvalue())


class TransitionTests(unittest.TestCase):
    def test_review_missing_review_status_raises(self):
        rep = {"task": "T", "step": "review", "status": "success"}
        with self.assertRaises(ValueError) as cm:
            dispatch.transition(dispatch.ROLE_REVIEW, rep, 0)
        self.assertIn("review_status", str(cm.exception))

    def test_review_invalid_review_status_raises(self):
        rep = {
            "task": "T",
            "step": "review",
            "status": "success",
            "review_status": "MAYBE",
        }
        with self.assertRaises(ValueError):
            dispatch.transition(dispatch.ROLE_REVIEW, rep, 0)

    def test_escalated_after_max_retries(self):
        rep = {
            "task": "T",
            "step": "review",
            "status": "success",
            "review_status": "FAILED",
        }
        status, _retry = dispatch.transition(
            dispatch.ROLE_REVIEW, rep, retry_count=2, max_retries=3
        )
        self.assertEqual(status, "escalated")

    def test_fixing_before_max_retries(self):
        rep = {
            "task": "T",
            "step": "review",
            "status": "success",
            "review_status": "FAILED",
        }
        status, retry = dispatch.transition(
            dispatch.ROLE_REVIEW, rep, retry_count=1, max_retries=3
        )
        self.assertEqual(status, "fixing")
        self.assertEqual(retry, 2)


def _gate_envelope(**overrides):
    """An 'answered' envelope in the fully validated shape, built without disk."""
    envelope = decisions_mod.pending_decision_envelope(
        decision_id="dec-extras-0001",
        question="Ship behind the flag now or after the migration?",
        options=["flag now", "after migration"],
        source="check_brief_staleness",
        repo="/tmp/extras-repo",
        subject="brief-x",
    )
    created = dt.datetime.fromisoformat(envelope["created_at"])
    envelope.update(
        {
            "status": "answered",
            "answered_at": (created + dt.timedelta(seconds=10)).isoformat(),
            "answer": "flag now",
        }
    )
    envelope.update(overrides)
    return envelope


class DecisionGateFailClosedDegradationTests(unittest.TestCase):
    """If workqueue.decisions cannot be imported, the gate refuses dispatch
    instead of letting an unvalidated envelope through."""

    def test_unavailable_primitives_refuse_fail_closed(self):
        with (
            mock.patch.object(dispatch, "_decision_helpers", return_value=(None, None)),
            self.assertRaises(DecisionDispatchError) as cm,
        ):
            validate_resolved_decision_input(_gate_envelope())
        self.assertIn("unavailable", str(cm.exception))
        self.assertIn("fail-closed", str(cm.exception))


class DecisionGateBoundaryInputTests(unittest.TestCase):
    """Envelope-shape and timestamp edges of the orchestrator dispatch gate."""

    def test_accepts_the_envelope_as_a_json_string(self):
        accepted = validate_resolved_decision_input(json.dumps(_gate_envelope()))
        self.assertEqual(accepted["decision_id"], "dec-extras-0001")
        self.assertEqual(accepted["answer"], "flag now")

    def test_none_envelope_is_refused(self):
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(None)
        self.assertIn("no pending-decision envelope", str(cm.exception))

    def test_naive_answered_at_is_refused(self):
        envelope = _gate_envelope(answered_at="2026-08-25T12:00:00")
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(envelope)
        self.assertIn("timezone-naive", str(cm.exception))

    def test_unparsable_answered_at_is_refused(self):
        envelope = _gate_envelope(answered_at="day-after-tomorrow")
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(envelope)
        self.assertIn("answered_at", str(cm.exception))

    def test_answered_before_created_is_refused_as_clock_skew(self):
        envelope = _gate_envelope()
        created = dt.datetime.fromisoformat(envelope["created_at"])
        envelope["answered_at"] = (created - dt.timedelta(seconds=30)).isoformat()
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(envelope)
        self.assertIn("precedes created_at", str(cm.exception))

    def test_accepted_result_round_trips_the_envelope_parser(self):
        accepted = validate_resolved_decision_input(_gate_envelope())
        self.assertEqual(
            decisions_mod.parse_pending_decision_envelope(accepted), accepted
        )

    def test_gate_is_deterministic_same_inputs_same_result(self):
        envelope = _gate_envelope()
        first = validate_resolved_decision_input(envelope)
        for _ in range(3):
            self.assertEqual(validate_resolved_decision_input(envelope), first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
