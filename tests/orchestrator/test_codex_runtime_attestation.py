"""Tests for codex_runtime_attestation.py -- managed attestation over the
existing codex probe, and its sanitized run-record entry."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import codex_probe
from worktrail.orchestrator import codex_runtime_attestation as att
from worktrail.orchestrator.codex_probe import ProbeReport, StageOutcome
from worktrail.router import run_record, skill_dispatch


def _init_git_repo(repo_dir: str) -> None:
    subprocess.run(["git", "init", "-q", repo_dir], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", repo_dir, "config", key, value], check=True)
    Path(repo_dir, "f.txt").write_text("x")
    subprocess.run(["git", "-C", repo_dir, "add", "f.txt"], check=True)
    subprocess.run(
        ["git", "-C", repo_dir, "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )


def _start_run_record(tmp: str) -> str:
    out = StringIO()
    with patch("sys.stdout", out):
        rc = run_record.main(
            ["start", "--repo", tmp, "--route", "F", "--risk", "low", "--dir", tmp]
        )
    assert rc == 0
    return json.loads(out.getvalue())["path"]


OK_STREAM = (
    '{"type": "thread.started", "thread_id": "t1", '
    '"model_provider": "codex"}\n'
    '{"type": "turn.completed"}\n'
    '{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}\n'
)
NO_IDENTITY_STREAM = (
    '{"type": "thread.started", "thread_id": "t1"}\n'
    '{"type": "turn.completed"}\n'
    '{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}\n'
)


class _AttestationHarness(unittest.TestCase):
    """Runs `run_attestation` hermetically: isolated child home, real
    read-only fixture, scripted nested `codex` outcome, auth inheritance
    stubbed (it would otherwise run `codex login status`)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        _init_git_repo(self.repo)
        self.child_home = os.path.join(self.tmp, "child-home")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        if os.access("/", os.W_OK):  # running as root: chmod is not honoured
            self.skipTest("read-only fixture requires a non-root user")

    def run_with(self, stdout="", returncode=0, *, side_effect=None, auth=None):
        completed = subprocess.CompletedProcess(
            args=["codex"], returncode=returncode, stdout=stdout, stderr=""
        )
        calls = []

        def fake_inherit(parent, child):
            calls.append((parent, child))
            if auth is not None:
                raise auth

        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(
                skill_dispatch,
                "default_worktrail_codex_home",
                return_value=self.child_home,
            ),
            patch.object(att, "inherit_codex_chatgpt_auth", side_effect=fake_inherit),
            patch.object(
                codex_probe.subprocess,
                "run",
                return_value=completed,
                side_effect=side_effect,
            ) as run,
        ):
            os.environ.pop("WORKTRAIL_CODEX_HOME", None)
            os.environ.pop("CODEX_HOME", None)
            result = att.run_attestation(timeout=7.5, repo_dir=self.repo)
        self.spawn_calls = run.call_args_list
        self.inherit_calls = calls
        return result


class TestDirectProbeReuse(_AttestationHarness):
    def test_uses_the_probe_api_not_a_second_launcher(self):
        self.assertIs(
            att.codex_probe.prepare_environment, codex_probe.prepare_environment
        )
        self.assertIs(
            att.inherit_codex_chatgpt_auth, skill_dispatch.inherit_codex_chatgpt_auth
        )
        with (
            patch.object(
                att.codex_probe,
                "build_probe_command",
                wraps=codex_probe.build_probe_command,
            ) as build,
            patch.object(
                att.codex_probe,
                "run_probe_command",
                wraps=codex_probe.run_probe_command,
            ) as run,
        ):
            self.run_with(OK_STREAM)
        build.assert_called_once()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[3], 7.5)
        self.assertEqual(run.call_args.kwargs["repo_dir"], self.repo)

    def test_timeout_propagates_to_the_nested_spawn(self):
        self.run_with(OK_STREAM)
        (spawn,) = self.spawn_calls
        self.assertEqual(spawn.kwargs["timeout"], 7.5)
        self.assertEqual(spawn.kwargs["env"]["CODEX_HOME"], self.child_home)

    def test_timeout_outcome_is_classified_and_recorded_as_timeout(self):
        result = self.run_with(
            side_effect=subprocess.TimeoutExpired(cmd=["codex"], timeout=7.5)
        )
        self.assertEqual(result.report.stage, StageOutcome.TIMEOUT)
        self.assertFalse(result.success)
        self.assertTrue(result.child_home_isolated_writable)
        self.assertFalse(result.runtime_ready)


