#!/usr/bin/env python3
"""Tests for the live.py resilience/speed helpers:

  - _review_exempt (#16)             review fast-path opt-in
  - _parse_model_map (#20)           per-role model overrides
  - RunLock (#4)                     single-owner run lock
  - set_task_status_completed (#14)  surgical frontmatter write-back
  - add_stacked_worktree (#7)        idempotent on a pre-existing branch
  - salvage_report (#11)             recover an implement/fix commit on bad report
  - cleanup_task_in_python (#14)     deterministic cleanup, no spawn

Git-touching tests build a throwaway repo under a temp dir. Run:
    python3 scripts/test_resilience_helpers.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402
from worktrail.orchestrator import integrate  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(root: Path, task_md: str | None = None) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x\n")
    if task_md is not None:
        td = repo / "docs" / "specs" / "001-x" / "tasks"
        td.mkdir(parents=True)
        (td / "TASK-001.md").write_text(task_md)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _write_sibling_task_file(repo, sibling_spec_id, sibling_task_id, status="completed"):
    """Materialize a sibling spec's task file so `resolve_external_dependency`
    reports the reference as satisfied (read-only filesystem check, no git)."""
    td = Path(repo) / "docs" / "specs" / sibling_spec_id / "tasks"
    td.mkdir(parents=True, exist_ok=True)
    (td / f"{sibling_task_id}.md").write_text(
        f"---\nid: {sibling_task_id}\nstatus: {status}\n---\nbody\n"
    )


class FrontierFileNormalize(unittest.TestCase):
    """#8: collision detection compares NORMALISED paths so the same file spelled
    two ways doesn't slip two tasks into one parallel batch."""

    def test_differently_spelled_same_file_collides(self):
        tasks = [
            {
                "id": "TASK-001",
                "status": "pending",
                "deps": [],
                "files": ["src/a.ts"],
                "kind": "impl",
            },
            {
                "id": "TASK-002",
                "status": "pending",
                "deps": [],
                "files": ["./src/a.ts"],
                "kind": "impl",
            },
        ]
        front = coordinator.runnable_frontier(tasks, max_workers=4)
        self.assertEqual(len(front), 1)  # same file -> only one may run this tick

    def test_disjoint_files_both_run(self):
        tasks = [
            {
                "id": "TASK-001",
                "status": "pending",
                "deps": [],
                "files": ["src/a.ts"],
                "kind": "impl",
            },
            {
                "id": "TASK-002",
                "status": "pending",
                "deps": [],
                "files": ["src/b.ts"],
                "kind": "impl",
            },
        ]
        front = coordinator.runnable_frontier(tasks, max_workers=4)
        self.assertEqual(len(front), 2)


class ReviewExempt(unittest.TestCase):
    def test_opt_out_values(self):
        for v in ("skip", "false", "no", "none", "off", "SKIP"):
            self.assertTrue(live._review_exempt({"review": v}), v)

    def test_docs_kind_exempt(self):
        self.assertTrue(live._review_exempt({"kind": "docs"}))

    def test_default_is_reviewed(self):
        self.assertFalse(live._review_exempt({}))
        self.assertFalse(live._review_exempt({"review": "yes", "kind": "impl"}))


class ParseModelMap(unittest.TestCase):
    def test_parses_pairs(self):
        self.assertEqual(
            live._parse_model_map("implement=sonnet,review=haiku"),
            {"implement": "sonnet", "review": "haiku"},
        )

    def test_empty_is_none(self):
        self.assertIsNone(live._parse_model_map(None))
        self.assertIsNone(live._parse_model_map(""))
        self.assertIsNone(live._parse_model_map(" , "))

    def test_skips_malformed(self):
        self.assertEqual(
            live._parse_model_map("implement=sonnet,garbage,=x,y="), {"implement": "sonnet"}
        )

    def test_codex_default_role_models_use_mini_model(self):
        self.assertEqual(
            live._effective_role_models("codex", None),
            {
                "implement": "gpt-5.4-mini",
                "review": "gpt-5.4-mini",
                "fix": "gpt-5.4-mini",
                "cleanup": "gpt-5.4-mini",
                "ci-fix": "gpt-5.4-mini",
            },
        )

    def test_explicit_role_models_win_for_codex(self):
        role_models = {"implement": "custom-model"}
        self.assertIs(live._effective_role_models("codex", role_models), role_models)

    def test_detect_default_agent_prefers_overrides_then_codex_host(self):
        with patch.dict(os.environ, {"GO_AGENT_CLI": "claude", "CODEX_CI": "1"}, clear=True):
            self.assertEqual(live._detect_default_agent(), "claude")
        with patch.dict(os.environ, {"ORCH_AGENT": "claude", "CODEX_THREAD_ID": "t"}, clear=True):
            self.assertEqual(live._detect_default_agent(), "claude")
        with patch.dict(os.environ, {"CODEX_CI": "1"}, clear=True):
            self.assertEqual(live._detect_default_agent(), "codex")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(live._detect_default_agent(), "claude")

    def test_detect_default_agent_uses_explicit_opencode_parent_marker(self):
        with patch.dict(os.environ, {"OPENCODE_PARENT": "1"}, clear=True):
            self.assertEqual(live._detect_default_agent(), "opencode")
        with patch.dict(os.environ, {"OPENCODE_PARENT": "1", "GO_AGENT_CLI": "claude"}, clear=True):
            self.assertEqual(live._detect_default_agent(), "claude")

    def test_detect_default_agent_rejects_unsupported_override(self):
        with patch.dict(os.environ, {"GO_AGENT_CLI": "not-a-real-agent"}, clear=True):
            self.assertEqual(live._detect_default_agent(), "claude")


