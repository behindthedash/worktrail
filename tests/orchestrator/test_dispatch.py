#!/usr/bin/env python3
"""Unit tests for dispatch.py prompt building."""

import datetime as dt
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from typing import ClassVar

from worktrail.orchestrator.dispatch import (
    _ROLE_ACTION,
    ROLE_ASSEMBLY_RESOLVE,
    ROLE_CI_FIX,
    ROLE_CLEANUP,
    ROLE_FIX,
    ROLE_IMPLEMENT,
    ROLE_RESOLVE,
    ROLE_REVIEW,
    DecisionDispatchError,
    ReviewWorkerPromptCtx,
    WorkerPromptCtx,
    agent_for,
    build_group_prompt,
    build_worker_prompt,
    transition,
    validate_resolved_decision_input,
)
from worktrail.workqueue import decisions as decisions_mod


def _make_ctx(spec_folder="docs/specs/004-test/"):
    return {
        "spec_id": "004-test",
        "spec_folder": spec_folder,
        "worktree_path": "/tmp/worktrees/004-test",
        "branch": "task/TASK-001",
        "base_commit": "abc1234",
        "reviewer_agent": "code-reviewer",
    }


def _make_task(task_id="TASK-001"):
    return {
        "id": task_id,
        "files": ["src/foo.py"],
        "agent": "claude",
    }


class TestReviewPathRendering(unittest.TestCase):
    def test_review_write_path_is_spec_folder_relative(self):
        """AC-010: rendered ROLE_REVIEW prompt uses spec-folder-relative path."""
        ctx = _make_ctx("docs/specs/004-orchestrator-review-writer-path/")
        task = _make_task("TASK-002")
        prompt = build_worker_prompt(ROLE_REVIEW, task, ctx)

        expected_path = (
            "docs/specs/004-orchestrator-review-writer-path/reviews/TASK-002-review.md"
        )
        bare_path = "reviews/TASK-002-review.md"

        self.assertIn(
            expected_path,
            prompt,
            f"ROLE_REVIEW prompt must contain spec-folder-relative path '{expected_path}'",
        )
        # The bare path must NOT appear as a standalone reference (not prefixed by spec_folder)
        # Find all occurrences of bare_path and check none are bare (i.e., not preceded by spec_folder)
        bare_occurrences = [
            m.start() for m in re.finditer(re.escape(bare_path), prompt)
        ]
        for pos in bare_occurrences:
            prefix = prompt[
                max(
                    0, pos - len("docs/specs/004-orchestrator-review-writer-path/")
                ) : pos
            ]
            self.assertTrue(
                prefix.endswith("docs/specs/004-orchestrator-review-writer-path/"),
                f"Found bare (non-prefixed) review path in ROLE_REVIEW prompt at position {pos}",
            )

    def test_review_write_and_fix_read_paths_match(self):
        """AC-011: ROLE_REVIEW write path and ROLE_FIX read path are identical."""
        spec_folder = "docs/specs/004-orchestrator-review-writer-path/"
        ctx = _make_ctx(spec_folder)
        task = _make_task("TASK-003")

        review_prompt = build_worker_prompt(ROLE_REVIEW, task, ctx)
        fix_prompt = build_worker_prompt(ROLE_FIX, task, ctx)

        expected_path = f"{spec_folder}reviews/TASK-003-review.md"

        self.assertIn(
            expected_path,
            review_prompt,
            f"ROLE_REVIEW prompt must reference '{expected_path}'",
        )
        self.assertIn(
            expected_path,
            fix_prompt,
            f"ROLE_FIX prompt must reference '{expected_path}'",
        )

    def test_review_path_prefix_comes_from_ctx_spec_folder(self):
        """REQ-004: path prefix is derived from ctx['spec_folder'], not hardcoded."""
        custom_folder = "docs/specs/999-custom-spec/"
        ctx = _make_ctx(custom_folder)
        task = _make_task("TASK-007")

        review_prompt = build_worker_prompt(ROLE_REVIEW, task, ctx)
        fix_prompt = build_worker_prompt(ROLE_FIX, task, ctx)

        expected = f"{custom_folder}reviews/TASK-007-review.md"
        self.assertIn(expected, review_prompt)
        self.assertIn(expected, fix_prompt)
        # No hardcoded docs/specs/ prefix outside the custom folder
        self.assertNotIn("docs/specs/000", review_prompt)

    def test_missing_spec_folder_raises_type_error(self):
        """REQ-NR006: missing spec_folder fails fast, no silent bare-path fallback.
        ctx is coerced into a WorkerPromptCtx at the top of build_worker_prompt
        (see its docstring); a required field absent from ctx now raises
        TypeError there -- naming every missing required field at once -- rather
        than the old ad-hoc KeyError raised deep in the function at whichever
        line happened to touch that one field."""
        ctx = {
            "spec_id": "004-test",
            # spec_folder intentionally omitted
            "worktree_path": "/tmp/worktrees/004-test",
            "branch": "task/TASK-001",
            "base_commit": "abc1234",
        }
        task = _make_task("TASK-001")
        with self.assertRaises(TypeError) as cm:
            build_worker_prompt(ROLE_REVIEW, task, ctx)
        self.assertIn("spec_folder", str(cm.exception))

    def test_missing_base_commit_raises_instead_of_head_sentinel_fallback(self):
        """A bare 'HEAD' base_commit is worktree-relative and renders ROLE_REVIEW's
        `git diff {base_commit}..HEAD` as a silent no-op inside the review worker's
        own worktree -- the defect fixed in PR #825 and PR #837. build_worker_prompt
        must fail fast instead of silently defaulting to it (regression guard against
        a third recurrence)."""
        ctx = {
            "spec_id": "004-test",
            "spec_folder": "docs/specs/004-test/",
            "worktree_path": "/tmp/worktrees/004-test",
            "branch": "task/TASK-001",
            # base_commit intentionally omitted
        }
        task = _make_task("TASK-001")
        with self.assertRaises(ValueError) as cm:
            build_worker_prompt(ROLE_REVIEW, task, ctx)
        self.assertIn("base_commit", str(cm.exception))

    def test_non_review_roles_do_not_require_base_commit(self):
        """Only ROLE_REVIEW's template consumes base_commit; other roles must not
        be gated on it."""
        ctx = {
            "spec_id": "004-test",
            "spec_folder": "docs/specs/004-test/",
            "worktree_path": "/tmp/worktrees/004-test",
            "branch": "task/TASK-001",
        }
        task = _make_task("TASK-001")
        build_worker_prompt(ROLE_IMPLEMENT, task, ctx)  # must not raise
        build_worker_prompt(ROLE_FIX, task, ctx)  # must not raise
        build_worker_prompt(ROLE_CLEANUP, task, ctx)  # must not raise


