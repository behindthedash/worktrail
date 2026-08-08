#!/usr/bin/env python3
"""Extra coverage for live.py: RunLock, read_or_create_run_id, set_task_status_completed,
_task_file_in_worktree, journal_path_for, _resume_quarantine_staleness_warning."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import agent_capacity
from worktrail.orchestrator import live
from worktrail.orchestrator import spawnlib


class ReviewerSystemPromptTests(unittest.TestCase):
    """Handoff 20260731-082737: the reviewer must flag a diff that deviates from
    the task's literal instruction rather than accept a rationale for it."""

    def test_flags_deviation_as_failed_worthy_on_its_own(self):
        self.assertIn("different approach than the task literally describes", live._REVIEWER_SYSTEM_PROMPT)
        self.assertIn("does not substitute for flagging it", live._REVIEWER_SYSTEM_PROMPT)


class RunLockTests(unittest.TestCase):
    def test_context_manager_enter_returns_self(self):
        with tempfile.TemporaryDirectory() as t:
            lock_path = os.path.join(t, "test.lock")
            lock = live.RunLock(lock_path)
            with lock as l:
                self.assertIs(l, lock)

    def test_context_manager_exit_releases(self):
        with tempfile.TemporaryDirectory() as t:
            lock_path = os.path.join(t, "test.lock")
            with live.RunLock(lock_path):
                pass
            # Can be acquired again after release
            with live.RunLock(lock_path):
                pass

    def test_release_with_fcntl_error_does_not_raise(self):
        with tempfile.TemporaryDirectory() as t:
            lock_path = os.path.join(t, "test.lock")
            lock = live.RunLock(lock_path)
            lock.acquire()
            with patch("fcntl.flock", side_effect=OSError("flock failed")):
                lock.release()  # Should not raise

    def test_double_release_is_safe(self):
        with tempfile.TemporaryDirectory() as t:
            lock_path = os.path.join(t, "test.lock")
            lock = live.RunLock(lock_path)
            lock.acquire()
            lock.release()
            lock.release()  # Second release is a no-op


