#!/usr/bin/env python3
"""A capacity-blocked task worker raises instead of reporting back (2.3).

`LiveSpawn.__call__` now routes its result through `spawnlib.raise_if_exhausted`
on the single shared return path, so an exhausted spawn -- whose `text` is the
provider's capacity notice, not a report-back -- never reaches the drive loop's
parser. The check sits after the `served_harness` label correction so the
journal still names the cell that was actually attempted.

Hermetic: no subprocesses, no git, no filesystem writes.
"""

import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worktrail.orchestrator import dispatch, spawnlib
from worktrail.orchestrator.live import LiveSpawn
from worktrail.runtime import selection
from worktrail.runtime.selection import NoExecutionTarget

GOOD_TEXT = (
    '{"task": "T001", "step": "implement", "status": "success",'
    ' "head_sha": "abc", "files_touched": [], "tests": "none",'
    ' "review_status": null, "critical_issues": 0, "major_issues": 0, "notes": "ok"}'
)
EXHAUSTED_TEXT = "All configured cells are capacity-gated (billing)."


def _result(text=GOOD_TEXT, *, exhausted=False, failure_class="", harness="claude"):
    return spawnlib.SpawnResult(
        text=text,
        usage={},
        served_harness=harness,
        exhausted=exhausted,
        failure_class=failure_class,
    )


class _Cell:
    """Minimal stand-in for a selection Cell."""

    def __init__(self, harness):
        self.harness = harness
        self.target = f"{harness}-target"
        self.model = "m"
        self.effort = None


def _recorder(result):
    """A fake spawn function returning `result` and recording its prompts."""

    def fake(prompt, worktree, **kwargs):
        fake.prompts.append(prompt)
        return result

    fake.prompts = []
    return fake


class _ParseRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, text, *a, **kw):
        self.calls.append(text)
        return {}


def _invoke(result, *, harness="claude", explicit=False, role="implement"):
    """Run LiveSpawn.__call__ against one of the four spawn branches.

    `harness` picks the claude / non-claude half, `explicit` picks the
    explicit-model-override half.
    """
    spawn = LiveSpawn(
        "spec",
        "docs/specs/spec/",
        agent=harness,
        role_models={role: "override-model"} if explicit else None,
    )
    claude_fake = _recorder(result)
    agent_fake = _recorder(result)
    parse = _ParseRecorder()
    with (
        patch.object(dispatch, "build_worker_prompt", return_value="fake-prompt"),
        patch.object(dispatch, "parse_report_back", parse),
        patch.object(selection, "select_cell", lambda *a, **kw: _Cell(harness)),
        patch.object(
            spawnlib, "explicit_cell_override", lambda *a, **kw: nullcontext()
        ),
        patch.object(spawnlib, "spawn_claude_p", claude_fake),
        patch.object(spawnlib, "spawn_agent", agent_fake),
    ):
        try:
            returned = spawn(role, {"id": "T001"}, Path("/fake/wt"))
            raised = None
        except BaseException as exc:  # noqa: BLE001 -- the assertion is the type
            returned, raised = None, exc
    return spawn, returned, raised, parse, claude_fake, agent_fake


BRANCHES = [
    ("explicit-claude", {"harness": "claude", "explicit": True}),
    ("explicit-agent", {"harness": "codex", "explicit": True}),
    ("auto-claude", {"harness": "claude", "explicit": False}),
    ("auto-agent", {"harness": "codex", "explicit": False}),
]


class TestExhaustedSpawnRaises(unittest.TestCase):
    def test_raises_spawn_exhausted(self):
        """An exhausted worker spawn makes __call__ raise SpawnExhausted."""
        _, _, raised, _, _, _ = _invoke(
            _result(EXHAUSTED_TEXT, exhausted=True, failure_class="billing")
        )
        self.assertIsInstance(raised, spawnlib.SpawnExhausted)
        self.assertEqual(raised.failure_class, "billing")
        self.assertIn("implement worker T001", str(raised))

    def test_raises_on_every_branch(self):
        """All four spawn branches share the one fail-closed return path."""
        for name, kwargs in BRANCHES:
            with self.subTest(branch=name):
                _, _, raised, _, _, _ = _invoke(
                    _result(EXHAUSTED_TEXT, exhausted=True, harness=kwargs["harness"]),
                    **kwargs,
                )
                self.assertIsInstance(raised, spawnlib.SpawnExhausted)

    def test_parse_report_back_never_sees_provider_text(self):
        """The provider's capacity notice is never handed to the report parser."""
        _, _, _, parse, _, _ = _invoke(
            _result(EXHAUSTED_TEXT, exhausted=True, failure_class="billing")
        )
        self.assertEqual(parse.calls, [])

    def test_caught_as_no_execution_target(self):
        """_safe_drive's classifier relies on the NoExecutionTarget shape."""
        caught = None
        try:
            raise _invoke(_result(EXHAUSTED_TEXT, exhausted=True))[2]
        except NoExecutionTarget as exc:
            caught = exc
        self.assertIsInstance(caught, spawnlib.SpawnExhausted)

    def test_label_correction_applied_before_raising(self):
        """served_harness is recorded even though the call ends in an exception."""
        spawn, _, raised, _, _, _ = _invoke(
            _result(EXHAUSTED_TEXT, exhausted=True, harness="codex"),
            harness="codex",
        )
        self.assertIsInstance(raised, spawnlib.SpawnExhausted)
        self.assertEqual(spawn.last_agent, "codex")


class TestNonExhaustedUnchanged(unittest.TestCase):
    def test_result_returned_unchanged_on_every_branch(self):
        """A non-exhausted spawn is returned as-is, identically, per branch."""
        for name, kwargs in BRANCHES:
            with self.subTest(branch=name):
                result = _result(harness=kwargs["harness"])
                spawn, returned, raised, _, claude, agent = _invoke(result, **kwargs)
                self.assertIsNone(raised)
                self.assertIs(returned, result)
                self.assertEqual(spawn.last_agent, kwargs["harness"])
                used = claude if kwargs["harness"] == "claude" else agent
                self.assertEqual(len(used.prompts), 1)


if __name__ == "__main__":
    unittest.main()
