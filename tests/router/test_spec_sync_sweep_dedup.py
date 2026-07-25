#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_dedup.py.

Run directly (from this directory): python3 test_spec_sync_sweep_dedup.py
Or as part of the go skill's suite: python3 -m pytest . -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router.spec_sync_sweep_dedup import find_unresolved_drift_brief


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def brief(repo: str = "", drift_source: str = "", status: str = "") -> str:
    lines = ["---"]
    if repo:
        lines.append(f"repo: {repo}")
    if drift_source:
        lines.append(f"drift-source: {drift_source}")
    if status:
        lines.append(f"status: {status}")
    lines.append("---")
    lines.append("")
    lines.append("## Focus")
    lines.append("")
    lines.append("test brief")
    return "\n".join(lines)


class FindUnresolvedDriftBriefTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue_base = Path(self._tmp.name)
        self.repo = "/home/user/projects/some-repo"

    def test_found_in_queue(self) -> None:
        write(
            self.queue_base / "queue" / "a.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="queued"),
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertEqual(result, self.queue_base / "queue" / "a.md")

    def test_found_in_picked_when_not_done(self) -> None:
        write(
            self.queue_base / "picked" / "b.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="picked"),
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertEqual(result, self.queue_base / "picked" / "b.md")

    def test_not_found_when_picked_is_done(self) -> None:
        write(
            self.queue_base / "picked" / "c.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="done"),
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertIsNone(result)

    def test_not_found_when_no_drift_source(self) -> None:
        write(
            self.queue_base / "queue" / "d.md",
            brief(repo=self.repo, status="queued"),
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertIsNone(result)

    def test_not_found_when_different_repo(self) -> None:
        write(
            self.queue_base / "queue" / "e.md",
            brief(repo="/home/user/projects/other-repo", drift_source="spec-sync-sweep", status="queued"),
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertIsNone(result)

    def test_empty_queue_returns_none(self) -> None:
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertIsNone(result)

    def test_absent_queue_dirs_return_none(self) -> None:
        result = find_unresolved_drift_brief(
            Path(self.repo), self.queue_base / "does-not-exist"
        )
        self.assertIsNone(result)

    def test_malformed_frontmatter_is_skipped(self) -> None:
        write(
            self.queue_base / "queue" / "broken.md",
            "---\nrepo: [unterminated\ndrift-source: spec-sync-sweep\n---\nbody\n",
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertIsNone(result)

    def test_dedup_across_repeated_sweep_runs(self) -> None:
        # Simulates AC-012: running the sweep twice should only see one item.
        write(
            self.queue_base / "queue" / "f.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="queued"),
        )
        first = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        second = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertEqual(first, second)
        self.assertIsNotNone(first)

    def test_new_brief_eligible_after_resolution(self) -> None:
        # AC-013: a previously-filed item that is now done makes the repo
        # eligible for a fresh brief once one is filed.
        write(
            self.queue_base / "picked" / "g.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="done"),
        )
        self.assertIsNone(find_unresolved_drift_brief(Path(self.repo), self.queue_base))
        write(
            self.queue_base / "queue" / "h.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="queued"),
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertEqual(result, self.queue_base / "queue" / "h.md")


class FindUnresolvedDriftBriefWidenedDriftSourceTest(unittest.TestCase):
    """TASK-CHG-002: (repo, drift-source) identity key widening."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue_base = Path(self._tmp.name)
        self.repo = "/home/user/projects/some-repo"

    def test_checkbox_drift_brief_found_with_explicit_drift_source(self) -> None:
        # AC-CHG-009
        write(
            self.queue_base / "queue" / "a.md",
            brief(repo=self.repo, drift_source="checkbox-drift-sweep", status="queued"),
        )
        result = find_unresolved_drift_brief(
            Path(self.repo), self.queue_base, drift_source="checkbox-drift-sweep"
        )
        self.assertEqual(result, self.queue_base / "queue" / "a.md")

    def test_checkbox_drift_brief_not_found_with_default_drift_source(self) -> None:
        # AC-CHG-009: a checkbox-drift brief must not be matched by the
        # default (spec-sync-sweep) query.
        write(
            self.queue_base / "queue" / "a.md",
            brief(repo=self.repo, drift_source="checkbox-drift-sweep", status="queued"),
        )
        result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        self.assertIsNone(result)

    def test_check_types_are_independent_when_both_outstanding(self) -> None:
        # AC-CHG-014: an outstanding brief of one check-type must not
        # suppress, or be returned in place of, the other check-type's.
        write(
            self.queue_base / "queue" / "spec-sync.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="queued"),
        )
        write(
            self.queue_base / "queue" / "checkbox.md",
            brief(repo=self.repo, drift_source="checkbox-drift-sweep", status="queued"),
        )

        spec_sync_result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        checkbox_result = find_unresolved_drift_brief(
            Path(self.repo), self.queue_base, drift_source="checkbox-drift-sweep"
        )

        self.assertEqual(spec_sync_result, self.queue_base / "queue" / "spec-sync.md")
        self.assertEqual(checkbox_result, self.queue_base / "queue" / "checkbox.md")

    def test_resolving_one_check_type_does_not_affect_the_other(self) -> None:
        # AC-CHG-010, AC-CHG-015: a done checkbox-drift brief makes the repo
        # eligible for a new checkbox-drift brief, while an unresolved
        # spec-sync-drift brief for the same repo is unaffected either way.
        write(
            self.queue_base / "picked" / "checkbox.md",
            brief(repo=self.repo, drift_source="checkbox-drift-sweep", status="done"),
        )
        write(
            self.queue_base / "queue" / "spec-sync.md",
            brief(repo=self.repo, drift_source="spec-sync-sweep", status="queued"),
        )

        checkbox_result = find_unresolved_drift_brief(
            Path(self.repo), self.queue_base, drift_source="checkbox-drift-sweep"
        )
        spec_sync_result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)

        self.assertIsNone(checkbox_result)
        self.assertEqual(spec_sync_result, self.queue_base / "queue" / "spec-sync.md")

    def test_unrecognized_drift_source_value_matches_neither_query(self) -> None:
        # Edge case: a malformed/future drift-source value is never matched
        # by either the default or an explicit query — exact match only.
        write(
            self.queue_base / "queue" / "unknown.md",
            brief(repo=self.repo, drift_source="future-check-type", status="queued"),
        )

        default_result = find_unresolved_drift_brief(Path(self.repo), self.queue_base)
        checkbox_result = find_unresolved_drift_brief(
            Path(self.repo), self.queue_base, drift_source="checkbox-drift-sweep"
        )

        self.assertIsNone(default_result)
        self.assertIsNone(checkbox_result)


if __name__ == "__main__":
    unittest.main()
