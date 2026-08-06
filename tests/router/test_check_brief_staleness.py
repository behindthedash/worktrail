#!/usr/bin/env python3
"""Unit tests for the pre-dispatch brief-staleness guard (stdlib unittest).

Extraction (`extract_probes()`) is pure text, no repository involved; history
search (`check()`) exercises a real throwaway git repo fixture rather than
mocking, mirroring test_check_spec_collision.py's fixture pattern and
philosophy.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router import check_brief_staleness as cbs


def _git(repo: str, *args: str, env: dict = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True, env=env,
    )


def _init_repo(branch: str = "main") -> str:
    d = tempfile.mkdtemp(prefix="brief-staleness-")
    _git(d, "init", "-q", "-b", branch)
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (Path(d) / "README.md").write_text("base\n", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _write(repo: str, name: str, content: str) -> None:
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(repo: str, message: str, date_iso: str) -> str:
    """Stage everything and commit with both author and committer date set
    to `date_iso`, so `git log --since` filters deterministically regardless
    of when the test actually runs. Returns the new commit's short SHA."""
    env = dict(os.environ, GIT_AUTHOR_DATE=date_iso, GIT_COMMITTER_DATE=date_iso)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message, env=env)
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


class TestExtractProbesPaths(unittest.TestCase):
    def test_bare_filename_qualifies_as_path_probe(self):
        text = "The fix lives in prevent-destructive-commands.py, see the guard."
        res = cbs.extract_probes(text)
        self.assertIn("prevent-destructive-commands.py", res["paths"])
        self.assertEqual(res["symbols"], [])
        self.assertEqual(res["dropped"], 0)


class TestExtractProbesSymbols(unittest.TestCase):
    def test_dotted_and_underscored_symbol_probes(self):
        text = "Touches `self.foo_bar` and `resolve_base_ref` in the router."
        res = cbs.extract_probes(text)
        self.assertEqual(res["paths"], [])
        self.assertIn("self.foo_bar", res["symbols"])
        self.assertIn("resolve_base_ref", res["symbols"])
        self.assertEqual(res["dropped"], 0)

    def test_unquoted_snake_case_identifier_is_a_symbol_probe(self):
        # Briefs captured via `worktrail-handoff --focus` are plain prose with
        # no backticks at all (verified against a real brief 2026-08-05:
        # zero backticks, four real identifiers). Requiring backticks made
        # symbol search dead on arrival for the primary capture path, so an
        # unquoted token that is distinctively snake_case now qualifies.
        text = "The first compile_run_plan pass gave apply_to_tasks the wrong edges."
        res = cbs.extract_probes(text)
        self.assertIn("compile_run_plan", res["symbols"])
        self.assertIn("apply_to_tasks", res["symbols"])

    def test_unquoted_ordinary_prose_is_not_a_symbol_probe(self):
        # The underscore is what makes the unquoted fallback safe: ordinary
        # words, hyphenated words, and short tokens must still be ignored.
        text = "We should resolve the base ref and re-check the file-scope soon."
        res = cbs.extract_probes(text)
        self.assertEqual(res["symbols"], [])

    def test_task_ids_and_version_numbers_are_not_path_probes(self):
        # `1.1` / `2.10` look like a filename with an extension, and
        # `2.1/2.2/2.3/2.4` looks like a path, but they are task ids -- the
        # single most common token shape in a brief. Admitting them crowds
        # real probes out under PATH_PROBE_CAP.
        text = "Tasks 1.1 and 2.10 plus the chain 2.1/2.2/2.3/2.4 all matter."
        res = cbs.extract_probes(text)
        self.assertEqual(res["paths"], [])

    def test_absolute_paths_and_call_site_blobs_are_not_path_probes(self):
        # An absolute path (a brief's `Repo: /home/...` line) points outside
        # the repo being searched; a backticked call-site list is prose, not a
        # pathspec. Both were observed producing useless or timing-out probes.
        text = (
            "Repo: /home/briank/projects/worktrail. "
            "See `needs_compile()/_print_scope_gap_error()` and 2.1->2.2->2.3."
        )
        res = cbs.extract_probes(text)
        for bad in ("/home/briank/projects/worktrail", "2.1->2.2->2.3"):
            self.assertNotIn(bad, res["paths"])
        self.assertFalse([p for p in res["paths"] if "(" in p or ")" in p])

    def test_trailing_call_parens_are_stripped_from_symbol_probes(self):
        text = "Fix `compile_run_plan()` so the edges close."
        res = cbs.extract_probes(text)
        self.assertIn("compile_run_plan", res["symbols"])