class TestWorkerPromptCtxContract(unittest.TestCase):
    """The role-keyed dataclass build_worker_prompt coerces ctx into (see its
    docstring): required fields raise together, at construction, instead of
    one at a time as the old ad-hoc per-field checks did."""

    def test_a_worker_prompt_ctx_instance_passes_through_unchanged(self):
        """Callers may also construct the typed ctx themselves and skip the
        dict-coercion step entirely."""
        ctx = WorkerPromptCtx(**_make_ctx())
        prompt = build_worker_prompt(ROLE_IMPLEMENT, _make_task(), ctx)
        self.assertIn("IMPLEMENT worker", prompt)

    def test_review_worker_prompt_ctx_requires_base_commit_at_construction(self):
        """Constructing ReviewWorkerPromptCtx directly (not via
        build_worker_prompt's dict coercion) is validated the same way."""
        kwargs = _make_ctx()
        del kwargs["base_commit"]
        with self.assertRaises(ValueError) as cm:
            ReviewWorkerPromptCtx(**kwargs)
        self.assertIn("base_commit", str(cm.exception))

    def test_multiple_missing_required_fields_are_named_together(self):
        """The whole point of moving to a dataclass: every missing required
        field for this role is reported in one error, not discovered one at a
        time across separate lines/roles."""
        with self.assertRaises(TypeError) as cm:
            build_worker_prompt(ROLE_IMPLEMENT, _make_task(), {"spec_id": "004-test"})
        message = str(cm.exception)
        for field_name in ("spec_folder", "worktree_path", "branch"):
            self.assertIn(field_name, message)

    def test_unexpected_ctx_key_raises_at_construction(self):
        """A typo'd or stale field name is now rejected up front instead of
        silently ignored, the way a plain dict's .get() would ignore it."""
        ctx = _make_ctx()
        ctx["group_branch"] = "run-001/base"  # not part of WorkerPromptCtx
        with self.assertRaises(TypeError) as cm:
            build_worker_prompt(ROLE_IMPLEMENT, _make_task(), ctx)
        self.assertIn("group_branch", str(cm.exception))


class TestReviewChecksAcDodCheckboxes(unittest.TestCase):
    """AC-CHG-009 through AC-CHG-013: reviewer ticks AC/DoD checkboxes in its
    own task file and couples review_status: PASSED to all-ticked."""

    # Exact pre-change text of the three roles this change must not touch.
    _UNCHANGED_ROLE_IMPLEMENT = (
        "Implement ONLY the files in scope, per the task's Acceptance Criteria and "
        "Files-to-Create. Write the tests the task specifies and run them. Commit. "
        "If a dependency's files are listed above under 'Read first', verify with "
        "`ls` that they are present in this worktree before concluding they are "
        "missing -- report context_quality: insufficient with a specific "
        "missing_context entry only after that check fails, never on assumption."
    )
    _UNCHANGED_ROLE_FIX = (
        "Read `{spec_folder}reviews/{task_id}-review.md` and fix ONLY the listed findings. "
        "Re-run the task's tests. Commit. "
        "If a finding requires touching a file outside this task's scope, decline it: "
        "report `status: failed` and list the untouchable file(s) as repo-relative paths "
        "in the report-back's `missing_context` field — never only in `notes`."
    )
    _UNCHANGED_ROLE_CLEANUP = (
        "Run cleanup on ONLY the files this task changed: remove debug logs + "
        "unused imports. No behavior changes. "
        "Format ONLY if the repo actually CONFIGURES a formatter -- a `format`/"
        "`lint:fix` script in package.json, or a config file "
        "(.prettierrc*/.editorconfig/pyproject [tool.black|tool.ruff]). If so, run "
        "THAT command, scoped to the changed files. NEVER run a bare global "
        "formatter (`npx prettier --write`, `black .`) on a repo with no config: "
        "it reflows whole files and flips quote/semicolon style, burying the real "
        "diff. If no formatter is configured, skip formatting. "
        "Do NOT touch the spec tree: task status is recorded in the run journal "
        "during a run and written to the spec artifact once per group at integrate "
        "time, not on this branch. Commit only your code changes."
    )

    # The AC/DoD checkbox behaviour is now conditional on the task format: it
    # applies when the brief is the task's OWN file (devkit) and is deliberately
    # withheld when the brief is one item in a file shared by the whole change
    # (OpenSpec), where the only checkboxes are orchestrator-owned run status.
    # These assert the RENDERED devkit prompt rather than the raw template --
    # a stronger check, since the rendered text is what a worker actually reads.
    def _devkit_review_prompt(self):
        return build_worker_prompt(ROLE_REVIEW, _make_task(), _make_ctx())

    def test_review_action_names_ac_and_dod_sections(self):
        """AC-CHG-009: ROLE_REVIEW action names both checkbox sections to tick."""
        action = self._devkit_review_prompt()
        self.assertIn("## Acceptance Criteria", action)
        self.assertIn("## Definition of Done (DoD)", action)

    def test_review_action_couples_passed_to_all_ticked(self):
        """AC-CHG-010: PASSED requires all AC/DoD ticked; FAILED + finding on
        any unticked item."""
        action = self._devkit_review_prompt()
        self.assertIn("PASSED", action)
        self.assertIn("EVERY AC/DoD checkbox ticked", action)
        self.assertIn("FAILED", action)
        self.assertIn("finding", action)

    def test_review_action_instructs_committing_task_file_edit(self):
        """AC-CHG-011: ROLE_REVIEW action instructs committing the task-file
        checkbox edit, in addition to writing the review file."""
        action = self._devkit_review_prompt()
        self.assertIn("Commit the task-file checkbox edit", action)
        self.assertIn("reviews/TASK-001-review.md", action)

    def test_a_shared_file_brief_forbids_ticking_instead(self):
        """The inverse, and the reason the above became conditional: on OpenSpec
        the only checkboxes in the brief are run status, owned by the
        orchestrator. A worker ticking one puts a `tasks.md` diff on its branch,
        and since every task shares that file, every sibling branch conflicts."""
        ctx = dict(_make_ctx())
        ctx["spec_folder"] = "openspec/changes/080-x/"
        ctx["task_brief"] = {
            "path_fmt": "openspec/changes/080-x/tasks.md",
            "anchor_fmt": "{task_id}",
        }
        action = build_worker_prompt(ROLE_REVIEW, _make_task(), ctx)
        self.assertIn("strictly READ-ONLY", action)
        self.assertNotIn("Commit the task-file checkbox edit", action)
        self.assertNotIn("tick it to", action)

    def test_review_verdict_flags_literal_instruction_deviation(self):
        """Handoff 20260731-082737: a diff that takes a different approach than
        the task literally describes is FAILED-worthy on its own -- the
        reviewer must not accept a plausible rationale for the deviation."""
        action = self._devkit_review_prompt()
        self.assertIn("different implementation approach than what the task", action)
        self.assertIn("human/planner decision", action)

    def test_review_verdict_deviation_rule_applies_to_shared_file_too(self):
        """Same rule for OpenSpec's shared-tasks.md review path, which uses a
        different verdict clause than the devkit own-file path."""
        ctx = dict(_make_ctx())
        ctx["spec_folder"] = "openspec/changes/080-x/"
        ctx["task_brief"] = {
            "path_fmt": "openspec/changes/080-x/tasks.md",
            "anchor_fmt": "{task_id}",
        }
        action = build_worker_prompt(ROLE_REVIEW, _make_task(), ctx)
        self.assertIn("different implementation approach than what the task", action)
        self.assertIn("human/planner decision", action)

    def test_other_roles_byte_for_byte_unchanged(self):
        """AC-CHG-012: ROLE_IMPLEMENT, ROLE_FIX, ROLE_CLEANUP action strings
        are exactly equal to their pre-change values (string equality, not
        substring)."""
        self.assertEqual(_ROLE_ACTION[ROLE_IMPLEMENT], self._UNCHANGED_ROLE_IMPLEMENT)
        self.assertEqual(_ROLE_ACTION[ROLE_FIX], self._UNCHANGED_ROLE_FIX)
        self.assertEqual(_ROLE_ACTION[ROLE_CLEANUP], self._UNCHANGED_ROLE_CLEANUP)

    def test_review_prompt_renders_with_extended_action(self):
        """The extended ROLE_REVIEW action still .format()s without error."""
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_REVIEW, task, ctx)
        self.assertIn("## Acceptance Criteria", prompt)
        self.assertIn("## Definition of Done (DoD)", prompt)
        self.assertIn(f"{ctx['spec_folder']}tasks/{task['id']}.md", prompt)


