#!/usr/bin/env python3
"""Tests for dist_tag_watch.py -- the dist-tag watcher.

Run: python3 -m pytest tests/workqueue/test_dist_tag_watch.py -q
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import patch

from worktrail.workqueue import dist_tag_watch as w


def _brief(
    watch: list[str] | None = None,
    next_check_after: str | None = None,
    extra: str = "",
) -> str:
    fm = ["status: queued"]
    if next_check_after:
        fm.append(f"next-check-after: {next_check_after}")
    if watch is not None:
        fm.append("watch:")
        for entry in watch:
            fm.append(f"  - {entry}")
    if extra:
        fm.append(extra)
    return "---\n" + "\n".join(fm) + "\n---\n\n## Focus\n\nsome work\n"


_GITHUB_API_RE = re.compile(
    r"https://api\.github\.com/repos/([^/]+)/([^/]+)/issues/(\d+)"
)


class _FakeFetch:
    """Injectable fetch stub -- returns canned registry JSON keyed by package
    name, canned GitHub issue-state JSON keyed by "owner/repo#number", or
    raises for keys listed in `errors`/`github_errors`. Never touches the
    network."""

    def __init__(
        self,
        versions: dict[str, str] | None = None,
        errors: Iterable[str] | None = None,
        github_states: dict[str, str] | None = None,
        github_errors: Iterable[str] | None = None,
    ):
        self.versions = dict(versions or {})
        self.errors = set(errors or ())
        self.github_states = dict(github_states or {})
        self.github_errors = set(github_errors or ())
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        gh_match = _GITHUB_API_RE.match(url)
        if gh_match:
            key = f"{gh_match.group(1)}/{gh_match.group(2)}#{gh_match.group(3)}"
            if key in self.github_errors:
                raise RuntimeError(f"simulated network error for {key}")
            if key in self.github_states:
                return json.dumps({"state": self.github_states[key]})
            raise AssertionError(f"unexpected fetch url with no stub configured: {url}")
        for pkg in self.errors:
            if f"/{pkg}/" in url:
                raise RuntimeError(f"simulated network error for {pkg}")
        for pkg, version in self.versions.items():
            if f"/{pkg}/" in url:
                if "pypi.org" in url:
                    return json.dumps({"info": {"version": version}})
                return json.dumps({"version": version})
        raise AssertionError(f"unexpected fetch url with no stub configured: {url}")


class WatcherTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.queue = self.base / "queue"
        self.queue.mkdir(parents=True, exist_ok=True)
        os.environ.pop("WORK_QUEUE_DIR", None)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def write(self, name: str, **kw) -> Path:
        p = self.queue / name
        p.write_text(_brief(**kw), encoding="utf-8")
        return p


class TestParseWatchEntry(unittest.TestCase):
    def test_npm_entry_parses(self):
        parsed, err = w.parse_watch_entry("npm:left-pad@1.0.0")
        self.assertIsNone(err)
        self.assertEqual(parsed, ("npm", "left-pad", "1.0.0"))

    def test_pypi_entry_parses(self):
        parsed, err = w.parse_watch_entry("pypi:requests@2.31.0")
        self.assertIsNone(err)
        self.assertEqual(parsed, ("pypi", "requests", "2.31.0"))

    def test_missing_version_is_unparsable_not_raised(self):
        """`npm:left-pad` with no `@version` is classified unparsable (AC-004)."""
        try:
            parsed, err = w.parse_watch_entry("npm:left-pad")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"parse_watch_entry raised {exc!r}")
        self.assertIsNone(parsed)
        self.assertIsNotNone(err)

    def test_unsupported_registry_is_unparsable_not_raised(self):
        """`gem:foo@1.0.0` (unsupported registry) is classified unparsable (AC-004)."""
        try:
            parsed, err = w.parse_watch_entry("gem:foo@1.0.0")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"parse_watch_entry raised {exc!r}")
        self.assertIsNone(parsed)
        self.assertIsNotNone(err)


class TestVersionChanged(WatcherTestBase):
    """AC-001: changed version clears next-check-after and rewrites the watch entry."""

    def test_npm_changed_rewrites_entry_and_clears_next_check_after(self):
        p = self.write(
            "a.md", watch=["npm:left-pad@1.0.0"], next_check_after="2099-01-01"
        )
        fetch = _FakeFetch(versions={"left-pad": "1.0.1"})
        result = w.check_brief(p, fetch=fetch)

        self.assertTrue(result["next_check_after_cleared"])
        self.assertTrue(result["written"])
        self.assertTrue(result["entries"][0]["changed"])
        self.assertEqual(result["entries"][0]["latest_version"], "1.0.1")

        fm = w.read_frontmatter(p)
        self.assertNotIn("next-check-after", fm)
        self.assertEqual(fm["watch"], ["npm:left-pad@1.0.1"])


class TestVersionUnchanged(WatcherTestBase):
    """AC-002: unchanged version leaves the file byte-identical."""

    def test_pypi_unchanged_is_byte_identical(self):
        p = self.write("a.md", watch=["pypi:requests@2.31.0"])
        content_before = p.read_text(encoding="utf-8")
        mtime_before = p.stat().st_mtime

        fetch = _FakeFetch(versions={"requests": "2.31.0"})
        result = w.check_brief(p, fetch=fetch)

        self.assertFalse(result["next_check_after_cleared"])
        self.assertFalse(result["written"])
        self.assertFalse(result["entries"][0]["changed"])
        self.assertEqual(p.read_text(encoding="utf-8"), content_before)
        self.assertEqual(p.stat().st_mtime, mtime_before)


class TestNoWatchField(WatcherTestBase):
    """AC-003: a brief with no watch: field is skipped, zero network calls."""

    def test_no_watch_field_makes_zero_fetch_calls(self):
        p = self.write("a.md")
        fetch = _FakeFetch()
        result = w.check_brief(p, fetch=fetch)
        self.assertIsNone(result)
        self.assertEqual(fetch.calls, [])


class TestMalformedEntries(WatcherTestBase):
    """AC-004: malformed entries are skipped with a warning; valid entries still checked."""

    def test_mixed_malformed_and_valid_changed(self):
        p = self.write("a.md", watch=["npm:left-pad", "npm:left-pad@1.0.0"])
        fetch = _FakeFetch(versions={"left-pad": "1.0.1"})
        result = w.check_brief(p, fetch=fetch)

        malformed_entry, valid_entry = result["entries"]
        self.assertIsNotNone(malformed_entry["error"])
        self.assertFalse(malformed_entry["changed"])
        self.assertIsNone(valid_entry["error"])
        self.assertTrue(valid_entry["changed"])
        self.assertTrue(result["written"])

        fm = w.read_frontmatter(p)
        self.assertEqual(fm["watch"], ["npm:left-pad", "npm:left-pad@1.0.1"])

    def test_unsupported_registry_entry_does_not_block_sibling(self):
        p = self.write("a.md", watch=["gem:foo@1.0.0", "pypi:requests@2.31.0"])
        fetch = _FakeFetch(versions={"requests": "2.32.0"})
        result = w.check_brief(p, fetch=fetch)

        gem_entry, pypi_entry = result["entries"]
        self.assertIsNotNone(gem_entry["error"])
        self.assertTrue(pypi_entry["changed"])
        self.assertEqual(
            fetch.calls, [w._REGISTRY_URLS["pypi"].format(package="requests")]
        )


class TestGithubIssueEntries(WatcherTestBase):
    """A `watch:` entry can be a bare GitHub-issue URL instead of a
    `registry:package@version` entry; its recorded state is "open" unless the
    URL carries a `#closed` suffix, and a state change clears next-check-after
    the same as a version bump."""

    URL = "https://github.com/jsx-eslint/eslint-plugin-react/issues/3977"

    def test_open_issue_still_open_is_byte_identical(self):
        p = self.write("a.md", watch=[self.URL])
        content_before = p.read_text(encoding="utf-8")
        mtime_before = p.stat().st_mtime

        fetch = _FakeFetch(
            github_states={"jsx-eslint/eslint-plugin-react#3977": "open"}
        )
        result = w.check_brief(p, fetch=fetch)

        self.assertFalse(result["next_check_after_cleared"])
        self.assertFalse(result["written"])
        entry = result["entries"][0]
        self.assertEqual(entry["registry"], "github-issue")
        self.assertEqual(entry["package"], "jsx-eslint/eslint-plugin-react#3977")
        self.assertEqual(entry["recorded_version"], "open")
        self.assertEqual(entry["latest_version"], "open")
        self.assertFalse(entry["changed"])
        self.assertEqual(p.read_text(encoding="utf-8"), content_before)
        self.assertEqual(p.stat().st_mtime, mtime_before)

    def test_issue_closed_clears_next_check_after_and_rewrites_marker(self):
        p = self.write("a.md", watch=[self.URL], next_check_after="2099-01-01")
        fetch = _FakeFetch(
            github_states={"jsx-eslint/eslint-plugin-react#3977": "closed"}
        )
        result = w.check_brief(p, fetch=fetch)

        self.assertTrue(result["next_check_after_cleared"])
        self.assertTrue(result["written"])
        entry = result["entries"][0]
        self.assertTrue(entry["changed"])
        self.assertEqual(entry["recorded_version"], "open")
        self.assertEqual(entry["latest_version"], "closed")

        fm = w.read_frontmatter(p)
        self.assertNotIn("next-check-after", fm)
        self.assertEqual(fm["watch"], [f"{self.URL}#closed"])

    def test_reopened_issue_is_detected_as_change(self):
        p = self.write("a.md", watch=[f"{self.URL}#closed"])
        fetch = _FakeFetch(
            github_states={"jsx-eslint/eslint-plugin-react#3977": "open"}
        )
        result = w.check_brief(p, fetch=fetch)

        entry = result["entries"][0]
        self.assertEqual(entry["recorded_version"], "closed")
        self.assertEqual(entry["latest_version"], "open")
        self.assertTrue(entry["changed"])
        self.assertTrue(result["next_check_after_cleared"])

        fm = w.read_frontmatter(p)
        self.assertEqual(fm["watch"], [self.URL])

    def test_second_run_after_closing_reports_no_further_change(self):
        p = self.write("a.md", watch=[self.URL])
        fetch = _FakeFetch(
            github_states={"jsx-eslint/eslint-plugin-react#3977": "closed"}
        )

        first = w.check_brief(p, fetch=fetch)
        self.assertTrue(first["entries"][0]["changed"])

        second = w.check_brief(p, fetch=fetch)
        self.assertFalse(second["entries"][0]["changed"])
        self.assertFalse(second["written"])

    def test_fetch_error_is_isolated_from_sibling_entry(self):
        p = self.write("a.md", watch=[self.URL, "pypi:requests@2.31.0"])
        fetch = _FakeFetch(
            versions={"requests": "2.32.0"},
            github_errors={"jsx-eslint/eslint-plugin-react#3977"},
        )
        result = w.check_brief(p, fetch=fetch)

        gh_entry, pypi_entry = result["entries"]
        self.assertIsNotNone(gh_entry["error"])
        self.assertFalse(gh_entry["changed"])
        self.assertTrue(pypi_entry["changed"])
        self.assertTrue(result["written"])

        fm = w.read_frontmatter(p)
        self.assertEqual(fm["watch"], [self.URL, "pypi:requests@2.32.0"])

    def test_missing_state_field_is_unparsable_not_raised(self):
        p = self.write("a.md", watch=[self.URL])

        def fetch(url: str) -> str:
            return json.dumps({"not_state": "open"})

        try:
            result = w.check_brief(p, fetch=fetch)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"check_brief raised {exc!r}")
        entry = result["entries"][0]
        self.assertIsNotNone(entry["error"])
        self.assertFalse(entry["changed"])
        self.assertFalse(result["written"])


class TestDryRun(WatcherTestBase):
    """AC-005: --dry-run reports the same verdict as a live run with zero writes."""

    def test_dry_run_reports_change_without_writing(self):
        p = self.write(
            "a.md", watch=["npm:left-pad@1.0.0"], next_check_after="2099-01-01"
        )
        content_before = p.read_text(encoding="utf-8")
        mtime_before = p.stat().st_mtime

        fetch = _FakeFetch(versions={"left-pad": "1.0.1"})
        result = w.check_brief(p, fetch=fetch, dry_run=True)

        self.assertTrue(result["next_check_after_cleared"])
        self.assertTrue(result["entries"][0]["changed"])
        self.assertEqual(result["entries"][0]["latest_version"], "1.0.1")
        self.assertFalse(result["written"])
        self.assertEqual(p.read_text(encoding="utf-8"), content_before)
        self.assertEqual(p.stat().st_mtime, mtime_before)

    def test_dry_run_verdict_matches_live_run(self):
        live_path = self.write("live.md", watch=["npm:left-pad@1.0.0"])
        dry_path = self.write("dry.md", watch=["npm:left-pad@1.0.0"])

        fetch_live = _FakeFetch(versions={"left-pad": "1.0.1"})
        fetch_dry = _FakeFetch(versions={"left-pad": "1.0.1"})
        live_result = w.check_brief(live_path, fetch=fetch_live, dry_run=False)
        dry_result = w.check_brief(dry_path, fetch=fetch_dry, dry_run=True)

        self.assertEqual(
            [e["changed"] for e in live_result["entries"]],
            [e["changed"] for e in dry_result["entries"]],
        )
        self.assertEqual(
            [e["latest_version"] for e in live_result["entries"]],
            [e["latest_version"] for e in dry_result["entries"]],
        )
        self.assertTrue(live_result["written"])
        self.assertFalse(dry_result["written"])


class TestErrorIsolation(WatcherTestBase):
    """AC-006: a network error for one entry doesn't block a second, healthy entry."""

    def test_error_entry_does_not_block_sibling_entry_same_brief(self):
        p = self.write("a.md", watch=["npm:left-pad@1.0.0", "pypi:requests@2.31.0"])
        fetch = _FakeFetch(versions={"requests": "2.32.0"}, errors={"left-pad"})
        result = w.check_brief(p, fetch=fetch)

        npm_entry, pypi_entry = result["entries"]
        self.assertIsNotNone(npm_entry["error"])
        self.assertFalse(npm_entry["changed"])
        self.assertIsNone(pypi_entry["error"])
        self.assertTrue(pypi_entry["changed"])
        self.assertTrue(result["written"])

        fm = w.read_frontmatter(p)
        self.assertEqual(fm["watch"], ["npm:left-pad@1.0.0", "pypi:requests@2.32.0"])

    def test_error_entry_does_not_block_second_brief(self):
        broken = self.write("broken.md", watch=["npm:left-pad@1.0.0"])
        healthy = self.write("healthy.md", watch=["pypi:requests@2.31.0"])
        fetch = _FakeFetch(versions={"requests": "2.32.0"}, errors={"left-pad"})

        broken_result = w.check_brief(broken, fetch=fetch)
        healthy_result = w.check_brief(healthy, fetch=fetch)

        self.assertIsNotNone(broken_result["entries"][0]["error"])
        self.assertFalse(broken_result["written"])
        self.assertTrue(healthy_result["entries"][0]["changed"])
        self.assertTrue(healthy_result["written"])


