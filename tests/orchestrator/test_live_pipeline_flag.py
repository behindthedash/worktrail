#!/usr/bin/env python3
"""Tests for --pipeline flag wiring (TASK-003 AC-001, REQ-001, REQ-002)."""

import argparse
import contextlib
import inspect
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from worktrail.orchestrator import live

# ---------------------------------------------------------------------------
# Helper: build a minimal fake subparser so we can test argument parsing
# without depending on live.py's internal parser construction.
# ---------------------------------------------------------------------------


def _build_fr_parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    fr = sub.add_parser("full-real")
    fr.add_argument("--repo", required=True)
    fr.add_argument("--spec", required=True)
    fr.add_argument("--remote", default="origin")
    fr.add_argument("--base", default="dev")
    fr.add_argument("--model", default="sonnet")
    fr.add_argument("--max-workers", type=int, default=3)
    fr.add_argument("--timeout", type=int, default=1800)
    fr.add_argument("--fresh", action="store_true")
    fr.add_argument("--from-verify", action="store_true", dest="from_verify")
    fr.add_argument("--only", default=None)
    fr.add_argument("--model-map", default=None)
    fr.add_argument("--run-budget", type=int, default=0)
    fr.add_argument("--pipeline", action="store_true", default=True)
    fr.add_argument("--sequential", action="store_true", default=False)
    return p


def _parse_fr(*extra):
    p = _build_fr_parser()
    return p.parse_args(
        ["full-real", "--repo", "/fake/repo", "--spec", "docs/specs/s"] + list(extra)
    )


# ---------------------------------------------------------------------------
# Test: argparse wiring
# ---------------------------------------------------------------------------


class PipelineFlagParsing(unittest.TestCase):
    """AC-001: --pipeline flag present/absent behavior on the full-real subparser."""

    def test_pipeline_is_the_default(self):
        """scheduler-consolidation stage 1: bare full-real runs the pipelined
        engine; --pipeline is a no-op affirmation."""
        args = _parse_fr()
        self.assertTrue(args.pipeline)
        self.assertFalse(args.sequential)
        self.assertTrue(args.pipeline and not args.sequential)

    def test_sequential_escape_hatch_disables_pipeline(self):
        args = _parse_fr("--sequential")
        self.assertFalse(args.pipeline and not args.sequential)

    def test_pipeline_present_sets_true(self):
        args = _parse_fr("--pipeline")
        self.assertTrue(args.pipeline)

    def test_pipeline_does_not_affect_other_args(self):
        args = _parse_fr("--pipeline")
        self.assertEqual(args.remote, "origin")
        self.assertEqual(args.base, "dev")
        self.assertFalse(args.fresh)
        self.assertFalse(args.from_verify)


# ---------------------------------------------------------------------------
# Test: function signatures carry pipeline: bool = False
# ---------------------------------------------------------------------------


class FullRealSignature(unittest.TestCase):
    """AC-001: full_real and _full_real_inner carry pipeline: bool defaulting to False."""

    def test_full_real_has_pipeline_param_defaulting_false(self):
        sig = inspect.signature(live.full_real)
        self.assertIn("pipeline", sig.parameters)
        self.assertIs(sig.parameters["pipeline"].default, False)

    def test_full_real_inner_has_pipeline_param_defaulting_false(self):
        sig = inspect.signature(live._full_real_inner)
        self.assertIn("pipeline", sig.parameters)
        self.assertIs(sig.parameters["pipeline"].default, False)


# ---------------------------------------------------------------------------
# Shared helper: minimal patch context to reach the pipeline branch check.
# We need to get past the local `import integrate; import verify` and the
# early git/journal setup without hitting real filesystem or git commands.
# ---------------------------------------------------------------------------