class DefaultModelSelection(unittest.TestCase):
    def test_claude_defaults_to_sonnet(self):
        self.assertEqual(spawnlib.default_model_for_agent("claude"), "sonnet")
        self.assertEqual(
            live.LiveSpawn("001-x", "docs/specs/001-x/", agent="claude").model, "sonnet"
        )

    def test_codex_defaults_to_mini_and_explicit_override_still_wins(self):
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.4-mini")
        self.assertEqual(
            live.LiveSpawn("001-x", "docs/specs/001-x/", agent="codex").model,
            "gpt-5.4-mini",
        )
        self.assertEqual(
            live.LiveSpawn("001-x", "docs/specs/001-x/", agent="codex", model="sonnet").model,
            "sonnet",
        )

    def test_opencode_defaults_to_sonnet_family(self):
        # clear=True wipes the conftest-level GO_MODEL_DEFAULTS_FILE isolation
        # too, so it must be re-supplied here -- without it this test would
        # read whatever is actually in the machine's real model-defaults.yaml
        # (under worktrail_home()) on a real operator machine instead of falling through to the hardcoded default.
        with patch.dict(os.environ, {"GO_MODEL_DEFAULTS_FILE": "/nonexistent-model-defaults.yaml"},
                        clear=True):
            self.assertEqual(
                spawnlib.default_model_for_agent("opencode"), "opencode/deepseek-v4-flash-free"
            )
            self.assertEqual(
                live.LiveSpawn("001-x", "docs/specs/001-x/", agent="opencode").model,
                "opencode/deepseek-v4-flash-free",
            )

    def test_opencode_model_override_remains_supported(self):
        with patch.dict(os.environ, {"ORCH_OPENCODE_MODEL": "provider/custom"}, clear=True):
            self.assertEqual(spawnlib.default_model_for_agent("opencode"), "provider/custom")


