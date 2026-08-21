#!/usr/bin/env python3
"""Unit tests for the pre-dispatch brief-staleness guard (stdlib unittest).

Extraction (`extract_probes()`) is pure text, no repository involved; history
search (`check()`) exercises a real throwaway git repo fixture rather than
mocking, mirroring test_check_spec_collision.py's fixture pattern and
philosophy.
"""
from __future__ import annotations

import json
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

    def test_prose_abbreviations_are_not_path_probes(self):
        # `e.g.`/`i.e.`/`a.k.a.` are prose, not paths -- once a trailing
        # period is stripped by `_strip_punct`, `e.g` looks like a bogus `.g`
        # extension to a naive `_EXT_RE` test. Covers both trailing-period
        # and bare forms, and mixed case, per the denylist's case-insensitive
        # check in `_is_path_token`.
        text = (
            "See e.g. the router, i.e. the same module, a.k.a. the guard. "
            "Also E.g without a trailing period, and I.E. in caps, and A.K.A "
            "bare and uppercase."
        )
        res = cbs.extract_probes(text)
        for bad in ("e.g", "i.e", "a.k.a", "E.g", "I.E", "A.K.A"):
            self.assertNotIn(bad, res["paths"])
        self.assertEqual(res["paths"], [])

    def test_short_extension_backtick_paths_still_qualify(self):
        # The denylist rejects abbreviation-shaped tokens by exact lowercased
        # match, not by extension length -- a real short-extension path like
        # `guard.py` must still pass, backtick-quoted or not.
        text = "See `guard.py`, `README.md`, and `deploy.sh` for details."
        res = cbs.extract_probes(text)
        self.assertIn("guard.py", res["paths"])
        self.assertIn("README.md", res["paths"])
        self.assertIn("deploy.sh", res["paths"])


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

    def test_backtick_quoted_cli_flag_is_a_symbol_probe(self):
        text = "Route through the `--tier-map` flag instead."
        res = cbs.extract_probes(text)
        self.assertIn("--tier-map", res["symbols"])
        self.assertEqual(res["paths"], [])

    def test_unquoted_cli_flag_is_a_symbol_probe(self):
        # Flags are named in prose as routinely as in backticks -- the same
        # reasoning that dropped the backtick requirement for snake_case
        # symbols applies here (see design.md).
        text = "Add the --json flag to the CLI so callers can parse output."
        res = cbs.extract_probes(text)
        self.assertIn("--json", res["symbols"])

    def test_hyphenated_prose_is_still_not_a_flag_probe(self):
        # A single leading hyphen or a hyphenated word must not qualify --
        # only a GNU long-form `--flag` shape does.
        text = "We should resolve the base ref and re-check the file-scope soon."
        res = cbs.extract_probes(text)
        self.assertEqual(res["symbols"], [])


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

    def test_commit_moments_before_capture_is_reported_as_evidence(self):
        # Regression for the same-session race: PR #325 merged 56s before a
        # duplicate brief was captured, and an exact-timestamp `--since`
        # boundary reported the brief as clean. RACE_GRACE_SECONDS=300
        # widens the boundary backward so a commit landing well within that
        # window still surfaces.
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        sha = _commit(repo, "Add widget support", "2026-06-01T00:05:30+00:00")

        res = cbs.check(
            Path(repo), "This touches src/widget.py directly.", "2026-06-01T00:10:00+00:00",
        )

        self.assertTrue(res["checked"])
        self.assertIn(sha, {m["sha"] for m in res["matches"] if m["probe"] == "src/widget.py"})

    def test_commit_before_grace_widened_boundary_is_not_evidence(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        _commit(repo, "Add widget support", "2026-06-01T00:00:00+00:00")

        res = cbs.check(
            Path(repo), "This touches src/widget.py directly.", "2026-06-01T00:10:00+00:00",
        )

        self.assertTrue(res["checked"])
        self.assertEqual(res["matches"], [])

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


class TestLookupPullRequestsGraceWindow(unittest.TestCase):
    """`_lookup_pull_requests()`'s merged-before-search-window exclusion uses
    the same grace-widened boundary `check()` computes for the git history
    search (see RACE_GRACE_SECONDS), so the two never disagree about whether
    a same-session race counts as evidence."""

    def _patch_gh(self, responses):
        """Patch `shutil.which` (so `gh` looks installed) and `subprocess.run`
        (so `gh auth status` succeeds and `gh pr view <n>` returns a canned
        response from `responses`), delegating every other call -- including
        every real `git` invocation `check()` still needs -- to the real
        `subprocess.run`. Returns the two originals for restoration."""
        real_which = cbs.shutil.which
        real_run = cbs.subprocess.run

        def fake_which(name):
            return "/usr/bin/gh" if name == "gh" else real_which(name)

        def fake_run(args, **kwargs):
            if isinstance(args, list) and args[:1] == ["gh"]:
                if "auth" in args:
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if "view" in args:
                    num = args[args.index("view") + 1]
                    if num in responses:
                        return subprocess.CompletedProcess(
                            args, 0, stdout=json.dumps(responses[num]), stderr="",
                        )
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="not found")
            return real_run(args, **kwargs)

        cbs.shutil.which = fake_which
        cbs.subprocess.run = fake_run
        return real_which, real_run

    def test_pr_merged_moments_before_capture_is_kept(self):
        repo = _init_repo()
        real_which, real_run = self._patch_gh({
            "42": {
                "number": 42, "title": "close race", "url": "https://example/42",
                "state": "MERGED", "mergedAt": "2026-06-01T00:05:30+00:00",
            },
        })
        try:
            res = cbs.check(Path(repo), "Delivered by PR #42.", "2026-06-01T00:10:00+00:00")
        finally:
            cbs.shutil.which = real_which
            cbs.subprocess.run = real_run

        self.assertTrue(res["checked"])
        self.assertIn(42, {pr["number"] for pr in res["pull_requests"]})

    def test_pr_merged_well_before_grace_boundary_is_excluded(self):
        repo = _init_repo()
        real_which, real_run = self._patch_gh({
            "43": {
                "number": 43, "title": "old unrelated work", "url": "https://example/43",
                "state": "MERGED", "mergedAt": "2026-06-01T00:00:00+00:00",
            },
        })
        try:
            res = cbs.check(Path(repo), "Delivered by PR #43.", "2026-06-01T00:10:00+00:00")
        finally:
            cbs.shutil.which = real_which
            cbs.subprocess.run = real_run

        self.assertTrue(res["checked"])
        self.assertNotIn(43, {pr["number"] for pr in res["pull_requests"]})


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


