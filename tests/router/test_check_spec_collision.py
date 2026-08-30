#!/usr/bin/env python3
"""Unit tests for the pre-dispatch spec-collision guard (stdlib unittest).

Exercises real throwaway fixture directories/git repos rather than mocking --
`verify()`'s logic *is* the git-tracking plumbing (via dashboard.py's
`_git_tracked`/`_task_files_are_shipped`), so a fake would just re-assert the
mock. Mirrors test_check_repo_freshness.py's shape and philosophy.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router import check_spec_collision as csc
from worktrail.workqueue import decisions


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _init_repo(branch: str = "main") -> str:
    d = tempfile.mkdtemp(prefix="spec-collision-")
    _git(d, "init", "-q", "-b", branch)
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (Path(d) / "README.md").write_text("base\n", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_spec(
    repo: str,
    spec_id: str,
    status: str = "Implemented",
    feature_summary: str = "Example feature for tests.",
    spec_name: str = "example-feature",
) -> Path:
    """Writes docs/specs/<spec_id>/<date>--<spec_name>.md with a **Status**:
    header and a **Feature Summary**: line. Returns the spec dir."""
    spec_dir = Path(repo) / "docs" / "specs" / spec_id
    _write(
        spec_dir / f"2026-01-01--{spec_name}.md",
        f"# {spec_id}\n\n**Status**: {status}\n\n"
        f"**Feature Summary**: {feature_summary}\n",
    )
    return spec_dir


def _make_task(
    spec_dir: Path, task_id: str, files: list, status: str = "completed"
) -> None:
    files_literal = ", ".join(files)
    _write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"---\nid: {task_id}\nstatus: {status}\nfiles: [{files_literal}]\n---\n"
        f"# {task_id}\n",
    )


class TestCheckNoSpecsDir(unittest.TestCase):
    def test_missing_docs_specs_dir_is_unchecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = csc.check(Path(tmp))
            self.assertFalse(res["checked"])
            self.assertEqual(res["candidates"], [])
            self.assertIsNone(res["warning"])


class TestCheckEmptySpecsDir(unittest.TestCase):
    def test_existing_empty_specs_dir_is_checked_with_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docs" / "specs").mkdir(parents=True)
            res = csc.check(Path(tmp))
            self.assertTrue(res["checked"])
            self.assertEqual(res["candidates"], [])


class TestCheckWithCandidates(unittest.TestCase):
    def test_populates_candidates_with_spec_id_and_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(
                tmp,
                "007-example-feature",
                feature_summary="Donors can search nonprofits.",
            )
            res = csc.check(Path(tmp))
            self.assertTrue(res["checked"])
            self.assertEqual(len(res["candidates"]), 1)
            candidate = res["candidates"][0]
            self.assertEqual(candidate["spec_id"], "007-example-feature")
            self.assertEqual(candidate["title"], "Example Feature")
            self.assertEqual(
                candidate["feature_summary"], "Donors can search nonprofits."
            )
            self.assertIn("stage", candidate)

    def test_multiple_specs_all_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-first", spec_name="first")
            _make_spec(tmp, "002-second", spec_name="second")
            res = csc.check(Path(tmp))
            self.assertTrue(res["checked"])
            ids = {c["spec_id"] for c in res["candidates"]}
            self.assertEqual(ids, {"001-first", "002-second"})

    def test_candidate_carries_its_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            res = csc.check(Path(tmp))
            self.assertEqual(res["candidates"][0]["root"], "docs/specs")


class TestCheckExtraRoots(unittest.TestCase):
    """Requirement: a repo with both a devkit `docs/specs/` tree and an
    OpenSpec `openspec/` tree gets candidates from both when the caller
    passes `extra_roots`, not only whichever root it defaults to (the gap
    behind brief 20260830-005833: dispatching against worktrail's own
    OpenSpec-format specs surfaced only an unrelated docs/specs/ candidate
    and missed the real controlling openspec/specs/ spec entirely)."""

    def test_extra_root_candidates_are_merged_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example", feature_summary="Devkit-format spec.")
            _write(
                Path(tmp) / "openspec" / "specs" / "backlog-seeding" / "spec.md",
                "# Backlog Seeding\n\n## Purpose\n\nOpenSpec-format capability.\n",
            )
            res = csc.check(Path(tmp), extra_roots=["openspec"])
            self.assertTrue(res["checked"])
            ids = {c["spec_id"] for c in res["candidates"]}
            self.assertEqual(ids, {"001-example", "backlog-seeding"})

    def test_merged_candidates_are_tagged_with_their_own_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            _write(
                Path(tmp) / "openspec" / "specs" / "backlog-seeding" / "spec.md",
                "# Backlog Seeding\n\n## Purpose\n\nOpenSpec-format capability.\n",
            )
            res = csc.check(Path(tmp), extra_roots=["openspec"])
            roots = {c["spec_id"]: c["root"] for c in res["candidates"]}
            self.assertEqual(roots["001-example"], "docs/specs")
            self.assertEqual(roots["backlog-seeding"], "openspec")

    def test_nonexistent_extra_root_is_a_silent_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            res = csc.check(Path(tmp), extra_roots=["openspec"])
            self.assertTrue(res["checked"])
            self.assertEqual(len(res["candidates"]), 1)

    def test_extra_root_alone_is_checked_when_primary_root_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "openspec" / "specs" / "backlog-seeding" / "spec.md",
                "# Backlog Seeding\n\n## Purpose\n\nOpenSpec-format capability.\n",
            )
            res = csc.check(Path(tmp), extra_roots=["openspec"])
            self.assertTrue(res["checked"])
            self.assertEqual(len(res["candidates"]), 1)
            self.assertEqual(res["candidates"][0]["spec_id"], "backlog-seeding")

    def test_verify_never_raises_against_an_openspec_sourced_candidate(self):
        """`verify()` looks up `repo/root/spec_id` directly and has no
        OpenSpec `changes/`-vs-`specs/` indirection of its own (unlike
        `check()`, which delegates that to `overlap_check.scan()`) -- an
        OpenSpec-sourced candidate's `root` tag is for the calling agent's own
        judgment, not necessarily a directly verifiable `--verify` root.
        Passing it through anyway must still degrade cleanly, never raise."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                Path(tmp) / "openspec" / "specs" / "backlog-seeding" / "spec.md",
                "# Backlog Seeding\n\n## Purpose\n\nOpenSpec-format capability.\n",
            )
            res = csc.check(Path(tmp), extra_roots=["openspec"])
            candidate = res["candidates"][0]
            verified = csc.verify(
                Path(tmp), candidate["spec_id"], root=candidate["root"]
            )
            self.assertFalse(verified["confirmed"])
            self.assertIsNotNone(verified["warning"])


