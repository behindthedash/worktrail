#!/usr/bin/env python3
"""Unit tests for check_durable_artifact_capture_gate.py: all three hit
kinds fire on transcript-local evidence -- a touched `docs/specs/**` /
`openspec/changes/**` path (session_touched_durable_artifact), a run
record finishing `planned_ready_for_implementation` read through
`run_record._load_lenient` (planned_run_record), and a merge marker plus
touched spec paths together (merged_docs_only_spec_pr); non-durable
inputs miss; and missing, unreadable, malformed, or non-UTF-8 inputs
degrade to zero hits with exit status 0 (Requirement: Fail-Open And
Headless-Excluded)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router import run_record
from worktrail.router.check_durable_artifact_capture_gate import (
    MERGED_DOCS_ONLY_SPEC_PR,
    PLANNED_RUN_RECORD,
    PLANNED_STATUS,
    SESSION_TOUCHED_DURABLE_ARTIFACT,
    find_hits,
    find_planned_run_records,
    is_durable_artifact_path,
    main,
    merge_markers_in,
    touched_durable_artifacts,
)


def _start_record(tmp: str) -> str:
    out = StringIO()
    with patch("sys.stdout", out):
        rc = run_record.main([
            "start", "--repo", "/tmp/fake-repo", "--request", "fix the thing",
            "--route", "F", "--risk", "low", "--dir", tmp,
        ])
    assert rc == 0
    return json.loads(out.getvalue())["path"]


def _finish(path: str, status: str) -> None:
    rc = run_record.main(["finish", path, "--status", status])
    assert rc == 0


class IsDurableArtifactPathTests(unittest.TestCase):
    def test_absolute_relative_and_backslash_forms_all_match(self):
        self.assertTrue(is_durable_artifact_path("/repo/docs/specs/x/spec.md"))
        self.assertTrue(is_durable_artifact_path("docs/specs/x/spec.md"))
        self.assertTrue(is_durable_artifact_path("openspec/changes/foo/tasks.md"))
        self.assertTrue(is_durable_artifact_path("/home/u/repo/openspec/changes/foo/specs/one/spec.md"))
        self.assertTrue(is_durable_artifact_path("C:\\repo\\docs\\specs\\a.md"))

    def test_match_is_case_insensitive(self):
        self.assertTrue(is_durable_artifact_path("/REPO/Docs/Specs/X/Spec.MD"))

    def test_bare_tree_root_never_counts_as_touched_artifact(self):
        self.assertFalse(is_durable_artifact_path("docs/specs"))
        self.assertFalse(is_durable_artifact_path("/repo/docs/specs"))
        self.assertFalse(is_durable_artifact_path("openspec/changes"))

    def test_unrelated_paths_miss(self):
        self.assertFalse(is_durable_artifact_path("src/widget.py"))
        self.assertFalse(is_durable_artifact_path("README.md"))
        self.assertFalse(is_durable_artifact_path("docs/design/history/go-v2.md"))

    def test_partial_segment_overlap_misses(self):
        # Segment-exact window: `specs2` is not `specs`, `changes-foo` is
        # not `changes`.
        self.assertFalse(is_durable_artifact_path("docs/specs2/x.md"))
        self.assertFalse(is_durable_artifact_path("openspec/changes-foo/x.md"))


class TouchedDurableArtifactsTests(unittest.TestCase):
    def test_collects_only_durable_paths_in_input_order(self):
        artifacts = touched_durable_artifacts([
            "src/widget.py",
            "openspec/changes/add-gate/tasks.md",
            "README.md",
            "docs/specs/002-retry/spec.md",
        ])

        self.assertEqual(artifacts, [
            "openspec/changes/add-gate/tasks.md",
            "docs/specs/002-retry/spec.md",
        ])

    def test_duplicate_paths_collapsed(self):
        artifacts = touched_durable_artifacts([
            "/repo/docs/specs/x/spec.md",
            "/repo/docs/specs/x/spec.md",
        ])

        self.assertEqual(artifacts, ["/repo/docs/specs/x/spec.md"])

    def test_normalized_forms_collapse_to_one_artifact(self):
        artifacts = touched_durable_artifacts([
            "repo/docs/specs/x/spec.md",
            "repo//docs/specs/x//spec.md",
        ])

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0], "repo/docs/specs/x/spec.md")

    def test_tilde_paths_expanded_against_home(self):
        with tempfile.TemporaryDirectory() as home:
            spec = Path(home) / "docs" / "specs" / "x"
            spec.mkdir(parents=True)
            (spec / "spec.md").write_text("# spec\n", encoding="utf-8")

            with patch.dict(os.environ, {"HOME": home}):
                artifacts = touched_durable_artifacts(["~/docs/specs/x/spec.md"])

            self.assertEqual(artifacts, [str(spec / "spec.md")])

    def test_empty_input_yields_empty_list(self):
        self.assertEqual(touched_durable_artifacts([]), [])


class FindPlannedRunRecordsTests(unittest.TestCase):
    def test_planned_status_record_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _finish(path, PLANNED_STATUS)

            planned = find_planned_run_records([path])

            self.assertEqual(planned, [
                {"run_record": path, "final_status": PLANNED_STATUS},
            ])

    def test_non_planned_terminal_status_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _finish(path, "investigation_complete")

            self.assertEqual(find_planned_run_records([path]), [])

    def test_open_record_without_final_status_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)

            self.assertEqual(find_planned_run_records([path]), [])

    def test_missing_file_degrades_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "no-such-run.yaml")

            self.assertEqual(find_planned_run_records([missing]), [])

    def test_malformed_file_degrades_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_key = Path(tmp) / "bad-key.yaml"
            bad_key.write_text(
                "final_status: planned_ready_for_implementation\n"
                "not a valid key!: oops\n",
                encoding="utf-8",
            )
            bad_json = Path(tmp) / "bad-json.yaml"
            bad_json.write_text(
                'final_status: "unclosed\n',
                encoding="utf-8",
            )

            self.assertEqual(
                find_planned_run_records([str(bad_key), str(bad_json)]), [],
            )

    def test_non_utf8_file_degrades_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "binary.yaml"
            binary.write_bytes(b"\xff\xfe\x00binary")

            self.assertEqual(find_planned_run_records([str(binary)]), [])

    def test_unreadable_file_degrades_to_zero_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked.yaml"
            locked.write_text("run_id: go-x\n", encoding="utf-8")
            locked.chmod(0o000)
            try:
                planned = find_planned_run_records([str(locked)])
            finally:
                locked.chmod(0o755)

            self.assertEqual(planned, [])

    def test_duplicate_paths_read_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _finish(path, PLANNED_STATUS)

            planned = find_planned_run_records([path, path])

            self.assertEqual(len(planned), 1)


class MergeMarkersInTests(unittest.TestCase):
    def test_detects_both_markers_case_insensitively(self):
        markers = merge_markers_in([
            "GH PR MERGE --squash main",
            "git merge feature/x",
        ])

        self.assertEqual(markers, ["gh pr merge", "git merge"])

    def test_markers_deduped_across_commands(self):
        markers = merge_markers_in([
            "gh pr merge 12 --squash",
            "gh pr merge 13 --merge",
        ])

        self.assertEqual(markers, ["gh pr merge"])

    def test_no_merge_commands_yield_no_markers(self):
        self.assertEqual(merge_markers_in(["ls -la", "pytest -q"]), [])


class FindHitsTests(unittest.TestCase):
    def test_all_three_kinds_reported_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _finish(path, PLANNED_STATUS)

            hits = find_hits(
                ["openspec/changes/add-gate/tasks.md"],
                [path],
                ["gh pr merge 7 --squash"],
            )

            self.assertEqual(
                [h["kind"] for h in hits],
                [
                    SESSION_TOUCHED_DURABLE_ARTIFACT,
                    PLANNED_RUN_RECORD,
                    MERGED_DOCS_ONLY_SPEC_PR,
                ],
            )
            self.assertEqual(hits[0]["path"], "openspec/changes/add-gate/tasks.md")
            self.assertEqual(hits[1], {
                "kind": PLANNED_RUN_RECORD, "run_record": path,
                "final_status": PLANNED_STATUS,
            })
            self.assertEqual(hits[2]["merge_markers"], ["gh pr merge"])
            self.assertEqual(hits[2]["spec_paths"], ["openspec/changes/add-gate/tasks.md"])

    def test_merge_marker_without_spec_paths_is_no_hit(self):
        hits = find_hits(["src/widget.py"], [], ["git merge feature/x"])

        self.assertEqual(hits, [])

    def test_spec_paths_without_merge_marker_is_no_hit(self):
        hits = find_hits(["docs/specs/x/spec.md"], [], ["pytest -q"])

        self.assertEqual(
            [h["kind"] for h in hits],
            [SESSION_TOUCHED_DURABLE_ARTIFACT],
        )

    def test_no_inputs_yields_zero_hits(self):
        self.assertEqual(find_hits([], [], []), [])

    def test_non_durable_inputs_yield_zero_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)

            hits = find_hits(["src/widget.py", "README.md"], [path], ["ls"])

            self.assertEqual(hits, [])

    def test_malformed_and_unreadable_inputs_degrade_to_zero_hits_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            garbage = Path(tmp) / "garbage.yaml"
            garbage.write_text("not a valid key!: oops\n", encoding="utf-8")
            locked = Path(tmp) / "locked.yaml"
            locked.write_bytes(b"\xff\xfe\x00")
            locked.chmod(0o000)
            try:
                hits = find_hits(
                    ["src/widget.py"],
                    [str(Path(tmp) / "missing.yaml"), str(garbage), str(locked)],
                    ["echo hello"],
                )
            finally:
                locked.chmod(0o755)

            self.assertEqual(hits, [])


class MainCliOutputTests(unittest.TestCase):
    def test_json_flag_prints_hits_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _finish(path, PLANNED_STATUS)
            out = StringIO()

            with patch("sys.stdout", out):
                rc = main([
                    "--touched-path", "docs/specs/x/spec.md",
                    "--run-record", path,
                    "--json",
                ])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(
                [h["kind"] for h in payload["hits"]],
                [SESSION_TOUCHED_DURABLE_ARTIFACT, PLANNED_RUN_RECORD],
            )

    def test_json_flag_no_hits_prints_empty_array_and_exits_zero(self):
        out = StringIO()

        with patch("sys.stdout", out):
            rc = main(["--json"])

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue()), {"hits": []})

    def test_human_mode_hit_output_names_the_artifacts_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _finish(path, PLANNED_STATUS)
            out = StringIO()

            with patch("sys.stdout", out):
                rc = main([
                    "--touched-path", "openspec/changes/add-gate/tasks.md",
                    "--run-record", path,
                ])

            self.assertEqual(rc, 0)
            text = out.getvalue()
            self.assertIn(
                "durable artifact touched this session: "
                "openspec/changes/add-gate/tasks.md",
                text,
            )
            self.assertIn(f"run record finished {PLANNED_STATUS}: {path}", text)

    def test_human_mode_merged_pr_hit_names_markers_and_spec_paths(self):
        out = StringIO()

        with patch("sys.stdout", out):
            rc = main([
                "--touched-path", "docs/specs/x/spec.md",
                "--bash-command", "git merge feature/x",
            ])

        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("merged docs-only spec PR", text)
        self.assertIn("git merge", text)
        self.assertIn("docs/specs/x/spec.md", text)

    def test_human_mode_no_hits_message_and_exit_zero(self):
        out = StringIO()

        with patch("sys.stdout", out):
            rc = main([])

        self.assertEqual(rc, 0)
        self.assertIn("No durable-artifact dedup hits.", out.getvalue())

    def test_fail_open_inputs_still_valid_output_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked.yaml"
            locked.write_text("run_id: go-x\n", encoding="utf-8")
            locked.chmod(0o000)
            try:
                out = StringIO()
                with patch("sys.stdout", out):
                    rc = main([
                        "--run-record", str(locked),
                        "--bash-command", "echo hi",
                    ])
            finally:
                locked.chmod(0o755)

            self.assertEqual(rc, 0)
            self.assertIn("No durable-artifact dedup hits.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