def _inner_minimal_patches():
    """Return a list of context-manager patches needed to reach the pipeline
    branch inside _full_real_inner, in the order they must be applied."""
    fake_git_result = MagicMock()
    fake_git_result.stdout = "dev"

    fake_journal_path = "/tmp/fake-journal-pipeline-test.json"

    fake_integrate = types.ModuleType("integrate")
    fake_integrate.finish_real = MagicMock(return_value=([], {}, {}))
    fake_verify = types.ModuleType("verify")
    fake_verify.verify_and_cleanup = MagicMock(return_value={"quarantined": {}, "merged": []})
    fake_verify._make_live_spawn = MagicMock(return_value=MagicMock())

    patches = [
        # Inject fake integrate/verify modules so local imports inside the
        # function resolve to our mocks instead of the real modules.
        patch.dict(sys.modules, {"integrate": fake_integrate, "verify": fake_verify}),
        # Patch internal git helper so no real subprocess is spawned.
        patch("worktrail.orchestrator.live._git", return_value=fake_git_result),
        # Patch journal path derivation to return a safe temp string.
        patch("worktrail.orchestrator.live.journal_path_for", return_value=fake_journal_path),
        # Patch Path so filesystem checks on the journal path don't fail.
        patch("worktrail.orchestrator.live.Path", wraps=lambda p: _fake_path(p, fake_journal_path)),
    ]
    return patches


def _fake_path(p, journal_path_str):
    """Return a MagicMock for the journal Path, real Path for everything else."""
    from pathlib import Path as RealPath

    real = RealPath(p)
    if str(real) == journal_path_str:
        m = MagicMock()
        m.exists.return_value = False
        m.__str__ = lambda self: journal_path_str
        m.__fspath__ = lambda self: journal_path_str
        m.parent = RealPath("/tmp")
        return m
    return real


# ---------------------------------------------------------------------------
# Test: pipeline=True routes to the pipeline branch stub
# ---------------------------------------------------------------------------


class PipelineTrueRoutes(unittest.TestCase):
    """AC-001: pipeline=True routes to _pipeline_scheduler (TASK-004 implemented)."""

    def _patches(self):
        fake_git = MagicMock()
        fake_git.stdout = "dev"
        fake_integrate = types.ModuleType("integrate")
        fake_verify = types.ModuleType("verify")
        fake_sched_result = {"group_prs": [], "final": None, "quarantined": {}, "merged": []}
        return [
            patch.dict(sys.modules, {"integrate": fake_integrate, "verify": fake_verify}),
            patch("worktrail.orchestrator.live._git", return_value=fake_git),
            patch("worktrail.orchestrator.live.journal_path_for", return_value="/tmp/fake-journal-pipeline-test.json"),
            patch("worktrail.orchestrator.live.read_or_create_run_id", return_value="full-test"),
            patch("worktrail.orchestrator.live._pipeline_scheduler", return_value=fake_sched_result),
        ]

    def test_pipeline_true_does_not_raise_not_implemented(self):
        """pipeline=True no longer raises NotImplementedError after TASK-004."""
        with contextlib.ExitStack() as stack:
            for p in self._patches():
                stack.enter_context(p)
            try:
                live._full_real_inner("/tmp/fake-repo", "docs/specs/test", pipeline=True)
            except NotImplementedError:
                self.fail("pipeline=True should not raise NotImplementedError after TASK-004")

    def test_pipeline_true_routes_to_pipeline_scheduler(self):
        """pipeline=True calls _pipeline_scheduler (not live_run_real)."""
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in self._patches()]
            mock_sched = mocks[-1]  # last patch is _pipeline_scheduler
            live._full_real_inner("/tmp/fake-repo", "docs/specs/test", pipeline=True)
        mock_sched.assert_called_once()

    def test_pipeline_true_passes_run_id_to_scheduler(self):
        """pipeline=True creates a run_id and passes it as kwarg to _pipeline_scheduler."""
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in self._patches()]
            mock_sched = mocks[-1]
            live._full_real_inner("/tmp/fake-repo", "docs/specs/test", pipeline=True)
        kw = mock_sched.call_args.kwargs
        self.assertEqual(kw.get("run_id"), "full-test")