class TestCleanupStatusWriteBack(unittest.TestCase):
    def test_cleanup_prompt_does_not_instruct_status_writeback(self):
        """Supersedes AC-008 (spec 006-orchestrator-task-status-writeback).

        P0 removed status write-back from task branches entirely: status lives in
        the run journal during a run and reaches the spec artifact once per group
        at integrate time. The cleanup prompt previously carried an explicit
        exception permitting the worker to write it, which would reintroduce the
        exact per-branch spec diff P0 deleted.
        """
        spec_folder = "docs/specs/006-orchestrator-task-status-writeback/"
        ctx = _make_ctx(spec_folder)
        prompt = build_worker_prompt(ROLE_CLEANUP, _make_task("TASK-002"), ctx)

        self.assertNotIn("set the `status` frontmatter field", prompt)
        self.assertNotIn("explicitly permitted", prompt)
        self.assertIn("run journal", prompt)
        self.assertIn("Do NOT modify docs/specs/** at all.", prompt)

    def test_cleanup_task_path_is_spec_folder_relative(self):
        """AC-009: cleanup task-file path is derived from ctx['spec_folder'],
        not a hardcoded docs/specs/<id>/ literal."""
        custom_folder = "docs/specs/999-custom-spec/"
        ctx = _make_ctx(custom_folder)
        task = _make_task("TASK-007")
        prompt = build_worker_prompt(ROLE_CLEANUP, task, ctx)

        expected_path = f"{custom_folder}tasks/TASK-007.md"
        self.assertIn(
            expected_path,
            prompt,
            f"ROLE_CLEANUP prompt must contain spec-folder-relative path '{expected_path}'",
        )
        # No hardcoded docs/specs/<literal-id>/ prefix for the task path
        self.assertNotIn(
            "docs/specs/006-",
            prompt,
            "ROLE_CLEANUP action string must not contain a hardcoded spec-id prefix",
        )
        self.assertNotIn(
            "docs/specs/004-",
            prompt,
            "ROLE_CLEANUP action string must not contain a hardcoded spec-id prefix",
        )


class TestTransitionReviewRouting(unittest.TestCase):
    """Regression tests for Bug 1 (fix-cycle bypass).

    A review worker that sets status:"failed" to signal code defects must still
    enter the fix cycle. transition() must route ROLE_REVIEW on review_status,
    not on status.
    """

    def test_review_status_failed_plus_review_status_failed_routes_to_fixing(self):
        """status:"failed" + review_status:"FAILED" must route to fixing, not "failed".

        This is the exact payload that caused the regression: the worker treated
        code defects as a worker crash, burning all retries instantly.
        """
        report = {
            "task": "TASK-001",
            "step": "review",
            "status": "failed",
            "review_status": "FAILED",
            "major_issues": 1,
            "notes": "typo in field name",
        }
        new_status, retry = transition(ROLE_REVIEW, report, retry_count=0)
        self.assertEqual(new_status, "fixing")
        self.assertEqual(retry, 1)

    def test_review_status_failed_plus_review_status_passed_routes_to_cleaning(self):
        """status:"failed" + review_status:"PASSED" still routes to cleaning."""
        report = {
            "task": "TASK-001",
            "step": "review",
            "status": "failed",
            "review_status": "PASSED",
        }
        new_status, retry = transition(ROLE_REVIEW, report, retry_count=0)
        self.assertEqual(new_status, "cleaning")
        self.assertEqual(retry, 0)

    def test_review_status_success_plus_review_status_failed_routes_to_fixing(self):
        """Canonical bad-review payload (status:success) still routes to fixing."""
        report = {
            "task": "TASK-001",
            "step": "review",
            "status": "success",
            "review_status": "FAILED",
            "major_issues": 2,
        }
        new_status, retry = transition(ROLE_REVIEW, report, retry_count=0)
        self.assertEqual(new_status, "fixing")
        self.assertEqual(retry, 1)

    def test_review_escalates_after_max_retries(self):
        """After MAX_REVIEW_RETRIES strikes the review loop escalates."""
        report = {
            "task": "TASK-001",
            "step": "review",
            "status": "failed",
            "review_status": "FAILED",
        }
        new_status, _retry = transition(
            ROLE_REVIEW, report, retry_count=2, max_retries=3
        )
        self.assertEqual(new_status, "escalated")

    def test_non_review_status_failed_still_returns_failed(self):
        """Non-review roles with status:"failed" must still return "failed" (no regression)."""
        for role in (ROLE_IMPLEMENT, ROLE_FIX, ROLE_CLEANUP):
            report = {"task": "TASK-001", "step": role, "status": "failed"}
            new_status, _retry = transition(role, report, retry_count=0)
            self.assertEqual(
                new_status,
                "failed",
                f"role={role} with status:failed should return 'failed'",
            )


class TestTransitionSkippedSmallDiff(unittest.TestCase):
    """review_status: SKIPPED-SMALL-DIFF is a passed review (task 5.1)."""

    def test_skipped_small_diff_routes_to_cleaning_retry_unchanged(self):
        report = {
            "task": "TASK-001",
            "step": "review",
            "status": "success",
            "review_status": "SKIPPED-SMALL-DIFF",
        }
        new_status, retry = transition(ROLE_REVIEW, report, retry_count=2)
        self.assertEqual(new_status, "cleaning")
        self.assertEqual(retry, 2)

    def test_unknown_review_status_still_raises(self):
        report = {
            "task": "TASK-001",
            "step": "review",
            "status": "success",
            "review_status": "BOGUS",
        }
        with self.assertRaises(ValueError):
            transition(ROLE_REVIEW, report, retry_count=0)


