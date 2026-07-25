#!/usr/bin/env python3
"""End-to-end tests chaining the pre-dispatch spec-collision guard's two
cooperating scripts:

  check_spec_collision.py  (router)   -- check() / verify()
  work_queue.py            (workqueue) -- done(..., note=...)

This reproduces Phase 5.5's two dispatch-source behaviors (see devkit's
`references/spec-collision-check.md`) end to end against real throwaway
git-repo fixtures, per devkit's stdlib-`unittest` "e2e" convention for
Python-only skills. Both scripts are called directly (not via subprocess)
for speed and clearer assertions, mirroring `test_work_queue.py`'s own
direct-call style. Fixture construction is reused rather than reinvented:
repo/spec/task builders come from `test_check_spec_collision.py`; the
queue/picked temp-dir harness is the real `QueueTestBase` from
`tests/workqueue/test_work_queue.py` (same repo now that both subsystems
live in worktrail -- devkit's own copy of this file used to carry a
temporary inlined duplicate of QueueTestBase pending this exact move).

Run: python3 -m pytest tests/router/test_check_spec_collision_e2e.py -q
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from worktrail.router import check_spec_collision as csc
from worktrail.workqueue import work_queue as wq
from .test_check_spec_collision import _git, _init_repo, _make_spec, _make_task, _write
from ..workqueue.test_work_queue import QueueTestBase


def _make_picked_brief(picked_dir: Path, name: str, focus: str) -> Path:
    """Write a fixture brief straight into picked/ (already claimed), matching
    `test_work_queue.py`'s `_picked_brief` shape plus the fields Phase 5.5's
    brief-sourced closure path reads/writes."""
    picked_dir.mkdir(parents=True, exist_ok=True)
    path = picked_dir / name
    path.write_text(
        "---\n"
        f"id: {Path(name).stem}\n"
        f"focus: {focus}\n"
        "status: picked\n"
        "recommended-route: D\n"
        "---\n\n"
        f"## Focus\n\n{focus}\n",
        encoding="utf-8",
    )
    return path


class TestHappyPathConfirmedCollisionClosesBrief(QueueTestBase):
    """AC-001-AC-007 chained with AC-012/AC-013: check() -> verify() ->
    work_queue.done(..., note=...) -> the brief on disk carries status: done,
    completed-at, and a ## Closure Note citing the matching spec."""

    def test_confirmed_collision_closes_brief_with_citing_note(self):
        repo = _init_repo()
        spec_dir = _make_spec(
            repo,
            "007-donor-search",
            status="Implemented",
            feature_summary="Donors can search nonprofits.",
            spec_name="donor-search",
        )
        target = Path(repo) / "src" / "search.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship donor search")
        _make_task(spec_dir, "TASK-001", files=["src/search.py"])

        # check(): pure extraction -- the calling agent's job is to judge a
        # candidate a strong match; here the fixture spec is the only one.
        checked = csc.check(Path(repo))
        self.assertTrue(checked["checked"])
        candidate = next(
            c for c in checked["candidates"] if c["spec_id"] == "007-donor-search"
        )
        self.assertEqual(candidate["title"], "Donor Search")

        # verify(): artifact verification of the judged candidate.
        verified = csc.verify(Path(repo), candidate["spec_id"])
        self.assertTrue(verified["confirmed"])
        self.assertEqual(verified["status"], "Implemented")
        self.assertEqual(verified["files"], ["src/search.py"])

        # A fixture brief sits in picked/ (already claimed) awaiting closure.
        brief_path = _make_picked_brief(
            self.picked, "20260724-090000-donor-search.md", "Add donor search"
        )

        note = (
            f"Closed as a pre-existing collision with {verified['spec_id']} "
            f"({candidate['title']}) -- Status: Implemented, "
            f"files: {verified['files']} all git-tracked on main."
        )
        result = wq.done(
            "20260724-090000-donor-search", implementation_complete=True, note=note
        )
        self.assertEqual(result["status"], "done")

        # Re-read the brief from disk -- not just the function's return value.
        on_disk = brief_path.read_text(encoding="utf-8")
        fm = wq._read_frontmatter(brief_path)
        self.assertEqual(fm["status"], "done")
        self.assertIn("completed-at", fm)
        self.assertIn("## Closure Note", on_disk)
        self.assertIn("007-donor-search", on_disk)
        self.assertIn("Donor Search", on_disk)


