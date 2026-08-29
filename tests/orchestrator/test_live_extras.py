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
        # conftest.py seeds codex-sub (harness=codex) at t2-build; agent="codex"
        # with no roles.review/role_agents override falls back to self.agent's
        # own declared target (LiveSpawn.__call__'s _target_for_harness).
        captured = {}
        fake_result = type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent="codex")
            spawn("review", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        self.assertEqual(captured["prefer"], "codex-sub")
        self.assertEqual(captured["extra_args"], [])
        self.assertIsNone(captured["resume_session_id"])


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
        # No explicit role_models entry for "review" -- must resolve claude-sub's
        # OWN configured tier cell (via tier/prefer), not carry over the run's
        # opencode default in any way. role_agents translates the harness name
        # to its declared target (LiveSpawn.__call__'s _target_for_harness).
        claude_captured, _ = self._call("review", agent="opencode", role_agents={"review": "claude"})
        self.assertEqual(claude_captured.get("prefer"), "claude-sub")

    def test_review_role_override_honors_explicit_role_model(self):
        # role_models forces the explicit-cell-override path: the model reaches
        # the (mocked-out) spawn_claude_p via a swapped WORKTRAIL_ROUTING_FILE,
        # not a kwarg -- read the swapped file's content to verify it, the same
        # way spawn_claude_p's own real implementation would.
        written = {}

        def _capture(*_a, **_kw):
            path = os.environ.get("WORKTRAIL_ROUTING_FILE")
            written["text"] = Path(path).read_text(encoding="utf-8") if path else None
            return type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()

        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p", side_effect=_capture):
            spawn = live.LiveSpawn(
                "spec-001", "docs/specs/001-spec", agent="opencode",
                role_agents={"review": "claude"}, role_models={"review": "opus"},
            )
            spawn("review", self._make_task(), Path("/tmp/wt"))
        self.assertIsNotNone(written["text"])
        self.assertIn("model: opus", written["text"])
        self.assertIn("claude-sub:", written["text"])

    def test_implement_role_stays_on_default_agent_when_only_review_overridden(self):
        claude_captured, agent_captured = self._call(
            "implement", agent="opencode", role_agents={"review": "claude"}
        )
        self.assertEqual(claude_captured, {}, "implement must not be routed to claude")
        self.assertEqual(agent_captured.get("prefer"), "opencode-free")

    def test_no_role_agents_falls_back_to_run_agent_for_every_role(self):
        for role in ("implement", "review", "fix", "cleanup"):
            claude_captured, agent_captured = self._call(role, agent="opencode", role_agents=None)
            self.assertEqual(claude_captured, {}, role)
            self.assertEqual(agent_captured.get("prefer"), "opencode-free", role)


class RoleAgentModelHelperTests(unittest.TestCase):
    """_role_agent_model is the shared per-role (agent, model) resolution used by
    the pipeline scheduler's assembly-resolve spawn site. Same semantics as
    LiveSpawn.__call__."""

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
        self.assertEqual(model, live._default_model_for_agent("claude"))

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
        self.assertEqual(resolve["model"], live._default_model_for_agent("claude"))
        self.assertEqual(ci_fix["agent"], "claude")
        self.assertEqual(ci_fix["model"], live._default_model_for_agent("claude"))

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
    """routing-target-selector task 4.1/4.2: LiveSpawn.__call__ resolves every
    implement/fix/cleanup spawn's tier via dispatch.tier_for()'s precedence
    (explicit task tier > purpose > complexity > default_tier); judgment
    roles (review here -- resolve/ci-fix/assembly-resolve never reach
    LiveSpawn.__call__) never consult task["tier"]/purpose/complexity."""

    ROUTING = (
        "targets:\n"
        "  claude-sub: {harness: claude, pool: subscription}\n"
        "  codex-sub: {harness: codex, pool: subscription}\n"
        "tiers:\n"
        "  complex:\n"
        "    codex-sub: {model: codex-complex-model}\n"
        "  trivial:\n"
        "    claude-sub: {model: claude-trivial-model}\n"
        "default_tier: trivial\n"
    )

    def _make_task(self, **overrides):
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        task.update(overrides)
        return task

    def _call(self, role, task, agent="claude", role_agents=None, role_models=None, purposes=None):
        claude_captured = {}
        agent_captured = {}
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with tempfile.TemporaryDirectory() as tmp:
            routing_file = Path(tmp) / "routing.yaml"
            routing_file.write_text(self.ROUTING, encoding="utf-8")
            with patch.dict(os.environ, {"GO_ROUTING_FILE": str(routing_file)}), \
                 patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
                 patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p",
                       side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result), \
                 patch("worktrail.orchestrator.live.spawnlib.spawn_agent",
                       side_effect=lambda *_, **kw: agent_captured.update(kw) or fake_result):
                spawn = live.LiveSpawn(
                    "spec-001", "docs/specs/001-spec",
                    agent=agent, role_agents=role_agents, role_models=role_models,
                    purpose_tier_map=purposes,
                )
                spawn(role, task, Path("/tmp/wt"))
        return spawn, claude_captured, agent_captured

    def test_complexity_selects_tier_row_for_implement(self):
        task = self._make_task(complexity="complex")
        _, claude_captured, agent_captured = self._call("implement", task, agent="claude")
        self.assertEqual(claude_captured, {}, "must not spawn via the claude path")
        self.assertEqual(agent_captured.get("tier"), "complex")

    def test_no_complexity_falls_through_to_default_tier(self):
        task = self._make_task()
        _, claude_captured, agent_captured = self._call("implement", task, agent="claude")
        self.assertEqual(agent_captured, {})
        self.assertEqual(claude_captured.get("tier"), "trivial")

    def test_unmatched_complexity_falls_through_to_default_tier(self):
        """A complexity value naming no declared routing.tiers row (here
        "medium"; ROUTING declares only complex/trivial) falls through to
        default_tier instead of reaching select_cell() as a nonexistent row
        and crashing the spawn with NoExecutionTarget -- confirmed live
        2026-08-28 (t1-t4 rows only + `complexity: medium`, no purpose)."""
        task = self._make_task(complexity="medium")
        _, claude_captured, agent_captured = self._call("implement", task, agent="claude")
        self.assertEqual(agent_captured, {})
        self.assertEqual(claude_captured.get("tier"), "trivial")

    def test_explicit_task_tier_outranks_complexity(self):
        task = self._make_task(complexity="complex", tier="trivial")
        _, claude_captured, agent_captured = self._call("implement", task, agent="claude")
        self.assertEqual(agent_captured, {})
        self.assertEqual(claude_captured.get("tier"), "trivial")

    def test_purpose_outranks_complexity(self):
        task = self._make_task(complexity="trivial", purpose="scaffolding")
        _, claude_captured, agent_captured = self._call(
            "implement", task, agent="claude", purposes={"scaffolding": "complex"},
        )
        self.assertEqual(claude_captured, {})
        self.assertEqual(agent_captured.get("tier"), "complex")

    def test_fix_and_cleanup_also_consult_complexity(self):
        """Only review/resolve/ci-fix/assembly-resolve are judgment roles;
        implement/fix/cleanup all consult complexity the same way."""
        for role in ("fix", "cleanup"):
            task = self._make_task(complexity="complex")
            _, claude_captured, agent_captured = self._call(role, task, agent="claude")
            self.assertEqual(claude_captured, {}, role)
            self.assertEqual(agent_captured.get("tier"), "complex", role)

    def test_review_ignores_complexity_and_purpose(self):
        """review (judgment role) never consults task purpose/complexity --
        only routing.roles.review, else its own review-specific default
        (t1-deep when declared, else default_tier; ROUTING declares neither
        routing.roles.review nor a t1-deep row, so this falls to trivial)."""
        task = self._make_task(complexity="complex", purpose="scaffolding")
        _, claude_captured, agent_captured = self._call(
            "review", task, agent="claude", purposes={"scaffolding": "complex"},
        )
        self.assertEqual(agent_captured, {})
        self.assertEqual(claude_captured.get("tier"), "trivial")

    def test_missing_complexity_no_keyerror(self):
        """Edge case: a ScriptedSpawn-style task dict with no complexity key
        at all falls through to default_tier, never raising KeyError."""
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        _, claude_captured, agent_captured = self._call("implement", task, agent="claude")
        self.assertEqual(agent_captured, {})
        self.assertEqual(claude_captured.get("tier"), "trivial")