class TestCheckDegrade(unittest.TestCase):
    def test_scan_import_failure_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            original = csc._scan
            csc._scan = None
            try:
                res = csc.check(Path(tmp))
            finally:
                csc._scan = original
            self.assertFalse(res["checked"])
            self.assertEqual(res["candidates"], [])
            self.assertIsNotNone(res["warning"])
            self.assertIn("overlap_check import failed", res["warning"])

    def test_scan_exception_degrades_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            original = csc._scan

            def _raiser(_specs_root):
                raise RuntimeError("simulated malformed frontmatter")

            csc._scan = _raiser
            try:
                res = csc.check(Path(tmp))
            finally:
                csc._scan = original
            self.assertFalse(res["checked"])
            self.assertEqual(res["candidates"], [])
            self.assertIsNotNone(res["warning"])


class TestVerifyStatusNotImplemented(unittest.TestCase):
    def test_non_implemented_status_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example", status="Draft")
            res = csc.verify(Path(tmp), "001-example")
            self.assertFalse(res["confirmed"])
            self.assertEqual(res["status"], "Draft")
            self.assertIsNotNone(res["warning"])


class TestVerifyFilesNotTracked(unittest.TestCase):
    def test_untracked_task_files_are_not_confirmed(self):
        repo = _init_repo()
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('untracked')\n")  # written but never git add/commit
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        res = csc.verify(Path(repo), "001-example")
        self.assertFalse(res["confirmed"])
        self.assertEqual(res["status"], "Implemented")
        self.assertEqual(res["files"], ["src/feature.py"])
        self.assertIsNotNone(res["warning"])


class TestVerifyFilesTracked(unittest.TestCase):
    def test_all_committed_task_files_are_confirmed(self):
        repo = _init_repo()
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        res = csc.verify(Path(repo), "001-example")
        self.assertTrue(res["confirmed"])
        self.assertEqual(res["status"], "Implemented")
        self.assertEqual(res["files"], ["src/feature.py"])
        self.assertIsNone(res["warning"])