class TestExtractProbesPullRequests(unittest.TestCase):
    def test_pr_hash_n_and_owner_repo_hash_n_dedupe_to_one_entry(self):
        text = "Delivered by PR #89, see also behindthedash/devops#89 for the merge."
        res = cbs.extract_probes(text)
        self.assertEqual(res["pull_requests"], ["89"])

    def test_owner_repo_hash_n_alone_is_recognized(self):
        text = "See behindthedash/devops#123 for the merge."
        res = cbs.extract_probes(text)
        self.assertEqual(res["pull_requests"], ["123"])

    def test_distinct_pr_numbers_are_kept_separately(self):
        text = "Follows on from PR #12 and closes #47 dupe."
        res = cbs.extract_probes(text)
        # "#47" alone (not adjacent to PR/pull) does not qualify.
        self.assertEqual(res["pull_requests"], ["12"])


class TestExtractProbesEmpty(unittest.TestCase):
    def test_prose_with_no_code_tokens_yields_empty_probes(self):
        text = "This is a plain english description with no code identifiers at all."
        res = cbs.extract_probes(text)
        self.assertEqual(res["paths"], [])
        self.assertEqual(res["symbols"], [])
        self.assertEqual(res["pull_requests"], [])
        self.assertEqual(res["dropped"], 0)

    def test_empty_string_yields_empty_probes_without_raising(self):
        res = cbs.extract_probes("")
        self.assertEqual(res["paths"], [])
        self.assertEqual(res["symbols"], [])
        self.assertEqual(res["pull_requests"], [])
        self.assertEqual(res["dropped"], 0)


class TestExtractProbesCapTruncation(unittest.TestCase):
    def test_cap_truncation_reports_dropped_and_keeps_most_distinctive(self):
        short_symbols = [f"sym_{i}" for i in range(3)]
        long_symbols = [
            f"a_very_long_and_distinctive_symbol_name_number_{i:02d}"
            for i in range(9)
        ]
        text = " ".join(f"`{s}`" for s in short_symbols + long_symbols)

        res = cbs.extract_probes(text)

        self.assertEqual(len(res["symbols"]), cbs.SYMBOL_PROBE_CAP)
        self.assertGreater(res["dropped"], 0)
        self.assertEqual(res["dropped"], len(short_symbols) + len(long_symbols) - cbs.SYMBOL_PROBE_CAP)
        # The short, generic-looking probes are the least distinctive and
        # must be the ones dropped, not the long, specific ones.
        for probe in short_symbols:
            self.assertNotIn(probe, res["symbols"])
        for probe in long_symbols[: cbs.SYMBOL_PROBE_CAP]:
            self.assertIn(probe, res["symbols"])