class TestMissingContextPathRule(unittest.TestCase):
    """AC: review/fix action text carries the missing_context path rule (task 5.1)."""

    def test_review_action_carries_missing_context_rule(self):
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_REVIEW, task, ctx)
        self.assertIn("missing_context", prompt)
        self.assertIn("repo-relative paths", prompt)
        self.assertIn("never only in `notes`", prompt)

    def test_fix_action_carries_missing_context_rule(self):
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_FIX, task, ctx)
        self.assertIn("missing_context", prompt)
        self.assertIn("status: failed", prompt)
        self.assertIn("repo-relative paths", prompt)

    def test_fix_action_text_in_role_action_table(self):
        self.assertIn("missing_context", _ROLE_ACTION[ROLE_FIX])
        self.assertIn("missing_context", _ROLE_ACTION[ROLE_REVIEW])


class TestPreCommitCmdHardRule(unittest.TestCase):
    """AC: implement/fix worker prompts and the ci-fix group prompt carry the
    pre_commit_cmd hard rule when set, and omit it when unset (task 5.1)."""

    def test_implement_prompt_carries_command_when_set(self):
        ctx = _make_ctx()
        ctx["pre_commit_cmd"] = "ruff check . --fix && ruff format ."
        task = _make_task()
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertIn("ruff check . --fix && ruff format .", prompt)
        self.assertIn("before every commit", prompt)

    def test_fix_prompt_carries_command_when_set(self):
        ctx = _make_ctx()
        ctx["pre_commit_cmd"] = "ruff check . --fix && ruff format ."
        task = _make_task()
        prompt = build_worker_prompt(ROLE_FIX, task, ctx)
        self.assertIn("ruff check . --fix && ruff format .", prompt)
        self.assertIn("before every commit", prompt)

    def test_implement_prompt_omits_line_when_unset(self):
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertNotIn("before every commit", prompt)

    def test_fix_prompt_omits_line_when_unset(self):
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_FIX, task, ctx)
        self.assertNotIn("before every commit", prompt)

    def test_review_and_cleanup_prompts_unaffected_by_pre_commit_cmd(self):
        ctx = _make_ctx()
        ctx["pre_commit_cmd"] = "ruff check . --fix && ruff format ."
        task = _make_task()
        for role in (ROLE_REVIEW, ROLE_CLEANUP):
            prompt = build_worker_prompt(role, task, ctx)
            self.assertNotIn("before every commit", prompt)

    def _group_ctx(self, **kw):
        base = {
            "spec_id": "004-test",
            "worktree_path": "/tmp/wt/004-test",
            "group_branch": "run-001/base",
            "base_branch": "main",
        }
        base.update(kw)
        return base

    def _group(self):
        return {"name": "base", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}

    def test_ci_fix_prompt_carries_command_when_set(self):
        ctx = self._group_ctx(pre_commit_cmd="ruff check . --fix && ruff format .")
        prompt = build_group_prompt(ROLE_CI_FIX, self._group(), ctx)
        self.assertIn("ruff check . --fix && ruff format .", prompt)
        self.assertIn("before every commit", prompt)

    def test_ci_fix_prompt_omits_line_when_unset(self):
        ctx = self._group_ctx()
        prompt = build_group_prompt(ROLE_CI_FIX, self._group(), ctx)
        self.assertNotIn("before every commit", prompt)


class TestReviewWorkerBriefFieldGuide(unittest.TestCase):
    """Regression tests for the belt-and-suspenders prompt note (Bug 1 Option A)."""

    def test_review_brief_contains_status_field_guide(self):
        """ROLE_REVIEW brief must include the status/review_status field guide note."""
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_REVIEW, task, ctx)
        self.assertIn("REVIEW FIELD GUIDE", prompt)
        self.assertIn("review_status", prompt)
        self.assertIn('status="success"', prompt)

    def test_non_review_brief_does_not_contain_field_guide(self):
        """Non-review briefs must NOT include the review-specific note."""
        ctx = _make_ctx()
        task = _make_task()
        for role in (ROLE_IMPLEMENT, ROLE_FIX, ROLE_CLEANUP):
            prompt = build_worker_prompt(role, task, ctx)
            self.assertNotIn(
                "REVIEW FIELD GUIDE",
                prompt,
                f"role={role} brief should not include review field guide",
            )


class TestImplementWorkerReads(unittest.TestCase):
    """AC: implement worker prompt no longer instructs reading spec/data-model/KG by default."""

    def test_implement_default_omits_spec_reads(self):
        """Default implement prompt must NOT instruct reading spec.md, data-model, or KG."""
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertNotIn("spec.md", prompt)
        self.assertNotIn("data-model.md", prompt)
        self.assertNotIn("knowledge-graph.json", prompt)

    def test_implement_default_instructs_task_file_only(self):
        """Default implement prompt references the task file as the sole read."""
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertIn(f"{ctx['spec_folder']}tasks/TASK-001.md", prompt)

    def test_implement_needs_spec_adds_spec_reads(self):
        """needs_spec=True opt-in re-adds spec.md, data-model, and KG reads."""
        ctx = _make_ctx()
        task = {**_make_task(), "needs_spec": True}
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertIn("spec.md", prompt)
        self.assertIn("data-model.md", prompt)
        self.assertIn("knowledge-graph.json", prompt)

    def test_review_and_fix_unchanged(self):
        """Review and fix prompts are unchanged — they already exclude spec/KG."""
        ctx = _make_ctx()
        task = _make_task()
        for role in (ROLE_REVIEW, ROLE_FIX):
            prompt = build_worker_prompt(role, task, ctx)
            self.assertNotIn("spec.md", prompt)
            self.assertNotIn("knowledge-graph.json", prompt)


class TestDependencyFilesInReads(unittest.TestCase):
    """AC: ROLE_IMPLEMENT prompt surfaces a declared dependency's delivered files
    (via by_id) so workers can verify presence instead of assuming missing."""

    def test_dependency_files_appear_in_implement_reads(self):
        """A task with deps + by_id must list the dependency's files in 'Read first'."""
        ctx = _make_ctx()
        task = {**_make_task("TASK-002"), "deps": ["TASK-001"]}
        by_id = {"TASK-001": {**_make_task("TASK-001"), "files": ["src/shared.py"]}}
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx, by_id=by_id)
        read_section = prompt[prompt.index("Read first, in order:") :]
        self.assertIn("src/shared.py", read_section)
        self.assertIn("delivered by TASK-001", read_section)

    def test_dependency_files_omitted_without_by_id(self):
        """No by_id supplied -> no dependency reads injected (backward compatible)."""
        ctx = _make_ctx()
        task = {**_make_task("TASK-002"), "deps": ["TASK-001"]}
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertNotIn("delivered by", prompt)

    def test_dependency_files_omitted_when_dep_id_unknown(self):
        """A dep id absent from by_id is silently skipped, not KeyError'd."""
        ctx = _make_ctx()
        task = {**_make_task("TASK-002"), "deps": ["TASK-999"]}
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx, by_id={})
        self.assertNotIn("delivered by", prompt)

    def test_multiple_dependency_files_all_present(self):
        """A dependency with several files surfaces every one of them."""
        ctx = _make_ctx()
        task = {**_make_task("TASK-003"), "deps": ["TASK-001"]}
        by_id = {
            "TASK-001": {
                **_make_task("TASK-001"),
                "files": ["src/a.py", "src/b.py"],
            }
        }
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx, by_id=by_id)
        self.assertIn("src/a.py", prompt)
        self.assertIn("src/b.py", prompt)

    def test_no_deps_no_injection_even_with_by_id(self):
        """A task with no deps gets no dependency reads, even if by_id is supplied."""
        ctx = _make_ctx()
        task = _make_task("TASK-002")
        by_id = {"TASK-001": {**_make_task("TASK-001"), "files": ["src/shared.py"]}}
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx, by_id=by_id)
        self.assertNotIn("delivered by", prompt)

    def test_implement_action_instructs_verify_before_reporting_missing(self):
        """_ROLE_ACTION[ROLE_IMPLEMENT] tells the worker to `ls` a dependency's
        files before reporting missing_context on assumption."""
        ctx = _make_ctx()
        task = _make_task()
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertIn("verify with `ls`", prompt)
        self.assertIn("missing_context entry only after that check fails", prompt)

    def test_other_roles_unaffected_by_by_id(self):
        """by_id is ROLE_IMPLEMENT-only; review/fix/cleanup prompts ignore it."""
        ctx = _make_ctx()
        task = {**_make_task("TASK-002"), "deps": ["TASK-001"]}
        by_id = {"TASK-001": {**_make_task("TASK-001"), "files": ["src/shared.py"]}}
        for role in (ROLE_REVIEW, ROLE_FIX, ROLE_CLEANUP):
            prompt = build_worker_prompt(role, task, ctx, by_id=by_id)
            self.assertNotIn("delivered by", prompt)


