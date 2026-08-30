#!/usr/bin/env python3
"""Unit tests for the plugin-cache freshness guard (stdlib unittest)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router import check_cache_freshness as ccf


def _mkgit(path: Path) -> None:
    (path / ".git").mkdir(parents=True, exist_ok=True)


def _fake_git_head(shas_by_repo):
    """Patch target for `_git_head_short`: map repo Path -> sha string|None."""

    def _fake(repo: Path):
        return shas_by_repo.get(Path(repo))

    return _fake


class TestResolvedSha(unittest.TestCase):
    def test_cache_path_extracts_embedded_sha_no_git_call(self):
        p = Path(
            "/home/u/.claude/plugins/cache/developer-kit/developer-kit-project-management/"
            "b821b3ce039c/skills/devkit-pm-go"
        )
        with patch.object(
            ccf, "_git_head_short", side_effect=AssertionError("should not call git")
        ):
            self.assertEqual(ccf.resolved_sha(p), "b821b3ce039c")

    def test_git_checkout_path_falls_back_to_head(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            marketplace = root / "marketplaces" / "developer-kit"
            skill = (
                marketplace
                / "plugins"
                / "developer-kit-project-management"
                / "skills"
                / "go"
            )
            skill.mkdir(parents=True)
            _mkgit(marketplace)
            with patch.object(
                ccf,
                "_git_head_short",
                side_effect=_fake_git_head({marketplace: "abc123def456"}),
            ):
                self.assertEqual(ccf.resolved_sha(skill), "abc123def456")

    def test_unrecognized_path_returns_none(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(
                ccf.resolved_sha(Path(t) / "not-a-repo" / "skills" / "go")
            )


class TestCheck(unittest.TestCase):
    def test_no_canonical_checkout_is_fresh(self):
        with tempfile.TemporaryDirectory() as t:
            missing = Path(t) / "does-not-exist"
            res = ccf.check(
                "/anything/skills/devkit-pm-go",
                "developer-kit-project-management",
                str(missing),
            )
            self.assertTrue(res["fresh"])
            self.assertIsNone(res["warning"])

    def test_matching_sha_is_fresh(self):
        with tempfile.TemporaryDirectory() as t:
            canonical = Path(t) / "developer-kit"
            _mkgit(canonical)
            resolved = (
                Path(t)
                / "cache"
                / "developer-kit"
                / "developer-kit-project-management"
                / "b821b3ce039c"
                / "skills"
                / "go"
            )
            with patch.object(
                ccf,
                "_git_head_short",
                side_effect=_fake_git_head({canonical: "b821b3ce039c"}),
            ):
                res = ccf.check(
                    str(resolved), "developer-kit-project-management", str(canonical)
                )
            self.assertTrue(res["fresh"])
            self.assertEqual(res["resolved_sha"], "b821b3ce039c")
            self.assertEqual(res["canonical_sha"], "b821b3ce039c")

    def test_mismatched_sha_is_stale_and_offers_prefer_path_when_it_exists(self):
        with tempfile.TemporaryDirectory() as t:
            canonical = Path(t) / "developer-kit"
            _mkgit(canonical)
            fresh_skill_dir = (
                canonical
                / "plugins"
                / "developer-kit-project-management"
                / "skills"
                / "go"
            )
            fresh_skill_dir.mkdir(parents=True)
            resolved = (
                Path(t)
                / "cache"
                / "developer-kit"
                / "developer-kit-project-management"
                / "c063e17f4378"
                / "skills"
                / "go"
            )
            with patch.object(
                ccf,
                "_git_head_short",
                side_effect=_fake_git_head({canonical: "2f463d5204d5"}),
            ):
                res = ccf.check(
                    str(resolved), "developer-kit-project-management", str(canonical)
                )
            self.assertFalse(res["fresh"])
            self.assertEqual(res["resolved_sha"], "c063e17f4378")
            self.assertEqual(res["canonical_sha"], "2f463d5204d5")
            self.assertEqual(res["prefer_path"], str(fresh_skill_dir))
            self.assertIn("stale plugin snapshot", str(res["warning"]))

    def test_stale_but_canonical_missing_skill_dir_offers_no_prefer_path(self):
        with tempfile.TemporaryDirectory() as t:
            canonical = Path(t) / "developer-kit"
            _mkgit(canonical)  # no plugins/ subtree at all
            resolved = (
                Path(t)
                / "cache"
                / "developer-kit"
                / "developer-kit-project-management"
                / "c063e17f4378"
                / "skills"
                / "go"
            )
            with patch.object(
                ccf,
                "_git_head_short",
                side_effect=_fake_git_head({canonical: "2f463d5204d5"}),
            ):
                res = ccf.check(
                    str(resolved), "developer-kit-project-management", str(canonical)
                )
            self.assertFalse(res["fresh"])
            self.assertIsNone(res["prefer_path"])

    def test_unresolvable_resolved_path_is_fresh_no_op(self):
        # resolved path has no embedded sha and isn't inside a git checkout --
        # nothing to compare, so treat as fresh rather than false-flagging.
        with tempfile.TemporaryDirectory() as t:
            canonical = Path(t) / "developer-kit"
            _mkgit(canonical)
            with patch.object(
                ccf,
                "_git_head_short",
                side_effect=_fake_git_head({canonical: "2f463d5204d5"}),
            ):
                res = ccf.check(
                    str(Path(t) / "weird" / "path"),
                    "developer-kit-project-management",
                    str(canonical),
                )
            self.assertTrue(res["fresh"])
            self.assertIsNone(res["resolved_sha"])


class TestCli(unittest.TestCase):
    def test_json_output_matches_check_result(self):
        import io
        import json
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as t:
            missing = Path(t) / "does-not-exist"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ccf.main(
                    [
                        "--resolved",
                        "/anything/skills/devkit-pm-go",
                        "--plugin",
                        "developer-kit-project-management",
                        "--canonical",
                        str(missing),
                        "--json",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(
                json.loads(buf.getvalue()),
                ccf.check(
                    "/anything/skills/devkit-pm-go",
                    "developer-kit-project-management",
                    str(missing),
                ),
            )


if __name__ == "__main__":
    unittest.main()
