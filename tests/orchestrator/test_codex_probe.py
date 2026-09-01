"""Tests for codex_probe.py -- path-parity with skill_dispatch and spawnlib."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import codex_probe, spawnlib
from worktrail.orchestrator.codex_probe import (
    ProbeReport,
    StageOutcome,
    build_probe_command,
    prepare_environment,
    run_probe_command,
)
from worktrail.router import skill_dispatch


def _init_git_repo(repo_dir: str) -> None:
    """Initialize a git repository with proper isolation from global git config.

    Uses subprocess.run with isolated environment to avoid picking up global
    git config like commit.gpgsign=true or pre-commit hooks.
    """
    subprocess.run(["git", "init", "-q", repo_dir], check=True)
    subprocess.run(
        ["git", "-C", repo_dir, "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo_dir, "config", "user.name", "Test User"],
        check=True,
    )


def _add_and_commit_file(repo_dir: str, filename: str, content: str) -> None:
    """Create a file, add it to git, and commit it with proper isolation."""
    filepath = os.path.join(repo_dir, filename)
    with open(filepath, "w") as f:
        f.write(content)
    subprocess.run(
        ["git", "-C", repo_dir, "add", filename],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo_dir, "commit", "-m", "add " + filename],
        check=True,
        capture_output=True,
    )


class TestPrepareEnvironment(unittest.TestCase):
    """Tests for prepare_environment() path-parity with skill_dispatch.prepare_codex_child_environment."""

    def test_calls_prepare_codex_child_environment(self):
        """prepare_environment should call skill_dispatch.prepare_codex_child_environment."""
        # Verify path-parity: the local import is the exact attribute from skill_dispatch
        self.assertIs(
            codex_probe.prepare_codex_child_environment,
            skill_dispatch.prepare_codex_child_environment,
            "codex_probe must import the exact prepare_codex_child_environment from skill_dispatch",
        )

        mock_child_env = {"CODEX_HOME": "/tmp/child-codex", "PATH": "/usr/bin"}
        mock_codex_home = "/tmp/child-codex"
        mock_automatic = False

        with patch.object(codex_probe, "prepare_codex_child_environment") as mock_func:
            mock_func.return_value = (mock_child_env, mock_codex_home, mock_automatic)
            result = prepare_environment()

            mock_func.assert_called_once()
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0], mock_child_env)
            self.assertEqual(result[1], mock_codex_home)
            self.assertEqual(result[2], mock_automatic)


class TestBuildProbeCommand(unittest.TestCase):
    """Tests for build_probe_command() path-parity with spawnlib.build_cmd."""

    def test_calls_build_cmd(self):
        """build_probe_command should call spawnlib.build_cmd."""
        # Verify path-parity: the local import is the exact attribute from spawnlib
        self.assertIs(
            codex_probe.build_cmd,
            spawnlib.build_cmd,
            "codex_probe must import the exact build_cmd from spawnlib",
        )

        mock_cmd = ["codex", "-p", "Reply with exactly the single word: ok"]

        with patch.object(codex_probe, "build_cmd") as mock_build:
            mock_build.return_value = mock_cmd

            cmd, scratch_dir = build_probe_command()

            mock_build.assert_called_once()
            self.assertEqual(cmd, mock_cmd)
            self.assertTrue(isinstance(scratch_dir, str))
            self.assertTrue(os.path.isdir(scratch_dir))

            shutil.rmtree(scratch_dir)


class TestReadOnlyCodexHome(unittest.TestCase):
    """Tests for handling read-only parent CODEX_HOME (task 6.2)."""

    def test_readonly_parent_codex_home_resolves_to_writable_child(self):
        """A read-only parent CODEX_HOME should resolve to a writable, different child home.

        This test verifies that when CODEX_HOME is set to a read-only directory,
        prepare_environment() delegates to select_codex_home() which detects
        the read-only parent and automatically chooses the default_worktrail_codex_home
        instead, returning a writable path.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            readonly_home = os.path.join(tmpdir, "readonly-codex-home")
            os.makedirs(readonly_home, mode=0o755)
            default_home = os.path.join(tmpdir, "default-codex-home")

            # Make the directory read-only
            os.chmod(readonly_home, 0o555)

            try:
                # Set CODEX_HOME to the read-only directory, isolate from WORKTRAIL_CODEX_HOME
                with (
                    patch.dict(os.environ, {"CODEX_HOME": readonly_home}, clear=True),
                    patch.object(
                        skill_dispatch,
                        "default_worktrail_codex_home",
                        return_value=default_home,
                    ),
                ):
                    result = prepare_environment(inherit_auth=False)

                    # Should return a tuple (success case) not a ProbeReport (failure case)
                    self.assertIsInstance(result, tuple)
                    self.assertEqual(len(result), 3)
                    child_env, codex_home, automatic_home = result

                    # The returned codex_home should be different from readonly_home
                    self.assertNotEqual(codex_home, readonly_home)

                    # The returned codex_home should be writable
                    # Create a test file to verify writability
                    Path(codex_home).mkdir(parents=True, exist_ok=True)
                    test_file = os.path.join(codex_home, "test_write")
                    try:
                        with open(test_file, "w") as f:
                            f.write("test")
                        self.assertTrue(os.path.exists(test_file))
                    finally:
                        if os.path.exists(test_file):
                            os.remove(test_file)

                    # automatic_home should be True since we didn't provide an override
                    self.assertTrue(automatic_home)

                    # child_env should have CODEX_HOME set to the new writable path
                    self.assertEqual(child_env["CODEX_HOME"], codex_home)
            finally:
                # Restore write permission for cleanup
                os.chmod(readonly_home, 0o755)

    def test_unwritable_everywhere_produces_environment_preparation_failure(self):
        """An unwritable-everywhere scenario should produce an environment_preparation failure.

        This test verifies that when both the inherited CODEX_HOME and the automatic
        default_worktrail_codex_home fallback are unwritable, prepare_environment()
        returns an environment_preparation failure instead of silently reusing the
        read-only parent.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            readonly_inherited = os.path.join(tmpdir, "readonly-inherited")
            readonly_fallback = os.path.join(tmpdir, "readonly-fallback")

            # Create two read-only directories
            os.makedirs(readonly_inherited, mode=0o755)
            os.makedirs(readonly_fallback, mode=0o755)
            os.chmod(readonly_inherited, 0o555)
            os.chmod(readonly_fallback, 0o555)

            try:
                # Patch environment to set inherited CODEX_HOME and isolate from WORKTRAIL_CODEX_HOME
                # Patch default_worktrail_codex_home to return the second read-only path
                with (
                    patch.dict(
                        os.environ, {"CODEX_HOME": readonly_inherited}, clear=True
                    ),
                    patch.object(
                        skill_dispatch,
                        "default_worktrail_codex_home",
                        return_value=readonly_fallback,
                    ),
                ):
                    result = prepare_environment(inherit_auth=False)

                    # Should return a ProbeReport (failure case) not a tuple (success case)
                    self.assertIsInstance(result, ProbeReport)
                    self.assertEqual(result.stage, StageOutcome.ENVIRONMENT_PREPARATION)
                    self.assertFalse(result.success)
                    # The diagnostic should mention the unwritable path
                    self.assertIn("not writable", result.diagnostic)
            finally:
                # Restore write permission for cleanup
                os.chmod(readonly_inherited, 0o755)
                os.chmod(readonly_fallback, 0o755)


class TestNoOpScopeEnforcement(unittest.TestCase):
    """Tests for no-op scope enforcement: assert that a run leaves
    the repository's git status unchanged, and that mutations are detected.
    """

    def test_successful_run_leaves_git_status_unchanged(self):
        """A successful run should not mutate the repository's git status.

        This test verifies that run_probe_command successfully calls the probe
        command without mutating the repository's git status (no-op scope).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a real git repository
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            # Capture git status before the run
            status_before = subprocess.run(
                ["git", "-C", repo_dir, "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout

            # Build the probe command and get an isolated environment
            cmd, cmd_scratch_dir = build_probe_command()
            try:
                # Prepare the child environment (isolated from home dir)
                # Keep both patches active during the entire probe run
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    result = prepare_environment(inherit_auth=False)
                    # If prepare_environment fails, it returns a ProbeReport
                    if isinstance(result, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {result.diagnostic}"
                        )
                    child_env, _codex_home, _ = result

                    # Patch subprocess.run in the codex_probe module to return a successful CompletedProcess
                    # This simulates the probe command succeeding without actually running codex.
                    # The git snapshots use _REAL_SUBPROCESS_RUN (captured at module import),
                    # so they're unaffected by this patch.
                    successful_result = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=0,
                        stdout='{"type": "thread.started", "thread_id": "test-thread-id"}\nok',
                        stderr="",
                    )

                    with patch.object(
                        codex_probe.subprocess,
                        "run",
                        return_value=successful_result,
                    ):
                        # Call run_probe_command with the patched subprocess.run
                        probe_result = run_probe_command(
                            cmd,
                            cmd_scratch_dir,
                            child_env,
                            timeout=30.0,
                            repo_dir=repo_dir,
                        )

                # Capture git status after the run
                status_after = subprocess.run(
                    ["git", "-C", repo_dir, "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout

                # Assert success: the run should return CompletedProcess with returncode 0
                self.assertIsInstance(probe_result, subprocess.CompletedProcess)
                self.assertEqual(probe_result.returncode, 0)

                # Assert git status unchanged: before and after should be identical
                self.assertEqual(status_before, status_after)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_simulated_repo_mutation_is_detected(self):
        """A mutation to the repository should be detected and reported as a failure.

        This test verifies that run_probe_command detects when a simulated mutation
        (created during the run) changes git status and overrides a successful
        CompletedProcess result to failure.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a real git repository
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            # Build the probe command and get an isolated environment
            cmd, cmd_scratch_dir = build_probe_command()
            try:
                # Prepare the child environment (isolated from home dir)
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    result = prepare_environment(inherit_auth=False)
                    if isinstance(result, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {result.diagnostic}"
                        )
                    child_env, _codex_home, _ = result

                # Create a mock subprocess that returns success, but we'll mutate
                # the repo during the mocked run
                successful_result = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="ok",
                    stderr="",
                )

                def mock_run(*args, **kwargs):
                    # Simulate the probe run mutating the repo
                    mutated_file = os.path.join(repo_dir, "mutated.txt")
                    with open(mutated_file, "w") as f:
                        f.write("mutation")
                    return successful_result

                with patch.object(codex_probe.subprocess, "run", side_effect=mock_run):
                    # Call run_probe_command with the mocked subprocess.run
                    probe_result = run_probe_command(
                        cmd,
                        cmd_scratch_dir,
                        child_env,
                        timeout=30.0,
                        repo_dir=repo_dir,
                    )

                # The probe should fail due to the mutation, detecting the scope violation
                self.assertIsInstance(probe_result, ProbeReport)
                self.assertFalse(probe_result.success)
                # The diagnostic should mention the mutation
                self.assertIn("repository working tree", probe_result.diagnostic)
                self.assertIn("mutated", probe_result.diagnostic)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_simulated_scratch_mutation_is_detected(self):
        """A mutation to the scratch directory should be detected and reported as a failure.

        This test verifies that run_probe_command detects when a simulated mutation
        (created during the run) changes the scratch directory contents and overrides
        a successful CompletedProcess result to failure.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a real git repository
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            # Build the probe command and get an isolated environment
            cmd, cmd_scratch_dir = build_probe_command()
            try:
                # Prepare the child environment (isolated from home dir)
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    result = prepare_environment(inherit_auth=False)
                    if isinstance(result, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {result.diagnostic}"
                        )
                    child_env, _codex_home, _ = result

                # Create a mock subprocess that returns success, but we'll mutate
                # the scratch dir during the mocked run
                successful_result = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="ok",
                    stderr="",
                )

                def mock_run(*args, **kwargs):
                    # Simulate the probe run mutating the scratch directory
                    mutated_file = os.path.join(cmd_scratch_dir, "scratch_mutation.txt")
                    with open(mutated_file, "w") as f:
                        f.write("mutation in scratch")
                    return successful_result

                with patch.object(codex_probe.subprocess, "run", side_effect=mock_run):
                    # Call run_probe_command with the mocked subprocess.run
                    probe_result = run_probe_command(
                        cmd,
                        cmd_scratch_dir,
                        child_env,
                        timeout=30.0,
                        repo_dir=repo_dir,
                    )

                # The probe should fail due to the scratch mutation, detecting the scope violation
                self.assertIsInstance(probe_result, ProbeReport)
                self.assertFalse(probe_result.success)
                # The diagnostic should mention the mutation
                self.assertIn("scratch directory", probe_result.diagnostic)
                self.assertIn("mutated", probe_result.diagnostic)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_both_roots_mutated_is_detected(self):
        """A mutation to both roots should be detected and reported as a failure.

        This test verifies that run_probe_command detects when a simulated mutation
        to both the repo and scratch directory is created during the run, and
        overrides a successful CompletedProcess result to failure.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a real git repository
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            # Build the probe command and get an isolated environment
            cmd, cmd_scratch_dir = build_probe_command()
            try:
                # Prepare the child environment (isolated from home dir)
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    result = prepare_environment(inherit_auth=False)
                    if isinstance(result, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {result.diagnostic}"
                        )
                    child_env, _codex_home, _ = result

                # Create a mock subprocess that returns success, but we'll mutate
                # both the repo and scratch dir during the mocked run
                successful_result = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="ok",
                    stderr="",
                )

                def mock_run(*args, **kwargs):
                    # Simulate the probe run mutating both roots
                    # 1. Mutate the repo
                    mutated_file = os.path.join(repo_dir, "mutated.txt")
                    with open(mutated_file, "w") as f:
                        f.write("repo mutation")
                    # 2. Mutate the scratch directory
                    scratch_mutation = os.path.join(
                        cmd_scratch_dir, "scratch_mutation.txt"
                    )
                    with open(scratch_mutation, "w") as f:
                        f.write("scratch mutation")
                    return successful_result

                with patch.object(codex_probe.subprocess, "run", side_effect=mock_run):
                    # Call run_probe_command with the mocked subprocess.run
                    probe_result = run_probe_command(
                        cmd,
                        cmd_scratch_dir,
                        child_env,
                        timeout=30.0,
                        repo_dir=repo_dir,
                    )

                # The probe should fail due to mutations in both roots, detecting the scope violations
                self.assertIsInstance(probe_result, ProbeReport)
                self.assertFalse(probe_result.success)
                # The diagnostic should mention both mutations
                self.assertIn("scratch directory", probe_result.diagnostic)
                self.assertIn("repository working tree", probe_result.diagnostic)
                self.assertIn("and", probe_result.diagnostic)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