class TestIdempotency(WatcherTestBase):
    """REQ-CHG-007: running the check twice reports no further change on the second pass."""

    def test_second_run_after_change_reports_no_change(self):
        p = self.write("a.md", watch=["npm:left-pad@1.0.0"])
        fetch = _FakeFetch(versions={"left-pad": "1.0.1"})

        first = w.check_brief(p, fetch=fetch)
        self.assertTrue(first["entries"][0]["changed"])
        self.assertTrue(first["written"])

        second = w.check_brief(p, fetch=fetch)
        self.assertFalse(second["entries"][0]["changed"])
        self.assertFalse(second["written"])


class TestJsonReportShape(WatcherTestBase):
    """REQ-CHG-009: --json output matches the report shape (checked/changed/errors/dry_run/briefs)."""

    def test_cli_json_output_matches_report_shape(self):
        self.write("a.md", watch=["gem:foo@1.0.0"])
        out = io.StringIO()
        with patch("sys.stdout", out):
            code = w.main(["--queue-dir", str(self.base), "--json"])
        self.assertEqual(code, 0)

        data = json.loads(out.getvalue())
        for key in ("checked", "changed", "errors", "dry_run", "briefs"):
            self.assertIn(key, data)
        self.assertEqual(data["checked"], 1)
        self.assertEqual(data["changed"], 0)
        self.assertEqual(data["errors"], 1)
        self.assertFalse(data["dry_run"])

        entry = data["briefs"][0]["entries"][0]
        for key in (
            "raw",
            "registry",
            "package",
            "recorded_version",
            "latest_version",
            "changed",
            "error",
        ):
            self.assertIn(key, entry)
        self.assertIsNotNone(entry["error"])


class TestQueueDirResolution(WatcherTestBase):
    """REQ-CHG-010: --queue-dir is honored over $WORK_QUEUE_DIR."""

    def test_queue_dir_flag_overrides_env(self):
        decoy_tmp = tempfile.TemporaryDirectory()
        try:
            decoy_base = Path(decoy_tmp.name)
            (decoy_base / "queue").mkdir(parents=True, exist_ok=True)
            (decoy_base / "queue" / "decoy.md").write_text(
                _brief(watch=["gem:decoy@1.0.0"]), encoding="utf-8"
            )
            os.environ["WORK_QUEUE_DIR"] = str(decoy_base)

            self.write("target.md", watch=["gem:foo@1.0.0"])

            out = io.StringIO()
            with patch("sys.stdout", out):
                code = w.main(["--queue-dir", str(self.base), "--json"])
            self.assertEqual(code, 0)

            data = json.loads(out.getvalue())
            self.assertEqual(data["checked"], 1)
            self.assertEqual(data["briefs"][0]["filename"], "target.md")
        finally:
            decoy_tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=1)
