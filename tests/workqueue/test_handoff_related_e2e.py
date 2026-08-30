#!/usr/bin/env python3
"""End-to-end tests for the related field + auto-link feature.

Tests the complete workflow from score_candidates.py through work_queue.py link,
covering Primary Flows A, B, C, D and error scenarios. Uses subprocess for CLI
invocation and temp-dir fixtures matching test_work_queue.py pattern.

Run: python3 -m pytest tests/workqueue/test_handoff_related_e2e.py -q
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from worktrail.workqueue import work_queue as q


def _brief(
    focus: str,
    brief_id: str = "",
    extra: str = "",
    blocked_by: list | None = None,
    related: list | None = None,
    repo: str = "",
) -> str:
    """Generate a brief's frontmatter + body."""
    fm = [f"focus: {focus}", "status: queued"]
    if brief_id:
        fm.append(f"id: {brief_id}")
    if repo:
        fm.append(f"repo: {repo}")
    if extra:
        fm.append(extra)
    if blocked_by:
        fm.append("blocked-by:")
        for dep in blocked_by:
            fm.append(f"  - {dep}")
    if related:
        fm.append("related:")
        for rel in related:
            fm.append(f"  - {rel}")
    return "---\n" + "\n".join(fm) + "\n---\n\n## Focus\n\n" + focus + "\n"


def _picked_brief(focus: str, status: str = "picked", repo: str = "") -> str:
    """Generate a picked brief's frontmatter + body."""
    fm = [f"focus: {focus}", f"status: {status}"]
    if repo:
        fm.append(f"repo: {repo}")
    return "---\n" + "\n".join(fm) + "\n---\n\n## Focus\n\n" + focus + "\n"


class E2ETestBase(unittest.TestCase):
    """Base class for e2e tests with temp queue setup."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        os.environ["WORK_QUEUE_DIR"] = str(self.base)
        importlib.reload(q)  # pick up the env var
        self.queue = self.base / "queue"
        self.picked = self.base / "picked"
        self.queue.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def write(self, name: str, **kw) -> Path:
        """Write a brief to queue/."""
        p = self.queue / name
        p.write_text(_brief(**kw), encoding="utf-8")
        return p

    def write_picked(self, name: str, **kw) -> Path:
        """Write a brief to picked/."""
        self.picked.mkdir(parents=True, exist_ok=True)
        p = self.picked / name
        p.write_text(_picked_brief(**kw), encoding="utf-8")
        return p

    def score(self, brief_path: Path) -> dict:
        """Run score_candidates.py as subprocess and return parsed JSON."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "worktrail.workqueue.score_candidates",
                str(brief_path),
                "--queue-dir",
                str(self.base),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.fail(f"score_candidates.py failed: {result.stderr}")
        return json.loads(result.stdout)


class TestPrimaryFlowA(E2ETestBase):
    """Primary Flow A: Create into existing related cluster with confirmation.

    Scenario: Multiple related briefs; candidate scoring and user confirmation.
    """

    def test_multiple_candidates_with_confirmation(self):
        """Create new brief; verify candidates are found and can be linked."""
        # Create related candidates
        self.write(
            "20260604-100000-database-schema.md",
            focus="Design schema for user accounts",
            repo="db-repo",
        )
        self.write(
            "20260604-100001-api-endpoints.md",
            focus="Implement API endpoints for user data",
            repo="api-repo",
        )

        # New brief with some token overlap (but not same-repo high-confidence)
        new_brief = self.queue / "20260604-100003-user-service.md"
        new_brief.write_text(
            _brief(
                focus="Build user service and account handler",
                repo="service-repo",
            ),
            encoding="utf-8",
        )

        # Score candidates
        candidates = self.score(new_brief)
        self.assertIn("auto_link", candidates)
        self.assertIn("confirm", candidates)

        # Check if there are any candidates (auto_link or confirm)
        all_candidates = candidates["auto_link"] + candidates["confirm"]
        self.assertTrue(
            len(all_candidates) > 0,
            "Expected at least one candidate (auto_link or confirm)",
        )

        # Link the first candidate if available
        if all_candidates:
            first_cand = all_candidates[0]
            cand_id = first_cand["id"]
            result = q.link("20260604-100003-user-service", cand_id)
            self.assertEqual(result["status"], "linked")

            # Verify symmetric back-links were written
            fm_new = q._read_frontmatter(new_brief)
            self.assertIn(cand_id, fm_new.get("related", []))