class TestNoCollisionDraftStatusLeavesBriefUntouched(QueueTestBase):
    """AC-005/AC-006 chained with AC-015: the same fixture repo/brief, but the
    candidate's Status is Draft (not Implemented) -> verify() returns
    confirmed: false -> work_queue.done is never invoked with a note and the
    brief remains untouched by any collision-closure logic."""

    def test_draft_status_is_not_confirmed_and_brief_stays_untouched(self):
        repo = _init_repo()
        spec_dir = _make_spec(
            repo,
            "007-donor-search",
            status="Draft",
            feature_summary="Donors can search nonprofits.",
            spec_name="donor-search",
        )
        target = Path(repo) / "src" / "search.py"
        _write(target, "print('not yet shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "wip donor search")
        _make_task(spec_dir, "TASK-001", files=["src/search.py"])

        checked = csc.check(Path(repo))
        self.assertTrue(checked["checked"])
        candidate = next(
            c for c in checked["candidates"] if c["spec_id"] == "007-donor-search"
        )

        verified = csc.verify(Path(repo), candidate["spec_id"])
        self.assertFalse(verified["confirmed"])
        self.assertEqual(verified["status"], "Draft")

        brief_path = _make_picked_brief(
            self.picked, "20260724-090000-donor-search.md", "Add donor search"
        )
        original = brief_path.read_text(encoding="utf-8")

        # Per Phase 5.5: CONFIRMED = False -> no closure call at all. The test
        # asserts that state by never invoking wq.done() here, then confirms
        # the brief on disk is byte-for-byte unchanged.
        self.assertEqual(brief_path.read_text(encoding="utf-8"), original)
        fm = wq._read_frontmatter(brief_path)
        self.assertEqual(fm["status"], "picked")
        self.assertNotIn("completed-at", fm)
        self.assertNotIn("## Closure Note", original)


class TestBrainstormSourcedNoBriefInvariant(unittest.TestCase):
    """AC-016: given the same confirmed verify() result but no brief file in
    play, nothing in check_spec_collision.py or work_queue.py requires or
    assumes a brief exists -- the documented flow never calls
    work_queue.py done in this scenario."""

    def test_confirmed_collision_requires_no_brief_and_no_workqueue_state(self):
        repo = _init_repo()
        spec_dir = _make_spec(
            repo,
            "007-donor-search",
            status="Implemented",
            feature_summary="Donors can search nonprofits.",
            spec_name="donor-search",
        )
        target = Path(repo) / "src" / "search.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship donor search")
        _make_task(spec_dir, "TASK-001", files=["src/search.py"])

        # No WORK_QUEUE_DIR is configured and no queue/picked directories
        # exist anywhere -- proving check()/verify() have no dependency on
        # work-queue state at all.
        with tempfile.TemporaryDirectory() as empty_queue_home:
            os.environ["WORK_QUEUE_DIR"] = str(Path(empty_queue_home) / "does-not-exist-yet")

            checked = csc.check(Path(repo))
            self.assertTrue(checked["checked"])
            candidate = next(
                c for c in checked["candidates"] if c["spec_id"] == "007-donor-search"
            )
            verified = csc.verify(Path(repo), candidate["spec_id"])
            self.assertTrue(verified["confirmed"])

            # The brainstorm-sourced path stops here (AskUserQuestion is
            # interactive prose, out of scope for this script-level test) --
            # the documented flow never calls work_queue.py done. Confirm
            # that omission left no incidental work-queue side effect: no
            # picked/ directory was created anywhere as a byproduct of
            # check()/verify(), and a `done()` call against this empty queue
            # (proving no brief is assumed to exist) degrades gracefully
            # rather than raising.
            self.assertFalse(wq.picked_dir().exists())
            result = wq.done("no-such-brief")
            self.assertEqual(result["status"], "none")

        os.environ.pop("WORK_QUEUE_DIR", None)


class TestDegradedSignalNoSpecsDirectory(unittest.TestCase):
    """AC-001 chained with AC-011/AC-015: check_spec_collision.py pointed at a
    repo with no docs/specs/ directory at all -> checked: false -> no
    downstream work_queue.py done call occurs and no exception propagates."""

    def test_no_docs_specs_dir_degrades_without_raising_or_closing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No docs/specs/ directory anywhere under tmp.
            checked = csc.check(Path(tmp))  # must not raise
            self.assertFalse(checked["checked"])
            self.assertEqual(checked["candidates"], [])

            # "unknown" (checked: false) is "no signal" -- there is no
            # candidate to judge, so verify() and work_queue.done are never
            # reached by the documented flow. Nothing here calls wq.done.


if __name__ == "__main__":
    unittest.main()
