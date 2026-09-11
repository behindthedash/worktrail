#!/usr/bin/env python3
"""Unit tests for the URL-keyed PR ledger and its recovery sweep."""

from __future__ import annotations

import datetime
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router import pr_ledger
from worktrail.shared.brief_frontmatter import (
    is_canonical_style,
    read_frontmatter,
    validate_brief,
)

URL = "https://github.com/acme/widgets/pull/42"
URL2 = "https://github.com/acme/widgets/pull/43"
T0 = datetime.datetime(2026, 9, 10, 12, 0, tzinfo=datetime.timezone.utc)


def _view(payload: dict | None, rc: int = 0):
    """A runner that answers every `gh pr view` with `payload`."""

    def runner(cmd, **_kw):
        if payload is None or rc != 0:
            return subprocess.CompletedProcess(cmd, rc or 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        )

    return runner


RED = {
    "state": "OPEN",
    "mergedAt": None,
    "autoMergeRequest": None,
    "mergeStateStatus": "UNSTABLE",
    "statusCheckRollup": [{"name": "ci", "conclusion": "FAILURE"}],
}
GREEN_AUTO = {
    "state": "OPEN",
    "mergedAt": None,
    "autoMergeRequest": {"enabledBy": {"login": "bot"}, "mergeMethod": "SQUASH"},
    "mergeStateStatus": "CLEAN",
    "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}],
}
BLOCKED = {
    "state": "OPEN",
    "mergedAt": None,
    "autoMergeRequest": None,
    "mergeStateStatus": "BLOCKED",
    "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}],
}
BLOCKED_AUTO_MERGE_CI_RUNNING = {
    "state": "OPEN",
    "mergedAt": None,
    "autoMergeRequest": {"enabledBy": {"login": "bot"}, "mergeMethod": "SQUASH"},
    "mergeStateStatus": "BLOCKED",
    "statusCheckRollup": [{"name": "ci", "status": "IN_PROGRESS", "conclusion": ""}],
}
PENDING = {
    "state": "OPEN",
    "mergedAt": None,
    "autoMergeRequest": None,
    "mergeStateStatus": "UNKNOWN",
    "statusCheckRollup": [{"name": "ci", "status": "IN_PROGRESS"}],
}
MERGED = {
    "state": "MERGED",
    "mergedAt": "2026-09-10T11:00:00Z",
    "statusCheckRollup": [],
}


class LedgerBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.ledger = self.tmp / "home" / "pr-ledger.json"
        self.queue = self.tmp / "work-queue"

    def _briefs(self) -> list[Path]:
        root = self.queue / "queue"
        return sorted(root.glob("*.md")) if root.is_dir() else []

    def _sweep(self, payload, now=T0, **kw):
        return pr_ledger.sweep(
            path=self.ledger,
            queue_base=self.queue,
            runner=_view(payload),
            now=now,
            **kw,
        )


class RegisterTest(LedgerBase):
    def test_register_creates_ledger_atomically_with_one_entry(self) -> None:
        entry = pr_ledger.register(
            URL, "/repo", branch="feat", path=self.ledger, now=T0
        )
        data = json.loads(self.ledger.read_text())
        self.assertEqual(list(data["prs"]), [URL])
        self.assertEqual(entry["number"], 42)
        self.assertEqual(entry["slug"], "acme/widgets")
        self.assertEqual(data["prs"][URL]["branch"], "feat")
        # No temp-file debris left beside the ledger.
        leftovers = [p for p in self.ledger.parent.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_repeat_register_upserts_instead_of_duplicating(self) -> None:
        pr_ledger.register(URL, "/repo", session_id="s1", path=self.ledger, now=T0)
        later = T0 + datetime.timedelta(minutes=5)
        pr_ledger.register(URL, "/repo", run_id="r1", path=self.ledger, now=later)
        data = json.loads(self.ledger.read_text())
        self.assertEqual(len(data["prs"]), 1)
        entry = data["prs"][URL]
        self.assertEqual(entry["opened_at"], pr_ledger._iso(T0))
        self.assertEqual(entry["updated_at"], pr_ledger._iso(later))
        self.assertEqual(entry["session_id"], "s1")  # not clobbered by None
        self.assertEqual(entry["run_id"], "r1")

    def test_rejects_non_pr_url(self) -> None:
        with self.assertRaises(pr_ledger.LedgerError):
            pr_ledger.register(
                "https://github.com/acme/widgets", "/repo", path=self.ledger
            )
        self.assertFalse(self.ledger.exists())

    def test_malformed_ledger_is_preserved_not_overwritten(self) -> None:
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text("{not json", encoding="utf-8")
        pr_ledger.register(URL, "/repo", path=self.ledger, now=T0)
        aside = [p for p in self.ledger.parent.glob("pr-ledger.json.malformed-*")]
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_text(encoding="utf-8"), "{not json")
        data = json.loads(self.ledger.read_text())
        self.assertEqual(list(data["prs"]), [URL])

    def test_wrong_shape_ledger_is_preserved_too(self) -> None:
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text('["a list"]', encoding="utf-8")
        pr_ledger.register(URL, "/repo", path=self.ledger, now=T0)
        self.assertTrue(list(self.ledger.parent.glob("pr-ledger.json.malformed-*")))