class TestExternalDependencyFilesInReads(unittest.TestCase):
    """AC-015: ROLE_IMPLEMENT prompt surfaces a satisfied external-dependency's
    resolved sibling's delivered files (via external_deps_by_ref), matching the
    same-spec dependency read-hint wording pattern but naming the sibling's
    spec-qualified id."""

    def test_satisfied_external_dep_files_appear_in_implement_reads(self):
        """A satisfied external_deps entry must list the sibling's files in
        'Read first', naming the sibling's spec-qualified id."""
        ctx = _make_ctx()
        ref = "098-recursive-organization-model/TASK-036"
        task = {**_make_task("TASK-002"), "external_deps": [ref]}
        external_deps_by_ref = {ref: {"id": "TASK-036", "files": ["src/x.py"]}}
        prompt = build_worker_prompt(
            ROLE_IMPLEMENT, task, ctx, external_deps_by_ref=external_deps_by_ref
        )
        read_section = prompt[prompt.index("Read first, in order:") :]
        self.assertIn("src/x.py", read_section)
        self.assertIn(f"delivered by {ref}", read_section)

    def test_unresolved_or_unsatisfied_external_dep_produces_no_hint(self):
        """A ref absent from external_deps_by_ref (unresolved/unsatisfied,
        per the caller's resolution) contributes zero read-hint lines --
        never implies the sibling's files are already present."""
        ctx = _make_ctx()
        ref = "098-recursive-organization-model/TASK-036"
        task = {**_make_task("TASK-002"), "external_deps": [ref]}
        prompt = build_worker_prompt(ROLE_IMPLEMENT, task, ctx, external_deps_by_ref={})
        self.assertNotIn("delivered by", prompt)

        prompt_none = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertNotIn("delivered by", prompt_none)

    def test_other_roles_unaffected_by_external_deps_by_ref(self):
        """external_deps_by_ref is ROLE_IMPLEMENT-only; review/fix/cleanup
        prompts ignore it, matching the same-spec dependency scoping."""
        ctx = _make_ctx()
        ref = "098-recursive-organization-model/TASK-036"
        task = {**_make_task("TASK-002"), "external_deps": [ref]}
        external_deps_by_ref = {ref: {"id": "TASK-036", "files": ["src/x.py"]}}
        for role in (ROLE_REVIEW, ROLE_FIX, ROLE_CLEANUP):
            prompt = build_worker_prompt(
                role, task, ctx, external_deps_by_ref=external_deps_by_ref
            )
            self.assertNotIn("delivered by", prompt)

    def test_no_external_deps_no_regression(self):
        """A task with zero external_deps entries is byte-identical to before
        this change, even when external_deps_by_ref is supplied (AC-019)."""
        ctx = _make_ctx()
        task = _make_task("TASK-002")
        external_deps_by_ref = {
            "098-recursive-organization-model/TASK-036": {
                "id": "TASK-036",
                "files": ["src/x.py"],
            }
        }
        prompt_with = build_worker_prompt(
            ROLE_IMPLEMENT, task, ctx, external_deps_by_ref=external_deps_by_ref
        )
        prompt_without = build_worker_prompt(ROLE_IMPLEMENT, task, ctx)
        self.assertEqual(prompt_with, prompt_without)
        self.assertNotIn("delivered by", prompt_with)


class TestWorkerBriefForbidsWaitLoop(unittest.TestCase):
    """Regression guard for tasks/lessons.md 'never hand-roll an unbounded
    background-wait loop' — the spawned-worker vector. Every per-task worker
    brief must forbid hand-rolling a wait/poll loop and direct the worker to
    run to completion and report back; the orchestrator owns the waiting."""

    def test_every_worker_brief_forbids_hand_rolled_wait_loop(self):
        ctx = _make_ctx()
        task = _make_task()
        for role in (ROLE_IMPLEMENT, ROLE_REVIEW, ROLE_FIX, ROLE_CLEANUP):
            prompt = build_worker_prompt(role, task, ctx)
            self.assertIn(
                "hand-roll a background-wait loop",
                prompt,
                f"role={role} brief must forbid hand-rolled wait loops",
            )
            self.assertIn(
                "report back",
                prompt,
                f"role={role} brief must tell the worker to report back",
            )


class TestWorkerBriefForbidsHarnessBackgrounding(unittest.TestCase):
    """Regression guard for brief 20260821-182348.

    The wait-loop rule above names only the SHELL form (`while true` / `until` /
    `sleep`), so run go-20260821-141546 lost three spawns to a worker that used
    its harness's own backgrounding affordance instead and ended its turn with
    "Test suite running in background; I will wait for its completion
    notification before continuing." A headless spawn is torn down at the end of
    that turn, so parse_report_back found no JSON block and salvage_report
    recovered nothing (the worker never committed). Every brief must name the
    affordance itself, not just the shell idiom.
    """

    def test_every_worker_brief_forbids_harness_backgrounding(self):
        ctx = _make_ctx()
        task = _make_task()
        for role in (ROLE_IMPLEMENT, ROLE_REVIEW, ROLE_FIX, ROLE_CLEANUP):
            prompt = build_worker_prompt(role, task, ctx)
            self.assertIn(
                "run_in_background",
                prompt,
                f"role={role} brief must name the harness backgrounding affordance",
            )
            self.assertIn(
                "single headless turn",
                prompt,
                f"role={role} brief must say no notification will arrive",
            )
            self.assertIn(
                "FOREGROUND",
                prompt,
                f"role={role} brief must require foreground execution to completion",
            )


