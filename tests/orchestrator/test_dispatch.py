#!/usr/bin/env python3
"""Unit tests for dispatch.py prompt building."""

import re
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from worktrail.orchestrator.dispatch import (
    agent_for,
    build_worker_prompt,
    transition,
    ROLE_CLEANUP,
    ROLE_REVIEW,
    ROLE_FIX,
    ROLE_IMPLEMENT,
    ROLE_RESOLVE,
    ROLE_CI_FIX,
    ROLE_ASSEMBLY_RESOLVE,
    _ROLE_ACTION,
)


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

        expected_path = "docs/specs/004-orchestrator-review-writer-path/reviews/TASK-002-review.md"
        bare_path = "reviews/TASK-002-review.md"

        self.assertIn(
            expected_path,
            prompt,
            f"ROLE_REVIEW prompt must contain spec-folder-relative path '{expected_path}'",
        )
        # The bare path must NOT appear as a standalone reference (not prefixed by spec_folder)
        # Find all occurrences of bare_path and check none are bare (i.e., not preceded by spec_folder)
        bare_occurrences = [m.start() for m in re.finditer(re.escape(bare_path), prompt)]
        for pos in bare_occurrences:
            prefix = prompt[
                max(0, pos - len("docs/specs/004-orchestrator-review-writer-path/")) : pos
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
            expected_path, review_prompt, f"ROLE_REVIEW prompt must reference '{expected_path}'"
        )
        self.assertIn(
            expected_path, fix_prompt, f"ROLE_FIX prompt must reference '{expected_path}'"
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

    def test_missing_spec_folder_raises_key_error(self):
        """REQ-NR006: missing spec_folder fails fast (KeyError), no silent bare-path fallback."""
        ctx = {
            "spec_id": "004-test",
            # spec_folder intentionally omitted
            "worktree_path": "/tmp/worktrees/004-test",
            "branch": "task/TASK-001",
            "base_commit": "abc1234",
        }
        task = _make_task("TASK-001")
        with self.assertRaises(KeyError):
            build_worker_prompt(ROLE_REVIEW, task, ctx)


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
        "Re-run the task's tests. Commit."
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
        "Before committing, set the `status` frontmatter field of "
        "`{spec_folder}tasks/{task_id}.md` to `completed` — change ONLY that field, "
        "leave every other line byte-for-byte unchanged. "
        "(Exception to the 'Do NOT modify docs/specs/** status' hard rule: this "
        "write-back is explicitly permitted for your own task file's `status` field "
        "only; no other docs/specs/** file or status may be altered.) Commit."
    )

    def test_review_action_names_ac_and_dod_sections(self):
        """AC-CHG-009: ROLE_REVIEW action names both checkbox sections to tick."""
        action = _ROLE_ACTION[ROLE_REVIEW]
        self.assertIn("## Acceptance Criteria", action)
        self.assertIn("## Definition of Done (DoD)", action)

    def test_review_action_couples_passed_to_all_ticked(self):
        """AC-CHG-010: PASSED requires all AC/DoD ticked; FAILED + finding on
        any unticked item."""
        action = _ROLE_ACTION[ROLE_REVIEW]
        self.assertIn("PASSED", action)
        self.assertIn("EVERY AC/DoD checkbox ticked", action)
        self.assertIn("FAILED", action)
        self.assertIn("finding", action)

    def test_review_action_instructs_committing_task_file_edit(self):
        """AC-CHG-011: ROLE_REVIEW action instructs committing the task-file
        checkbox edit, in addition to writing the review file."""
        action = _ROLE_ACTION[ROLE_REVIEW]
        self.assertIn("Commit the task-file checkbox edit", action)
        self.assertIn("reviews/{task_id}-review.md", action)

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

    def test_cleanup_prompt_contains_status_writeback(self):
        """AC-008: rendered ROLE_CLEANUP prompt contains 'status', 'completed',
        and the spec-folder-relative task-file path."""
        spec_folder = "docs/specs/006-orchestrator-task-status-writeback/"
        ctx = _make_ctx(spec_folder)
        task = _make_task("TASK-002")
        prompt = build_worker_prompt(ROLE_CLEANUP, task, ctx)

        expected_path = f"{spec_folder}tasks/TASK-002.md"

        self.assertIn(
            "status",
            prompt,
            "ROLE_CLEANUP prompt must mention the field name 'status'",
        )
        self.assertIn(
            "completed",
            prompt,
            "ROLE_CLEANUP prompt must mention the target value 'completed'",
        )
        self.assertIn(
            expected_path,
            prompt,
            f"ROLE_CLEANUP prompt must reference spec-folder-relative path '{expected_path}'",
        )

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
        new_status, retry = transition(ROLE_REVIEW, report, retry_count=2, max_retries=3)
        self.assertEqual(new_status, "escalated")

    def test_non_review_status_failed_still_returns_failed(self):
        """Non-review roles with status:"failed" must still return "failed" (no regression)."""
        for role in (ROLE_IMPLEMENT, ROLE_FIX, ROLE_CLEANUP):
            report = {"task": "TASK-001", "step": role, "status": "failed"}
            new_status, retry = transition(role, report, retry_count=0)
            self.assertEqual(
                new_status, "failed", f"role={role} with status:failed should return 'failed'"
            )


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
            prompt = build_worker_prompt(role, task, ctx, external_deps_by_ref=external_deps_by_ref)
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
                "report back", prompt, f"role={role} brief must tell the worker to report back"
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
        self.assertEqual(build_worker_prompt(ROLE_FIX, task, ctx, extra_reads=None), base)

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
                "src/shared.py", prompt, f"extra read must appear in {role} prompt when supplied"
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

    TIER_MAP = {("complex", "backend"): {"agent_cli": "codex", "agent_model": "gpt-tier"}}
    ROLE_MAP = {"implement": {"agent_cli": "opencode", "agent_model": "oc-model"}}

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
        """AC-011: implement + tier match + no per-task/role override -> tier's agent+model."""
        task = self._task()
        result = agent_for(ROLE_IMPLEMENT, task, tier_map=self.TIER_MAP)
        self.assertEqual(result, {"agent_cli": "codex", "agent_model": "gpt-tier"})

    def test_tier_match_no_match_falls_through_to_run_default(self):
        """A tier_map with no entry for this task's (complexity, domain) falls through."""
        task = self._task(complexity="simple", domain="frontend")
        result = agent_for(ROLE_IMPLEMENT, task, default_agent="claude", tier_map=self.TIER_MAP)
        self.assertEqual(result, {"agent_cli": "claude", "agent_model": None})

    def test_per_task_override_beats_tier_match(self):
        """AC-012: task['agent'] outranks a tier match, for implement/fix/cleanup."""
        for role in (ROLE_IMPLEMENT, ROLE_FIX, ROLE_CLEANUP):
            task = self._task(agent="claude")
            result = agent_for(role, task, tier_map=self.TIER_MAP, role_agent_map=self.ROLE_MAP)
            self.assertEqual(
                result,
                {"agent_cli": "claude", "agent_model": None},
                f"per-task override must win for role={role!r}",
            )

    def test_role_override_beats_tier_match(self):
        """AC-013: an operator role_agent_map entry for implement outranks a tier match."""
        task = self._task()
        result = agent_for(
            ROLE_IMPLEMENT, task, tier_map=self.TIER_MAP, role_agent_map=self.ROLE_MAP
        )
        self.assertEqual(result, {"agent_cli": "opencode", "agent_model": "oc-model"})

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
        self.assertEqual(result, {"agent_cli": "code-reviewer", "agent_model": None})

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
        self.assertEqual(result, {"agent_cli": "gemini", "agent_model": None})

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
                {"agent_cli": "claude-run-default", "agent_model": None},
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
                {"agent_cli": "codex", "agent_model": None},
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
                {"agent_cli": "claude", "agent_model": None},
            ),
            (
                ROLE_IMPLEMENT,
                self._task(agent=None),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None},
            ),
            (
                ROLE_IMPLEMENT,
                self._task(agent=None),
                {},
                {"agent_cli": "claude", "agent_model": None},
            ),
            (
                ROLE_FIX,
                self._task(agent="opencode"),
                {"default_agent": "codex"},
                {"agent_cli": "opencode", "agent_model": None},
            ),
            (
                ROLE_CLEANUP,
                self._task(agent=None),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None},
            ),
            (
                ROLE_REVIEW,
                self._task(agent="claude"),
                {"reviewer_agent": "code-reviewer"},
                {"agent_cli": "code-reviewer", "agent_model": None},
            ),
            (
                ROLE_RESOLVE,
                self._task(agent="claude"),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None},
            ),
            (
                ROLE_CI_FIX,
                self._task(agent="claude"),
                {"default_agent": "codex"},
                {"agent_cli": "codex", "agent_model": None},
            ),
            (
                ROLE_ASSEMBLY_RESOLVE,
                self._task(agent="claude"),
                {},
                {"agent_cli": "claude", "agent_model": None},
            ),
        ]
        for role, task, kwargs, expected in cases:
            result = agent_for(role, task, **kwargs)
            self.assertEqual(result, expected, f"role={role!r} kwargs={kwargs!r}")

    def test_deterministic_same_inputs_same_output(self):
        """REQ-NR002: same inputs always produce the same output."""
        task = self._task()
        kwargs = dict(
            reviewer_agent="code-reviewer",
            default_agent="claude",
            role_agent_map=self.ROLE_MAP,
            tier_map=self.TIER_MAP,
        )
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


if __name__ == "__main__":
    unittest.main()