class TestVerifyEdgeCases(unittest.TestCase):
    def test_no_tasks_dir_degrades_without_raising(self):
        repo = _init_repo()
        _make_spec(repo, "001-example", status="Implemented")
        # No tasks/ dir and no traceability-matrix.md -- pre-task-split spec.
        res = csc.verify(Path(repo), "001-example")
        self.assertFalse(res["confirmed"])
        self.assertEqual(res["files"], [])
        self.assertIsNotNone(res["warning"])
        self.assertIn("no task files found", res["warning"])

    def test_missing_spec_document_degrades_without_raising(self):
        repo = _init_repo()
        spec_dir = Path(repo) / "docs" / "specs" / "001-example"
        spec_dir.mkdir(parents=True)
        # No spec .md file at all -- find_spec_file() finds nothing to read
        # a Status:/frontmatter header from.
        res = csc.verify(Path(repo), "001-example")
        self.assertFalse(res["confirmed"])
        self.assertIsNotNone(res["warning"])

    def test_unknown_spec_id_degrades_without_raising(self):
        repo = _init_repo()
        res = csc.verify(Path(repo), "999-does-not-exist")
        self.assertFalse(res["confirmed"])
        self.assertIsNotNone(res["warning"])


class TestCheckThenVerifyIntegration(unittest.TestCase):
    def test_full_chain_matches_confirmed_collision_shape(self):
        repo = _init_repo()
        spec_dir = _make_spec(
            repo,
            "001-example",
            status="Implemented",
            feature_summary="Donors can search nonprofits.",
        )
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        checked = csc.check(Path(repo))
        self.assertTrue(checked["checked"])
        candidate = next(
            c for c in checked["candidates"] if c["spec_id"] == "001-example"
        )
        self.assertEqual(candidate["title"], "Example Feature")

        verified = csc.verify(Path(repo), candidate["spec_id"])
        self.assertTrue(verified["confirmed"])
        self.assertEqual(verified["status"], "Implemented")
        self.assertEqual(verified["files"], ["src/feature.py"])


def _write_openspec_tasks(repo: str, change_id: str, body: str) -> None:
    _write(Path(repo) / "openspec" / "changes" / change_id / "tasks.md", body)


def _write_openspec_proposal(repo: str, change_id: str, capability: str) -> None:
    _write(
        Path(repo) / "openspec" / "changes" / change_id / "proposal.md",
        f"## Capabilities\n\n{capability}\n",
    )