class TestExtraReadsContextWidening(unittest.TestCase):
    """AC: extra_reads parameter threads missing context into the 'Read first' block."""

    def test_extra_reads_appear_in_fix_prompt(self):
        """extra_reads items must appear in the ROLE_FIX 'Read first, in order' section."""
        ctx = _make_ctx()
        task = _make_task()
        extra = ["src/missing_module.py", "tests/missing_test.py"]
        prompt = build_worker_prompt(ROLE_FIX, task, ctx, extra_reads=extra)
        read_section = prompt[prompt.index("Read first, in order:") :]
        for path in extra:
            self.assertIn(
                path,
                read_section,
                f"extra read {path!r} must appear in the 'Read first' section of fix prompt",
            )

    def test_extra_reads_none_unchanged(self):
        """extra_reads=None must produce the same prompt as calling without extra_reads."""
        ctx = _make_ctx()
        task = _make_task()
        base = build_worker_prompt(ROLE_FIX, task, ctx)
        self.assertEqual(
            build_worker_prompt(ROLE_FIX, task, ctx, extra_reads=None), base
        )

    def test_extra_reads_empty_list_unchanged(self):
        """extra_reads=[] must produce the same prompt as calling without extra_reads."""
        ctx = _make_ctx()
        task = _make_task()
        base = build_worker_prompt(ROLE_FIX, task, ctx)
        self.assertEqual(build_worker_prompt(ROLE_FIX, task, ctx, extra_reads=[]), base)

    def test_extra_reads_not_restricted_by_role(self):
        """extra_reads can be injected into any role prompt (caller decides when to use)."""
        ctx = _make_ctx()
        task = _make_task()
        extra = ["src/shared.py"]
        for role in (ROLE_IMPLEMENT, ROLE_REVIEW, ROLE_FIX):
            prompt = build_worker_prompt(role, task, ctx, extra_reads=extra)
            self.assertIn(
                "src/shared.py",
                prompt,
                f"extra read must appear in {role} prompt when supplied",
            )

    def test_extra_reads_multiple_items_all_present(self):
        """All items in extra_reads must appear in the prompt."""
        ctx = _make_ctx()
        task = _make_task()
        extra = ["api/routes.py", "models/user.py", "tests/test_routes.py"]
        prompt = build_worker_prompt(ROLE_FIX, task, ctx, extra_reads=extra)
        for path in extra:
            self.assertIn(path, prompt)


class TestAgentForPrecedence(unittest.TestCase):
    """worktrail.orchestrator.dispatch.agent_for(): 4-tier precedence + judgment-role guard (TASK-006)."""

    TIER_MAP: ClassVar = {
        ("complex", "backend"): {
            "agent_cli": "codex",
            "agent_model": "gpt-tier",
            "effort": "high",
        }
    }
    ROLE_MAP: ClassVar = {
        "implement": {"agent_cli": "opencode", "agent_model": "oc-model"}
    }

    def _task(self, **overrides):
        task = {
            "id": "TASK-001",
            "files": ["src/foo.py"],
            "complexity": "complex",
            "domain": "backend",
        }
        task.update(overrides)
        return task

    def test_tier_match_no_overrides_resolves_to_tier_agent(self):
        """AC-011: implement + tier match + no per-task/role override -> tier's
        agent+model+effort (model-tier-routing 3.2)."""
        task = self._task()
        result = agent_for(ROLE_IMPLEMENT, task, tier_map=self.TIER_MAP)
        self.assertEqual(
            result, {"agent_cli": "codex", "agent_model": "gpt-tier", "effort": "high"}
        )

    def test_tier_match_no_match_falls_through_to_run_default(self):
        """A tier_map with no entry for this task's (complexity, domain) falls through."""
        task = self._task(complexity="simple", domain="frontend")
        result = agent_for(
            ROLE_IMPLEMENT, task, default_agent="claude", tier_map=self.TIER_MAP
        )
        self.assertEqual(
            result, {"agent_cli": "claude", "agent_model": None, "effort": None}
        )

    def test_per_task_override_beats_tier_match(self):
        """AC-012: task['agent'] outranks a tier match, for implement/fix/cleanup."""
        for role in (ROLE_IMPLEMENT, ROLE_FIX, ROLE_CLEANUP):
            task = self._task(agent="claude")
            result = agent_for(
                role, task, tier_map=self.TIER_MAP, role_agent_map=self.ROLE_MAP
            )
            self.assertEqual(
                result,
                {"agent_cli": "claude", "agent_model": None, "effort": None},
                f"per-task override must win for role={role!r}",
            )

    def test_role_override_beats_tier_match(self):
        """AC-013: an operator role_agent_map entry for implement outranks a tier match."""
        task = self._task()
        result = agent_for(
            ROLE_IMPLEMENT, task, tier_map=self.TIER_MAP, role_agent_map=self.ROLE_MAP
        )
        self.assertEqual(
            result, {"agent_cli": "opencode", "agent_model": "oc-model", "effort": None}
        )

    def test_review_ignores_per_task_override_and_tier_match(self):
        """AC-014: review uses only role override / run default, even with both
        a per-task override and a tier match configured."""
        task = self._task(agent="claude")
        result = agent_for(
            ROLE_REVIEW,
            task,
            reviewer_agent="code-reviewer",
            tier_map=self.TIER_MAP,
            role_agent_map={},
        )
        self.assertEqual(
            result, {"agent_cli": "code-reviewer", "agent_model": None, "effort": None}
        )

    def test_review_role_override_still_applies(self):
        """A role_agent_map entry for review IS consulted (it's tier 2, judgment
        roles only skip tiers 1 and 3)."""
        task = self._task(agent="claude")
        result = agent_for(
            ROLE_REVIEW,
            task,
            tier_map=self.TIER_MAP,
            role_agent_map={"review": "gemini"},
        )
        self.assertEqual(
            result, {"agent_cli": "gemini", "agent_model": None, "effort": None}
        )

    def test_role_override_carries_effort(self):
        """model-tier-routing 3.2: an effort on a role_agent_map entry reaches
        the resolved result, for both an implement/fix/cleanup role and a
        judgment role (tier 2 for both)."""
        role_map = {
            "implement": {
                "agent_cli": "opencode",
                "agent_model": "oc-model",
                "effort": "low",
            },
            "review": {"agent_cli": "gemini", "agent_model": None, "effort": "medium"},
        }
        task = self._task()
        self.assertEqual(
            agent_for(ROLE_IMPLEMENT, task, role_agent_map=role_map),
            {"agent_cli": "opencode", "agent_model": "oc-model", "effort": "low"},
        )
        self.assertEqual(
            agent_for(ROLE_REVIEW, task, role_agent_map=role_map),
            {"agent_cli": "gemini", "agent_model": None, "effort": "medium"},
        )

    def test_resolve_ci_fix_assembly_resolve_ignore_per_task_and_tier(self):
        """AC-015: resolve, ci-fix, and assembly-resolve behave identically to review."""
        for role in (ROLE_RESOLVE, ROLE_CI_FIX, ROLE_ASSEMBLY_RESOLVE):
            task = self._task(agent="claude")
            result = agent_for(
                role,
                task,
                default_agent="claude-run-default",
                tier_map=self.TIER_MAP,
                role_agent_map={},
            )
            self.assertEqual(
                result,
                {
                    "agent_cli": "claude-run-default",
                    "agent_model": None,
                    "effort": None,
                },
                f"judgment-role guard must hold for role={role!r}",
            )
            override_result = agent_for(
                role,
                task,
                default_agent="claude-run-default",
                tier_map=self.TIER_MAP,
                role_agent_map={role: "codex"},
            )
            self.assertEqual(
                override_result,
                {"agent_cli": "codex", "agent_model": None, "effort": None},
                f"role override must still apply for role={role!r}",
            )

    def test_no_routing_no_tier_matches_pre_spec_behavior(self):
        """REQ-016: with no role_agent_map/tier_map, agent_for returns exactly the
        pre-spec truth table for every role."""
        cases = [
            (
                ROLE_IMPLEMENT,
                self._task(agent="claude"),
                {"default_agent": "codex"},
                {"agent_cli": "claude", "agent_model": None, "effort": None},
            ),
            (
                ROLE_IMPLEMENT,
                self._task(agent=None),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None, "effort": None},
            ),
            (
                ROLE_IMPLEMENT,
                self._task(agent=None),
                {},
                {"agent_cli": "claude", "agent_model": None, "effort": None},
            ),
            (
                ROLE_FIX,
                self._task(agent="opencode"),
                {"default_agent": "codex"},
                {"agent_cli": "opencode", "agent_model": None, "effort": None},
            ),
            (
                ROLE_CLEANUP,
                self._task(agent=None),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None, "effort": None},
            ),
            (
                ROLE_REVIEW,
                self._task(agent="claude"),
                {"reviewer_agent": "code-reviewer"},
                {"agent_cli": "code-reviewer", "agent_model": None, "effort": None},
            ),
            (
                ROLE_RESOLVE,
                self._task(agent="claude"),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None, "effort": None},
            ),
            (
                ROLE_CI_FIX,
                self._task(agent="claude"),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None, "effort": None},
            ),
            (
                ROLE_ASSEMBLY_RESOLVE,
                self._task(agent="claude"),
                {},
                {"agent_cli": "claude", "agent_model": None, "effort": None},
            ),
        ]
        for role, task, kwargs, expected in cases:
            result = agent_for(role, task, **kwargs)
            self.assertEqual(result, expected, f"role={role!r} kwargs={kwargs!r}")

    def test_deterministic_same_inputs_same_output(self):
        """REQ-NR002: same inputs always produce the same output."""
        task = self._task()
        kwargs = {
            "reviewer_agent": "code-reviewer",
            "default_agent": "claude",
            "role_agent_map": self.ROLE_MAP,
            "tier_map": self.TIER_MAP,
        }
        for role in (
            ROLE_IMPLEMENT,
            ROLE_REVIEW,
            ROLE_FIX,
            ROLE_CLEANUP,
            ROLE_RESOLVE,
            ROLE_CI_FIX,
            ROLE_ASSEMBLY_RESOLVE,
        ):
            first = agent_for(role, task, **kwargs)
            for _ in range(5):
                self.assertEqual(agent_for(role, task, **kwargs), first)