class ReadOrCreateRunIdTests(unittest.TestCase):
    def test_creates_journal_when_absent(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "run.json"
            run_id = live.read_or_create_run_id(path)
            self.assertTrue(run_id.startswith("full-"))
            self.assertTrue(path.exists())
            data = json.loads(path.read_text())
            self.assertEqual(data["run_id"], run_id)

    def test_returns_existing_run_id(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "run.json"
            path.write_text(json.dumps({"run_id": "full-12345", "entries": []}))
            run_id = live.read_or_create_run_id(path)
            self.assertEqual(run_id, "full-12345")

    def test_injects_run_id_into_legacy_journal(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "run.json"
            path.write_text(json.dumps({"entries": [{"task": "TASK-001"}]}))
            run_id = live.read_or_create_run_id(path)
            self.assertTrue(run_id.startswith("full-"))
            data = json.loads(path.read_text())
            self.assertEqual(data["run_id"], run_id)
            self.assertEqual(len(data["entries"]), 1)  # entries preserved

    def test_corrupted_journal_creates_fresh(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "run.json"
            path.write_text("{not valid json")
            run_id = live.read_or_create_run_id(path)
            self.assertTrue(run_id.startswith("full-"))


class TaskFileInWorktreeTests(unittest.TestCase):
    def test_standard_tasks_dir_layout(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            spec_rel = "docs/specs/001-spec"
            tasks_dir = wt / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)
            tf = tasks_dir / "TASK-001.md"
            tf.write_text("---\nid: TASK-001\n---\n")
            result = live._task_file_in_worktree(wt, spec_rel, "TASK-001")
            self.assertEqual(result, tf)

    def test_changes_dir_layout(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            spec_rel = "docs/specs/001-spec"
            change_dir = wt / spec_rel / "changes" / "20260101-fix"
            change_dir.mkdir(parents=True)
            tf = change_dir / "TASK-001.md"
            tf.write_text("---\nid: TASK-001\n---\n")
            result = live._task_file_in_worktree(wt, spec_rel, "TASK-001")
            self.assertEqual(result, tf)

    def test_missing_returns_canonical_path(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            spec_rel = "docs/specs/001-spec"
            (wt / spec_rel).mkdir(parents=True)
            result = live._task_file_in_worktree(wt, spec_rel, "TASK-999")
            self.assertIn("TASK-999", result.name)


class RequireTaskFileTests(unittest.TestCase):
    """Defense-in-depth guard: a task worktree branched before its tasks/ commit
    landed must fail loud at dispatch time instead of silently reaching the
    IMPLEMENT worker with no brief (handoff 20260711-125811 / PR #248 follow-up)."""

    def test_raises_when_task_file_missing(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            spec_rel = "docs/specs/001-spec"
            (wt / spec_rel).mkdir(parents=True)
            with self.assertRaises(live.WorktreeMissingTaskFileError) as ctx:
                live._require_task_file(wt, spec_rel, "TASK-CHG-001")
            self.assertIn("TASK-CHG-001", str(ctx.exception))
            self.assertIn(str(wt), str(ctx.exception))

    def test_does_not_raise_for_standard_tasks_dir_layout(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            spec_rel = "docs/specs/001-spec"
            tasks_dir = wt / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "TASK-001.md").write_text("---\nid: TASK-001\n---\n")
            live._require_task_file(wt, spec_rel, "TASK-001")  # should not raise

    def test_does_not_raise_for_changes_dir_layout(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            spec_rel = "docs/specs/001-spec"
            change_dir = wt / spec_rel / "changes" / "20260101-fix"
            change_dir.mkdir(parents=True)
            (change_dir / "TASK-CHG-001.md").write_text("---\nid: TASK-CHG-001\n---\n")
            live._require_task_file(wt, spec_rel, "TASK-CHG-001")  # should not raise


class SetTaskStatusCompletedTests(unittest.TestCase):
    def test_sets_status_to_completed(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: TASK-001\nstatus: pending\n---\nbody\n")
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertTrue(changed)
            content = path.read_text()
            self.assertIn("status: completed", content)
            self.assertIn("id: TASK-001", content)
        finally:
            os.unlink(path)

    def test_no_change_when_already_completed(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: TASK-001\nstatus: completed\n---\nbody\n")
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertFalse(changed)
        finally:
            os.unlink(path)

    def test_nonexistent_file_returns_false(self):
        changed = live.set_task_status_completed(Path("/nonexistent/TASK-001.md"))
        self.assertFalse(changed)

    def test_no_frontmatter_returns_false(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("no frontmatter here\n")
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertFalse(changed)
        finally:
            os.unlink(path)

    def test_appends_status_when_missing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: TASK-001\n---\nbody\n")
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertTrue(changed)
            self.assertIn("status: completed", path.read_text())
        finally:
            os.unlink(path)

    def test_ticks_unticked_checkboxes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(
                "---\nid: TASK-001\nstatus: pending\n---\n"
                "## Acceptance Criteria\n- [ ] one\n- [ ] two\n"
            )
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertTrue(changed)
            content = path.read_text()
            self.assertIn("status: completed", content)
            self.assertNotIn("- [ ]", content)
            self.assertEqual(content.count("- [x]"), 2)
        finally:
            os.unlink(path)

    def test_body_without_checkboxes_unaffected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: TASK-001\nstatus: pending\n---\nbody\nno checkboxes here\n")
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertTrue(changed)
            content = path.read_text()
            self.assertIn("status: completed", content)
            self.assertIn("body\nno checkboxes here\n", content)
        finally:
            os.unlink(path)

    def test_no_change_when_already_completed_and_fully_ticked(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            original = (
                "---\nid: TASK-001\nstatus: completed\n---\n"
                "## Definition of Done\n- [x] one\n- [x] two\n"
            )
            f.write(original)
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertFalse(changed)
            self.assertEqual(path.read_text(), original)
        finally:
            os.unlink(path)

    def test_mixed_checkboxes_and_prose_only_ticks_checkboxes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(
                "---\nid: TASK-001\nstatus: pending\nother_field: keep-me\n---\n"
                "# Heading\nSome prose about brackets like [not a checkbox].\n"
                "- [ ] unticked\n- [x] already ticked\nMore prose.\n"
            )
            path = Path(f.name)
        try:
            changed = live.set_task_status_completed(path)
            self.assertTrue(changed)
            content = path.read_text()
            self.assertIn("status: completed", content)
            self.assertIn("other_field: keep-me", content)
            self.assertIn("# Heading", content)
            self.assertIn("Some prose about brackets like [not a checkbox].", content)
            self.assertIn("More prose.", content)
            self.assertIn("- [x] unticked", content)
            self.assertIn("- [x] already ticked", content)
            self.assertNotIn("- [ ]", content)
        finally:
            os.unlink(path)

    def test_non_checkbox_body_text_never_changed(self):
        """Regression guard: the checkbox-conversion step must never alter
        non-checkbox body text (narrowed, not removed, byte-for-byte
        constraint -- AC-CHG-006)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(
                "---\nid: TASK-001\nstatus: pending\n---\n"
                "Body prose that must survive unchanged.\n- [ ] a checkbox\n"
                "Trailing prose that must survive unchanged.\n"
            )
            path = Path(f.name)
        try:
            live.set_task_status_completed(path)
            content = path.read_text()
            self.assertIn("Body prose that must survive unchanged.\n", content)
            self.assertIn("Trailing prose that must survive unchanged.\n", content)
        finally:
            os.unlink(path)


class JournalPathForTests(unittest.TestCase):
    def test_journal_path_structure(self):
        repo = Path("/home/user/projects/my-app")
        p = live.journal_path_for(repo, "docs/specs/042-my-feature/")
        self.assertIn("my-app-worktrees", str(p))
        self.assertIn("042-my-feature", str(p))
        self.assertTrue(str(p).endswith(".json"))


class LiveSpawnLeanFlagsTests(unittest.TestCase):
    """LiveSpawn passes lean flags to every worker; reviewer also gets --append-system-prompt."""

    def _make_task(self):
        return {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}

    def _call_role(self, role: str):
        captured = {}
        fake_result = type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p", side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent="claude")
            spawn(role, self._make_task(), Path("/tmp/wt"))
        return captured.get("extra_args", [])

    def test_implement_gets_lean_flags(self):
        args = self._call_role("implement")
        self.assertNotIn("--bare", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("Read", args)
        self.assertIn("Bash", args)

    def test_implement_does_not_get_reviewer_prompt(self):
        args = self._call_role("implement")
        self.assertNotIn("--append-system-prompt", args)

    def test_fix_gets_lean_flags(self):
        args = self._call_role("fix")
        self.assertNotIn("--bare", args)
        self.assertNotIn("--append-system-prompt", args)

    def test_review_gets_lean_flags_and_system_prompt(self):
        args = self._call_role("review")
        self.assertNotIn("--bare", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--append-system-prompt", args)

    def test_lean_flags_are_not_mutated_between_calls(self):
        # list(_LEAN_WORKER_FLAGS) must not grow across invocations
        args1 = self._call_role("implement")
        args2 = self._call_role("implement")
        self.assertEqual(args1, args2)

    def test_every_role_excludes_user_setting_source(self):
        # Regression for investigation 20260711-130900: a user-level Stop hook
        # (fires on any worker that commits or writes a file -- i.e. every
        # role) was consuming a worker's final turn with an unrelated
        # "next-step suggestion" continuation, so the report-back JSON never
        # appeared in the parsed final message. --setting-sources project,local
        # drops the "user" source (and its hooks) from every worker spawn
        # while keeping this repo's own project-level hooks active.
        for role in ("implement", "review", "fix", "cleanup"):
            args = self._call_role(role)
            self.assertIn("--setting-sources", args, role)
            idx = args.index("--setting-sources")
            self.assertEqual(args[idx + 1], "project,local", role)


class LiveSpawnClaudeFallbackTests(unittest.TestCase):
    """brief 20260723-111700-claude-primary-fallback-inert: a claude-primary
    LiveSpawn must thread --fallback-agent through to spawn_claude_p, the same
    way a non-claude primary already threads it to spawn_agent."""

    def _make_task(self):
        return {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}

    def _call(self, agent="claude", fallback_agent=None):
        captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [],
                      "paused_s": 0.0, "session_id": ""}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent=agent, fallback_agent=fallback_agent
            )
            spawn("implement", self._make_task(), Path("/tmp/wt"))
        return captured

    def test_configured_fallback_agent_reaches_spawn_claude_p(self):
        captured = self._call(agent="claude", fallback_agent="codex")
        self.assertEqual(
            captured.get("fallback_agent"), "codex",
            "spawn_claude_p must receive --fallback-agent for a claude-primary run",
        )

    def test_no_fallback_configured_passes_none(self):
        captured = self._call(agent="claude", fallback_agent=None)
        self.assertIsNone(captured.get("fallback_agent"))

    def test_fallback_chain_reaches_spawn_claude_p(self):
        captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [],
                      "paused_s": 0.0, "session_id": ""}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="claude",
                fallback_chain=["codex", "opencode"],
            )
            spawn("implement", self._make_task(), Path("/tmp/wt"))
        self.assertEqual(captured.get("fallback_agent"), ["codex", "opencode"])


class RunResearchSessionExcludesUserSettingSourceTests(unittest.TestCase):
    """Regression for handoff 20260712-214530: run_research_session's
    --fork-research pre-load spawn calls spawnlib.spawn_agent directly,
    bypassing LiveSpawn.__call__ entirely, so it never got PR #252's
    --setting-sources fix. Lower severity than the task/group workers (its
    output isn't parsed for a report-back) but it still paid the operator's
    global CLAUDE.md/AGENTS.md cache-token tax PR #252 measured at ~65%."""

    def _captured_extra_args(self, agent="claude"):
        captured = {}
        fake_result = type("R", (), {"session_id": "sid-123"})()
        with tempfile.TemporaryDirectory() as t:
            spec_folder = Path(t) / "docs" / "specs" / "001-spec"
            spec_folder.mkdir(parents=True)
            with patch(
                "worktrail.orchestrator.live.spawnlib.spawn_agent",
                side_effect=lambda *_, **kw: captured.update(kw) or fake_result,
            ):
                live.run_research_session(spec_folder, agent=agent)
        return captured.get("extra_args", [])

    def test_claude_excludes_user_setting_source(self):
        args = self._captured_extra_args("claude")
        self.assertIn("--setting-sources", args)
        idx = args.index("--setting-sources")
        self.assertEqual(args[idx + 1], "project,local")

    def test_claude_gets_no_tools_restriction(self):
        # Read-only context pre-load, not a worker that edits/commits.
        args = self._captured_extra_args("claude")
        self.assertNotIn("--tools", args)

    def test_non_claude_gets_no_extra_args(self):
        args = self._captured_extra_args("codex")
        self.assertEqual(args, [])


class LiveSpawnCodexTests(unittest.TestCase):
    def test_codex_worker_skips_claude_only_flags(self):
        captured = {}
        fake_result = type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent="codex", model="gpt-5.3-codex")
            spawn("review", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        self.assertEqual(captured["agent"], "codex")
        self.assertEqual(captured["extra_args"], [])
        self.assertIsNone(captured["resume_session_id"])

    def test_default_agent_threads_fallback_to_spawnlib(self):
        captured = {}
        fake_result = type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="codex",
                model="gpt-5.4-mini", fallback_agent="opencode",
            )
            spawn("implement", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        self.assertEqual(captured["fallback_agent"], "opencode")


class LiveSpawnRoleAgentsTests(unittest.TestCase):
    """role_agents lets one role (e.g. review) run on a different headless CLI
    than the run's default `agent` -- e.g. an independent claude/sonnet reviewer
    while implement/fix stay on a cheaper opencode agent."""

    def _make_task(self):
        return {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}

    def _call(self, role: str, agent="opencode", role_agents=None, role_models=None):
        claude_captured = {}
        agent_captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                   side_effect=lambda *_, **kw: agent_captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec",
                agent=agent, role_agents=role_agents, role_models=role_models,
            )
            spawn(role, self._make_task(), Path("/tmp/wt"))
        return claude_captured, agent_captured

    def test_review_role_override_uses_claude_spawn_path(self):
        claude_captured, agent_captured = self._call(
            "review", agent="opencode", role_agents={"review": "claude"}
        )
        self.assertTrue(claude_captured, "spawn_claude_p should have been called for review")
        self.assertEqual(agent_captured, {}, "spawn_agent (opencode path) should not have been called")
        # Reviewer independence flag still applies on the overridden agent.
        self.assertIn("--append-system-prompt", claude_captured.get("extra_args", []))

    def test_review_role_override_resolves_claude_default_model_not_run_model(self):
        # No explicit role_models entry for "review" -- must fall back to
        # claude's OWN default model, not the run's opencode default model
        # (which would be an invalid model id for the claude CLI).
        claude_captured, _ = self._call("review", agent="opencode", role_agents={"review": "claude"})
        self.assertEqual(claude_captured.get("model"), spawnlib.default_model_for_agent("claude"))

    def test_review_role_override_honors_explicit_role_model(self):
        claude_captured, _ = self._call(
            "review", agent="opencode",
            role_agents={"review": "claude"}, role_models={"review": "opus"},
        )
        self.assertEqual(claude_captured.get("model"), "opus")

    def test_implement_role_stays_on_default_agent_when_only_review_overridden(self):
        claude_captured, agent_captured = self._call(
            "implement", agent="opencode", role_agents={"review": "claude"}
        )
        self.assertEqual(claude_captured, {}, "implement must not be routed to claude")
        self.assertEqual(agent_captured.get("agent"), "opencode")

    def test_no_role_agents_falls_back_to_run_agent_for_every_role(self):
        for role in ("implement", "review", "fix", "cleanup"):
            claude_captured, agent_captured = self._call(role, agent="opencode", role_agents=None)
            self.assertEqual(claude_captured, {}, role)
            self.assertEqual(agent_captured.get("agent"), "opencode", role)


class RoleAgentModelHelperTests(unittest.TestCase):
    """_role_agent_model is the shared per-role (agent, model) resolution used by
    both assembly-resolve spawn sites (pipeline scheduler and the non-pipeline
    finish_real call). Same semantics as LiveSpawn.__call__."""

    ROLE = "assembly-resolve"

    def test_no_maps_falls_back_to_primary_agent_and_model(self):
        self.assertEqual(
            live._role_agent_model(self.ROLE, "opencode", "oc-model", None, None),
            ("opencode", "oc-model"),
        )

    def test_mapped_agent_resolves_its_own_default_model(self):
        agent, model = live._role_agent_model(
            self.ROLE, "opencode", "oc-model", {self.ROLE: "claude"}, None
        )
        self.assertEqual(agent, "claude")
        self.assertEqual(model, spawnlib.default_model_for_agent("claude"))

    def test_explicit_role_model_wins(self):
        self.assertEqual(
            live._role_agent_model(
                self.ROLE, "opencode", "oc-model",
                {self.ROLE: "claude"}, {self.ROLE: "opus"},
            ),
            ("claude", "opus"),
        )

    def test_map_without_this_role_stays_on_primary(self):
        self.assertEqual(
            live._role_agent_model(
                self.ROLE, "opencode", "oc-model", {"review": "claude"}, None
            ),
            ("opencode", "oc-model"),
        )


class VerifierRoleSpawnsTests(unittest.TestCase):
    """_verifier_role_spawns builds the Verifier's (resolve, ci-fix) spawn pair
    honoring --role-agent-map / --model-map for the group-level verify roles;
    unmapped roles keep the pipeline path's historical defaults."""

    def _captured(self, role_agents=None, role_models=None,
                  agent="opencode", model="oc-model", timeout=1234):
        calls = []

        def fake_make_live_spawn(model_, timeout_=1800, agent="claude"):
            calls.append({"model": model_, "timeout": timeout_, "agent": agent})
            return lambda prompt, wt: ""

        with patch("worktrail.orchestrator.verify._make_live_spawn", side_effect=fake_make_live_spawn):
            live._verifier_role_spawns(agent, model, timeout, role_agents, role_models)
        self.assertEqual(len(calls), 2)
        return calls[0], calls[1]  # (resolve, ci-fix)

    def test_unmapped_keeps_historical_defaults(self):
        from worktrail.orchestrator import verify
        resolve, ci_fix = self._captured()
        self.assertEqual(resolve, {"model": "oc-model", "timeout": 1234, "agent": "opencode"})
        self.assertEqual(
            ci_fix,
            {"model": verify.DEFAULT_MODEL, "timeout": verify.CI_FIX_TIMEOUT, "agent": "opencode"},
        )

    def test_mapped_roles_route_to_override_agent_with_own_default_model(self):
        resolve, ci_fix = self._captured(
            role_agents={"resolve": "claude", "ci-fix": "claude"}
        )
        self.assertEqual(resolve["agent"], "claude")
        self.assertEqual(resolve["model"], spawnlib.default_model_for_agent("claude"))
        self.assertEqual(ci_fix["agent"], "claude")
        self.assertEqual(ci_fix["model"], spawnlib.default_model_for_agent("claude"))

    def test_explicit_role_models_win(self):
        resolve, ci_fix = self._captured(
            role_agents={"resolve": "claude"},
            role_models={"resolve": "opus", "ci-fix": "haiku"},
        )
        self.assertEqual(resolve, {"model": "opus", "timeout": 1234, "agent": "claude"})
        self.assertEqual(ci_fix["model"], "haiku")
        self.assertEqual(ci_fix["agent"], "opencode")

    def test_review_only_map_leaves_verify_roles_on_primary(self):
        resolve, ci_fix = self._captured(role_agents={"review": "claude"})
        self.assertEqual(resolve["agent"], "opencode")
        self.assertEqual(ci_fix["agent"], "opencode")


class FormatAutomergeEvidenceNoteTests(unittest.TestCase):
    """route:J automerge_evidence consumer audit: verify.run_all()'s
    automerge_evidence was computed and threaded through but never surfaced to
    the stdout stream that's the documented way of monitoring `full-real` runs
    (see docs/specs/research/go-policy-integrity-audit.md). This is the pure
    formatting helper both live.py call sites now print unconditionally."""

    def test_empty_evidence_yields_no_note(self):
        self.assertIsNone(live._format_automerge_evidence_note({}))

    def test_single_group_note_names_group_and_actor(self):
        note = live._format_automerge_evidence_note(
            {"feature-a": {"enabledBy": "app/github-actions", "mergedBy": "app/github-actions"}}
        )
        self.assertIn("1 group(s)", note)
        self.assertIn("feature-a", note)
        self.assertIn("enabledBy=app/github-actions", note)

    def test_multiple_groups_all_named(self):
        note = live._format_automerge_evidence_note({
            "a": {"enabledBy": "bot", "mergedBy": "bot"},
            "b": {"enabledBy": "bot", "mergedBy": "bot"},
        })
        self.assertIn("2 group(s)", note)
        self.assertIn("a (enabledBy=bot)", note)
        self.assertIn("b (enabledBy=bot)", note)


class LiveSpawnTierRoutingTests(unittest.TestCase):
    """TASK-007: LiveSpawn.__call__ resolves every implement/fix/cleanup spawn's
    (agent, model) via dispatch.agent_for's precedence (per-task override > role
    override > tier match > run default); judgment roles (review here --
    resolve/ci-fix/assembly-resolve never reach LiveSpawn.__call__) never
    consult task["agent"] or self.tier_map (AC-011..AC-015)."""

    TIER_MAP = {("complex", "backend"): {"agent_cli": "codex", "agent_model": None}}

    def _make_task(self, **overrides):
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        task.update(overrides)
        return task

    def _call(self, role, task, agent="claude", role_agents=None, role_models=None, tier_map=None):
        claude_captured = {}
        agent_captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                   side_effect=lambda *_, **kw: agent_captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec",
                agent=agent, role_agents=role_agents, role_models=role_models, tier_map=tier_map,
            )
            spawn(role, task, Path("/tmp/wt"))
        return spawn, claude_captured, agent_captured

    def test_tier_match_selects_tier_agent_for_implement(self):
        """AC-011: tier match, no per-task/role override -> tier's agent, and its
        OWN default model when the tier entry left the model unpinned."""
        task = self._make_task(complexity="complex", domain="backend")
        _, claude_captured, agent_captured = self._call(
            "implement", task, agent="claude", tier_map=self.TIER_MAP,
        )
        self.assertEqual(claude_captured, {}, "must not spawn via the claude path")
        self.assertEqual(agent_captured.get("agent"), "codex")
        self.assertEqual(agent_captured.get("model"), spawnlib.default_model_for_agent("codex"))

    def test_tier_match_honors_a_pinned_model(self):
        tier_map = {("complex", "backend"): {"agent_cli": "codex", "agent_model": "gpt-tier"}}
        task = self._make_task(complexity="complex", domain="backend")
        _, _, agent_captured = self._call("implement", task, agent="claude", tier_map=tier_map)
        self.assertEqual(agent_captured.get("model"), "gpt-tier")

    def test_no_tier_match_falls_through_to_run_default(self):
        task = self._make_task(complexity="trivial", domain="frontend")
        _, claude_captured, agent_captured = self._call(
            "implement", task, agent="claude", tier_map=self.TIER_MAP,
        )
        self.assertEqual(agent_captured, {})
        self.assertTrue(claude_captured)

    def test_per_task_override_outranks_tier_match(self):
        """AC-012."""
        task = self._make_task(complexity="complex", domain="backend", agent="opencode")
        _, claude_captured, agent_captured = self._call(
            "implement", task, agent="claude", tier_map=self.TIER_MAP,
        )
        self.assertEqual(claude_captured, {})
        self.assertEqual(agent_captured.get("agent"), "opencode")

    def test_role_override_outranks_tier_match(self):
        """AC-013."""
        task = self._make_task(complexity="complex", domain="backend")
        _, claude_captured, agent_captured = self._call(
            "implement", task, agent="claude",
            role_agents={"implement": "opencode"}, tier_map=self.TIER_MAP,
        )
        self.assertEqual(claude_captured, {})
        self.assertEqual(agent_captured.get("agent"), "opencode")

    def test_fix_and_cleanup_also_consult_tier_map(self):
        """Only review/resolve/ci-fix/assembly-resolve are judgment roles;
        implement/fix/cleanup all consult the tier map the same way."""
        for role in ("fix", "cleanup"):
            task = self._make_task(complexity="complex", domain="backend")
            _, claude_captured, agent_captured = self._call(
                role, task, agent="claude", tier_map=self.TIER_MAP,
            )
            self.assertEqual(claude_captured, {}, role)
            self.assertEqual(agent_captured.get("agent"), "codex", role)

    def test_review_ignores_tier_match_and_per_task_override(self):
        """AC-014: review stays on self.agent (its run default) even with a
        matching tier AND a per-task override both present."""
        task = self._make_task(complexity="complex", domain="backend", agent="opencode")
        _, claude_captured, agent_captured = self._call(
            "review", task, agent="claude", tier_map=self.TIER_MAP,
        )
        self.assertEqual(agent_captured, {})
        self.assertTrue(claude_captured, "review must still spawn via self.agent (claude)")

    def test_review_role_override_still_applies_over_tier(self):
        """A role_agents override for review IS still consulted (only the tier
        match and the per-task override are skipped for judgment roles)."""
        task = self._make_task(complexity="complex", domain="backend", agent="opencode")
        _, claude_captured, agent_captured = self._call(
            "review", task, agent="opencode",
            role_agents={"review": "claude"}, tier_map=self.TIER_MAP,
        )
        self.assertEqual(agent_captured, {})
        self.assertTrue(claude_captured)

    def test_missing_complexity_domain_keys_no_keyerror(self):
        """Edge case (REQ-013): a ScriptedSpawn-style task dict with no
        complexity/domain keys at all falls through to the run default,
        never raising KeyError."""
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        _, claude_captured, agent_captured = self._call(
            "implement", task, agent="claude", tier_map=self.TIER_MAP,
        )
        self.assertEqual(agent_captured, {})
        self.assertTrue(claude_captured)


class LiveSpawnEffortTests(unittest.TestCase):
    """model-tier-routing 3.3: LiveSpawn threads effort through the same
    dispatch.agent_for() resolution as agent/model, down to the spawn call."""

    def _make_task(self, **overrides):
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        task.update(overrides)
        return task

    def _call(self, role, task, agent="claude", effort=None, role_agents=None, tier_map=None):
        claude_captured = {}
        agent_captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                   side_effect=lambda *_, **kw: agent_captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec",
                agent=agent, effort=effort, role_agents=role_agents, tier_map=tier_map,
            )
            spawn(role, task, Path("/tmp/wt"))
        return claude_captured, agent_captured

    def test_run_default_effort_reaches_spawn_claude_p(self):
        task = self._make_task()
        claude_captured, _ = self._call("implement", task, agent="claude", effort="high")
        self.assertEqual(claude_captured.get("effort"), "high")

    def test_no_effort_configured_passes_none(self):
        task = self._make_task()
        claude_captured, _ = self._call("implement", task, agent="claude")
        self.assertIsNone(claude_captured.get("effort"))

    def test_tier_effort_reaches_spawn_agent(self):
        """A configured tier's effort (model-tier-routing 3.2's dispatch.agent_for
        resolved shape) reaches the spawned command."""
        tier_map = {
            ("complex", "backend"): {"agent_cli": "codex", "agent_model": None, "effort": "low"}
        }
        task = self._make_task(complexity="complex", domain="backend")
        _, agent_captured = self._call(
            "implement", task, agent="claude", tier_map=tier_map,
        )
        self.assertEqual(agent_captured.get("agent"), "codex")
        self.assertEqual(agent_captured.get("effort"), "low")

    def test_tier_effort_outranks_run_default(self):
        tier_map = {
            ("complex", "backend"): {"agent_cli": "codex", "agent_model": None, "effort": "low"}
        }
        task = self._make_task(complexity="complex", domain="backend")
        _, agent_captured = self._call(
            "implement", task, agent="claude", effort="high", tier_map=tier_map,
        )
        self.assertEqual(agent_captured.get("effort"), "low")

    def test_role_override_effort_reaches_spawn_agent(self):
        role_agents = {"implement": {"agent_cli": "opencode", "agent_model": None, "effort": "medium"}}
        task = self._make_task()
        _, agent_captured = self._call(
            "implement", task, agent="claude", role_agents=role_agents,
        )
        self.assertEqual(agent_captured.get("agent"), "opencode")
        self.assertEqual(agent_captured.get("effort"), "medium")

    def test_run_default_effort_not_carried_to_a_different_pinned_agent(self):
        """No default_effort_for_agent() equivalent exists (unlike model): a
        role/tier pinned to a different agent than the run default only gets
        an effort when the resolution itself carried one."""
        tier_map = {("complex", "backend"): {"agent_cli": "codex", "agent_model": None}}
        task = self._make_task(complexity="complex", domain="backend")
        _, agent_captured = self._call(
            "implement", task, agent="claude", effort="high", tier_map=tier_map,
        )
        self.assertEqual(agent_captured.get("agent"), "codex")
        self.assertIsNone(agent_captured.get("effort"))


class LiveSpawnPreSpecParityTests(unittest.TestCase):
    """AC-016/REQ-016/REQ-019: with no tier_map/fallback_chain configured, every
    role's resolved agent is exactly `self.role_agents.get(role, self.agent)` --
    the pre-spec truth table -- so an existing cassette/golden run (built before
    TASK-007) still dispatches identically."""

    def _make_task(self):
        return {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}

    def _resolved(self, role, agent, role_agents):
        claude_captured = {}
        agent_captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                   side_effect=lambda *_, **kw: agent_captured.update(kw) or fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent=agent, role_agents=role_agents)
            spawn(role, self._make_task(), Path("/tmp/wt"))
        if claude_captured:
            return "claude"
        return agent_captured.get("agent")

    def test_parity_formula_for_every_role(self):
        role_agents = {"review": "claude"}
        for role in ("implement", "review", "fix", "cleanup"):
            expected = role_agents.get(role, "opencode")
            self.assertEqual(self._resolved(role, "opencode", role_agents), expected, role)

    def test_parity_formula_with_no_role_agents_at_all(self):
        for role in ("implement", "review", "fix", "cleanup"):
            self.assertEqual(self._resolved(role, "codex", None), "codex", role)


class LiveSpawnFallbackChainTests(unittest.TestCase):
    """AC-017/REQ-018: a configured ordered fallback chain reaches spawn_agent
    intact; with no chain configured, the legacy single --fallback-agent
    behavior is unchanged."""

    def _call(self, agent="opencode", fallback_agent=None, fallback_chain=None):
        captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                   side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent=agent,
                fallback_agent=fallback_agent, fallback_chain=fallback_chain,
            )
            spawn("implement", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        return captured

    def test_configured_three_entry_chain_reaches_spawn_agent_intact(self):
        chain = ["codex", "opencode-2", "claude"]
        captured = self._call(agent="opencode", fallback_chain=chain)
        self.assertEqual(captured.get("fallback_agent"), chain)

    def test_no_chain_falls_back_to_legacy_single_fallback_agent(self):
        captured = self._call(agent="opencode", fallback_agent="codex", fallback_chain=None)
        self.assertEqual(captured.get("fallback_agent"), "codex")

    def test_chain_wins_over_legacy_single_fallback_when_both_configured(self):
        captured = self._call(agent="opencode", fallback_agent="claude", fallback_chain=["codex"])
        self.assertEqual(captured.get("fallback_agent"), ["codex"])

    def test_no_fallback_at_all_passes_none(self):
        captured = self._call(agent="opencode", fallback_agent=None, fallback_chain=None)
        self.assertIsNone(captured.get("fallback_agent"))

    def test_role_overridden_non_judgment_agent_still_gets_fallback(self):
        """A role/tier override on a non-judgment role (implement/fix/cleanup)
        away from self.agent still gets the run's configured fallback chain --
        a pinned tier/role model going unavailable must not leave the task with
        zero automatic recovery (brief 20260805-144349)."""
        captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                   side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="claude",
                role_agents={"implement": "opencode"}, fallback_chain=["codex"],
            )
            spawn("implement", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        self.assertEqual(captured.get("fallback_agent"), ["codex"])

    def test_tier_overridden_fix_and_cleanup_also_get_fallback(self):
        """Same as implement above, for fix/cleanup -- all non-judgment roles
        share the extended gate."""
        tier_map = {("complex", "backend"): {"agent_cli": "opencode", "agent_model": None}}
        for role in ("fix", "cleanup"):
            captured = {}
            fake_result = type(
                "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
            )()
            with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
                 patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                       side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
                spawn = live.LiveSpawn(
                    "spec-001", "docs/specs/001-spec", agent="claude",
                    tier_map=tier_map, fallback_chain=["codex"],
                )
                spawn(
                    role,
                    {
                        "id": "TASK-001", "status": "pending", "files": ["src/foo.py"],
                        "complexity": "complex", "domain": "backend",
                    },
                    Path("/tmp/wt"),
                )
            self.assertEqual(captured.get("fallback_agent"), ["codex"], role)

    def test_role_overridden_judgment_agent_gets_no_fallback(self):
        """A judgment role (review here -- resolve/ci-fix/assembly-resolve never
        reach LiveSpawn.__call__) pinned to a different agent than self.agent
        keeps the old no-fallback gating: a silent fallback must never erode
        the independent-reviewer guarantee (13.3, DEC-003)."""
        claude_captured = {}
        agent_captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                   side_effect=lambda *_, **kw: agent_captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="opencode",
                role_agents={"review": "claude"}, fallback_chain=["codex"],
            )
            spawn("review", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        self.assertEqual(agent_captured, {}, "review must spawn via the claude path")
        self.assertIsNone(claude_captured.get("fallback_agent"))

    def test_review_on_run_default_agent_still_gets_fallback(self):
        """A judgment role NOT pinned away from self.agent (the common case --
        no role_agents override for review) is unaffected: fallback still
        applies exactly as it did pre-spec."""
        claude_captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                   side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="claude", fallback_chain=["codex"],
            )
            spawn("review", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        self.assertEqual(claude_captured.get("fallback_agent"), ["codex"])


class LiveSpawnServingAgentLabelTests(unittest.TestCase):
    """AC-026/REQ-027: self.last_agent (read by the journal-entry write sites in
    _commit_step) reflects the actual capacity-gated serving agent, including
    when the primary hop is gated and a configured fallback hop serves instead."""

    def _call(self, agent="opencode", fallback_chain=None, gated_agents=()):
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()

        def fake_check(cand_agent, cand_model, *a, **kw):
            if cand_agent in gated_agents:
                raise agent_capacity.ProviderUnavailable(f"{cand_agent}:{cand_model}", {})

        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", return_value=fake_result), \
             patch("worktrail.orchestrator.live.agent_capacity.check", side_effect=fake_check):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent=agent, fallback_chain=fallback_chain,
            )
            spawn("implement", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        return spawn

    def test_no_gate_serving_agent_is_the_primary(self):
        spawn = self._call(agent="opencode", fallback_chain=["codex"], gated_agents=())
        self.assertEqual(spawn.last_agent, "opencode")

    def test_primary_gated_serving_agent_is_the_fallback_hop(self):
        spawn = self._call(agent="opencode", fallback_chain=["codex"], gated_agents=("opencode",))
        self.assertEqual(spawn.last_agent, "codex")

    def test_no_fallback_configured_label_stays_the_primary_even_if_gated(self):
        # No chain configured -- spawn_agent's own internal check is authoritative
        # here (this is a best-effort label, not a re-implementation of gating).
        spawn = self._call(agent="opencode", fallback_chain=None, gated_agents=("opencode",))
        self.assertEqual(spawn.last_agent, "opencode")

    def test_tier_resolved_primary_gated_serving_agent_is_fallback_hop(self):
        """Cost-visibility (brief 20260805-144349): a cheap tier-pinned agent
        that falls back to a more expensive configured hop must show up as
        such in the journal's `agent` label (last_agent), not silently stay
        labeled as the (never-actually-serving) tier-pinned agent -- this is
        the existing last_agent/_serving_agent_guess machinery, now reachable
        for tier-resolved spawns since the fallback gate covers them too."""
        tier_map = {("cheap", "trivia"): {"agent_cli": "opencode", "agent_model": None}}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()

        def fake_check(cand_agent, cand_model, *_a, **_kw):
            if cand_agent == "opencode":
                raise agent_capacity.ProviderUnavailable("opencode:x", {})

        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", return_value=fake_result), \
             patch("worktrail.orchestrator.live.agent_capacity.check", side_effect=fake_check):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="claude",
                tier_map=tier_map, fallback_chain=["codex"],
            )
            spawn(
                "implement",
                {
                    "id": "TASK-001", "status": "pending", "files": ["src/foo.py"],
                    "complexity": "cheap", "domain": "trivia",
                },
                Path("/tmp/wt"),
            )
        self.assertEqual(spawn.last_agent, "codex")

    def test_serving_agent_guess_never_raises_on_capacity_error(self):
        """Best-effort: an unexpected error reading the capacity cache must not
        propagate out of __call__ -- last_agent just stays the primary."""
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", return_value=fake_result), \
             patch("worktrail.orchestrator.live.agent_capacity.check", side_effect=RuntimeError("cache corrupted")):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="opencode", fallback_chain=["codex"],
            )
            result = spawn(
                "implement", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt")
            )
        self.assertIs(result, fake_result)
        self.assertEqual(spawn.last_agent, "opencode")


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


class ResumeQuarantineStalenessWarningTests(unittest.TestCase):
    """journal-resume-staleness-warning 1.3: on resume, a QUARANTINED group
    whose task branch has fallen behind `base` gets a loud, per-group warning
    naming the group and the drift count, recommending --fresh."""

    def _repo_with_task_branch(self, tmp: Path, branch: str, drift_commits: int) -> Path:
        repo = tmp / "repo"
        if not repo.exists():
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            _run_git(repo, "config", "user.email", "t@t")
            _run_git(repo, "config", "user.name", "T")
            (repo / "README.md").write_text("init\n")
            _run_git(repo, "add", "-A")
            _run_git(repo, "commit", "-q", "-m", "init")
            _run_git(repo, "branch", "-M", "main")

        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "checkout", "-q", "-b", branch)
        (repo / f"{branch.replace('/', '-')}.txt").write_text("work\n")
        _run_git(repo, "add", "-A")
        _run_git(repo, "commit", "-q", "-m", f"{branch} work")
        _run_git(repo, "checkout", "-q", "main")

        for i in range(drift_commits):
            (repo / f"drift-{branch.replace('/', '-')}-{i}.txt").write_text(f"{i}\n")
            _run_git(repo, "add", "-A")
            _run_git(repo, "commit", "-q", "-m", f"base moved on {branch} {i}")
        return repo

    def test_warns_naming_group_and_drift_count(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = self._repo_with_task_branch(tmp, "001-x/task-001", drift_commits=4)
            groups = [{"name": "group-a", "tasks": ["TASK-001"]}]
            groups_journal = {"group-a": {"state": "QUARANTINED"}}

            out = io.StringIO()
            with redirect_stdout(out):
                live._resume_quarantine_staleness_warning(repo, "main", "001-x", groups, groups_journal)

            output = out.getvalue()
            self.assertIn("PIPELINE RESUME WARNING", output)
            self.assertIn("group-a", output)
            self.assertIn("QUARANTINED", output)
            self.assertIn("4 commit(s)", output)
            self.assertIn("--fresh", output)

    def test_silent_when_quarantined_group_has_zero_drift(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = self._repo_with_task_branch(tmp, "001-x/task-001", drift_commits=0)
            groups = [{"name": "group-a", "tasks": ["TASK-001"]}]
            groups_journal = {"group-a": {"state": "QUARANTINED"}}

            out = io.StringIO()
            with redirect_stdout(out):
                live._resume_quarantine_staleness_warning(repo, "main", "001-x", groups, groups_journal)

            self.assertEqual(out.getvalue(), "")

    def test_silent_when_no_quarantined_groups(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = self._repo_with_task_branch(tmp, "001-x/task-001", drift_commits=4)
            groups = [{"name": "group-a", "tasks": ["TASK-001"]}]
            groups_journal = {"group-a": {"state": "MERGED"}}

            out = io.StringIO()
            with redirect_stdout(out):
                live._resume_quarantine_staleness_warning(repo, "main", "001-x", groups, groups_journal)
                # _resume_drift_report's own output must be unaffected by this call.
                live._resume_drift_report(repo, "main", "001-x", [{"id": "TASK-001"}])

            output = out.getvalue()
            self.assertNotIn("PIPELINE RESUME WARNING", output)
            self.assertIn("PIPELINE RESUME: base 'main' is 4 commit(s) ahead", output)

    def test_silent_and_no_raise_when_task_branch_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = tmp / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            _run_git(repo, "config", "user.email", "t@t")
            _run_git(repo, "config", "user.name", "T")
            (repo / "README.md").write_text("init\n")
            _run_git(repo, "add", "-A")
            _run_git(repo, "commit", "-q", "-m", "init")
            _run_git(repo, "branch", "-M", "main")

            groups = [{"name": "group-a", "tasks": ["TASK-001"]}]
            groups_journal = {"group-a": {"state": "QUARANTINED"}}

            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    live._resume_quarantine_staleness_warning(repo, "main", "001-x", groups, groups_journal)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"must never raise when a quarantined group's task branch is missing; got {exc!r}")
            self.assertEqual(out.getvalue(), "")

    def test_uses_max_drift_across_multiple_group_task_branches(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = self._repo_with_task_branch(tmp, "001-x/task-001", drift_commits=2)
            self._repo_with_task_branch(tmp, "001-x/task-002", drift_commits=5)
            groups = [{"name": "group-a", "tasks": ["TASK-001", "TASK-002"]}]
            groups_journal = {"group-a": {"state": "QUARANTINED"}}

            out = io.StringIO()
            with redirect_stdout(out):
                live._resume_quarantine_staleness_warning(repo, "main", "001-x", groups, groups_journal)

            self.assertIn("7 commit(s)", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