class HeartbeatTest(LedgerBase):
    def test_heartbeat_marks_fresh_and_unwatch_clears(self) -> None:
        pr_ledger.register(URL, "/repo", path=self.ledger, now=T0)
        entry = pr_ledger.heartbeat(URL, pid=123, path=self.ledger, now=T0)
        self.assertTrue(pr_ledger.watcher_is_fresh(entry, now=T0))
        self.assertEqual(entry["watcher"]["pid"], 123)
        stale_now = T0 + datetime.timedelta(
            seconds=pr_ledger.DEFAULT_HEARTBEAT_WINDOW_S + 1
        )
        self.assertFalse(pr_ledger.watcher_is_fresh(entry, now=stale_now))
        entry = pr_ledger.unwatch(URL, path=self.ledger, now=T0)
        self.assertIsNone(entry["watcher"])
        self.assertFalse(pr_ledger.watcher_is_fresh(entry, now=T0))

    def test_heartbeat_on_unregistered_pr_creates_nothing(self) -> None:
        self.assertIsNone(pr_ledger.heartbeat(URL, path=self.ledger))
        self.assertEqual(json.loads(self.ledger.read_text())["prs"], {})


class SessionQueryTest(LedgerBase):
    def test_session_entries_filters_by_owner_and_terminal_state(self) -> None:
        pr_ledger.register(URL, "/repo", session_id="s1", path=self.ledger, now=T0)
        pr_ledger.register(URL2, "/repo", session_id="s2", path=self.ledger, now=T0)
        own = pr_ledger.session_entries("s1", path=self.ledger)
        self.assertEqual([e["url"] for e in own], [URL])
        self.assertEqual(pr_ledger.session_entries("nobody", path=self.ledger), [])
        self._sweep(MERGED)
        self.assertEqual(pr_ledger.session_entries("s1", path=self.ledger), [])


class ClassifyTest(unittest.TestCase):
    def test_classification_table(self) -> None:
        self.assertEqual(pr_ledger.classify_state(None), "query-failed")
        self.assertEqual(pr_ledger.classify_state(MERGED), "merged")
        self.assertEqual(pr_ledger.classify_state({"state": "CLOSED"}), "closed")
        self.assertEqual(pr_ledger.classify_state(RED), "red")
        self.assertEqual(pr_ledger.classify_state(BLOCKED), "blocked")
        self.assertEqual(pr_ledger.classify_state(GREEN_AUTO), "green-auto-merge")
        self.assertEqual(pr_ledger.classify_state(PENDING), "pending")

    def test_auto_merge_wins_over_blocked_while_checks_run(self) -> None:
        # GitHub reports BLOCKED while required checks are still pending; with
        # auto-merge armed and nothing red, GitHub will land it -- not a recovery.
        self.assertEqual(
            pr_ledger.classify_state(BLOCKED_AUTO_MERGE_CI_RUNNING),
            "green-auto-merge",
        )

    def test_red_wins_over_auto_merge(self) -> None:
        payload = dict(RED, autoMergeRequest=GREEN_AUTO["autoMergeRequest"])
        self.assertEqual(pr_ledger.classify_state(payload), "red")


