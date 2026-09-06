"""Tests for codex_probe.py -- path-parity with skill_dispatch and spawnlib."""

import dataclasses
import json
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
    extract_auth_failure_marker,
    extract_authenticated_marker,
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
                        stdout=(
                            '{"type": "thread.started", "thread_id": "test-thread-id"}\n'
                            '{"type": "turn.completed"}\n'
                            '{"type": "item.completed", "item": '
                            '{"type": "agent_message", "text": "ok"}}\n'
                        ),
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

                # Assert success: the run should classify as a successful report_back
                self.assertIsInstance(probe_result, ProbeReport)
                self.assertEqual(probe_result.stage, StageOutcome.REPORT_BACK)
                self.assertTrue(probe_result.success)

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


class TestAuthInheritanceStageAttribution(unittest.TestCase):
    """Tests for the pre-spawn half of the authentication stage (task 4.3):
    an auth-inheritance raise from prepare_codex_child_environment must be
    reported as AUTHENTICATION, not ENVIRONMENT_PREPARATION.
    """

    def test_auth_inheritance_raise_is_classified_as_authentication(self):
        """A raise that only happens with inherit_auth=True is an auth failure."""

        def only_auth_half_fails(_override=None, *, inherit_auth=True):
            if inherit_auth:
                raise OSError(
                    "parent Codex is not authenticated with ChatGPT; run "
                    "'codex login' or omit --inherit-codex-auth"
                )
            return ({"CODEX_HOME": "/tmp/child"}, "/tmp/child", False)

        with patch.object(
            codex_probe,
            "prepare_codex_child_environment",
            side_effect=only_auth_half_fails,
        ):
            report = prepare_environment(inherit_auth=True)

        self.assertIsInstance(report, ProbeReport)
        self.assertEqual(report.stage, StageOutcome.AUTHENTICATION)
        self.assertFalse(report.success)
        self.assertFalse(report.auth_usable)
        self.assertIn("not authenticated with ChatGPT", report.diagnostic)

    def test_home_half_failure_stays_environment_preparation(self):
        """A raise that persists with inherit_auth=False is a home failure."""

        def both_halves_fail(_override=None, *, inherit_auth=True):
            raise OSError("CODEX_HOME '/ro/home' is not writable")

        with patch.object(
            codex_probe,
            "prepare_codex_child_environment",
            side_effect=both_halves_fail,
        ):
            report = prepare_environment(inherit_auth=True)

        self.assertIsInstance(report, ProbeReport)
        self.assertEqual(report.stage, StageOutcome.ENVIRONMENT_PREPARATION)
        self.assertIsNone(report.auth_usable)

    def test_no_attribution_retry_when_auth_was_not_requested(self):
        """With inherit_auth=False there is no auth half to blame, so the
        helper must not be called a second time."""

        def always_fails(_override=None, *, inherit_auth=True):
            raise OSError("CODEX_HOME '/ro/home' is not writable")

        with patch.object(
            codex_probe,
            "prepare_codex_child_environment",
            side_effect=always_fails,
        ) as mock_prepare:
            report = prepare_environment(inherit_auth=False)

        self.assertEqual(mock_prepare.call_count, 1)
        self.assertIsInstance(report, ProbeReport)
        self.assertEqual(report.stage, StageOutcome.ENVIRONMENT_PREPARATION)