class TestAgentForPurposeTierPrecedence(unittest.TestCase):
    """worktrail.orchestrator.dispatch.agent_for(): purpose-derived tier
    precedence + agent-aware tier_map lookup (task-purpose-classification 4.2/4.3)."""

    PURPOSE_TIER_MAP: ClassVar = {"architecture-design": "t1-deep"}

    TIER_MAP: ClassVar = {
        ("t1-deep", "backend"): {
            "agent_cli": "codex",
            "agent_model": None,
            "effort": None,
        },
        ("complex", "backend"): {
            "agent_cli": "opencode",
            "agent_model": None,
            "effort": None,
        },
    }

    AGENT_AWARE_TIER_MAP: ClassVar = {
        ("t1-deep-codex", "backend"): {
            "agent_cli": "codex",
            "agent_model": "codex-model",
            "effort": "high",
        },
        ("t1-deep", "backend"): {
            "agent_cli": "claude",
            "agent_model": "claude-model",
            "effort": "low",
        },
    }

    def _task(self, **overrides):
        task = {
            "id": "TASK-001",
            "files": ["src/foo.py"],
            "complexity": "complex",
            "domain": "backend",
            "purpose": "architecture-design",
        }
        task.update(overrides)
        return task

    def test_purpose_tier_takes_precedence_over_complexity(self):
        """4.4: when purpose resolves via purpose_tier_map, its tier is used
        instead of task['complexity'] for the tier_map lookup."""
        task = self._task()
        result = agent_for(
            ROLE_IMPLEMENT,
            task,
            tier_map=self.TIER_MAP,
            purpose_tier_map=self.PURPOSE_TIER_MAP,
        )
        self.assertEqual(
            result, {"agent_cli": "codex", "agent_model": None, "effort": None}
        )

    def test_falls_back_to_complexity_when_purpose_does_not_resolve(self):
        """4.4: an unmapped purpose (or none) falls back to task['complexity']."""
        task = self._task(purpose="unmapped-purpose")
        result = agent_for(
            ROLE_IMPLEMENT,
            task,
            tier_map=self.TIER_MAP,
            purpose_tier_map=self.PURPOSE_TIER_MAP,
        )
        self.assertEqual(
            result, {"agent_cli": "opencode", "agent_model": None, "effort": None}
        )

        task_no_purpose = self._task(purpose=None)
        result_no_purpose = agent_for(
            ROLE_IMPLEMENT,
            task_no_purpose,
            tier_map=self.TIER_MAP,
            purpose_tier_map=self.PURPOSE_TIER_MAP,
        )
        self.assertEqual(
            result_no_purpose,
            {"agent_cli": "opencode", "agent_model": None, "effort": None},
        )

    def test_judgment_roles_never_consult_purpose_or_purpose_tier_map(self):
        """4.4: JUDGMENT_ROLES ignore purpose/purpose_tier_map entirely, even
        when both are populated and would otherwise resolve a tier match."""
        for role in (ROLE_REVIEW, ROLE_RESOLVE, ROLE_CI_FIX, ROLE_ASSEMBLY_RESOLVE):
            task = self._task()
            result = agent_for(
                role,
                task,
                default_agent="claude-run-default",
                reviewer_agent="code-reviewer",
                tier_map=self.TIER_MAP,
                purpose_tier_map=self.PURPOSE_TIER_MAP,
                role_agent_map={},
            )
            expected_cli = (
                "code-reviewer" if role == ROLE_REVIEW else "claude-run-default"
            )
            self.assertEqual(
                result,
                {"agent_cli": expected_cli, "agent_model": None, "effort": None},
                f"purpose_tier_map must be ignored for judgment role={role!r}",
            )

    def test_agent_aware_key_preferred_over_plain_key(self):
        """4.4: (f'{tier}-{agent}', domain) is tried before (tier, domain)
        when both are present in tier_map."""
        task = self._task(agent=None)
        result = agent_for(
            ROLE_IMPLEMENT,
            task,
            default_agent="codex",
            tier_map=self.AGENT_AWARE_TIER_MAP,
            purpose_tier_map=self.PURPOSE_TIER_MAP,
        )
        self.assertEqual(
            result,
            {"agent_cli": "codex", "agent_model": "codex-model", "effort": "high"},
        )

    def test_falls_back_to_plain_key_when_no_agent_specific_entry(self):
        """4.4: when only the plain (tier, domain) key exists, that match is
        used."""
        task = self._task(agent=None)
        result = agent_for(
            ROLE_IMPLEMENT,
            task,
            default_agent="claude",
            tier_map=self.TIER_MAP,
            purpose_tier_map=self.PURPOSE_TIER_MAP,
        )
        self.assertEqual(
            result, {"agent_cli": "codex", "agent_model": None, "effort": None}
        )

    def test_byte_identical_to_pre_change_agent_for_without_purpose(self):
        """4.4: with no purpose/purpose_tier_map/agent-aware keys involved,
        output is identical to pre-change agent_for() behavior."""
        task = {
            "id": "TASK-001",
            "files": ["src/foo.py"],
            "complexity": "complex",
            "domain": "backend",
        }
        legacy_tier_map = {
            ("complex", "backend"): {
                "agent_cli": "codex",
                "agent_model": "gpt-tier",
                "effort": "high",
            }
        }
        result_without_purpose_kw = agent_for(
            ROLE_IMPLEMENT, task, tier_map=legacy_tier_map
        )
        result_with_none_purpose_map = agent_for(
            ROLE_IMPLEMENT, task, tier_map=legacy_tier_map, purpose_tier_map=None
        )
        result_with_empty_purpose_map = agent_for(
            ROLE_IMPLEMENT, task, tier_map=legacy_tier_map, purpose_tier_map={}
        )
        expected = {"agent_cli": "codex", "agent_model": "gpt-tier", "effort": "high"}
        self.assertEqual(result_without_purpose_kw, expected)
        self.assertEqual(result_with_none_purpose_map, expected)
        self.assertEqual(result_with_empty_purpose_map, expected)