class LiveSpawnEffortTests(unittest.TestCase):
    """LiveSpawn.__call__'s own effort responsibility is now narrow: a tier
    cell's own declared effort is resolved entirely inside spawn_agent/
    spawn_claude_p (opaque to LiveSpawn, covered by spawnlib's own test
    suite) -- the only effort behavior LiveSpawn itself owns is the explicit
    `--effort`/`--model-map` override path, which threads its value through
    `spawnlib.explicit_cell_override()`'s throwaway routing file, not a kwarg."""

    def _make_task(self, **overrides):
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        task.update(overrides)
        return task

    def _call_capturing_routing_file(self, role, task, **kwargs):
        written = {}

        def _capture(*_a, **_kw):
            path = os.environ.get("WORKTRAIL_ROUTING_FILE")
            written["text"] = Path(path).read_text(encoding="utf-8") if path else None
            return type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()

        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p", side_effect=_capture), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", side_effect=_capture):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", **kwargs)
            spawn(role, task, Path("/tmp/wt"))
        return written.get("text")

    def test_run_default_effort_triggers_explicit_override(self):
        text = self._call_capturing_routing_file(
            "implement", self._make_task(), agent="claude", effort="high",
        )
        self.assertIsNotNone(text, "an effort override must route through explicit_cell_override")
        self.assertIn("effort: high", text)

    def test_no_effort_or_model_configured_skips_the_override_path(self):
        text = self._call_capturing_routing_file("implement", self._make_task(), agent="claude")
        self.assertIsNone(text, "no override configured -- no routing file should be swapped in")

    def test_role_model_override_triggers_explicit_override(self):
        text = self._call_capturing_routing_file(
            "implement", self._make_task(), agent="claude",
            role_models={"implement": "opus"},
        )
        self.assertIn("model: opus", text)

    def test_run_default_effort_not_applied_to_judgment_roles(self):
        """Unlike role_models (still consulted for review), the RUN-level
        --effort default is a per-task-worker concept and does not apply to
        judgment roles (review here -- resolve/ci-fix/assembly-resolve never
        reach LiveSpawn.__call__) -- no override configured means no swap."""
        text = self._call_capturing_routing_file(
            "review", self._make_task(), agent="claude", effort="high",
        )
        self.assertIsNone(text)

    def test_review_role_model_override_still_applies(self):
        """role_models has no JUDGMENT_ROLES restriction -- an explicit
        --model-map review=... override still works."""
        text = self._call_capturing_routing_file(
            "review", self._make_task(), agent="claude",
            role_models={"review": "opus"},
        )
        self.assertIn("model: opus", text)


