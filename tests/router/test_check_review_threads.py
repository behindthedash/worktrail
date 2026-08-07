#!/usr/bin/env python3
"""Unit tests for the CI-watch-loop review-thread resolution gate
(stdlib unittest, mirrors test_automerge_preflight.py's fake-runner style)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict, List

from worktrail.router import check_review_threads as crt


class _FakeResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRunner:
    """Scripted `subprocess.run`-alike keyed by the command's leading argv
    tokens; the first key whose tokens fully prefix `cmd` wins."""

    def __init__(self, responses: Dict[str, _FakeResult]) -> None:
        self.responses = responses
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str], **kwargs: Any) -> _FakeResult:
        self.calls.append(cmd)
        for key, result in self.responses.items():
            tokens = key.split()
            if cmd[: len(tokens)] == tokens:
                return result
        raise AssertionError(f"unscripted command: {cmd}")


def _threads_response(nodes: List[Dict[str, Any]], has_next: bool = False, end_cursor: str = "") -> _FakeResult:
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                        "nodes": nodes,
                    }
                }
            }
        }
    }
    return _FakeResult(0, json.dumps(payload))


def _thread(thread_id: str, path: str, resolved: bool, created_at: str = "2026-08-01T00:00:00Z",
            author: str = "security-review-llm", body: str = "possible SQL injection") -> Dict[str, Any]:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": False,
        "path": path,
        "line": 42,
        "comments": {"nodes": [{"author": {"login": author}, "body": body, "createdAt": created_at}]},
    }


class TestFetchReviewThreads(unittest.TestCase):
    def test_single_page(self) -> None:
        runner = _FakeRunner({
            "gh api graphql": _threads_response([_thread("T_1", "a.py", False)]),
        })
        threads, warning = crt.fetch_review_threads("acme", "widgets", 42, runner=runner)
        self.assertIsNone(warning)
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["id"], "T_1")

    def test_paginates_until_no_next_page(self) -> None:
        calls = {"n": 0}

        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            calls["n"] += 1
            if calls["n"] == 1:
                return _threads_response([_thread("T_1", "a.py", False)], has_next=True, end_cursor="c1")
            return _threads_response([_thread("T_2", "b.py", False)], has_next=False)

        threads, warning = crt.fetch_review_threads("acme", "widgets", 42, runner=runner)
        self.assertIsNone(warning)
        self.assertEqual([t["id"] for t in threads], ["T_1", "T_2"])
        self.assertEqual(calls["n"], 2)

    def test_gh_unavailable_returns_none_with_warning(self) -> None:
        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            raise FileNotFoundError("gh not found")

        threads, warning = crt.fetch_review_threads("acme", "widgets", 42, runner=runner)
        self.assertIsNone(threads)
        self.assertIsNotNone(warning)

    def test_gh_nonzero_exit_returns_none_with_warning(self) -> None:
        runner = _FakeRunner({
            "gh api graphql": _FakeResult(1, "", "GraphQL: Could not resolve to a PullRequest"),
        })
        threads, warning = crt.fetch_review_threads("acme", "widgets", 999, runner=runner)
        self.assertIsNone(threads)
        self.assertIn("gh api graphql failed", warning)

    def test_malformed_json_returns_none_with_warning(self) -> None:
        runner = _FakeRunner({"gh api graphql": _FakeResult(0, "not json")})
        threads, warning = crt.fetch_review_threads("acme", "widgets", 42, runner=runner)
        self.assertIsNone(threads)
        self.assertIn("unparseable", warning)

    def test_unexpected_shape_returns_none_with_warning(self) -> None:
        runner = _FakeRunner({"gh api graphql": _FakeResult(0, json.dumps({"data": {}}))})
        threads, warning = crt.fetch_review_threads("acme", "widgets", 42, runner=runner)
        self.assertIsNone(threads)
        self.assertIn("unexpected", warning)


class TestCorrelate(unittest.TestCase):
    def test_resolved_thread_excluded_from_both_buckets(self) -> None:
        threads = [_thread("T_1", "a.py", resolved=True)]
        runner = _FakeRunner({})  # no git call expected
        grouped = crt.correlate(Path("/repo"), threads, [], runner=runner)
        self.assertEqual(grouped["unresolved"], [])
        self.assertEqual(grouped["addressed"], [])
        self.assertEqual(grouped["unaddressed"], [])

    def test_commit_touching_path_since_marks_addressed(self) -> None:
        threads = [_thread("T_1", "a.py", resolved=False, created_at="2026-08-01T00:00:00Z")]
        runner = _FakeRunner({
            "git -C": _FakeResult(0, "abc123\n"),
        })
        grouped = crt.correlate(Path("/repo"), threads, [], runner=runner)
        self.assertEqual([t["id"] for t in grouped["addressed"]], ["T_1"])
        self.assertEqual(grouped["unaddressed"], [])

    def test_no_matching_commit_and_no_decision_is_unaddressed(self) -> None:
        threads = [_thread("T_1", "a.py", resolved=False)]
        runner = _FakeRunner({
            "git -C": _FakeResult(0, ""),  # no commits touched the path
        })
        grouped = crt.correlate(Path("/repo"), threads, [], runner=runner)
        self.assertEqual(grouped["addressed"], [])
        self.assertEqual([t["id"] for t in grouped["unaddressed"]], ["T_1"])

    def test_decision_log_mentioning_path_marks_addressed(self) -> None:
        threads = [_thread("T_1", "a.py", resolved=False)]
        runner = _FakeRunner({
            "git -C": _FakeResult(0, ""),  # no commit evidence
        })
        decisions = ["investigated a.py finding, false positive, not fixing"]
        grouped = crt.correlate(Path("/repo"), threads, decisions, runner=runner)
        self.assertEqual([t["id"] for t in grouped["addressed"]], ["T_1"])
        self.assertEqual(grouped["unaddressed"], [])

    def test_decision_log_mentioning_thread_id_marks_addressed(self) -> None:
        threads = [_thread("T_1", "a.py", resolved=False)]
        runner = _FakeRunner({"git -C": _FakeResult(0, "")})
        decisions = ["thread T_1 addressed: dismissed as intentional design"]
        grouped = crt.correlate(Path("/repo"), threads, decisions, runner=runner)
        self.assertEqual([t["id"] for t in grouped["addressed"]], ["T_1"])

    def test_thread_with_no_path_or_comments_is_unaddressed_without_decision(self) -> None:
        thread = {"id": "T_1", "isResolved": False, "path": None, "comments": {"nodes": []}}
        runner = _FakeRunner({})  # no path/created_at -> no git call attempted
        grouped = crt.correlate(Path("/repo"), [thread], [], runner=runner)
        self.assertEqual(grouped["addressed"], [])
        self.assertEqual([t["id"] for t in grouped["unaddressed"]], ["T_1"])


class TestReplyAndResolve(unittest.TestCase):
    def test_both_mutations_succeed(self) -> None:
        runner = _FakeRunner({"gh api graphql": _FakeResult(0, "{}")})
        ok, err = crt.reply_and_resolve("T_1", "done", runner=runner)
        self.assertTrue(ok)
        self.assertIsNone(err)
        # both the reply and resolve mutation were issued
        self.assertEqual(len(runner.calls), 2)

    def test_reply_failure_short_circuits_before_resolve(self) -> None:
        runner = _FakeRunner({"gh api graphql": _FakeResult(1, "", "permission denied")})
        ok, err = crt.reply_and_resolve("T_1", "done", runner=runner)
        self.assertFalse(ok)
        self.assertIn("reply failed", err)
        self.assertEqual(len(runner.calls), 1)

    def test_resolve_failure_after_successful_reply(self) -> None:
        calls = {"n": 0}

        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResult(0, "{}")
            return _FakeResult(1, "", "already resolved by another actor")

        ok, err = crt.reply_and_resolve("T_1", "done", runner=runner)
        self.assertFalse(ok)
        self.assertIn("resolve failed", err)


class TestCheck(unittest.TestCase):
    def test_checked_false_when_owner_repo_unresolvable(self) -> None:
        runner = _FakeRunner({"git remote": _FakeResult(1, "")})
        res = crt.check(Path("/repo"), 42, runner=runner)
        self.assertFalse(res["checked"])
        self.assertFalse(res["blocking"])
        self.assertIsNotNone(res["warning"])

    def test_checked_false_when_gh_unavailable(self) -> None:
        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            if cmd[:2] == ["git", "remote"]:
                return _FakeResult(0, "https://github.com/acme/widgets.git\n")
            raise FileNotFoundError("gh not found")

        res = crt.check(Path("/repo"), 42, runner=runner)
        self.assertFalse(res["checked"])
        self.assertFalse(res["blocking"])

    def test_clean_pr_no_unresolved_threads(self) -> None:
        runner = _FakeRunner({
            "git remote": _FakeResult(0, "https://github.com/acme/widgets.git\n"),
            "gh api graphql": _threads_response([_thread("T_1", "a.py", resolved=True)]),
        })
        res = crt.check(Path("/repo"), 42, runner=runner)
        self.assertTrue(res["checked"])
        self.assertFalse(res["blocking"])
        self.assertEqual(res["unresolved_count"], 0)

    def test_addressed_thread_is_resolved_and_not_blocking(self) -> None:
        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            if cmd[:2] == ["git", "remote"]:
                return _FakeResult(0, "https://github.com/acme/widgets.git\n")
            if cmd[:2] == ["gh", "api"] and "query" in " ".join(cmd) and "reviewThreads" in " ".join(cmd):
                return _threads_response([_thread("T_1", "a.py", resolved=False)])
            if cmd[:2] == ["git", "-C"]:
                return _FakeResult(0, "abc123\n")  # commit touched a.py since thread opened
            if cmd[:2] == ["gh", "api"]:
                return _FakeResult(0, "{}")  # reply / resolve mutations
            raise AssertionError(f"unscripted command: {cmd}")

        res = crt.check(Path("/repo"), 42, runner=runner)
        self.assertTrue(res["checked"])
        self.assertFalse(res["blocking"])
        self.assertEqual(len(res["resolved_now"]), 1)
        self.assertTrue(res["resolved_now"][0]["resolved"])

    def test_unaddressed_thread_blocks_and_applies_no_automerge_label(self) -> None:
        calls: List[List[str]] = []

        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            calls.append(cmd)
            if cmd[:2] == ["git", "remote"]:
                return _FakeResult(0, "https://github.com/acme/widgets.git\n")
            if cmd[:2] == ["gh", "api"] and "reviewThreads" in " ".join(cmd):
                return _threads_response([_thread("T_1", "a.py", resolved=False)])
            if cmd[:2] == ["git", "-C"]:
                return _FakeResult(0, "")  # nothing touched a.py
            if cmd[:3] == ["gh", "pr", "view"]:
                return _FakeResult(0, json.dumps({"labels": []}))  # no label yet
            if cmd[:2] == ["gh", "api"] and "/labels" in cmd[2]:
                return _FakeResult(0, "")
            raise AssertionError(f"unscripted call: {cmd}")

        res = crt.check(Path("/repo"), 42, runner=runner)
        self.assertTrue(res["checked"])
        self.assertTrue(res["blocking"])
        self.assertEqual(len(res["unaddressed"]), 1)
        self.assertEqual(res["unaddressed"][0]["path"], "a.py")
        self.assertEqual(res["resolved_now"], [])
        # no reply/resolve GraphQL mutation was issued for the thread itself
        self.assertFalse(any("addPullRequestReviewThreadReply" in " ".join(c) or
                              "resolveReviewThread" in " ".join(c) for c in calls))
        # native auto-merge is blocked via the additive go:no-automerge label
        self.assertEqual(res["no_automerge_label_applied"], "go:no-automerge")
        self.assertIn(["gh", "api", "repos/acme/widgets/issues/42/labels",
                       "-X", "POST", "-f", "labels[]=go:no-automerge"], calls)

    def test_unaddressed_thread_skips_label_when_already_present(self) -> None:
        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            if cmd[:2] == ["git", "remote"]:
                return _FakeResult(0, "https://github.com/acme/widgets.git\n")
            if cmd[:2] == ["gh", "api"] and "reviewThreads" in " ".join(cmd):
                return _threads_response([_thread("T_1", "a.py", resolved=False)])
            if cmd[:2] == ["git", "-C"]:
                return _FakeResult(0, "")
            if cmd[:3] == ["gh", "pr", "view"]:
                return _FakeResult(0, json.dumps({"labels": [{"name": "go:no-automerge"}]}))
            if cmd[:2] == ["gh", "api"] and "/labels" in cmd[2]:
                raise AssertionError("must not re-add an already-present label")
            raise AssertionError(f"unscripted call: {cmd}")

        res = crt.check(Path("/repo"), 42, runner=runner)
        self.assertTrue(res["blocking"])
        self.assertIsNone(res["no_automerge_label_applied"])

    def test_dry_run_blocking_never_applies_no_automerge_label(self) -> None:
        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            if cmd[:2] == ["git", "remote"]:
                return _FakeResult(0, "https://github.com/acme/widgets.git\n")
            if cmd[:2] == ["gh", "api"] and "reviewThreads" in " ".join(cmd):
                return _threads_response([_thread("T_1", "a.py", resolved=False)])
            if cmd[:2] == ["git", "-C"]:
                return _FakeResult(0, "")
            raise AssertionError(f"dry-run must never call a mutation: {cmd}")

        res = crt.check(Path("/repo"), 42, dry_run=True, runner=runner)
        self.assertTrue(res["blocking"])
        self.assertIsNone(res["no_automerge_label_applied"])

    def test_dry_run_never_mutates(self) -> None:
        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            if cmd[:2] == ["git", "remote"]:
                return _FakeResult(0, "https://github.com/acme/widgets.git\n")
            if cmd[:2] == ["gh", "api"] and "reviewThreads" in " ".join(cmd):
                return _threads_response([_thread("T_1", "a.py", resolved=False)])
            if cmd[:2] == ["git", "-C"]:
                return _FakeResult(0, "abc123\n")
            raise AssertionError(f"dry-run must never call a mutation: {cmd}")

        res = crt.check(Path("/repo"), 42, dry_run=True, runner=runner)
        self.assertTrue(res["checked"])
        self.assertFalse(res["blocking"])
        self.assertTrue(res["resolved_now"][0]["dry_run"])

    def test_explicit_owner_name_skips_git_remote_lookup(self) -> None:
        def runner(cmd: List[str], **kwargs: Any) -> _FakeResult:
            if cmd[:2] == ["git", "remote"]:
                raise AssertionError("should not resolve owner/repo when both are given explicitly")
            if cmd[:2] == ["gh", "api"] and "reviewThreads" in " ".join(cmd):
                return _threads_response([])
            raise AssertionError(f"unscripted command: {cmd}")

        res = crt.check(Path("/repo"), 42, owner="acme", name="widgets", runner=runner)
        self.assertTrue(res["checked"])
        self.assertEqual(res["unresolved_count"], 0)


if __name__ == "__main__":
    unittest.main()