class ResolvedDecisionDispatchGateTests(unittest.TestCase):
    """Spec pending-user-decision-dispatch-contract 3.1: orchestrator dispatch
    rejects unresolved decision envelopes and accepts only provenance-validated
    resolved input."""

    DECISION_ID = "dec-dispatch-gate-0001"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue_base = Path(self.tmp.name) / "queue"

    def _ask(self, decision_id=DECISION_ID, **kw):
        params = {
            "question": "Which scope should this request take?",
            "background": "The shipped spec already covers the requested scope.",
            "why": "Scope direction is a product call.",
            "context": "verify() confirmed Implemented status and git-tracked files.",
            "options": [
                "extend: continue the existing spec",
                "proceed-anyway: dispatch despite the collision",
            ],
            "source": "check_spec_collision",
            "repo": "/tmp/some-repo",
            "subject": "spec-a",
            "decision_id": decision_id,
            "queue_base": self.queue_base,
        }
        params.update(kw)
        result = decisions_mod.ask(**params)
        self.assertEqual(result["status"], "created")
        return result

    def _answer(self, decision_id=DECISION_ID):
        answered = decisions_mod.answer(
            decision_id,
            "extend: continue the existing spec",
            queue_base=self.queue_base,
        )
        self.assertEqual(answered["status"], "answered")

    def _load(self, decision_id=DECISION_ID):
        envelope = decisions_mod.load_decision_envelope(decision_id, self.queue_base)
        self.assertIsNotNone(envelope)
        return envelope

    def test_open_envelope_is_rejected_as_unresolved(self):
        self._ask()
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(self._load())
        message = str(cm.exception)
        self.assertIn(self.DECISION_ID, message)
        self.assertIn("not 'answered'", message)

    def test_never_filed_pending_envelope_is_rejected(self):
        envelope = decisions_mod.pending_decision_envelope(
            decision_id=self.DECISION_ID,
            question="Which scope should this request take?",
            options=["extend", "proceed-anyway"],
            source="check_spec_collision",
        )
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(envelope)
        self.assertIn("'pending'", str(cm.exception))

    def test_superseded_record_is_rejected_and_names_the_replacement(self):
        self._ask()
        replacement = "dec-dispatch-replacement-0002"
        self._ask(decision_id=replacement, subject="spec-a-moved")
        superseded = decisions_mod.supersede(
            self.DECISION_ID, replacement, queue_base=self.queue_base
        )
        self.assertEqual(superseded["status"], "superseded")
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(self._load())
        self.assertIn(replacement, str(cm.exception))

    def test_already_consumed_answer_is_never_replayed_into_dispatch(self):
        self._ask()
        self._answer()
        consumed = decisions_mod.consume_answer(
            self.DECISION_ID, consumed_by="run-go-1", queue_base=self.queue_base
        )
        self.assertEqual(consumed["status"], "consumed")
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(self._load())
        self.assertIn("not 'answered'", str(cm.exception))

    def test_malformed_dispatch_input_is_rejected(self):
        for bad in ({}, None, "not-json", ["nope"], 42):
            with self.subTest(bad=bad), self.assertRaises(DecisionDispatchError):
                validate_resolved_decision_input(bad)

    def test_wrong_schema_or_version_is_rejected(self):
        envelope = self._answered_envelope()
        for key, value in (("schema", "other.schema"), ("version", 99)):
            mutated = dict(envelope)
            mutated[key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaises(DecisionDispatchError) as cm:
                    validate_resolved_decision_input(mutated)
                self.assertIn(key, str(cm.exception))

    def test_provenance_mismatch_is_rejected(self):
        envelope = self._answered_envelope()
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(envelope, expected_subject="spec-b")
        self.assertIn("provenance subject mismatch", str(cm.exception))

    def test_provenance_validated_answered_input_is_accepted(self):
        envelope = self._answered_envelope()
        accepted = validate_resolved_decision_input(
            envelope,
            expected_source="check_spec_collision",
            expected_repo="/tmp/some-repo",
            expected_subject="spec-a",
        )
        self.assertEqual(accepted["decision_id"], self.DECISION_ID)
        self.assertEqual(accepted["status"], "answered")
        self.assertEqual(accepted["answer"], "extend: continue the existing spec")

    def test_stale_answer_beyond_freshness_window_is_rejected(self):
        envelope = self._answered_envelope()
        answered_at = dt.datetime.fromisoformat(envelope["answered_at"])
        late_now = answered_at + dt.timedelta(seconds=120)
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(envelope, max_age_seconds=60, now=late_now)
        self.assertIn("stale", str(cm.exception))

    def test_all_failed_expectations_are_reported_together(self):
        self._ask()  # still open, and the run expects a different subject
        with self.assertRaises(DecisionDispatchError) as cm:
            validate_resolved_decision_input(self._load(), expected_subject="spec-b")
        message = str(cm.exception)
        self.assertIn("not 'answered'", message)
        self.assertIn("provenance subject mismatch", message)

    def _answered_envelope(self):
        self._ask()
        self._answer()
        return self._load()


if __name__ == "__main__":
    unittest.main()