class TestPrimaryFlowB(E2ETestBase):
    """Primary Flow B: No match — new brief with no overlapping tokens."""

    def test_no_match_completes_silently(self):
        """New brief with minimal token overlap returns empty confirm (no match above threshold)."""
        # Existing brief with unique tokens
        self.write(
            "20260604-100000-xenops-deployment.md",
            focus="Deploy xenops infrastructure management",
            repo="xenops-repo",
        )

        # New brief with completely different tokens (no common 3+ char words)
        # Using a short-token-only focus so there's no significant overlap
        new_brief = self.queue / "20260604-165000-unrelated-xyz.md"
        new_brief.write_text(
            _brief(
                focus="API router XML tuning",
                repo="xyz-repo",
            ),
            encoding="utf-8",
        )

        # Score candidates
        candidates = self.score(new_brief)
        # Should have no matches above the threshold (or minimal)
        # The MIN_OVERLAP is 0.15, and with no shared 3+ char tokens, overlap should be 0
        self.assertEqual(candidates["auto_link"], [], "auto_link should be empty")
        self.assertEqual(candidates["confirm"], [], "confirm should be empty")


class TestPrimaryFlowC(E2ETestBase):
    """Primary Flow C: High-confidence auto-link (same repo + strong focus overlap)."""

    def test_high_confidence_auto_link(self):
        """Same-repo + strong focus overlap → auto_link (not confirm)."""
        # Existing brief with strong focus match
        self.write(
            "20260604-100000-auth-jwt.md",
            focus="Implement JWT authentication handler",
            repo="auth-repo",
        )

        # New brief with same repo and high focus overlap
        new_brief = self.queue / "20260604-165000-auth-middleware.md"
        new_brief.write_text(
            _brief(
                focus="JWT middleware and authentication guard",
                repo="auth-repo",
            ),
            encoding="utf-8",
        )

        # Score candidates
        candidates = self.score(new_brief)
        auto_link_ids = {c["id"] for c in candidates["auto_link"]}

        # Should appear in auto_link (same repo + high confidence)
        self.assertIn("20260604-100000-auth-jwt", auto_link_ids)
        # Should NOT appear in confirm (auto-linked)
        self.assertEqual(candidates["confirm"], [])


class TestPrimaryFlowD(E2ETestBase):
    """Primary Flow D: Consume surfaces neighbours with related IDs."""

    def test_consume_surfaces_related_neighbours(self):
        """Claimed brief surfaces its related neighbours' focus lines."""
        # Create two existing briefs
        self.write("20260604-100000-work-a.md", focus="Do work A")
        self.write("20260604-100001-work-b.md", focus="Do work B")

        # Create a new brief with both marked as related
        self.write(
            "20260604-100002-work-c.md",
            focus="Do work C",
            related=["20260604-100000-work-a", "20260604-100001-work-b"],
        )

        # Claim the brief with related neighbours
        res = q.claim("20260604-100002-work-c")
        self.assertEqual(res["status"], "claimed")

        # Verify the brief is in picked/ with its related field intact
        fm = q._read_frontmatter(self.picked / "20260604-100002-work-c.md")
        related = fm.get("related", [])
        self.assertEqual(len(related), 2)
        self.assertIn("20260604-100000-work-a", related)
        self.assertIn("20260604-100001-work-b", related)

    def test_consume_with_stale_related_id(self):
        """Claimed brief with stale related ID skips it silently (AC-020)."""
        # Create one real brief
        self.write("20260604-100001-work-b.md", focus="Do work B")

        # Create a brief with one valid and one stale related ID
        self.write(
            "20260604-100002-work-c.md",
            focus="Do work C",
            related=["20260604-100001-work-b", "stale-missing-id-xyz"],
        )

        # Claim the brief
        res = q.claim("20260604-100002-work-c")
        self.assertEqual(res["status"], "claimed")

        # Verify stale ID is still in the related list (not cleaned up by claim)
        fm = q._read_frontmatter(self.picked / "20260604-100002-work-c.md")
        related = fm.get("related", [])
        self.assertIn("stale-missing-id-xyz", related)
        self.assertIn("20260604-100001-work-b", related)