class ModelDefaultsFileTest(unittest.TestCase):
    """worktrail_home()/model-defaults.yaml (GO_MODEL_DEFAULTS_FILE): an operator-maintained
    override so a vendor renaming/retiring a model doesn't need a code change --
    confirmed live 2026-08-03: DEFAULT_CODEX_MODEL had drifted to "gpt-5.4-mini"
    while the operator's actual codex CLI listed "gpt-5.6-sol" as current."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.defaults_file = Path(self._tmp.name) / "model-defaults.yaml"
        self._env_patch = patch.dict(
            os.environ, {"GO_MODEL_DEFAULTS_FILE": str(self.defaults_file)}, clear=False
        )
        self._env_patch.start()
        # Saved and restored explicitly (not just popped) so a real
        # ORCH_CODEX_MODEL/ORCH_OPENCODE_MODEL in the ambient environment
        # survives this test class rather than being silently dropped.
        self._removed_env = {}
        for var in ("ORCH_CODEX_MODEL", "ORCH_OPENCODE_MODEL"):
            if var in os.environ:
                self._removed_env[var] = os.environ.pop(var)

    def tearDown(self):
        os.environ.update(self._removed_env)
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_missing_file_falls_through_to_hardcoded_default(self):
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.4-mini")

    def test_file_value_overrides_hardcoded_default(self):
        self.defaults_file.write_text("codex: gpt-5.6-luna\n")
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.6-luna")

    def test_file_covers_all_three_agents(self):
        self.defaults_file.write_text(
            "claude: opus\ncodex: gpt-5.6-luna\nopencode: opencode/gpt-5.6-luna\n"
        )
        self.assertEqual(spawnlib.default_model_for_agent("claude"), "opus")
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.6-luna")
        self.assertEqual(spawnlib.default_model_for_agent("opencode"), "opencode/gpt-5.6-luna")

    def test_agent_absent_from_file_falls_through(self):
        self.defaults_file.write_text("claude: opus\n")
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.4-mini")

    def test_explicit_env_var_wins_over_file(self):
        self.defaults_file.write_text("codex: gpt-5.6-luna\n")
        with patch.dict(os.environ, {"ORCH_CODEX_MODEL": "gpt-5.6-sol"}):
            self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.6-sol")

    def test_malformed_yaml_degrades_to_hardcoded_default(self):
        self.defaults_file.write_text("codex: [unterminated\n")
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.4-mini")

    def test_non_mapping_yaml_degrades_to_hardcoded_default(self):
        self.defaults_file.write_text("- just\n- a\n- list\n")
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.4-mini")

    def test_non_string_value_ignored(self):
        self.defaults_file.write_text("codex: 123\n")
        self.assertEqual(spawnlib.default_model_for_agent("codex"), "gpt-5.4-mini")

    def test_codex_role_models_pick_up_file_value(self):
        # Regression: CODEX_DEFAULT_ROLE_MODELS used to be a dict frozen at
        # live.py's import time from spawnlib.DEFAULT_CODEX_MODEL directly --
        # the exact staleness bug this file exists to fix, just one layer up.
        self.defaults_file.write_text("codex: gpt-5.6-luna\n")
        role_models = live._effective_role_models("codex", None)
        assert role_models is not None
        self.assertEqual(role_models["implement"], "gpt-5.6-luna")
        self.assertEqual(role_models["review"], "gpt-5.6-luna")
        self.assertEqual(set(role_models), {"implement", "review", "fix", "cleanup", "ci-fix"})


class RunLockTest(unittest.TestCase):
    def test_second_acquire_blocks_then_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            jp = Path(tmp) / "run-008.json"
            lock1 = live.RunLock(jp).acquire()
            try:
                with self.assertRaises(live.RunLockHeld):
                    live.RunLock(jp).acquire()
            finally:
                lock1.release()
            # released -> re-acquirable
            lock2 = live.RunLock(jp).acquire()
            lock2.release()


class SetTaskStatus(unittest.TestCase):
    def test_flips_only_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "TASK-001.md"
            f.write_text("---\nid: TASK-001\nstatus: pending\ntitle: x\n---\n\nbody line\n")
            self.assertTrue(live.set_task_status_completed(f))
            txt = f.read_text()
            self.assertIn("status: completed", txt)
            self.assertIn("id: TASK-001", txt)
            self.assertIn("body line", txt)
            self.assertNotIn("status: pending", txt)

    def test_idempotent_when_already_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "TASK-001.md"
            f.write_text("---\nstatus: completed\n---\nbody\n")
            self.assertFalse(live.set_task_status_completed(f))

    def test_appends_when_no_status_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "TASK-001.md"
            f.write_text("---\nid: TASK-001\n---\nbody\n")
            self.assertTrue(live.set_task_status_completed(f))
            self.assertIn("status: completed", f.read_text())


class AddStackedWorktreeIdempotent(unittest.TestCase):
    def test_reuses_existing_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            wt_base = Path(tmp) / "wt"
            wt_base.mkdir()
            task = {"id": "TASK-001", "deps": []}
            wt1 = wt_base / "001-x-task-001"
            live.add_stacked_worktree(repo, "001-x", task, {"TASK-001": task}, wt1)
            self.assertTrue(wt1.exists())
            # free the branch (dir gone, branch remains -- the resume hazard)
            _git(repo, "worktree", "remove", str(wt1), "--force")
            wt2 = wt_base / "001-x-task-001-again"
            # must NOT crash on `add -b <existing branch>`; reuses the branch
            live.add_stacked_worktree(repo, "001-x", task, {"TASK-001": task}, wt2)
            self.assertTrue(wt2.exists())
            head = subprocess.run(
                ["git", "-C", str(wt2), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(head, "001-x/task-001")

    def test_rejects_retained_branch_that_predates_current_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            wt_base = Path(tmp) / "wt"
            wt_base.mkdir()
            task = {"id": "TASK-001", "deps": []}
            wt1 = wt_base / "001-x-task-001"
            live.add_stacked_worktree(repo, "001-x", task, {"TASK-001": task}, wt1)
            _git(repo, "worktree", "remove", str(wt1), "--force")

            (repo / "README.md").write_text("new base\n")
            _git(repo, "add", "README.md")
            _git(repo, "commit", "-q", "-m", "advance base")

            with self.assertRaises(live.WorktreeAddError) as ctx:
                live.add_stacked_worktree(
                    repo,
                    "001-x",
                    task,
                    {"TASK-001": task},
                    wt_base / "001-x-task-001-again",
                )
            self.assertIn("stale", str(ctx.exception))


class AddStackedWorktreeCrossSpecFallback(unittest.TestCase):
    """TASK-006/AC-017: a satisfied external dependency whose branch was
    already merged to base (no worktree branch materialized) must not raise
    `WorktreeAddError` -- `add_stacked_worktree` falls back to the base ref,
    same as it does for an already-integrated same-spec dependency."""

    def test_falls_back_to_base_ref_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            sibling_spec = "098-x"
            _write_sibling_task_file(repo, sibling_spec, "TASK-036")
            # No `098-x/task-036` branch is ever created: the sibling's work
            # was already merged to base under this naming convention.
            task = {
                "id": "TASK-002",
                "deps": [],
                "external_deps": [f"{sibling_spec}/TASK-036"],
            }
            wt = Path(tmp) / "wt"
            live.add_stacked_worktree(repo, "001-x", task, {"TASK-002": task}, wt)  # must not raise
            self.assertTrue(wt.exists())
            head = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(head, "001-x/task-002")


class SalvageReport(unittest.TestCase):
    def _worktree(self, tmp):
        repo = _init_repo(Path(tmp))
        wt = Path(tmp) / "wt"
        live.add_stacked_worktree(
            repo,
            "001-x",
            {"id": "TASK-001", "deps": []},
            {"TASK-001": {"id": "TASK-001", "deps": []}},
            wt,
        )
        return repo, wt

    def test_no_new_commit_is_not_salvageable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, wt = self._worktree(tmp)
            pre = live._git(wt, "rev-parse", "HEAD").stdout.strip()
            self.assertIsNone(live.salvage_report("implement", {"id": "TASK-001"}, wt, pre))

    def test_commit_present_is_salvaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, wt = self._worktree(tmp)
            pre = live._git(wt, "rev-parse", "HEAD").stdout.strip()
            (wt / "f.txt").write_text("work\n")
            _git(wt, "add", "f.txt")
            _git(wt, "commit", "-q", "-m", "did the work")
            rep = live.salvage_report("implement", {"id": "TASK-001"}, wt, pre)
            assert rep is not None
            self.assertEqual(rep["status"], "success")
            self.assertTrue(rep["head_sha"])

    def test_review_role_never_salvaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, wt = self._worktree(tmp)
            pre = "deadbeef"
            self.assertIsNone(live.salvage_report("review", {"id": "TASK-001"}, wt, pre))


class ReviewerIndependence(unittest.TestCase):
    """#6: the review role gets an appended 'independent reviewer' system prompt
    and may use a different model; other roles keep the DEFAULT system prompt
    (preserving prompt-cache reuse, #19)."""

    def setUp(self):
        self._orig = live.spawnlib.spawn_claude_p
        self.captured = {}

        def fake(
            prompt,
            wt,
            *,
            model=None,
            effort=None,
            timeout=None,
            extra_args=None,
            resume_session_id=None,
            fallback_agent=None,
            log=None,
        ):
            self.captured = {"extra_args": extra_args, "model": model}
            return (
                '```json\n{"task":"TASK-001","step":"review","status":"success",'
                '"review_status":"PASSED"}\n```'
            )

        live.spawnlib.spawn_claude_p = fake

    def tearDown(self):
        live.spawnlib.spawn_claude_p = self._orig

    def test_review_gets_append_system_prompt_and_role_model(self):
        ls = live.LiveSpawn(
            "001-x",
            "docs/specs/001-x/",
            agent="claude",
            model="haiku",
            role_models={"review": "sonnet"},
        )
        ls("review", {"id": "TASK-001", "files": ["a.py"]}, Path("/tmp/wt"))
        self.assertIsNotNone(self.captured["extra_args"])
        self.assertIn("--append-system-prompt", self.captured["extra_args"])
        self.assertEqual(self.captured["model"], "sonnet")  # role override

    def test_implement_keeps_default_system_prompt(self):
        ls = live.LiveSpawn("001-x", "docs/specs/001-x/", agent="claude", model="haiku")
        ls("implement", {"id": "TASK-001", "files": ["a.py"]}, Path("/tmp/wt"))
        # --append-system-prompt must NOT be present for non-review roles (cache reuse)
        args = self.captured["extra_args"] or []
        self.assertNotIn("--append-system-prompt", args)
        self.assertEqual(self.captured["model"], "haiku")


class CleanupInPython(unittest.TestCase):
    def test_reports_success_without_touching_the_task_branch(self):
        """Cleanup is a pure state transition: the task file must NOT change and
        no commit may be added. Writing status here is what put a docs/specs/**
        diff on every task branch and forced _strip_spec_folder_to_base() to
        exist; the artifact write now happens once per group at integrate time.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(
                Path(tmp), task_md="---\nid: TASK-001\nstatus: reviewing\n---\nbody\n"
            )
            wt = Path(tmp) / "wt"
            live.add_stacked_worktree(
                repo,
                "001-x",
                {"id": "TASK-001", "deps": []},
                {"TASK-001": {"id": "TASK-001", "deps": []}},
                wt,
            )
            head_before = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()

            rep = live.cleanup_task_in_python(wt, "TASK-001")

            self.assertEqual(rep["status"], "success")
            tf = wt / "docs" / "specs" / "001-x" / "tasks" / "TASK-001.md"
            self.assertIn("status: reviewing", tf.read_text())
            self.assertNotIn("status: completed", tf.read_text())
            head_after = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(head_before, head_after, "cleanup must add no commit")
            porcelain = subprocess.run(
                ["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(porcelain, "", "cleanup must leave the worktree clean")


class WriteGroupTaskStatus(unittest.TestCase):
    """integrate._write_group_task_status: the one place run bookkeeping reaches
    the spec artifact."""

    def _repo_with_tasks(self, tmp):
        repo = _init_repo(
            Path(tmp), task_md="---\nid: TASK-001\nstatus: reviewing\n---\n- [ ] do it\n"
        )
        second = repo / "docs" / "specs" / "001-x" / "tasks" / "TASK-002.md"
        second.write_text("---\nid: TASK-002\nstatus: reviewing\n---\n- [ ] other\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "add TASK-002")
        return repo

    def test_writes_only_this_groups_tasks_and_commits_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_with_tasks(tmp)
            head_before = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()

            integrate._write_group_task_status(
                repo,
                "001-x",
                {"name": "feature-1", "tasks": ["TASK-001"]},
                {"TASK-001": "completed", "TASK-002": "completed"},
            )

            base = repo / "docs" / "specs" / "001-x" / "tasks"
            self.assertIn("status: completed", (base / "TASK-001.md").read_text())
            self.assertIn("- [x] do it", (base / "TASK-001.md").read_text())
            # TASK-002 belongs to another group -> untouched, so sibling group
            # branches touch disjoint files and cannot add/add conflict.
            self.assertIn("status: reviewing", (base / "TASK-002.md").read_text())

            log = subprocess.run(
                ["git", "-C", str(repo), "log", "--oneline", f"{head_before}..HEAD"],
                capture_output=True, text=True,
            ).stdout.strip().splitlines()
            self.assertEqual(len(log), 1, f"expected exactly one commit, got {log}")

    def test_no_commit_when_no_task_is_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_with_tasks(tmp)
            head_before = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()

            integrate._write_group_task_status(
                repo,
                "001-x",
                {"name": "feature-1", "tasks": ["TASK-001"]},
                {"TASK-001": "failed"},
            )

            head_after = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(head_before, head_after)

    def test_missing_task_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_with_tasks(tmp)
            integrate._write_group_task_status(
                repo,
                "001-x",
                {"name": "feature-1", "tasks": ["TASK-404"]},
                {"TASK-404": "completed"},
            )  # must not raise

    def test_openspec_writes_completion_to_shared_tasks_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            change = repo / "openspec" / "changes" / "001-x"
            change.mkdir(parents=True)
            tasks_md = change / "tasks.md"
            tasks_md.write_text("## 1. Setup\n\n- [ ] 1.1 do it\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "add OpenSpec change")

            integrate._write_group_task_status(
                repo,
                "001-x",
                {"name": "feature-1", "tasks": ["1.1"]},
                {"1.1": "completed"},
            )

            self.assertIn("- [x] 1.1 do it", tasks_md.read_text())
            log = subprocess.run(
                ["git", "-C", str(repo), "log", "-1", "--oneline"],
                capture_output=True, text=True, check=True,
            ).stdout
            self.assertIn("mark 1 task(s) completed", log)

if __name__ == "__main__":
    unittest.main(verbosity=2)