class TestExtractAuthFailureMarker(unittest.TestCase):
    """Tests for the post-spawn half of the authentication stage (task 4.3):
    a non-secret label derived from the nested process's own output.
    """

    def test_not_logged_in_error_event(self):
        stdout = (
            '{"type": "thread.started", "thread_id": "t1"}\n'
            '{"type": "error", "message": "Not logged in. Run codex login."}\n'
        )
        self.assertEqual(extract_auth_failure_marker(stdout), "not_logged_in")

    def test_unauthorized_error_event(self):
        stdout = '{"type": "error", "message": "request failed: HTTP 401"}\n'
        self.assertEqual(extract_auth_failure_marker(stdout), "unauthorized")

    def test_healthy_stream_reports_no_marker(self):
        """Absence of an auth signal is not an auth failure ("if available")."""
        stdout = (
            '{"type": "thread.started", "thread_id": "t1"}\n'
            '{"type": "turn.completed", "usage": {}}\n'
        )
        self.assertIsNone(extract_auth_failure_marker(stdout))

    def test_non_auth_error_event_reports_no_marker(self):
        stdout = '{"type": "error", "message": "sandbox denied write"}\n'
        self.assertIsNone(extract_auth_failure_marker(stdout))

    def test_non_auth_message_containing_401_reports_no_marker(self):
        """`401` and `unauthorized` must not be matched as bare substrings:
        they occur in request ids, millisecond values, and sandbox denials,
        and a wrong `authentication` verdict sends an operator to
        `codex login` for an error that has nothing to do with auth."""
        for message in (
            "stream error: internal server error (request id req_4013abcd)",
            "model overloaded; retry after 401 ms",
            "sandbox denied: unauthorized to write /etc/hosts",
        ):
            with self.subTest(message=message):
                stdout = json.dumps({"type": "error", "message": message})
                self.assertIsNone(extract_auth_failure_marker(stdout + "\n"))

    def test_malformed_lines_are_skipped_not_raised(self):
        stdout = (
            "not json at all\n"
            "[1, 2, 3]\n"
            '{"type": "error"}\n'
            '{"type": "error", "message": 42}\n'
            '{"type": "error", "message": "unauthorized"}\n'
            '{"type": "error", "messa\n'
        )
        self.assertEqual(extract_auth_failure_marker(stdout), "unauthorized")

    def test_returns_only_a_fixed_label_never_the_message(self):
        """The nested process's message can quote an account or token; only
        this module's own vocabulary may leave the extractor."""
        stdout = (
            '{"type": "error", "message": "401 Unauthorized for '
            'sk-secret-token / user@example.com"}\n'
        )
        marker = extract_auth_failure_marker(stdout)
        self.assertEqual(marker, "unauthorized")
        self.assertNotIn("sk-secret-token", marker)
        self.assertNotIn("user@example.com", marker)


class TestExtractAuthenticatedMarker(unittest.TestCase):
    """Tests for the positive half of the authentication stage (task 4.3):
    the "(if available) authenticated signal" from the nested process's own
    output.
    """

    def test_served_turn_is_positive_evidence(self):
        stdout = (
            '{"type": "thread.started", "thread_id": "t1"}\n'
            '{"type": "turn.completed", "usage": {}}\n'
        )
        self.assertTrue(extract_authenticated_marker(stdout))

    def test_refused_run_reports_no_positive_signal(self):
        stdout = (
            '{"type": "thread.started", "thread_id": "t1"}\n'
            '{"type": "error", "message": "401 Unauthorized"}\n'
        )
        self.assertIsNone(extract_authenticated_marker(stdout))

    def test_silent_stream_is_none_never_false(self):
        """Absence of a served turn is not evidence of an auth failure -- a
        timeout, a sandbox denial and a 500 all look the same here."""
        self.assertIsNone(extract_authenticated_marker(""))
        self.assertIsNone(
            extract_authenticated_marker(
                '{"type": "thread.started", "thread_id": "t1"}\n'
            )
        )

    def test_malformed_lines_are_skipped_not_raised(self):
        stdout = (
            "not json at all\n"
            "[1, 2, 3]\n"
            '{"type": "turn.completed"}\n'
            '{"type": "turn.compl\n'
        )
        self.assertTrue(extract_authenticated_marker(stdout))


