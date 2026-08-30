#!/usr/bin/env python3
"""Unit tests for handoff_seed -- seed-mapping over a single brief file.

Queue listing / identifier resolution / claim lifecycle live in the handoff
skill's work_queue.py (and test_work_queue.py); handoff_seed is seed-only.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Allow running from the scripts/ directory or from the repo root
from worktrail.router import handoff_seed as hs

# ---------------------------------------------------------------------------
# Sample brief fixtures
# ---------------------------------------------------------------------------

_BRIEF_FULL = """\
---
id: 20260531-141200-auth-middleware-error-handling
created: 2026-05-31T14:12:00-05:00
focus: Surface the real failure reason in the auth middleware instead of swallowing it
repo: /home/briank/projects/acme-api
remote: https://github.com/acme/acme-api
base-branch: main
status: queued
suggested-skills:
  - devkit.fix-debugging
---

## Focus

The auth middleware catches every token-validation error and returns 401 with no logging,
so expired vs. malformed vs. revoked tokens are indistinguishable in production.

## Discovery context

- Found while implementing /reports (PR #214) -- a known-valid token returned 401.
- src/middleware/auth.ts:42 wraps verifyToken() in a try/catch that discards err.

## Suggested approach

1. Branch on the jwt error name (TokenExpiredError vs. JsonWebTokenError) and log it.
2. Add a regression test per error class in test/auth.middleware.test.ts.

## Key artifacts

| Artifact | Location |
|---|---|
| Offending code | src/middleware/auth.ts:42 |

## Open questions / blockers

- **Needs investigation** -- is the clock-skew hypothesis real? Check server NTP.

## Suggested skills

- `devkit.fix-debugging` -- systematic root-cause for the swallowed error.
"""

_BRIEF_NULL_REPO = _BRIEF_FULL.replace(
    "repo: /home/briank/projects/acme-api", "repo: null"
)

_BRIEF_NO_FOCUS = """\
---
id: no-focus-brief
status: queued
---

## Discovery context

Some context about the problem.
"""

_BRIEF_NO_FRONTMATTER = """\
## Focus

A feature idea with no frontmatter at all.

## Suggested approach

Do the thing.
"""


def _write(path: Path, content: str = _BRIEF_FULL, mtime: float | None = None) -> Path:
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# ---------------------------------------------------------------------------
# Seed mapping
# ---------------------------------------------------------------------------


class TestBuildSeed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.brief = (
            Path(self._tmp.name) / "20260531-141200-auth-middleware-error-handling.md"
        )
        _write(self.brief)

    def tearDown(self):
        self._tmp.cleanup()

    def test_feature_idea_contains_focus_and_suggested_approach(self):
        seed = hs.build_seed(self.brief)
        self.assertIsNone(seed["error"])
        self.assertIn("Surface the real failure reason", seed["feature_idea"])
        self.assertIn("Branch on the jwt error name", seed["feature_idea"])

    def test_constraints_contains_discovery_context(self):
        seed = hs.build_seed(self.brief)
        self.assertIn("PR #214", seed["constraints"])

    def test_constraints_contains_key_artifacts(self):
        seed = hs.build_seed(self.brief)
        self.assertIn("src/middleware/auth.ts:42", seed["constraints"])

    def test_constraints_contains_open_questions(self):
        seed = hs.build_seed(self.brief)
        self.assertIn("clock-skew", seed["constraints"])

    def test_repo_hint_from_frontmatter(self):
        seed = hs.build_seed(self.brief)
        self.assertEqual(seed["repo"], "/home/briank/projects/acme-api")

    def test_base_branch_from_frontmatter(self):
        seed = hs.build_seed(self.brief)
        self.assertEqual(seed["base_branch"], "main")

    def test_null_repo_preserved_as_none(self):
        p = Path(self._tmp.name) / "null-repo.md"
        _write(p, content=_BRIEF_NULL_REPO)
        seed = hs.build_seed(p)
        self.assertIsNone(seed["repo"])
        self.assertIsNone(seed["error"])

    def test_focus_exposed_for_display(self):
        seed = hs.build_seed(self.brief)
        self.assertIn("Surface the real failure reason", seed["focus"])

    def test_suggested_skills_exposed_for_display(self):
        seed = hs.build_seed(self.brief)
        self.assertIn("devkit.fix-debugging", seed["suggested_skills"])

    def test_missing_focus_does_not_crash(self):
        p = Path(self._tmp.name) / "no-focus.md"
        _write(p, content=_BRIEF_NO_FOCUS)
        seed = hs.build_seed(p)
        self.assertIsNone(seed["error"])
        self.assertEqual(seed["focus"], "")
        self.assertIn("Some context about the problem", seed["constraints"])

    def test_missing_frontmatter_does_not_crash(self):
        p = Path(self._tmp.name) / "no-fm.md"
        _write(p, content=_BRIEF_NO_FRONTMATTER)
        seed = hs.build_seed(p)
        self.assertIsNone(seed["error"])
        self.assertIn("A feature idea with no frontmatter", seed["feature_idea"])
        self.assertIsNone(seed["repo"])
        self.assertEqual(seed["suggested_skills"], [])
        self.assertIsNone(seed["recommended_route"])

    def test_closing_frontmatter_without_trailing_newline_still_parses(self):
        p = Path(self._tmp.name) / "no-trailing-newline.md"
        _write(p, content="---\nfocus: Trim parser duplication\n---")
        seed = hs.build_seed(p)
        self.assertIsNone(seed["error"])
        self.assertEqual(seed["focus"], "Trim parser duplication")

    def test_recommended_route_normalized(self):
        p = Path(self._tmp.name) / "route.md"
        _write(
            p,
            content=_BRIEF_FULL.replace(
                "status: queued", "status: queued\nrecommended-route: f"
            ),
        )
        self.assertEqual(hs.build_seed(p)["recommended_route"], "F")

    def test_recommended_route_invalid_or_absent_is_none(self):
        seed = hs.build_seed(self.brief)  # _BRIEF_FULL has no recommended-route
        self.assertIsNone(seed["recommended_route"])
        p = Path(self._tmp.name) / "bad-route.md"
        _write(
            p,
            content=_BRIEF_FULL.replace(
                "status: queued", "status: queued\nrecommended-route: Z"
            ),
        )
        self.assertIsNone(hs.build_seed(p)["recommended_route"])

    def test_implementation_intent_defaults_to_unknown_and_normalizes(self):
        self.assertEqual(hs.build_seed(self.brief)["implementation_intent"], "unknown")
        p = Path(self._tmp.name) / "intent.md"
        _write(
            p,
            content=_BRIEF_FULL.replace(
                "status: queued", "status: queued\nimplementation-intent: requested"
            ),
        )
        self.assertEqual(hs.build_seed(p)["implementation_intent"], "requested")

    def test_invalid_implementation_intent_defaults_to_unknown(self):
        p = Path(self._tmp.name) / "bad-intent.md"
        _write(
            p,
            content=_BRIEF_FULL.replace(
                "status: queued", "status: queued\nimplementation-intent: maybe"
            ),
        )
        self.assertEqual(hs.build_seed(p)["implementation_intent"], "unknown")

    def test_change_kind_and_target_spec_hints(self):
        p = Path(self._tmp.name) / "change-hints.md"
        _write(
            p,
            content=_BRIEF_FULL.replace(
                "status: queued",
                "status: queued\nchange-kind: bugfix\ntarget-spec: 003-handoff-go-input",
            ),
        )
        seed = hs.build_seed(p)
        self.assertEqual(seed["change_kind"], "bugfix")
        self.assertEqual(seed["target_spec"], "003-handoff-go-input")

    def test_invalid_change_kind_is_none(self):
        p = Path(self._tmp.name) / "bad-change-kind.md"
        _write(
            p,
            content=_BRIEF_FULL.replace(
                "status: queued", "status: queued\nchange-kind: rewrite"
            ),
        )
        self.assertIsNone(hs.build_seed(p)["change_kind"])


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


class TestSafety(unittest.TestCase):
    def test_uses_yaml_safe_load_not_yaml_load(self):
        """Verify safe_load is used (architecture §3.8 requirement)."""
        import inspect

        source = inspect.getsource(hs)
        self.assertIn("yaml.safe_load", source)
        # Must not contain bare yaml.load( call
        self.assertNotIn("yaml.load(", source)

    @unittest.skipIf(os.getuid() == 0, "root can read any file; skip chmod test")
    def test_unreadable_file_returns_error_in_parse_brief(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "unreadable.md"
            _write(p)
            p.chmod(0o000)
            try:
                result = hs.parse_brief(p)
                self.assertIsNotNone(result["error"])
                self.assertEqual(result["frontmatter"], {})
                self.assertEqual(result["sections"], {})
            finally:
                p.chmod(0o644)

    @unittest.skipIf(os.getuid() == 0, "root can read any file; skip chmod test")
    def test_unreadable_file_surfaces_error_in_build_seed(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "unreadable.md"
            _write(p)
            p.chmod(0o000)
            try:
                seed = hs.build_seed(p)
                self.assertIsNotNone(seed["error"])
                self.assertEqual(seed["feature_idea"], "")
                self.assertIsNone(seed["repo"])
            finally:
                p.chmod(0o644)


# ---------------------------------------------------------------------------
# CLI flag-position resilience
# ---------------------------------------------------------------------------


class TestCLIJsonFlag(unittest.TestCase):
    """--json must be accepted before OR after the subcommand (AC: resilient flag position)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.brief = Path(self._tmp.name) / "brief.md"
        _write(self.brief)

    def tearDown(self):
        self._tmp.cleanup()

    def test_json_flag_before_subcommand(self):
        """handoff_seed.py --json seed PATH  (classic top-level flag position)."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = hs.main(["--json", "seed", str(self.brief)])
        self.assertEqual(rc, 0)
        import json

        result = json.loads(buf.getvalue())
        self.assertIsNone(result["error"])
        self.assertIn("Surface the real failure reason", result["feature_idea"])

    def test_json_flag_after_path(self):
        """handoff_seed.py seed PATH --json  (flag after positional — was broken before fix)."""
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = hs.main(["seed", str(self.brief), "--json"])
        self.assertEqual(rc, 0)
        import json

        result = json.loads(buf.getvalue())
        self.assertIsNone(result["error"])
        self.assertIn("Surface the real failure reason", result["feature_idea"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
