#!/usr/bin/env python3
"""
End-to-end test for the handoff-seed feature across BOTH scripts that cooperate:

  work_queue.py  (handoff skill)  -- the single owner: list / resolve / claim / done
  handoff_seed.py (go)            -- seed-mapping over the claimed brief path

This exercises the real integration contract `sdd-workflow handoff` relies on:
  list (newest-first) -> claim (atomic queue/ -> picked/) -> seed -> done.

Two folders only: $WORK_QUEUE_DIR/{queue,picked}; "done" is a frontmatter status
within picked/. Run: python3 scripts/test_handoff_seed_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from worktrail.router import handoff_seed as hs

_BRIEF_A = """\
---
id: 20260531-180000-teardown-cleanup
created: 2026-05-31T18:00:00-05:00
focus: Teardown leaves orphan worktrees
repo: /home/briank/projects/developer-kit
remote: https://github.com/behindthedash/developer-kit
base-branch: main
status: queued
suggested-skills:
  - specs-sdd-workflow
---

## Focus

The sdd-workflow conductor's teardown step leaves orphan git worktrees when interrupted.

## Discovery context

- Found during spec 002 cleanup; `git worktree list` shows stale entries.

## Suggested approach

1. Add a prune step to the teardown section of the sdd-workflow SKILL.md.

## Key artifacts

| Artifact | Location |
|---|---|
| Teardown section | plugins/developer-kit-specs/skills/specs-sdd-workflow/SKILL.md |

## Open questions / blockers

- Does `git worktree prune` need the --expire flag to be safe?

## Suggested skills