class LiveSpawnPreSpecParityTests(unittest.TestCase):
    """With no role_agents/role_models/purpose_tier_map configured, every
    role's resolved target is exactly the run's own --agent, translated to
    its declared routing.targets entry -- the pre-spec parity every
    construction site that never configures roles/role_agents depends on."""

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
            return claude_captured.get("prefer")
        return agent_captured.get("prefer")

    def test_parity_formula_for_every_role(self):
        # conftest.py seeds opencode-free/claude-sub; role_agents overrides
        # review to claude, everything else stays on the run's own agent.
        role_agents = {"review": "claude"}
        for role in ("implement", "review", "fix", "cleanup"):
            expected = "claude-sub" if role == "review" else "opencode-free"
            self.assertEqual(self._resolved(role, "opencode", role_agents), expected, role)

    def test_parity_formula_with_no_role_agents_at_all(self):
        for role in ("implement", "review", "fix", "cleanup"):
            self.assertEqual(self._resolved(role, "codex", None), "codex-sub", role)


class LiveSpawnServedTargetCorrectionTests(unittest.TestCase):
    """last_agent (read by the journal-entry write sites in _commit_step)
    reflects the actually-served HARNESS -- SpawnResult.served_harness, task
    4.2's "record the served cell" -- correcting the pre-call peek label when
    spawn_agent's own internal same-row re-selection (task 3.4) hops to a
    different cell than the one peeked."""

    def _call(self, served_harness):
        fake_result = type(
            "R", (), {
                "text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0,
                "served_target": "whatever", "served_harness": served_harness,
            },
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", return_value=fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p", return_value=fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent="opencode")
            spawn("implement", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt"))
        return spawn

    def test_served_harness_corrects_the_peeked_label_after_a_hop(self):
        spawn = self._call(served_harness="codex")
        self.assertEqual(spawn.last_agent, "codex")

    def test_served_harness_matching_the_peek_is_a_no_op(self):
        spawn = self._call(served_harness="opencode")
        self.assertEqual(spawn.last_agent, "opencode")

    def test_a_result_double_with_no_served_harness_field_keeps_the_peeked_label(self):
        """A caller/test double patching spawn_agent out with its own minimal
        fake result object (no served_harness attribute) must not crash --
        the peeked label from before the call is kept as-is."""
        fake_result = type(
            "R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0}
        )()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", return_value=fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p", return_value=fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent="opencode")
            result = spawn(
                "implement", {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}, Path("/tmp/wt")
            )
        self.assertIs(result, fake_result)
        self.assertEqual(spawn.last_agent, "opencode")