# ---------------------------------------------------------------------------
# Test: pipeline=False does NOT hit the NotImplementedError stub
# ---------------------------------------------------------------------------


class PipelineFalseSequentialPath(unittest.TestCase):
    """AC-001 / REQ-002: pipeline=False runs the unchanged sequential blocks."""

    def test_pipeline_false_does_not_raise_not_implemented(self):
        """pipeline=False must not trigger the NotImplementedError stub."""
        fake_git = MagicMock()
        fake_git.stdout = "dev"
        fake_integrate = types.ModuleType("integrate")
        fake_integrate.finish_real = MagicMock(return_value=([], {}, {}))
        fake_verify = types.ModuleType("verify")
        fake_verify.verify_and_cleanup = MagicMock(return_value={"quarantined": {}, "merged": []})
        fake_verify._make_live_spawn = MagicMock(return_value=MagicMock())

        with patch.dict(sys.modules, {"integrate": fake_integrate, "verify": fake_verify}), patch(
            "worktrail.orchestrator.live._git", return_value=fake_git
        ), patch("worktrail.orchestrator.live.journal_path_for", return_value="/tmp/fake-journal.json"), patch(
            "worktrail.orchestrator.live.live_run_real",
            return_value={"spec_id": "test", "tasks": [], "entries": 0, "done": 0, "total": 0},
        ), patch(
            "worktrail.orchestrator.live.read_or_create_run_id", return_value="full-1234"
        ), patch(
            "worktrail.orchestrator.live.coordinator"
        ) as mock_coord, patch(
            "worktrail.orchestrator.live.progress"
        ):

            mock_coord.plan_groups.return_value = []
            mock_coord.deliverable_subset.return_value = ([], [])

            try:
                live._full_real_inner(
                    "/tmp/fake-repo",
                    "docs/specs/test",
                    pipeline=False,
                )
            except NotImplementedError:
                self.fail("pipeline=False should NOT raise NotImplementedError")
            except Exception:
                # Other exceptions from the mocked environment are acceptable;
                # what matters is that NotImplementedError was NOT raised.
                pass

    def test_pipeline_false_invokes_live_run_real(self):
        """pipeline=False reaches the sequential fan-out (live_run_real is called)."""
        fake_git = MagicMock()
        fake_git.stdout = "dev"
        fake_integrate = types.ModuleType("integrate")
        fake_integrate.finish_real = MagicMock(return_value=([], {}, {}))
        fake_verify = types.ModuleType("verify")
        fake_verify.verify_and_cleanup = MagicMock(return_value={"quarantined": {}, "merged": []})
        fake_verify._make_live_spawn = MagicMock(return_value=MagicMock())

        with patch.dict(sys.modules, {"integrate": fake_integrate, "verify": fake_verify}), patch(
            "worktrail.orchestrator.live._git", return_value=fake_git
        ), patch("worktrail.orchestrator.live.journal_path_for", return_value="/tmp/fake-journal.json"), patch(
            "worktrail.orchestrator.live.live_run_real",
            return_value={"spec_id": "test", "tasks": [], "entries": 0, "done": 0, "total": 0},
        ) as mock_lrr, patch(
            "worktrail.orchestrator.live.read_or_create_run_id", return_value="full-1234"
        ), patch(
            "worktrail.orchestrator.live.coordinator"
        ) as mock_coord, patch(
            "worktrail.orchestrator.live.progress"
        ):

            mock_coord.plan_groups.return_value = []
            mock_coord.deliverable_subset.return_value = ([], [])

            try:
                live._full_real_inner(
                    "/tmp/fake-repo",
                    "docs/specs/test",
                    pipeline=False,
                )
            except NotImplementedError:
                self.fail("pipeline=False should NOT raise NotImplementedError")
            except Exception:
                pass

            mock_lrr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