class TestCheckHistorySearch(unittest.TestCase):
    def test_bare_filename_probe_matches_at_repo_root_and_nested(self):
        # Regression for the `**/` pathspec defect: under git's *default*
        # pathspec matching `**` must consume at least one component, so a
        # bare-name probe found a nested file but silently missed a repo-root
        # one -- the exact bare-filename case the probe kind exists for.
        # `:(glob)` makes `**/` match zero or more components.
        repo = _init_repo()
        _write(repo, "widget.py", "print('root')\n")
        root_sha = _commit(repo, "Add root widget", "2026-06-01T00:00:00")
        _write(repo, "src/widget.py", "print('nested')\n")
        nested_sha = _commit(repo, "Add nested widget", "2026-06-02T00:00:00")

        res = cbs.check(Path(repo), "The fix lives in widget.py somewhere.", "2026-01-01T00:00:00")

        self.assertTrue(res["checked"])
        found = {m["sha"] for m in res["matches"] if m["probe"] == "widget.py"}
        self.assertIn(root_sha, found)
        self.assertIn(nested_sha, found)

    def test_commit_naming_symbol_only_in_its_message_is_found(self):
        # `-S` only sees commits that change a symbol's occurrence count, so a
        # commit that moved, reverted, or merely described the work is
        # invisible to it while naming the symbol plainly in its subject. The
        # `--grep` pass covers that; `kind` distinguishes the two.
        repo = _init_repo()
        _write(repo, "notes.txt", "nothing relevant here\n")
        sha = _commit(repo, "Rework apply_to_tasks ordering", "2026-06-01T00:00:00")

        res = cbs.check(Path(repo), "Concerns apply_to_tasks behaviour.", "2026-01-01T00:00:00")

        self.assertTrue(res["checked"])
        message_hits = [m for m in res["matches"] if m["kind"] == "message"]
        self.assertIn(sha, {m["sha"] for m in message_hits})

    def test_symbol_found_both_ways_is_reported_once(self):
        repo = _init_repo()
        _write(repo, "src/mod.py", "def apply_to_tasks():\n    pass\n")
        sha = _commit(repo, "Add apply_to_tasks helper", "2026-06-01T00:00:00")

        res = cbs.check(Path(repo), "Concerns apply_to_tasks behaviour.", "2026-01-01T00:00:00")

        hits = [m for m in res["matches"] if m["sha"] == sha and m["probe"] == "apply_to_tasks"]
        self.assertEqual(len(hits), 1)

    def test_commit_after_created_is_reported_with_sha_date_subject(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        sha = _commit(repo, "Add widget support", "2026-06-01T00:00:00")

        res = cbs.check(Path(repo), "This touches src/widget.py directly.", "2026-01-01T00:00:00")

        self.assertTrue(res["checked"])
        match = next(m for m in res["matches"] if m["probe"] == "src/widget.py")
        self.assertEqual(match["sha"], sha)
        self.assertEqual(match["date"], "2026-06-01")
        self.assertEqual(match["subject"], "Add widget support")
        self.assertEqual(match["kind"], "path")

    def test_commit_before_created_is_not_reported(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v1')\n")
        _commit(repo, "Add widget support", "2026-01-01T00:00:00")

        res = cbs.check(Path(repo), "This touches src/widget.py directly.", "2026-06-01T00:00:00")

        self.assertTrue(res["checked"])
        self.assertEqual(res["matches"], [])

    def test_commit_reachable_only_from_remote_tracking_ref_is_still_found(self):
        bare = tempfile.mkdtemp(prefix="brief-staleness-bare-")
        _git(bare, "init", "-q", "--bare", "-b", "main")

        work = _init_repo(branch="main")
        _git(work, "remote", "add", "origin", bare)
        _git(work, "push", "-q", "origin", "main")

        other = tempfile.mkdtemp(prefix="brief-staleness-other-")
        subprocess.run(["git", "clone", "-q", bare, other], capture_output=True, text=True, check=True)
        _git(other, "config", "user.email", "test@example.com")
        _git(other, "config", "user.name", "Test")
        _write(other, "src/widget.py", "print('v2')\n")
        sha = _commit(other, "Add widget support", "2026-06-01T00:00:00")
        _git(other, "push", "-q", "origin", "main")

        # `work`'s local main branch never merges the commit pushed by `other`
        # -- only its remote-tracking ref (`origin/main`) sees it.
        _git(work, "fetch", "-q", "origin")
        local_log = _git(work, "log", "main", "--oneline").stdout
        self.assertNotIn(sha, local_log)

        res = cbs.check(Path(work), "This touches src/widget.py directly.", "2026-01-01T00:00:00", base="main")

        self.assertTrue(res["checked"])
        match = next(m for m in res["matches"] if m["probe"] == "src/widget.py")
        self.assertEqual(match["sha"], sha)


class TestCheckFailsOpen(unittest.TestCase):
    """Task 2.3 -- every unanswerable condition degrades to `checked: false`
    with a warning, and `check()` raises for none of them."""

    def test_non_git_path_yields_checked_false_without_raising(self):
        d = tempfile.mkdtemp(prefix="brief-staleness-nongit-")
        res = cbs.check(Path(d), "Touches `src/widget.py`.", "2026-01-01T00:00:00")
        self.assertFalse(res["checked"])
        self.assertIsNotNone(res["warning"])
        self.assertEqual(res["matches"], [])

    def test_missing_created_timestamp_yields_checked_false(self):
        repo = _init_repo()
        res = cbs.check(Path(repo), "Touches `src/widget.py`.", None)
        self.assertFalse(res["checked"])
        self.assertIn("created", str(res["warning"]))

    def test_malformed_created_timestamp_yields_checked_false(self):
        repo = _init_repo()
        res = cbs.check(Path(repo), "Touches `src/widget.py`.", "not-a-timestamp")
        self.assertFalse(res["checked"])
        self.assertIn("created", str(res["warning"]))

    def test_no_probes_yields_checked_false_not_a_clean_negative(self):
        repo = _init_repo()
        res = cbs.check(Path(repo), "Plain english with nothing to search for.", "2026-01-01T00:00:00")
        self.assertFalse(res["checked"])
        self.assertIsNotNone(res["warning"])

    def test_subprocess_timeout_degrades_to_warning_not_exception(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        _commit(repo, "Add widget support", "2026-06-01T00:00:00")

        real_run = cbs.subprocess.run

        def _timeout(*args, **kwargs):
            if args and isinstance(args[0], list) and "log" in args[0]:
                raise cbs.subprocess.TimeoutExpired(cmd=args[0], timeout=1)
            return real_run(*args, **kwargs)

        cbs.subprocess.run = _timeout
        try:
            res = cbs.check(Path(repo), "Touches src/widget.py.", "2026-01-01T00:00:00")
        finally:
            cbs.subprocess.run = real_run

        # The search itself was attempted, so `checked` stays true; the
        # timed-out probe contributes no matches and is named in the warning.
        self.assertEqual(res["matches"], [])
        self.assertIn("timed out", str(res["warning"]))

    def test_gh_unavailable_leaves_git_evidence_intact(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        _commit(repo, "Add widget support", "2026-06-01T00:00:00")

        real_which = cbs.shutil.which
        cbs.shutil.which = lambda name: None
        try:
            res = cbs.check(Path(repo), "Touches src/widget.py.", "2026-01-01T00:00:00")
        finally:
            cbs.shutil.which = real_which

        self.assertTrue(res["checked"])
        self.assertEqual(res["pull_requests"], [])
        self.assertTrue(any(m["probe"] == "src/widget.py" for m in res["matches"]))

    def test_searched_but_clean_is_checked_true_with_no_matches(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v1')\n")
        _commit(repo, "Add widget support", "2026-01-01T00:00:00")

        res = cbs.check(Path(repo), "Touches src/widget.py.", "2026-06-01T00:00:00")

        # Distinct from `checked: false`: the question WAS asked and the
        # answer is a definite negative.
        self.assertTrue(res["checked"])
        self.assertEqual(res["matches"], [])

    def test_check_never_raises_for_any_degraded_input(self):
        repo = _init_repo()
        cases = [
            (Path("/nonexistent/path/xyz"), "text", "2026-01-01T00:00:00"),
            (Path(repo), None, "2026-01-01T00:00:00"),
            (Path(repo), "", None),
            (Path(repo), "`a.py`", 12345),
            (Path(repo), "`a.py`", ""),
        ]
        for repo_arg, text, since in cases:
            with self.subTest(text=text, since=since):
                res = cbs.check(repo_arg, text, since)
                self.assertIn("checked", res)


class TestCli(unittest.TestCase):
    """Task 2.4 -- CLI contract: `--json` shape, `--brief` reading, exit 0 on
    every path including unanswerable ones."""

    def _run(self, argv):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cbs.main(argv)
        return code, buf.getvalue()

    def _brief(self, created: str = "2026-01-01T00:00:00-07:00", focus: str = "Touches src/widget.py.") -> str:
        d = tempfile.mkdtemp(prefix="brief-staleness-brief-")
        p = Path(d) / "20260101-000000-some-brief.md"
        p.write_text(
            f"---\nid: 20260101-000000-some-brief\ncreated: '{created}'\n"
            f"focus: |-\n  {focus}\nrepo: null\nstatus: queued\n---\n\n## Focus\n\n{focus}\n",
            encoding="utf-8",
        )
        return str(p)

    def test_json_shape_has_every_documented_key(self):
        repo = _init_repo()
        code, out = self._run(["--repo", repo, "--text", "Touches src/widget.py.",
                               "--since", "2026-01-01T00:00:00", "--json"])
        self.assertEqual(code, 0)
        import json as _json
        data = _json.loads(out)
        for key in ("checked", "probes", "matches", "pull_requests", "warning"):
            self.assertIn(key, data)

    def test_brief_flag_reads_focus_and_created(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        sha = _commit(repo, "Add widget support", "2026-06-01T00:00:00")

        code, out = self._run(["--repo", repo, "--brief", self._brief(), "--json"])

        self.assertEqual(code, 0)
        import json as _json
        data = _json.loads(out)
        self.assertTrue(data["checked"])
        self.assertIn(sha, {m["sha"] for m in data["matches"]})

    def test_unreadable_brief_exits_zero_and_reports_unknown(self):
        repo = _init_repo()
        code, out = self._run(["--repo", repo, "--brief", "/nonexistent/brief.md"])
        self.assertEqual(code, 0)
        self.assertIn("unknown", out)

    def test_non_git_repo_exits_zero(self):
        d = tempfile.mkdtemp(prefix="brief-staleness-nongit-cli-")
        code, out = self._run(["--repo", d, "--text", "Touches src/widget.py.",
                               "--since", "2026-01-01T00:00:00"])
        self.assertEqual(code, 0)
        self.assertIn("unknown", out)

    def test_clean_brief_reports_no_evidence_and_exits_zero(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v1')\n")
        _commit(repo, "Add widget support", "2026-01-01T00:00:00")
        code, out = self._run(["--repo", repo, "--text", "Touches src/widget.py.",
                               "--since", "2026-06-01T00:00:00"])
        self.assertEqual(code, 0)
        self.assertIn("no evidence", out)


if __name__ == "__main__":
    unittest.main()