class TestReadBrief(unittest.TestCase):
    """Task 3.3 -- `_read_brief()` prefers `original-created:` over `created:`
    for the `since` value it hands back, and still falls back to `created:`
    unchanged when a brief carries no `original-created:` field."""

    def _write_brief(self, extra_frontmatter: str, focus: str = "Touches src/widget.py.") -> Path:
        d = tempfile.mkdtemp(prefix="brief-staleness-readbrief-")
        p = Path(d) / "20260101-000000-some-brief.md"
        p.write_text(
            "---\nid: 20260101-000000-some-brief\ncreated: '2026-01-01T00:00:00-07:00'\n"
            f"{extra_frontmatter}"
            f"focus: |-\n  {focus}\nrepo: null\nstatus: queued\n---\n\n## Focus\n\n{focus}\n",
            encoding="utf-8",
        )
        return p

    def test_original_created_present_wins_as_since(self):
        path = self._write_brief("original-created: '2025-11-01T00:00:00-07:00'\n")

        text, since, error = cbs._read_brief(path)

        self.assertIsNone(error)
        self.assertEqual(since, "2025-11-01T00:00:00-07:00")
        self.assertIn("src/widget.py", text)

    def test_only_created_present_falls_back_to_created_unchanged(self):
        path = self._write_brief("")

        text, since, error = cbs._read_brief(path)

        self.assertIsNone(error)
        self.assertEqual(since, "2026-01-01T00:00:00-07:00")
        self.assertIn("src/widget.py", text)

    def test_released_at_present_wins_over_original_created_and_created(self):
        path = self._write_brief(
            "original-created: '2025-11-01T00:00:00-07:00'\n"
            "released-at: '2026-08-12T02:35:29-07:00'\n"
        )

        text, since, error = cbs._read_brief(path)

        self.assertIsNone(error)
        self.assertEqual(since, "2026-08-12T02:35:29-07:00")
        self.assertIn("src/widget.py", text)

    def test_released_at_absent_falls_back_to_original_created_unchanged(self):
        path = self._write_brief("original-created: '2025-11-01T00:00:00-07:00'\n")

        text, since, error = cbs._read_brief(path)

        self.assertIsNone(error)
        self.assertEqual(since, "2025-11-01T00:00:00-07:00")
        self.assertIn("src/widget.py", text)