class TestReadOnlyParentToWritableChild(_AttestationHarness):
    def test_fixture_is_read_only_and_child_is_distinct_and_writable(self):
        seen = {}
        real_prepare = codex_probe.prepare_environment

        def spy(override, *, inherit_auth):
            seen["parent"] = os.environ.get("CODEX_HOME")
            seen["worktrail_home"] = os.environ.get("WORKTRAIL_CODEX_HOME")
            seen["parent_writable"] = os.access(seen["parent"], os.W_OK)
            seen["inherit_auth"] = inherit_auth
            return real_prepare(override, inherit_auth=inherit_auth)

        with patch.object(att.codex_probe, "prepare_environment", side_effect=spy):
            result = self.run_with(OK_STREAM)
        self.assertTrue(result.success)
        self.assertFalse(seen["parent_writable"])
        self.assertIsNone(seen["worktrail_home"])
        self.assertFalse(seen["inherit_auth"], "fixture holds no credential")
        self.assertTrue(result.child_home_isolated_writable)
        self.assertEqual(result.report.codex_home, self.child_home)
        self.assertNotEqual(result.report.codex_home, seen["parent"])
        self.assertTrue(result.report.automatic_home)
        self.assertTrue(os.access(self.child_home, os.W_OK))
        # Fixture is torn down and the ambient env restored.
        self.assertFalse(os.path.exists(seen["parent"]))
        self.assertNotIn("CODEX_HOME", os.environ)

    def test_auth_is_inherited_from_the_real_parent_into_the_child(self):
        real_parent = Path(self.tmp, "real")
        with patch.object(att, "resolve_parent_codex_home", return_value=real_parent):
            self.run_with(OK_STREAM)
        # The real ambient parent (resolved before the fixture replaces
        # CODEX_HOME) feeds the child -- never the empty read-only fixture.
        self.assertEqual(self.inherit_calls, [(real_parent, Path(self.child_home))])

    def test_unwritable_child_home_fails_environment_preparation(self):
        os.makedirs(self.child_home, mode=0o755)
        os.chmod(self.child_home, 0o555)
        self.addCleanup(os.chmod, self.child_home, 0o755)
        result = self.run_with(OK_STREAM)
        self.assertEqual(result.report.stage, StageOutcome.ENVIRONMENT_PREPARATION)
        self.assertFalse(result.child_home_isolated_writable)
        self.assertEqual(self.spawn_calls, [])

    def test_child_equal_to_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("not distinct", att.verify_child_home(d, d))
            nested = os.path.join(d, "nested")
            os.makedirs(nested)
            self.assertIn("not distinct", att.verify_child_home(nested, d))
            self.assertIsNone(att.verify_child_home(d, os.path.join(d, "x")))
            self.assertIn("does not exist", att.verify_child_home(d + "/nope", d))

    def test_writable_fixture_is_an_environment_failure(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(att.os, "access", return_value=True),
        ):
            outcome = att.create_readonly_parent_home(d)
        self.assertIsInstance(outcome, ProbeReport)
        self.assertEqual(outcome.stage, StageOutcome.ENVIRONMENT_PREPARATION)