class TestRunProbeCommandAuthenticationStage(unittest.TestCase):
    """run_probe_command must classify an authenticated-refusal run as
    AUTHENTICATION without storing the nested process's raw output.
    """

    def test_auth_failure_in_output_is_classified_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    prepared = prepare_environment(inherit_auth=False)
                    if isinstance(prepared, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {prepared.diagnostic}"
                        )
                    child_env, _codex_home, _ = prepared

                    # Startup and the session-started signal both pass; the
                    # nested process then reports an auth refusal quoting a
                    # secret-looking value that must not reach the report.
                    refused = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=0,
                        stdout=(
                            '{"type": "thread.started", "thread_id": "t1"}\n'
                            '{"type": "error", "message": "401 Unauthorized '
                            'for token sk-secret-token"}\n'
                        ),
                        stderr="",
                    )
                    with patch.object(
                        codex_probe.subprocess, "run", return_value=refused
                    ):
                        result = run_probe_command(
                            cmd,
                            cmd_scratch_dir,
                            child_env,
                            timeout=30.0,
                            repo_dir=repo_dir,
                        )

                self.assertIsInstance(result, ProbeReport)
                self.assertEqual(result.stage, StageOutcome.AUTHENTICATION)
                self.assertFalse(result.success)
                self.assertFalse(result.auth_usable)
                self.assertIn("unauthorized", result.diagnostic)
                self.assertNotIn("sk-secret-token", result.diagnostic)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_auth_failure_on_non_zero_exit_is_still_authentication(self):
        """The realistic auth refusal: codex exits non-zero *and* emits an
        auth `error` event. `spawnlib.is_infra_failure` calls any non-zero
        exit a startup failure before it looks at stdout, so without the auth
        check running first this run would be reported as `startup` and send
        the operator to debug PATH and spawn plumbing for an expired session.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    prepared = prepare_environment(inherit_auth=False)
                    if isinstance(prepared, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {prepared.diagnostic}"
                        )
                    child_env, _codex_home, _ = prepared

                    stdout = '{"type": "error", "message": "401 Unauthorized"}\n'
                    # Guard the premise, so this test still means something if
                    # spawnlib's classifier ever changes underneath it.
                    self.assertTrue(spawnlib.is_infra_failure(1, stdout))

                    refused = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=1,
                        stdout=stdout,
                        stderr="",
                    )
                    with patch.object(
                        codex_probe.subprocess, "run", return_value=refused
                    ):
                        result = run_probe_command(
                            cmd,
                            cmd_scratch_dir,
                            child_env,
                            timeout=30.0,
                            repo_dir=repo_dir,
                        )

                self.assertIsInstance(result, ProbeReport)
                self.assertEqual(result.stage, StageOutcome.AUTHENTICATION)
                self.assertFalse(result.success)
                self.assertFalse(result.auth_usable)
                self.assertIn("unauthorized", result.diagnostic)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_auth_failure_without_session_started_is_still_authentication(self):
        """An auth refusal that never got a `thread.started` event out is an
        authentication failure, not a provider_selection one: the provider was
        reached and refused the credential."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    prepared = prepare_environment(inherit_auth=False)
                    if isinstance(prepared, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {prepared.diagnostic}"
                        )
                    child_env, _codex_home, _ = prepared

                    refused = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=0,
                        stdout=(
                            '{"type": "error", "message": "Not logged in. '
                            'Run codex login."}\n'
                        ),
                        stderr="",
                    )
                    with patch.object(
                        codex_probe.subprocess, "run", return_value=refused
                    ):
                        result = run_probe_command(
                            cmd,
                            cmd_scratch_dir,
                            child_env,
                            timeout=30.0,
                            repo_dir=repo_dir,
                        )

                self.assertIsInstance(result, ProbeReport)
                self.assertEqual(result.stage, StageOutcome.AUTHENTICATION)
                self.assertFalse(result.auth_usable)
                self.assertIn("not_logged_in", result.diagnostic)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_startup_failure_without_auth_evidence_stays_startup(self):
        """Stage order is preserved in the other direction: a non-zero exit
        with no parseable output really did fail to get off the ground, so it
        stays STARTUP rather than being swept into the auth stage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    prepared = prepare_environment(inherit_auth=False)
                    if isinstance(prepared, ProbeReport):
                        self.fail(
                            f"Environment preparation failed: {prepared.diagnostic}"
                        )
                    child_env, _codex_home, _ = prepared

                    failed_startup = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=1,
                        stdout="",
                        stderr="",
                    )
                    with patch.object(
                        codex_probe.subprocess, "run", return_value=failed_startup
                    ):
                        result = run_probe_command(
                            cmd,
                            cmd_scratch_dir,
                            child_env,
                            timeout=30.0,
                            repo_dir=repo_dir,
                        )

                self.assertIsInstance(result, ProbeReport)
                self.assertEqual(result.stage, StageOutcome.STARTUP)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)


class TestSixStageOutcomes(unittest.TestCase):
    """One fixture per stage outcome not already covered elsewhere, so a
    future regression pinpoints which stage broke (task 6.6).

    `environment_preparation` is covered by `TestReadOnlyCodexHome`,
    `startup` by `test_startup_failure_without_auth_evidence_stays_startup`,
    and `authentication` by `TestRunProbeCommandAuthenticationStage` --
    this class adds the three stages those don't reach:
    `provider_selection`, `timeout`, and a `report_back` failure (its
    success case is covered by
    `test_successful_run_leaves_git_status_unchanged`).
    """

    def _prepared_env(self, tmpdir):
        with patch.dict(
            os.environ,
            {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
            clear=True,
        ):
            prepared = prepare_environment(inherit_auth=False)
        if isinstance(prepared, ProbeReport):
            self.fail(f"Environment preparation failed: {prepared.diagnostic}")
        child_env, _codex_home, _automatic_home = prepared
        return child_env

    def test_provider_selection_stage(self):
        """Startup passes (non-empty output, exit 0) but the nested process
        never reports a `thread.started` session -- classified as
        `provider_selection`, not `startup`."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                child_env = self._prepared_env(tmpdir)
                no_session = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout='{"type": "turn.started"}\n',
                    stderr="",
                )
                with patch.object(
                    codex_probe.subprocess, "run", return_value=no_session
                ):
                    result = run_probe_command(
                        cmd,
                        cmd_scratch_dir,
                        child_env,
                        timeout=30.0,
                        repo_dir=repo_dir,
                    )

                self.assertIsInstance(result, ProbeReport)
                self.assertEqual(result.stage, StageOutcome.PROVIDER_SELECTION)
                self.assertFalse(result.success)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_timeout_stage(self):
        """A subprocess that exceeds the wall-clock bound is classified as
        `timeout` rather than propagating `subprocess.TimeoutExpired`."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                child_env = self._prepared_env(tmpdir)

                def mock_run(*args, **kwargs):
                    raise subprocess.TimeoutExpired(cmd=cmd, timeout=30.0)

                with patch.object(codex_probe.subprocess, "run", side_effect=mock_run):
                    result = run_probe_command(
                        cmd,
                        cmd_scratch_dir,
                        child_env,
                        timeout=30.0,
                        repo_dir=repo_dir,
                    )

                self.assertIsInstance(result, ProbeReport)
                self.assertEqual(result.stage, StageOutcome.TIMEOUT)
                self.assertFalse(result.success)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)

    def test_report_back_failure_stage(self):
        """The nested process starts, authenticates, and completes, but its
        final reply does not match the expected sentinel -- classified as a
        failing `report_back`, not a silent pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                child_env = self._prepared_env(tmpdir)
                wrong_reply = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=(
                        '{"type": "thread.started", "thread_id": "t1"}\n'
                        '{"type": "item.completed", "item": '
                        '{"type": "agent_message", "text": "not ok"}}\n'
                    ),
                    stderr="",
                )
                with patch.object(
                    codex_probe.subprocess, "run", return_value=wrong_reply
                ):
                    result = run_probe_command(
                        cmd,
                        cmd_scratch_dir,
                        child_env,
                        timeout=30.0,
                        repo_dir=repo_dir,
                    )

                self.assertIsInstance(result, ProbeReport)
                self.assertEqual(result.stage, StageOutcome.REPORT_BACK)
                self.assertFalse(result.success)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)