class TestRecheckSearchBoundary(unittest.TestCase):
    """Task 2.2 -- end-to-end: a rechecked brief's `released-at:` excludes a
    commit that landed before that recheck (already accounted for), even
    though the same commit lands after the brief's original `created:`."""

    def _write_brief(self, extra_frontmatter: str, focus: str = "Touches src/widget.py.") -> Path:
        d = tempfile.mkdtemp(prefix="brief-staleness-recheck-")
        p = Path(d) / "20260101-000000-some-brief.md"
        p.write_text(
            "---\nid: 20260101-000000-some-brief\ncreated: '2026-01-01T00:00:00-07:00'\n"
            f"{extra_frontmatter}"
            f"focus: |-\n  {focus}\nrepo: null\nstatus: queued\n---\n\n## Focus\n\n{focus}\n",
            encoding="utf-8",
        )
        return p

    def test_commit_between_created_and_released_at_is_excluded(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        _commit(repo, "Add widget support", "2026-03-01T00:00:00+00:00")

        path = self._write_brief("released-at: '2026-08-12T02:35:29-07:00'\n")
        text, since, error = cbs._read_brief(path)
        self.assertIsNone(error)

        res = cbs.check(Path(repo), text, since)

        self.assertTrue(res["checked"])
        self.assertEqual(res["matches"], [])

    def test_same_commit_is_evidence_without_released_at(self):
        # Control: the identical commit IS reported once released-at: is
        # absent and the boundary falls back to created: (2026-01-01), which
        # predates the commit.
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        sha = _commit(repo, "Add widget support", "2026-03-01T00:00:00+00:00")

        path = self._write_brief("")
        text, since, error = cbs._read_brief(path)
        self.assertIsNone(error)

        res = cbs.check(Path(repo), text, since)

        self.assertTrue(res["checked"])
        self.assertIn(sha, {m["sha"] for m in res["matches"] if m["probe"] == "src/widget.py"})


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


class TestSearchResearchNotesWindow(unittest.TestCase):
    """Task 4.1 -- `_search_research_notes()`'s window boundary: the search
    window is `[since_str - RESEARCH_LOOKBACK_DAYS days, since_str +
    RACE_GRACE_SECONDS]` (see `_offset_since`), anchored to the brief's own
    `since_str`, not wall-clock "now"."""

    NOTE_PATH = "docs/specs/research/investigation.md"

    def _write_note(self, repo: str, content: str, date_iso: str) -> str:
        _write(repo, self.NOTE_PATH, content)
        return _commit(repo, "Add research note", date_iso)

    def test_note_touched_inside_lookback_window_is_a_match(self):
        # 17 days before since_str -- inside the 30-day RESEARCH_LOOKBACK_DAYS
        # window, which opens 2026-05-02.
        repo = _init_repo()
        sha = self._write_note(repo, "This investigation covers widget.py in depth.\n", "2026-05-15T00:00:00+00:00")

        probes = {"paths": ["widget.py"], "symbols": []}
        matches, warning = cbs._search_research_notes(
            Path(repo), "main", probes, "2026-06-01T00:00:00+00:00", cbs.SUBPROCESS_TIMEOUT_SECONDS,
        )

        found = [m for m in matches if m["path"] == self.NOTE_PATH and m["probe"] == "widget.py"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["sha"], sha)
        self.assertEqual(found[0]["kind"], "path")

    def test_note_touched_before_window_start_is_not_a_match(self):
        # 61 days before since_str -- before the 30-day lookback window opens
        # (2026-05-02), so this note falls outside `git log --since`.
        repo = _init_repo()
        self._write_note(repo, "This investigation covers widget.py in depth.\n", "2026-04-01T00:00:00+00:00")

        probes = {"paths": ["widget.py"], "symbols": []}
        matches, warning = cbs._search_research_notes(
            Path(repo), "main", probes, "2026-06-01T00:00:00+00:00", cbs.SUBPROCESS_TIMEOUT_SECONDS,
        )

        self.assertEqual(matches, [])

    def test_note_touched_moments_after_capture_within_grace_is_a_match(self):
        # 3 minutes after since_str -- within RACE_GRACE_SECONDS (300s), the
        # same same-session-race grace window the forward-looking history
        # search applies via `_widen_since`.
        repo = _init_repo()
        sha = self._write_note(repo, "This investigation covers widget.py in depth.\n", "2026-06-01T00:03:00+00:00")

        probes = {"paths": ["widget.py"], "symbols": []}
        matches, warning = cbs._search_research_notes(
            Path(repo), "main", probes, "2026-06-01T00:00:00+00:00", cbs.SUBPROCESS_TIMEOUT_SECONDS,
        )

        found = [m for m in matches if m["path"] == self.NOTE_PATH and m["probe"] == "widget.py"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["sha"], sha)

    def test_pull_request_only_probe_set_yields_no_research_note_matches(self):
        # A note touched squarely inside the window still yields nothing when
        # `probes["paths"]`/`probes["symbols"]` are both empty -- only a
        # pull-request probe was extracted from the brief.
        repo = _init_repo()
        self._write_note(repo, "This investigation covers widget.py in depth.\n", "2026-05-15T00:00:00+00:00")

        probes = {"paths": [], "symbols": [], "pull_requests": ["89"]}
        matches, warning = cbs._search_research_notes(
            Path(repo), "main", probes, "2026-06-01T00:00:00+00:00", cbs.SUBPROCESS_TIMEOUT_SECONDS,
        )

        self.assertEqual(matches, [])


class TestResearchNoteIndependentDegradation(unittest.TestCase):
    """Task 4.2 -- the research-note phase and the forward-looking history
    phase in `check()` degrade independently: a failure in one leaves the
    other's results untouched, and each failure is folded into the combined
    `warning` string rather than swallowed."""

    def test_research_note_search_failure_leaves_matches_pull_requests_checked_untouched(self):
        # Only the research-note candidate-listing `git log` call (the one
        # naming `RESEARCH_NOTES_GLOB`) is made to fail/time out; the
        # forward-looking history search's own `git log` calls, and a faked
        # `gh`, both run for real -- so `pull_requests` surviving untouched
        # is actually exercised rather than trivially `[]` because `gh` was
        # made to look absent (see 4.2-review.md major issue #1).
        repo = _init_repo()
        _write(repo, "src/widget.py", "print('v2')\n")
        sha = _commit(repo, "Add widget support", "2026-06-01T00:00:00")

        real_run = cbs.subprocess.run
        real_which = cbs.shutil.which

        def fake_which(name):
            return "/usr/bin/gh" if name == "gh" else None

        def fake_run(args, **kwargs):
            if isinstance(args, list) and cbs.RESEARCH_NOTES_GLOB in args:
                raise cbs.subprocess.TimeoutExpired(cmd=args, timeout=1)
            if isinstance(args, list) and args[:1] == ["gh"]:
                if "auth" in args:
                    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
                if "view" in args:
                    num = args[args.index("view") + 1]
                    if num == "42":
                        return subprocess.CompletedProcess(
                            args, 0, stdout=json.dumps({
                                "number": 42, "title": "close race", "url": "https://example/42",
                                "state": "MERGED", "mergedAt": "2026-01-01T00:00:00+00:00",
                            }), stderr="",
                        )
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="not found")
            return real_run(args, **kwargs)

        cbs.subprocess.run = fake_run
        cbs.shutil.which = fake_which
        try:
            res = cbs.check(
                Path(repo), "Touches src/widget.py. Delivered by PR #42.", "2026-01-01T00:00:00",
            )
        finally:
            cbs.subprocess.run = real_run
            cbs.shutil.which = real_which

        self.assertTrue(res["checked"])
        self.assertIn(sha, {m["sha"] for m in res["matches"]})
        self.assertIn(42, {pr["number"] for pr in res["pull_requests"]})
        self.assertEqual(res["research_notes"], [])
        self.assertIn("research note", str(res["warning"]))

    def test_history_search_failure_does_not_suppress_research_notes(self):
        # Only the forward-looking history search's `git log` calls (the ones
        # carrying `_LOG_FORMAT`, unique to `_search_probe()`) are made to
        # fail/time out; the research-note phase's own git calls -- listing,
        # last-touch, and content-read -- run for real.
        repo = _init_repo()
        note_path = "docs/specs/research/investigation.md"
        _write(repo, note_path, "This investigation covers widget.py in depth.\n")
        sha = _commit(repo, "Add research note", "2026-01-15T00:00:00+00:00")

        real_run = cbs.subprocess.run

        def _fail_forward_search(*args, **kwargs):
            if args and isinstance(args[0], list) and f"--format={cbs._LOG_FORMAT}" in args[0]:
                raise cbs.subprocess.TimeoutExpired(cmd=args[0], timeout=1)
            return real_run(*args, **kwargs)

        cbs.subprocess.run = _fail_forward_search
        try:
            res = cbs.check(Path(repo), "The fix lives in widget.py somewhere.", "2026-01-20T00:00:00+00:00")
        finally:
            cbs.subprocess.run = real_run

        self.assertTrue(res["checked"])
        self.assertEqual(res["matches"], [])
        self.assertIn("timed out", str(res["warning"]))
        found = [m for m in res["research_notes"] if m["path"] == note_path]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["sha"], sha)


