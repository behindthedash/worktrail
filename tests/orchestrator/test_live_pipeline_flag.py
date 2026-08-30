#!/usr/bin/env python3
"""Scheduler-flag wiring after scheduler-consolidation stage 2.

The pipelined engine is `full-real`'s ONLY scheduler: `--pipeline` remains a
no-op affirmation, `--sequential` is a hard error naming the change, and
`_full_real_inner` routes every non---from-verify run to `_pipeline_scheduler`
unconditionally (the `pipeline=` routing kwarg no longer exists).
"""

import contextlib
import inspect
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from worktrail.orchestrator import live


class SequentialHardError(unittest.TestCase):
    """scheduler-consolidation 2.1: --sequential errors out, names the change's
    removal notice, and never reaches full_real."""

    def _main(self, *extra):
        argv = [
            "full-real",
            "--repo",
            "/fake/repo",
            "--spec",
            "docs/specs/001-foo",
        ] + list(extra)
        printed = []
        with (
            patch.object(live, "full_real", return_value={}) as mock_fr,
            patch(
                "builtins.print",
                side_effect=lambda *a, **k: printed.append(" ".join(map(str, a))),
            ),
        ):
            rc = live.main(argv)
        return rc, mock_fr, printed

    def test_sequential_is_a_hard_error_naming_the_change(self):
        rc, mock_fr, printed = self._main("--sequential")
        self.assertEqual(rc, 2)
        mock_fr.assert_not_called()
        out = "\n".join(printed)
        self.assertIn("--sequential was removed", out)
        self.assertIn("scheduler-consolidation", out)

    def test_bare_full_real_still_dispatches(self):
        rc, mock_fr, _ = self._main()
        self.assertEqual(rc, 0)
        mock_fr.assert_called_once()

    def test_pipeline_flag_is_noop_affirmation(self):
        rc, mock_fr, _ = self._main("--pipeline")
        self.assertEqual(rc, 0)
        mock_fr.assert_called_once()


class FullRealSignature(unittest.TestCase):
    """The scheduler-routing kwarg is gone: passing pipeline= must TypeError
    loudly instead of being silently accepted."""

    def test_full_real_has_no_pipeline_param(self):
        self.assertNotIn("pipeline", inspect.signature(live.full_real).parameters)

    def test_full_real_inner_has_no_pipeline_param(self):
        self.assertNotIn(
            "pipeline", inspect.signature(live._full_real_inner).parameters
        )


class UnconditionalPipelineRouting(unittest.TestCase):
    """_full_real_inner routes every ordinary run to _pipeline_scheduler."""

    def _patches(self):
        fake_git = MagicMock()
        fake_git.stdout = "dev"
        fake_integrate = types.ModuleType("integrate")
        fake_verify = types.ModuleType("verify")
        fake_sched_result = {
            "group_prs": [],
            "final": None,
            "quarantined": {},
            "merged": [],
        }
        return [
            patch.dict(
                sys.modules, {"integrate": fake_integrate, "verify": fake_verify}
            ),
            patch("worktrail.orchestrator.live._git", return_value=fake_git),
            patch(
                "worktrail.orchestrator.live.journal_path_for",
                return_value="/tmp/fake-journal-pipeline-test.json",
            ),
            patch(
                "worktrail.orchestrator.live.read_or_create_run_id",
                return_value="full-test",
            ),
            patch(
                "worktrail.orchestrator.live._pipeline_scheduler",
                return_value=fake_sched_result,
            ),
        ]

    def test_routes_to_pipeline_scheduler(self):
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in self._patches()]
            mock_sched = mocks[-1]  # last patch is _pipeline_scheduler
            live._full_real_inner("/tmp/fake-repo", "docs/specs/test")
        mock_sched.assert_called_once()

    def test_passes_run_id_to_scheduler(self):
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in self._patches()]
            mock_sched = mocks[-1]
            live._full_real_inner("/tmp/fake-repo", "docs/specs/test")
        kw = mock_sched.call_args.kwargs
        self.assertEqual(kw.get("run_id"), "full-test")


if __name__ == "__main__":
    unittest.main()