class LiveSpawnDispatchIdTests(unittest.TestCase):
    """LiveSpawn threads dispatch_id into spawn_agent/spawn_claude_p calls."""

    def _make_task(self):
        return {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}

    def _call_opencode(self, dispatch_id=None):
        claude_captured = {}
        agent_captured = {}
        fake_result = type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p", side_effect=lambda *_, **kw: claude_captured.update(kw) or fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", side_effect=lambda *_, **kw: agent_captured.update(kw) or fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent="opencode", dispatch_id=dispatch_id)
            spawn("implement", self._make_task(), Path("/tmp/wt"))
        return claude_captured, agent_captured

    def test_dispatch_id_threaded_into_spawn_agent_call(self):
        claude_captured, agent_captured = self._call_opencode(dispatch_id="go-abc123")
        self.assertEqual(claude_captured, {}, "spawn_claude_p should not be called with opencode agent")
        self.assertEqual(agent_captured.get("dispatch_id"), "go-abc123", "dispatch_id must be passed to spawn_agent")

    def test_dispatch_id_absent_when_omitted(self):
        claude_captured, agent_captured = self._call_opencode(dispatch_id=None)
        self.assertEqual(claude_captured, {}, "spawn_claude_p should not be called with opencode agent")
        self.assertIsNone(agent_captured.get("dispatch_id"), "dispatch_id must be absent when not provided")

    def _call_claude(self, dispatch_id=None):
        captured = {}
        fake_result = type("R", (), {"text": "ok", "usage": {}, "tools_used": [], "skills_used": [], "paused_s": 0.0})()
        with patch("worktrail.orchestrator.live.dispatch.build_worker_prompt", return_value="prompt"), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_claude_p", side_effect=lambda *_, **kw: captured.update(kw) or fake_result), \
             patch("worktrail.orchestrator.live.spawnlib.spawn_agent", side_effect=lambda *_, **kw: captured.update(kw) or fake_result):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", agent="claude", dispatch_id=dispatch_id)
            spawn("implement", self._make_task(), Path("/tmp/wt"))
        return captured

    def test_dispatch_id_threaded_into_spawn_call(self):
        captured = self._call_claude(dispatch_id="go-abc123")
        self.assertEqual(captured.get("dispatch_id"), "go-abc123")

    def test_dispatch_id_absent_when_omitted_claude(self):
        captured = self._call_claude(dispatch_id=None)
        self.assertIsNone(captured.get("dispatch_id"))


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


class FullRealDispatchIdArgparseTests(unittest.TestCase):
    """full-real --dispatch-id wiring: passing --dispatch-id go-abc123 results
    in full_real() being called with dispatch_id="go-abc123"."""

    def _main(self, *extra):
        argv = [
            "full-real",
            "--repo", "/fake/repo",
            "--spec", "docs/specs/001-foo",
        ] + list(extra)
        with patch.object(live, "full_real", return_value={}) as mock_fr:
            rc = live.main(argv)
        return rc, mock_fr

    def test_dispatch_id_passed_through_to_full_real(self):
        rc, mock_fr = self._main("--dispatch-id", "go-abc123")
        self.assertEqual(rc, 0)
        mock_fr.assert_called_once()
        # Verify dispatch_id kwarg was passed to full_real
        call_kwargs = mock_fr.call_args.kwargs
        self.assertEqual(call_kwargs.get("dispatch_id"), "go-abc123")

    def test_dispatch_id_absent_when_not_specified(self):
        rc, mock_fr = self._main()
        self.assertEqual(rc, 0)
        mock_fr.assert_called_once()
        # Verify dispatch_id is None when not provided
        call_kwargs = mock_fr.call_args.kwargs
        self.assertIsNone(call_kwargs.get("dispatch_id"))


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