class TestFormatVerifiedAbsentEvidence(unittest.TestCase):
    """Task 1.3 -- coverage for `format_verified_absent_evidence`, mirroring
    `test_check_brief_predicate.py`'s style of asserting on the exact
    rendered string shape rather than substring presence alone."""

    MATCH = {"sha": "abc1234", "kind": "path", "probe": "src/widget.py"}
    PR = {"number": 42, "title": "close race", "url": "https://example/42"}

    def test_matches_only(self):
        out = cbs.format_verified_absent_evidence([self.MATCH], [], "already removed on disk")
        self.assertEqual(
            out,
            "File-state verification found the brief's described work "
            "verifiably absent despite 1 matched commit(s) and 0 matched "
            "pull request(s) (abc1234 (path probe: src/widget.py)): "
            "already removed on disk. Proceeded automatically without an "
            "operator prompt.",
        )

    def test_pull_requests_only(self):
        out = cbs.format_verified_absent_evidence([], [self.PR], "reverted after merge")
        self.assertEqual(
            out,
            "File-state verification found the brief's described work "
            "verifiably absent despite 0 matched commit(s) and 1 matched "
            "pull request(s) (PR #42): reverted after merge. Proceeded "
            "automatically without an operator prompt.",
        )

    def test_matches_and_pull_requests_both(self):
        out = cbs.format_verified_absent_evidence([self.MATCH], [self.PR], "no trace remains")
        self.assertEqual(
            out,
            "File-state verification found the brief's described work "
            "verifiably absent despite 1 matched commit(s) and 1 matched "
            "pull request(s) (abc1234 (path probe: src/widget.py), PR #42): "
            "no trace remains. Proceeded automatically without an operator "
            "prompt.",
        )

    def test_no_matches_or_pull_requests_renders_empty_citation_list(self):
        out = cbs.format_verified_absent_evidence([], [], "nothing was matched")
        self.assertEqual(
            out,
            "File-state verification found the brief's described work "
            "verifiably absent despite 0 matched commit(s) and 0 matched "
            "pull request(s) (): nothing was matched. Proceeded "
            "automatically without an operator prompt.",
        )