class SweepTest(LedgerBase):
    def setUp(self) -> None:
        super().setUp()
        pr_ledger.register(
            URL, "/repo", branch="feat", session_id="s1", path=self.ledger, now=T0
        )

    def test_merged_pr_is_removed(self) -> None:
        report = self._sweep(MERGED)
        self.assertEqual([r["url"] for r in report["removed"]], [URL])
        self.assertEqual(json.loads(self.ledger.read_text())["prs"], {})
        self.assertEqual(self._briefs(), [])

    def test_green_auto_merge_is_retained_without_brief(self) -> None:
        report = self._sweep(GREEN_AUTO, now=T0 + datetime.timedelta(hours=5))
        self.assertEqual(report["retained"][0]["state"], "green-auto-merge")
        self.assertEqual(report["recovered"], [])
        self.assertIn(URL, json.loads(self.ledger.read_text())["prs"])
        self.assertEqual(self._briefs(), [])

    def test_query_failure_retains_entry_untouched(self) -> None:
        report = self._sweep(None)
        self.assertEqual([r["url"] for r in report["query_failed"]], [URL])
        entry = json.loads(self.ledger.read_text())["prs"][URL]
        self.assertNotIn("last_state", entry)
        self.assertIn("last_query_failed_at", entry)
        self.assertEqual(self._briefs(), [])

    def test_red_unwatched_recovers_immediately(self) -> None:
        report = self._sweep(RED)
        self.assertEqual(len(report["recovered"]), 1)
        self.assertEqual(report["recovered"][0]["state"], "red")
        self.assertEqual(len(self._briefs()), 1)

    def test_blocked_unwatched_recovers(self) -> None:
        report = self._sweep(BLOCKED)
        self.assertEqual(report["recovered"][0]["state"], "blocked")
        self.assertEqual(len(self._briefs()), 1)

    def test_blocked_with_auto_merge_armed_is_retained_unwatched(self) -> None:
        report = self._sweep(
            BLOCKED_AUTO_MERGE_CI_RUNNING, now=T0 + datetime.timedelta(hours=2)
        )
        self.assertEqual(report["recovered"], [])
        self.assertEqual(report["retained"][0]["state"], "green-auto-merge")
        self.assertEqual(self._briefs(), [])

    def test_red_with_fresh_watcher_is_retained(self) -> None:
        pr_ledger.heartbeat(URL, path=self.ledger, now=T0)
        report = self._sweep(RED, now=T0 + datetime.timedelta(seconds=60))
        self.assertEqual(report["recovered"], [])
        self.assertIn("watching", report["retained"][0]["reason"])
        self.assertEqual(self._briefs(), [])

    def test_pending_recovers_only_once_stale(self) -> None:
        fresh = self._sweep(PENDING, now=T0 + datetime.timedelta(seconds=60))
        self.assertEqual(fresh["recovered"], [])
        stale = self._sweep(
            PENDING,
            now=T0 + datetime.timedelta(seconds=pr_ledger.DEFAULT_STALE_AFTER_S + 1),
        )
        self.assertEqual(len(stale["recovered"]), 1)
        self.assertEqual(len(self._briefs()), 1)

    def test_fake_opener_red_ci_repro_yields_exactly_one_pr_fix_brief(self) -> None:
        """A fake opener registers, watches briefly, then vanishes; CI goes
        red; repeated five-minute sweeps file ONE `pr fix` brief total."""
        pr_ledger.heartbeat(URL, pid=4242, path=self.ledger, now=T0)
        gone = T0 + datetime.timedelta(
            seconds=pr_ledger.DEFAULT_HEARTBEAT_WINDOW_S + 300
        )
        first = self._sweep(RED, now=gone)
        self.assertEqual(len(first["recovered"]), 1)
        for i in range(1, 4):
            again = self._sweep(RED, now=gone + datetime.timedelta(minutes=5 * i))
            self.assertEqual(again["recovered"], [])
            self.assertEqual([r["url"] for r in again["already_briefed"]], [URL])
        briefs = self._briefs()
        self.assertEqual(len(briefs), 1)
        fm = read_frontmatter(briefs[0])
        self.assertEqual(fm.get("intent"), "pr fix")
        self.assertEqual(fm.get("pr-url"), URL)
        self.assertEqual(fm.get("status"), "queued")
        self.assertEqual(fm.get("repo"), "/repo")
        self.assertTrue(str(fm.get("focus", "")).startswith("pr fix "))
        ok, why = validate_brief(briefs[0], required=("id", "status", "focus"))
        self.assertTrue(ok, why)
        self.assertTrue(
            is_canonical_style(briefs[0].read_text(encoding="utf-8")),
            "recovery brief must be written in the canonical frontmatter style",
        )
        entry = json.loads(self.ledger.read_text())["prs"][URL]
        self.assertEqual(entry["recovery_brief"], str(briefs[0]))

    def test_one_failed_brief_does_not_lose_the_rest_of_the_pass(self) -> None:
        """A brief write failure for one PR is recorded and skipped; the merged
        PR's removal and every other ledger update still land."""
        from unittest import mock

        pr_ledger.register(URL2, "/repo", branch="other", path=self.ledger, now=T0)
        by_url = {URL: MERGED, URL2: RED}

        def runner(cmd, **_kw):
            url = cmd[cmd.index("view") + 1]
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(by_url[url]), stderr=""
            )

        real = pr_ledger.file_recovery_brief

        def flaky(entry, state, reason, queue_base):
            if entry["url"] == URL2:
                raise OSError("disk full")
            return real(entry, state, reason, queue_base)

        with mock.patch.object(pr_ledger, "file_recovery_brief", flaky):
            report = pr_ledger.sweep(
                path=self.ledger, queue_base=self.queue, runner=runner, now=T0
            )
        self.assertEqual([r["url"] for r in report["removed"]], [URL])
        self.assertEqual([r["url"] for r in report["brief_failed"]], [URL2])
        self.assertEqual(report["recovered"], [])
        prs = json.loads(self.ledger.read_text())["prs"]
        self.assertNotIn(URL, prs)
        self.assertEqual(prs[URL2]["last_state"], "red")
        self.assertIn("disk full", prs[URL2]["last_brief_error"])
        self.assertEqual(self._briefs(), [])

    def test_invalid_brief_is_not_left_behind(self) -> None:
        from unittest import mock

        with mock.patch.object(
            pr_ledger, "_render_brief", lambda *_a, **_k: "not a brief\n"
        ):
            report = self._sweep(RED)
        self.assertEqual([r["url"] for r in report["brief_failed"]], [URL])
        self.assertEqual(self._briefs(), [])
        self.assertIn(URL, json.loads(self.ledger.read_text())["prs"])

    def test_live_queries_run_outside_the_ledger_lock(self) -> None:
        """`register`/`heartbeat` must never block behind a slow `gh` call."""
        import fcntl

        seen: list[bool] = []

        def runner(cmd, **_kw):
            lock = self.ledger.with_name(self.ledger.name + ".lock")
            lock.parent.mkdir(parents=True, exist_ok=True)
            with open(lock, "a+", encoding="utf-8") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    seen.append(True)
                except OSError:
                    seen.append(False)
            # A heartbeat landing mid-query must be honoured by the sweep.
            pr_ledger.heartbeat(URL, path=self.ledger, now=T0)
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(RED), stderr=""
            )

        report = pr_ledger.sweep(
            path=self.ledger, queue_base=self.queue, runner=runner, now=T0
        )
        self.assertEqual(seen, [True])
        self.assertEqual(report["recovered"], [])
        self.assertIn("watching", report["retained"][0]["reason"])
        self.assertEqual(self._briefs(), [])

    def test_picked_brief_still_dedups(self) -> None:
        self._sweep(RED)
        brief = self._briefs()[0]
        picked = self.queue / "picked" / brief.name
        picked.parent.mkdir()
        brief.rename(picked)
        again = self._sweep(RED, now=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(again["recovered"], [])
        self.assertEqual(self._briefs(), [])

    def test_brief_recreated_if_previous_one_was_consumed(self) -> None:
        self._sweep(RED)
        self._briefs()[0].unlink()
        again = self._sweep(RED, now=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(len(again["recovered"]), 1)
        self.assertEqual(len(self._briefs()), 1)


class CliTest(LedgerBase):
    def _run(self, *args: str) -> tuple[int, str]:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = pr_ledger.main(["--ledger", str(self.ledger), *args])
        return rc, out.getvalue()

    def test_register_session_and_unwatch_via_cli(self) -> None:
        rc, out = self._run(
            "register", "--url", URL, "--repo", "/repo", "--session-id", "s9"
        )
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["session_id"], "s9")
        rc, out = self._run("session", "--session-id", "s9")
        self.assertEqual(rc, 0)
        self.assertEqual([e["url"] for e in json.loads(out)], [URL])
        rc, _ = self._run("heartbeat", "--url", URL, "--pid", "7")
        self.assertEqual(rc, 0)
        rc, _ = self._run("heartbeat", "--url", URL2)
        self.assertEqual(rc, 1)
        rc, _ = self._run("unwatch", "--url", URL)
        self.assertEqual(rc, 0)

    def test_register_bad_url_exits_nonzero(self) -> None:
        rc, _ = self._run("register", "--url", "nope", "--repo", "/repo")
        self.assertEqual(rc, 1)

    def test_sweep_cli_uses_queue_base_flag(self) -> None:
        pr_ledger.register(URL, "/repo", path=self.ledger, now=T0)
        from unittest import mock

        with mock.patch.object(pr_ledger, "query_pr_state", return_value=RED):
            rc, out = self._run("sweep", "--queue-base", str(self.queue))
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)["recovered"]), 1)
        self.assertEqual(len(self._briefs()), 1)

    def test_console_script_is_registered(self) -> None:
        text = (
            Path(__file__).resolve().parents[2].joinpath("pyproject.toml").read_text()
        )
        self.assertIn('worktrail-pr-ledger = "worktrail.router.pr_ledger:main"', text)


if __name__ == "__main__":
    unittest.main()