- `specs-sdd-workflow` -- the sdd-workflow conductor skill itself.
"""

_BRIEF_B = _BRIEF_A.replace("20260531-180000-teardown-cleanup", "20260530-141200-auth-middleware").replace(
    "Teardown leaves orphan worktrees", "Auth middleware swallows errors"
)


def _write(path: Path, content: str, mtime: float | None = None) -> Path:
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _wq(args: list[str], base: Path) -> tuple[int, Any]:
    """Run work_queue.py with WORK_QUEUE_DIR=base; return (rc, parsed-json-or-stdout)."""
    env = {**os.environ, "WORK_QUEUE_DIR": str(base)}
    r = subprocess.run(
        [sys.executable, "-m", "worktrail.workqueue.work_queue"] + args,
        capture_output=True, text=True, env=env
    )
    out = r.stdout.strip()
    try:
        return r.returncode, json.loads(out)
    except json.JSONDecodeError:
        return r.returncode, out


def _seed_cli(path: str) -> tuple[int, dict]:
    r = subprocess.run(
        [sys.executable, "-m", "worktrail.router.handoff_seed", "--json", "seed", path],
        capture_output=True, text=True
    )
    return r.returncode, json.loads(r.stdout)


class E2EBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.queue = self.base / "queue"
        self.picked = self.base / "picked"
        self.queue.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()


class TestListClaimSeed(E2EBase):
    def test_full_flow_list_claim_seed_done(self):
        _write(self.queue / "20260530-141200-auth-middleware.md", _BRIEF_B, mtime=1_000.0)
        _write(self.queue / "20260531-180000-teardown-cleanup.md", _BRIEF_A, mtime=2_000.0)

        # list newest-first
        rc, listing = _wq(["list", "--json"], self.base)
        self.assertEqual(rc, 0)
        self.assertEqual(listing["briefs"][0]["filename"], "20260531-180000-teardown-cleanup.md")
        self.assertIn("Teardown leaves orphan worktrees", listing["briefs"][0]["focus"])

        # claim the newest (atomic queue -> picked)
        rc, claim = _wq(["claim", "20260531-180000", "--json"], self.base)
        self.assertEqual(rc, 0)
        self.assertEqual(claim["status"], "claimed")
        picked_path = claim["path"]
        self.assertTrue(Path(picked_path).exists())
        self.assertFalse((self.queue / "20260531-180000-teardown-cleanup.md").exists())

        # seed from the claimed path
        rc, seed = _seed_cli(picked_path)
        self.assertEqual(rc, 0)
        self.assertIsNone(seed["error"])
        self.assertIn("Teardown leaves orphan worktrees", seed["feature_idea"])
        self.assertIn("Add a prune step", seed["feature_idea"])
        self.assertIn("stale entries", seed["constraints"])
        self.assertEqual(seed["repo"], "/home/briank/projects/developer-kit")
        self.assertIn("specs-sdd-workflow", seed["suggested_skills"])

        # done -> stamped in picked/, file stays
        rc, dn = _wq(["done", "20260531-180000", "--json"], self.base)
        self.assertEqual(rc, 0)
        self.assertEqual(dn["status"], "done")
        self.assertTrue(Path(picked_path).exists())

    def test_seed_field_mapping_from_claimed_brief(self):
        _write(self.queue / "20260531-180000-teardown-cleanup.md", _BRIEF_A)
        rc, claim = _wq(["claim", "20260531-180000-teardown-cleanup.md", "--json"], self.base)
        self.assertEqual(claim["status"], "claimed")
        seed = hs.build_seed(Path(claim["path"]))
        self.assertIn("orphan worktrees", seed["feature_idea"])
        self.assertEqual(seed["base_branch"], "main")


class TestSelectionBranches(E2EBase):
    def test_empty_queue_lists_nothing(self):
        rc, listing = _wq(["list", "--json"], self.base)
        self.assertEqual(rc, 0)
        self.assertEqual(listing["briefs"], [])

    def test_missing_queue_dir_is_empty(self):
        # remove the queue dir entirely
        self.queue.rmdir()
        rc, listing = _wq(["list", "--json"], self.base)
        self.assertEqual(rc, 0)
        self.assertEqual(listing["briefs"], [])

    def test_unrecognised_id_claim_is_none(self):
        _write(self.queue / "20260531-180000-teardown-cleanup.md", _BRIEF_A)
        rc, claim = _wq(["claim", "nope-not-here", "--json"], self.base)
        self.assertEqual(rc, 2)  # claim exit code for none
        self.assertEqual(claim["status"], "none")

    def test_ambiguous_prefix_claim_is_ambiguous(self):
        _write(self.queue / "20260531-180000-teardown-cleanup.md", _BRIEF_A, mtime=2_000.0)
        _write(self.queue / "20260531-183000-another.md", _BRIEF_B, mtime=1_000.0)
        rc, claim = _wq(["claim", "20260531", "--json"], self.base)
        self.assertEqual(rc, 3)  # ambiguous
        self.assertEqual(claim["status"], "ambiguous")
        self.assertEqual(len(claim["candidates"]), 2)


class TestLifecycle(E2EBase):
    def test_claim_moves_out_of_queue_into_picked(self):
        _write(self.queue / "20260531-180000-teardown-cleanup.md", _BRIEF_A)
        original = (self.queue / "20260531-180000-teardown-cleanup.md").read_bytes()
        _wq(["claim", "20260531-180000", "--json"], self.base)
        moved = self.picked / "20260531-180000-teardown-cleanup.md"
        self.assertTrue(moved.exists())
        self.assertEqual(len(list(self.queue.glob("*.md"))), 0)
        # body bytes preserved (only frontmatter status/claimed-* fields change)
        self.assertIn(b"## Focus", moved.read_bytes())
        self.assertIn(b"orphan git worktrees", moved.read_bytes())
        self.assertIn(b"status: picked", moved.read_bytes())
        self.assertNotEqual(moved.read_bytes(), original)  # stamped

    def test_second_claim_cannot_double_pick(self):
        """The whole point: once claimed, another agent cannot claim the same brief."""
        _write(self.queue / "20260531-180000-teardown-cleanup.md", _BRIEF_A)
        rc1, c1 = _wq(["claim", "20260531-180000", "--json"], self.base)
        rc2, c2 = _wq(["claim", "20260531-180000", "--json"], self.base)
        self.assertEqual(c1["status"], "claimed")
        self.assertEqual(rc1, 0)
        self.assertIn(c2["status"], ("none", "already-claimed"))  # not claimable a second time
        self.assertNotEqual(rc2, 0)

    def test_release_returns_to_queue(self):
        _write(self.queue / "20260531-180000-teardown-cleanup.md", _BRIEF_A)
        _wq(["claim", "20260531-180000", "--json"], self.base)
        rc, rel = _wq(["release", "20260531-180000", "--json"], self.base)
        self.assertEqual(rc, 0)
        self.assertEqual(rel["status"], "released")
        self.assertTrue((self.queue / "20260531-180000-teardown-cleanup.md").exists())


# ---------------------------------------------------------------------------
# AC-018 [EXT] — External Verification (manual, live Claude Code session).
# Not an automated test (a assertTrue(True) placeholder only inflated the pass
# count). Manual checklist:
#   1. Put at least one brief in $WORK_QUEUE_DIR/queue/ (default ~/work-queue/queue/)
#      via /handoff, or copy a fixture there.
#   2. Launch a session and run:  /specs-sdd-workflow handoff
#   3. Verify:
#      a. Briefs listed newest-first; selection prompted (or auto-confirm for one).
#      b. After selection, the brief is CLAIMED: moved queue/ -> picked/ and stamped
#         status: picked (so a concurrent session can no longer pick it).
#      c. The `new` pipeline runs using the brief's feature_idea/constraints seed.
#      d. Work happens on a dedicated per-spec worktree; base checkout untouched.
#      e. On completion, the brief is stamped status: done (stays in picked/).
#      f. Brief body bytes unchanged (only frontmatter status/claimed-* fields differ).
#   PASS: all of (a)-(f) observed without error.
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main(verbosity=2)