class TestFormatVerifiedPresentClosureNote(unittest.TestCase):
    """Task 1.3 -- coverage for `format_verified_present_closure_note`,
    mirroring `test_check_brief_predicate.py`'s style of asserting on the
    exact rendered string shape rather than substring presence alone."""

    MATCH = {"sha": "abc1234", "kind": "symbol", "probe": "apply_to_tasks"}
    PR = {"number": 89, "title": "ship the feature", "url": "https://example/89"}

    def test_matches_only(self):
        out = cbs.format_verified_present_closure_note([self.MATCH], [], "helper exists and is wired in")
        self.assertEqual(
            out,
            "Closed as already-delivered: file-state verification found "
            "the brief's described work verifiably present, confirmed by "
            "1 matched commit(s) and 0 matched pull request(s) "
            "(abc1234 (symbol probe: apply_to_tasks)): helper exists and "
            "is wired in. Surfaced by the file-state verification step; "
            "closed automatically without an operator prompt.",
        )

    def test_pull_requests_only(self):
        out = cbs.format_verified_present_closure_note([], [self.PR], "PR shipped the described change")
        self.assertEqual(
            out,
            "Closed as already-delivered: file-state verification found "
            "the brief's described work verifiably present, confirmed by "
            "0 matched commit(s) and 1 matched pull request(s) (PR #89): "
            "PR shipped the described change. Surfaced by the file-state "
            "verification step; closed automatically without an operator "
            "prompt.",
        )

    def test_matches_and_pull_requests_both(self):
        out = cbs.format_verified_present_closure_note([self.MATCH], [self.PR], "confirmed on disk and in PR")
        self.assertEqual(
            out,
            "Closed as already-delivered: file-state verification found "
            "the brief's described work verifiably present, confirmed by "
            "1 matched commit(s) and 1 matched pull request(s) "
            "(abc1234 (symbol probe: apply_to_tasks), PR #89): confirmed "
            "on disk and in PR. Surfaced by the file-state verification "
            "step; closed automatically without an operator prompt.",
        )

    def test_no_matches_or_pull_requests_renders_empty_citation_list(self):
        out = cbs.format_verified_present_closure_note([], [], "confirmed by direct inspection")
        self.assertEqual(
            out,
            "Closed as already-delivered: file-state verification found "
            "the brief's described work verifiably present, confirmed by "
            "0 matched commit(s) and 0 matched pull request(s) (): "
            "confirmed by direct inspection. Surfaced by the file-state "
            "verification step; closed automatically without an operator "
            "prompt.",
        )


if __name__ == "__main__":
    unittest.main()