class TestSecretRedaction(unittest.TestCase):
    """Task 6.5: a secret-shaped value anywhere in the nested process's
    stdout/stderr must never appear in the structured report, regardless of
    which stage classifies the run."""

    def test_secret_in_report_back_success_output_is_not_leaked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = os.path.join(tmpdir, "test-repo")
            os.makedirs(repo_dir)
            _init_git_repo(repo_dir)
            _add_and_commit_file(repo_dir, "test.txt", "initial content")

            cmd, cmd_scratch_dir = build_probe_command()
            try:
                with patch.dict(
                    os.environ,
                    {"WORKTRAIL_CODEX_HOME": os.path.join(tmpdir, "codex-home")},
                    clear=True,
                ):
                    prepared = prepare_environment(inherit_auth=False)
                if isinstance(prepared, ProbeReport):
                    self.fail(f"Environment preparation failed: {prepared.diagnostic}")
                child_env, _codex_home, _ = prepared

                secret = "sk-supersecrettoken-should-never-leak"
                leaky = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout=(
                        '{"type": "thread.started", "thread_id": "t1", '
                        f'"cookie": "{secret}"}}\n'
                        '{"type": "item.completed", "item": '
                        f'{{"type": "agent_message", "text": "ok ({secret})"}}}}\n'
                    ),
                    stderr=f"leaked stderr token {secret}",
                )
                with patch.object(codex_probe.subprocess, "run", return_value=leaky):
                    result = run_probe_command(
                        cmd,
                        cmd_scratch_dir,
                        child_env,
                        timeout=30.0,
                        repo_dir=repo_dir,
                    )

                self.assertIsInstance(result, ProbeReport)
                report_str = json.dumps(dataclasses.asdict(result))
                self.assertNotIn(secret, report_str)
            finally:
                shutil.rmtree(cmd_scratch_dir, ignore_errors=True)


class TestLauncherTimeoutRequired(unittest.TestCase):
    """Task 6.4: the launcher requires a bounded `--timeout` -- no unbounded
    run is possible."""

    def test_main_requires_timeout_argument(self):
        with self.assertRaises(SystemExit):
            codex_probe.main([])

    def test_main_accepts_explicit_timeout_and_exits_on_failure(self):
        """A parsed, bounded --timeout flows through to a real classified
        outcome (mocking environment preparation to fail fast avoids
        spawning a real codex process in this unit test)."""
        failing_report = ProbeReport(
            stage=StageOutcome.ENVIRONMENT_PREPARATION,
            success=False,
            diagnostic="forced failure for --timeout wiring test",
        )
        with patch.object(
            codex_probe, "prepare_environment", return_value=failing_report
        ):
            exit_code = codex_probe.main(["--timeout", "5"])
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