class TestSignalsAndIdentity(_AttestationHarness):
    def test_success_attests_all_direct_runtime_signals(self):
        result = self.run_with(OK_STREAM)
        self.assertTrue(result.success)
        self.assertTrue(result.child_home_isolated_writable)
        self.assertTrue(result.runtime_ready)
        self.assertTrue(result.auth_usable)
        self.assertTrue(result.report_back_success)
        self.assertEqual(result.report.selected_provider, "codex")
        self.assertEqual(result.report.effective_provider, "codex")

    def test_readiness_false_when_no_session(self):
        result = self.run_with('{"type": "turn.started"}\n')
        self.assertEqual(result.report.stage, StageOutcome.PROVIDER_SELECTION)
        self.assertFalse(result.runtime_ready)

    def test_missing_effective_identity_is_unverified_not_failed(self):
        # codex-cli 0.154.0 never reports identity on thread.started, so an
        # absent effective identity is not distinguishable from "correct but
        # unobservable" -- the attestation still succeeds on its other
        # signals, and the entry records the identity as unverified (null).
        result = self.run_with(NO_IDENTITY_STREAM)
        self.assertEqual(result.report.stage, StageOutcome.REPORT_BACK)
        self.assertTrue(result.success)
        self.assertTrue(result.runtime_ready)
        self.assertIsNone(result.report.effective_provider)

    def test_mismatched_identity_reclassification_keeps_observed_report_back_signal(
        self,
    ):
        # The probe DID reply with the sentinel; only the identity rule
        # failed. The observed signal must survive into the result and entry.
        result = self.run_with(OK_STREAM.replace('"codex"', '"other"'))
        self.assertEqual(result.report.stage, StageOutcome.PROVIDER_SELECTION)
        self.assertFalse(result.success)
        self.assertTrue(result.report_back_success)
        entry = att.build_entry(result, "nonce-1")
        self.assertTrue(entry["report_back_success"])
        self.assertFalse(entry["success"])

    def test_mismatched_provider_is_provider_selection(self):
        result = self.run_with(OK_STREAM.replace('"codex"', '"other"'))
        self.assertEqual(result.report.stage, StageOutcome.PROVIDER_SELECTION)
        self.assertIn("'other' != selected 'codex'", result.report.diagnostic)

    def test_model_must_match_only_when_selected(self):
        base = ProbeReport(
            stage=StageOutcome.REPORT_BACK,
            success=True,
            diagnostic="ok",
            session_started_marker="t1",
            selected_provider="codex",
            effective_provider="codex",
        )
        self.assertTrue(att.check_identity(base).success)
        pinned = codex_probe.dataclasses.replace(base, selected_model="gpt-5")
        # No effective_model reported -- unverified, not a failure.
        self.assertTrue(att.check_identity(pinned).success)
        matched = codex_probe.dataclasses.replace(pinned, effective_model="gpt-5")
        self.assertTrue(att.check_identity(matched).success)
        wrong = codex_probe.dataclasses.replace(pinned, effective_model="gpt-4")
        self.assertEqual(
            att.check_identity(wrong).stage, StageOutcome.PROVIDER_SELECTION
        )

    def test_pre_session_failures_keep_their_stage(self):
        report = ProbeReport(
            stage=StageOutcome.STARTUP, success=False, diagnostic="exit 1"
        )
        self.assertIs(att.check_identity(report), report)

    def test_already_failing_report_keeps_stage_and_diagnostic(self):
        # codex-cli 0.154.0 reports no effective identity, so the identity
        # rule must not overwrite a failure the probe already classified.
        for stage, diagnostic in (
            (
                StageOutcome.AUTHENTICATION,
                "codex probe reported an authentication failure",
            ),
            (
                StageOutcome.REPORT_BACK,
                "no-op scope violated: repository working tree mutated",
            ),
        ):
            with self.subTest(stage=stage):
                report = ProbeReport(
                    stage=stage,
                    success=False,
                    diagnostic=diagnostic,
                    session_started_marker="t1",
                    selected_provider="codex",
                    effective_provider=None,
                )
                self.assertIs(att.check_identity(report), report)

    def test_nested_auth_refusal_without_identity_stays_authentication(self):
        result = self.run_with(
            '{"type": "thread.started", "thread_id": "t1"}\n'
            '{"type": "error", "message": "Not logged in, token=abc123"}\n',
            returncode=1,
        )
        self.assertEqual(result.report.stage, StageOutcome.AUTHENTICATION)
        self.assertIn("authentication", result.report.diagnostic)
        self.assertNotIn("abc123", result.report.diagnostic)
        self.assertTrue(result.runtime_ready)
        self.assertIsNone(result.report.effective_provider)

    def test_detected_no_op_violation_survives_into_the_recorded_entry(self):
        def mutate_repo(*args, **kwargs):
            Path(self.repo, "stray.txt").write_text("x")
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=NO_IDENTITY_STREAM, stderr=""
            )

        result = self.run_with(side_effect=mutate_repo)
        self.assertFalse(result.success)
        self.assertEqual(result.report.stage, StageOutcome.REPORT_BACK)
        self.assertIn("no-op scope violated", result.report.diagnostic)
        self.assertIn("repository", result.report.diagnostic)
        entry = att.build_entry(result, "nonce-1")
        self.assertEqual(entry["stage"], "report_back")
        self.assertIn("no-op scope violated", entry["diagnostic"])

    def test_authentication_rejection_is_classified_without_credential_text(self):
        result = self.run_with(
            OK_STREAM,
            auth=OSError(
                "parent Codex is not authenticated with ChatGPT; run 'codex login'"
            ),
        )
        self.assertEqual(result.report.stage, StageOutcome.AUTHENTICATION)
        self.assertFalse(result.auth_usable)
        self.assertTrue(result.child_home_isolated_writable)
        self.assertEqual(self.spawn_calls, [], "no spawn after auth rejection")

    def test_nested_auth_refusal_is_authentication(self):
        result = self.run_with(
            '{"type": "thread.started", "thread_id": "t1", '
            '"model_provider": "codex", "model": "gpt-5"}\n'
            '{"type": "error", "message": "Not logged in, token=abc123"}\n',
            returncode=1,
        )
        self.assertEqual(result.report.stage, StageOutcome.AUTHENTICATION)
        self.assertNotIn("abc123", result.report.diagnostic)
        # The runtime reported readiness and identity before refusing auth;
        # a classified failure must keep those observed signals, not drop them.
        self.assertTrue(result.runtime_ready)
        self.assertEqual(result.report.session_started_marker, "t1")
        self.assertEqual(result.report.effective_provider, "codex")
        self.assertEqual(result.report.effective_model, "gpt-5")

    def test_repository_scope_is_unchanged(self):
        before = subprocess.run(
            ["git", "-C", self.repo, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.run_with(OK_STREAM)
        after = subprocess.run(
            ["git", "-C", self.repo, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(before, after)
        self.assertEqual(before, "")


class TestEntryAndCommand(_AttestationHarness):
    def test_sanitize_diagnostic_strips_paths_and_credential_files(self):
        out = att.sanitize_diagnostic(
            "CODEX_HOME '/home/u/.codex' is not writable\n(nearest existing "
            "directory '/home') required Codex authentication file is "
            "unavailable: auth.json; see ~/.codex/auth.json.save"
        )
        self.assertNotIn("/home", out)
        self.assertNotIn("auth.json", out)
        self.assertNotIn("\n", out)
        self.assertIn("<path>", out)
        self.assertIn("<credential-file>", out)
        self.assertLessEqual(
            len(att.sanitize_diagnostic("x" * 1000)),
            run_record.MANAGED_CODEX_ATTESTATION_DIAGNOSTIC_MAX,
        )

    def test_worktrail_identity_is_safe_or_unavailable(self):
        version, commit = att.worktrail_identity()
        self.assertTrue(version)
        self.assertTrue(commit == att.UNAVAILABLE or len(commit) >= 7)
        with patch.object(att.subprocess, "run", side_effect=OSError("no git")):
            self.assertEqual(att.worktrail_identity()[1], att.UNAVAILABLE)

    def test_command_records_success_entry_and_exits_zero(self):
        path = _start_run_record(self.tmp)
        out = StringIO()
        with (
            patch.object(att, "run_attestation", wraps=att.run_attestation) as run,
            patch("sys.stdout", out),
        ):
            # Drive main() through the same hermetic setup as run_with.
            completed = subprocess.CompletedProcess(
                args=["codex"], returncode=0, stdout=OK_STREAM, stderr=""
            )
            with (
                patch.object(
                    skill_dispatch,
                    "default_worktrail_codex_home",
                    return_value=self.child_home,
                ),
                patch.object(att, "inherit_codex_chatgpt_auth"),
                patch.object(codex_probe.subprocess, "run", return_value=completed),
                patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("WORKTRAIL_CODEX_HOME", None)
                os.environ.pop("CODEX_HOME", None)
                rc = att.main(
                    [
                        "--run-record",
                        path,
                        "--timeout",
                        "9",
                        "--nonce",
                        "session-a1",
                        "--repo-dir",
                        self.repo,
                    ]
                )
        self.assertEqual(rc, 0)
        run.assert_called_once_with(9.0, repo_dir=self.repo)
        entries = run_record.load_managed_codex_attestations(Path(path))
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertTrue(entry["success"])
        self.assertEqual(entry["stage"], "report_back")
        self.assertEqual(entry["nonce"], "session-a1")
        self.assertEqual(entry["selected_provider"], "codex")
        self.assertEqual(entry["effective_provider"], "codex")
        for field in (
            "child_home_isolated_writable",
            "runtime_ready",
            "auth_usable",
            "report_back_success",
        ):
            self.assertIs(entry[field], True, field)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["entry"], entry)
        self.assertEqual(set(entry), run_record.MANAGED_CODEX_ATTESTATION_FIELDS)

    def test_failure_is_recorded_once_before_non_zero_exit(self):
        path = _start_run_record(self.tmp)
        failing = att.AttestationResult(
            report=ProbeReport(
                stage=StageOutcome.AUTHENTICATION,
                success=False,
                diagnostic=(
                    "required Codex authentication file is unavailable: auth.json "
                    "under /home/u/.codex"
                ),
                auth_usable=False,
            ),
            child_home_isolated_writable=True,
            runtime_ready=False,
            auth_usable=False,
            report_back_success=False,
        )
        with (
            patch.object(att, "run_attestation", return_value=failing),
            patch("sys.stdout", StringIO()),
        ):
            rc = att.main(["--run-record", path, "--timeout", "5"])
        self.assertEqual(rc, 1)
        entries = run_record.load_managed_codex_attestations(Path(path))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["stage"], "authentication")
        self.assertFalse(entries[0]["success"])
        self.assertFalse(entries[0]["auth_usable"])
        self.assertNotIn("auth.json", entries[0]["diagnostic"])
        self.assertNotIn("/home", entries[0]["diagnostic"])
        self.assertRegex(entries[0]["nonce"], r"^[0-9a-f]{16}$")
        # Only the attestation entry was added; the record stays parseable.
        record = run_record._load(Path(path))
        self.assertEqual(len(record["managed_codex_attestations"]), 1)

    def test_each_invocation_appends_exactly_one_entry(self):
        path = _start_run_record(self.tmp)
        ok = att.AttestationResult(
            report=ProbeReport(
                stage=StageOutcome.REPORT_BACK,
                success=True,
                diagnostic="ok",
                session_started_marker="t1",
                auth_usable=True,
                selected_provider="codex",
                effective_provider="codex",
            ),
            child_home_isolated_writable=True,
            runtime_ready=True,
            auth_usable=True,
            report_back_success=True,
        )
        with (
            patch.object(att, "run_attestation", return_value=ok),
            patch("sys.stdout", StringIO()),
        ):
            for nonce in ("n-one", "n-two"):
                self.assertEqual(
                    att.main(
                        ["--run-record", path, "--timeout", "5", "--nonce", nonce]
                    ),
                    0,
                )
        nonces = [
            e["nonce"] for e in run_record.load_managed_codex_attestations(Path(path))
        ]
        self.assertEqual(nonces, ["n-one", "n-two"])

    def test_command_requires_timeout_and_run_record(self):
        path = _start_run_record(self.tmp)
        with self.assertRaises(SystemExit):
            att.main(["--run-record", path])
        with self.assertRaises(SystemExit):
            att.main(["--timeout", "5"])
        with self.assertRaises(SystemExit):
            att.main(
                ["--run-record", os.path.join(self.tmp, "missing"), "--timeout", "5"]
            )
        with self.assertRaises(SystemExit):
            att.main(["--run-record", path, "--timeout", "0"])


if __name__ == "__main__":
    unittest.main()