class TestCheckTaskCandidatesDistinctFromWholeSpecMatch(unittest.TestCase):
    """Requirement: Dispatch-Time Guard Distinguishes Task-Level Matches From
    Whole-Spec Matches -- `task_candidates` is a separate key from
    `candidates`, populated only when an explicit `target` resolves to an
    OpenSpec change with open, unchecked tasks."""

    def test_target_with_unchecked_tasks_populates_task_candidates_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_openspec_proposal(tmp, "add-auth", "Users can log in.")
            _write_openspec_tasks(
                tmp,
                "add-auth",
                "## 1. Login\n\n"
                "- [ ] 1.1 Add login form\n"
                "- [x] 1.2 Add logout button\n",
            )
            res = csc.check(Path(tmp), root="openspec", target="add-auth")

            self.assertTrue(res["checked"])
            self.assertEqual(len(res["task_candidates"]), 1)
            task = res["task_candidates"][0]
            self.assertEqual(task["spec_id"], "add-auth")
            self.assertEqual(task["task_id"], "1.1")
            self.assertIn("Add login form", task["task_text"])
            self.assertFalse(task["checked"])

    def test_task_candidates_never_merged_into_whole_spec_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_openspec_proposal(tmp, "add-auth", "Users can log in.")
            _write_openspec_tasks(
                tmp,
                "add-auth",
                "## 1. Login\n\n- [ ] 1.1 Add login form\n",
            )
            res = csc.check(Path(tmp), root="openspec", target="add-auth")

            self.assertEqual(len(res["candidates"]), 1)
            self.assertEqual(res["candidates"][0]["spec_id"], "add-auth")
            # The whole-spec candidate shape carries no task_id/checked --
            # a task-level match is structurally distinguishable from it.
            self.assertNotIn("task_id", res["candidates"][0])
            self.assertNotIn("checked", res["candidates"][0])
            self.assertEqual(len(res["task_candidates"]), 1)
            self.assertIn("task_id", res["task_candidates"][0])

    def test_no_target_leaves_task_candidates_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_openspec_proposal(tmp, "add-auth", "Users can log in.")
            _write_openspec_tasks(
                tmp,
                "add-auth",
                "## 1. Login\n\n- [ ] 1.1 Add login form\n",
            )
            res = csc.check(Path(tmp), root="openspec")
            self.assertTrue(res["checked"])
            self.assertEqual(res["task_candidates"], [])

    def test_target_change_fully_checked_yields_no_task_level_match(self):
        """No task-level match (every task already checked) leaves the rest
        of `check()`'s output unmodified -- `checked`/`candidates` unaffected,
        `task_candidates` simply empty."""
        with tempfile.TemporaryDirectory() as tmp:
            _write_openspec_proposal(tmp, "add-auth", "Users can log in.")
            _write_openspec_tasks(
                tmp,
                "add-auth",
                "## 1. Login\n\n- [x] 1.1 Add login form\n- [x] 1.2 Add logout button\n",
            )
            res = csc.check(Path(tmp), root="openspec", target="add-auth")
            self.assertTrue(res["checked"])
            self.assertEqual(len(res["candidates"]), 1)
            self.assertEqual(res["task_candidates"], [])

    def test_target_with_no_readable_tasks_md_yields_no_task_level_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_openspec_proposal(tmp, "add-auth", "Users can log in.")
            res = csc.check(Path(tmp), root="openspec", target="add-auth")
            self.assertTrue(res["checked"])
            self.assertEqual(res["task_candidates"], [])

    def test_devkit_shaped_root_with_target_yields_no_task_level_match(self):
        """A devkit-shaped root has no per-task granularity -- `target` is
        ignored and `task_candidates` stays empty, unchanged behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "007-example-feature")
            res = csc.check(Path(tmp), target="add-auth")
            self.assertTrue(res["checked"])
            self.assertEqual(len(res["candidates"]), 1)
            self.assertEqual(res["task_candidates"], [])


class TestTaskLevelMatchNeverAutoCloses(unittest.TestCase):
    """Requirement: Dispatch-Time Guard Distinguishes Task-Level Matches From
    Whole-Spec Matches -- a task-level match (open, unchecked work) is never
    grounds for the existing auto-close-on-Implemented behavior; only
    `verify()`'s explicit `Implemented` + shipped-artifacts check can confirm
    a collision (Route C/D auto-close semantics)."""

    def test_task_level_match_alone_does_not_confirm_a_collision(self):
        """A change with only open, unchecked tasks (a task-level match) has
        no `docs/specs/<target>` entry for `verify()` to confirm against --
        confirming it never happens off `task_candidates` data alone."""
        with tempfile.TemporaryDirectory() as tmp:
            _write_openspec_proposal(tmp, "add-auth", "Users can log in.")
            _write_openspec_tasks(
                tmp,
                "add-auth",
                "## 1. Login\n\n- [ ] 1.1 Add login form\n",
            )
            checked = csc.check(Path(tmp), root="openspec", target="add-auth")
            self.assertEqual(len(checked["task_candidates"]), 1)

            verified = csc.verify(Path(tmp), "add-auth")
            self.assertFalse(verified["confirmed"])
            self.assertIsNotNone(verified["warning"])

    def test_task_candidates_present_alongside_a_separate_confirmed_whole_spec_match(
        self,
    ):
        """A task-level match on `target` (open, unchecked work under
        `openspec/`) and a confirmed whole-spec match on a different,
        already-`Implemented` spec (under `docs/specs/`) coexist without
        either contaminating the other's key or code path -- `verify()` is
        only ever called against the whole-spec candidate, never against
        anything from `task_candidates`."""
        repo = _init_repo()
        _write_openspec_proposal(repo, "add-auth", "Users can log in.")
        _write_openspec_tasks(
            repo,
            "add-auth",
            "## 1. Login\n\n- [ ] 1.1 Add login form\n",
        )
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target_file = Path(repo) / "src" / "feature.py"
        _write(target_file, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        checked = csc.check(Path(repo), root="openspec", target="add-auth")
        self.assertEqual(len(checked["task_candidates"]), 1)
        self.assertEqual(checked["task_candidates"][0]["task_id"], "1.1")

        # The whole-spec candidate lives under a different root entirely --
        # `check()`'s `root` only governs whole-spec candidates/task
        # candidates together, `verify()` takes its own independent `root`.
        verified = csc.verify(Path(repo), "001-example", root="docs/specs")
        self.assertTrue(verified["confirmed"])


class TestPendingDecisionEnvelope(unittest.TestCase):
    """Task 2.1 (pending-user-decision-dispatch-contract) -- a CONFIRMED
    collision carries the provider-neutral, versioned pending-decision
    envelope under `pending_decision`; every other outcome carries none."""

    def _confirm_collision(self, repo: str) -> None:
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

    def test_confirmed_collision_carries_valid_envelope(self):
        repo = _init_repo()
        self._confirm_collision(repo)
        res = csc.verify(Path(repo), "001-example")
        self.assertTrue(res["confirmed"])
        envelope = res["pending_decision"]
        self.assertIsNotNone(envelope)
        parsed = decisions.parse_pending_decision_envelope(envelope)
        self.assertEqual(parsed["schema"], decisions.DECISION_ENVELOPE_SCHEMA)
        self.assertEqual(parsed["version"], decisions.DECISION_ENVELOPE_VERSION)
        self.assertEqual(parsed["status"], "pending")
        self.assertTrue(parsed["decision_id"].startswith("dec-"))
        self.assertEqual(parsed["provenance"]["source"], csc.GUARD_SOURCE)
        self.assertEqual(parsed["provenance"]["subject"], "001-example")
        self.assertGreaterEqual(len(parsed["options"]), 2)
        self.assertTrue(all(o.strip() for o in parsed["options"]))

    def test_identity_is_deterministic_across_re_runs(self):
        repo = _init_repo()
        self._confirm_collision(repo)
        first = csc.verify(Path(repo), "001-example")["pending_decision"]
        second = csc.verify(Path(repo), "001-example")["pending_decision"]
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(
            first["decision_id"],
            decisions.decision_identity(
                csc.GUARD_SOURCE,
                str(Path(repo).resolve()),
                "001-example",
                csc.DECISION_QUESTION,
            ),
        )

    def test_provenance_threads_run_id_and_dispatch_mode(self):
        repo = _init_repo()
        self._confirm_collision(repo)
        res = csc.verify(
            Path(repo),
            "001-example",
            run_id="go-20260825-101010",
            dispatch_mode="adapter",
        )
        prov = res["pending_decision"]["provenance"]
        self.assertEqual(prov["run_id"], "go-20260825-101010")
        self.assertEqual(prov["dispatch_mode"], "adapter")

    def test_unconfirmed_verify_leaves_pending_decision_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example", status="Draft")
            res = csc.verify(Path(tmp), "001-example")
            self.assertFalse(res["confirmed"])
            self.assertIsNone(res["pending_decision"])

    def test_degraded_verify_leaves_pending_decision_none(self):
        repo = _init_repo()
        res = csc.verify(Path(repo), "999-does-not-exist")
        self.assertFalse(res["confirmed"])
        self.assertIsNone(res["pending_decision"])

    def test_check_output_never_carries_a_pending_decision(self):
        """check() is pure extraction -- it makes no semantic judgment, so it
        never presumes a collision worth a decision."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example", status="Implemented")
            res = csc.check(Path(tmp))
            self.assertTrue(res["checked"])
            self.assertTrue(res["candidates"])
            self.assertIsNone(res["pending_decision"])

    def test_unavailable_decision_primitives_degrade_to_none_without_raising(self):
        repo = _init_repo()
        self._confirm_collision(repo)
        original = csc._decision_helpers
        csc._decision_helpers = lambda: (None, None)
        try:
            res = csc.verify(Path(repo), "001-example")
        finally:
            csc._decision_helpers = original
        self.assertTrue(res["confirmed"])
        self.assertIsNone(res["pending_decision"])


class TestCli(unittest.TestCase):
    def test_json_check_output_matches_check_result(self):
        import io
        import json
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = csc.main(["--repo", tmp, "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue()), csc.check(Path(tmp)))

    def test_extra_root_flag_merges_candidates(self):
        import io
        import json
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            _write(
                Path(tmp) / "openspec" / "specs" / "backlog-seeding" / "spec.md",
                "# Backlog Seeding\n\n## Purpose\n\nOpenSpec-format capability.\n",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = csc.main(["--repo", tmp, "--extra-root", "openspec", "--json"])
            self.assertEqual(rc, 0)
            ids = {c["spec_id"] for c in json.loads(buf.getvalue())["candidates"]}
            self.assertEqual(ids, {"001-example", "backlog-seeding"})

    def test_json_verify_output_matches_verify_result(self):
        import io
        import json
        from contextlib import redirect_stdout

        repo = _init_repo()
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = csc.main(["--repo", repo, "--verify", "001-example", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            json.loads(buf.getvalue()), csc.verify(Path(repo), "001-example")
        )


if __name__ == "__main__":
    unittest.main()