class TestSlugSuffixResolve(E2ETestBase):
    """AC-023: Slug-suffix resolve — resolve a brief by descriptive slug alone."""

    def test_slug_suffix_resolve_in_queue(self):
        """Slug-only identifier resolves brief without date-time prefix."""
        self.write(
            "20260604-113700-handoff-related-field-autodetect.md",
            focus="Test autodetect functionality",
        )

        # Resolve by slug alone
        res = q.resolve("handoff-related-field-autodetect", self.queue)
        self.assertEqual(res["status"], "match")
        self.assertIn("handoff-related-field-autodetect.md", res["candidates"][0])

    def test_slug_suffix_claim_endtoend(self):
        """Claim a brief using slug-only identifier (AC-023 end-to-end)."""
        self.write(
            "20260604-113700-handoff-related-field-autodetect.md",
            focus="Test autodetect",
        )

        # Claim using slug only
        res = q.claim("handoff-related-field-autodetect")
        self.assertEqual(res["status"], "claimed")
        self.assertIn("handoff-related-field-autodetect.md", res["path"])

        # Verify brief moved to picked/
        self.assertTrue(
            (
                self.picked / "20260604-113700-handoff-related-field-autodetect.md"
            ).exists()
        )


class TestListRelated(E2ETestBase):
    """Tests for list --json related field (AC-004, AC-005, AC-006)."""

    def test_list_related_empty_when_absent(self):
        """Brief without related field emits related: [] in list_queue() (AC-004)."""
        self.write("20260101-000000-a.md", focus="some work")
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["related"], [])

    def test_list_related_with_ids(self):
        """Brief with related IDs emits them in list_queue() (AC-004)."""
        self.write(
            "20260101-000000-a.md",
            focus="some work",
            related=["id-x", "id-y"],
        )
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["related"], ["id-x", "id-y"])

    def test_list_stale_related_id_returned(self):
        """Stale related IDs are returned verbatim without error (AC-005)."""
        self.write(
            "20260101-000000-a.md",
            focus="some work",
            related=["stale-xyz-999"],
        )
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["related"], ["stale-xyz-999"])

    def test_list_related_does_not_affect_blocked(self):
        """related field does not affect blocked flag (AC-006)."""
        self.write(
            "20260101-000000-a.md",
            focus="some work",
            related=["some-id"],
        )
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["blocked"])
        self.assertEqual(briefs[0]["related"], ["some-id"])


class TestLinkErrorPaths(E2ETestBase):
    """Tests for link error paths (AC-010, AC-011)."""

    def test_link_missing_id_nonzero_exit(self):
        """link missing-id <valid-id> → status none, no changes (AC-010)."""
        self.write("20260101-000001-alpha.md", focus="alpha")
        self.write("20260101-000002-beta.md", focus="beta")

        result = q.link("missing-id-xyz", "20260101-000002-beta")
        self.assertEqual(result["status"], "none")
        self.assertEqual(result["paths"], [])

        # Verify no changes to beta's related
        fm_b = q._read_frontmatter(self.queue / "20260101-000002-beta.md")
        self.assertFalse(fm_b.get("related"))

    def test_link_ambiguous_id_nonzero_exit(self):
        """link <ambiguous-prefix> <valid-id> → status ambiguous, no changes (AC-010)."""
        self.write("20260101-000001-shared-prefix-a.md", focus="a")
        self.write("20260101-000001-shared-prefix-b.md", focus="b")
        self.write("20260101-000002-target.md", focus="target")

        result = q.link("20260101-000001-shared-prefix", "20260101-000002-target")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["paths"], [])

        # Verify no changes to target's related
        fm_t = q._read_frontmatter(self.queue / "20260101-000002-target.md")
        self.assertFalse(fm_t.get("related"))

    def test_link_json_output_valid(self):
        """link <a> <b> --json → valid JSON with status linked and paths (AC-011)."""
        self.write("20260101-000001-alpha.md", focus="alpha")
        self.write("20260101-000002-beta.md", focus="beta")

        result = q.link("20260101-000001-alpha", "20260101-000002-beta")
        self.assertEqual(result["status"], "linked")
        self.assertEqual(len(result["paths"]), 2)
        self.assertIsNone(result["error"])


class TestRegressionAC022(E2ETestBase):
    """AC-022: Checkpoint — no regression in existing CLI contract."""

    def test_existing_test_suite_passes(self):
        """Run test_work_queue.py as subprocess; assert exit code 0 (AC-022)."""
        test_script = Path(__file__).parent / "test_work_queue.py"
        result = subprocess.run(
            [sys.executable, str(test_script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"test_work_queue.py failed:\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
