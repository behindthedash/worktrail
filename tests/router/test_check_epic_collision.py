#!/usr/bin/env python3
"""Unit tests for the pre-dispatch epic-collision guard (Route B), stdlib
unittest. Mirrors test_check_spec_collision.py's shape: real throwaway
fixture directories rather than mocking, since `check()` is a thin wrapper
over `dashboard.detect_epic_stage()` -- a fake would just re-assert a mock."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router import check_epic_collision as cec


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _mk_epic(
    repo: Path,
    epic_id: str,
    *,
    title: str,
    business_objective: str = "",
    features: int = 1,
    status: str | None = None,
) -> Path:
    body = [f"# {title}", ""]
    if status is not None:
        body.append(f"**Status:** {status}")
        body.append("")
    if business_objective:
        body.append("## Business Objective")
        body.append("")
        body.append(business_objective)
        body.append("")
    for n in range(1, features + 1):
        body.append(f"### Feature {n}")
        body.append(f"Feature {n} body.")
        body.append("")
    path = repo / "docs" / "specs" / "epics" / f"{epic_id}.md"
    _write(path, "\n".join(body))
    return path


def _mk_citing_spec(repo: Path, spec_id: str, epic_id: str) -> None:
    _write(
        repo / "docs" / "specs" / spec_id / "spec.md",
        f"# Spec {spec_id}\n\nOwning epic: {epic_id}\n",
    )


class CheckEpicCollision(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_epics_dir_is_unchecked(self):
        result = cec.check(self.repo)
        self.assertFalse(result["checked"])
        self.assertEqual(result["candidates"], [])

    def test_candidate_carries_title_summary_and_citing_specs(self):
        _mk_epic(
            self.repo,
            "004-james-agentic-vertical-slice",
            title="Epic: James Agentic Vertical Slice",
            business_objective="Prove Lena can drive the workflow end to end.",
            features=2,
        )
        _mk_citing_spec(
            self.repo,
            "088-output-first-workflow-vertical-slice",
            "004-james-agentic-vertical-slice",
        )

        result = cec.check(self.repo)

        self.assertTrue(result["checked"])
        self.assertEqual(len(result["candidates"]), 1)
        c = result["candidates"][0]
        self.assertEqual(c["epic_id"], "004-james-agentic-vertical-slice")
        self.assertEqual(c["title"], "Epic: James Agentic Vertical Slice")
        self.assertEqual(
            c["feature_summary"], "Prove Lena can drive the workflow end to end."
        )
        self.assertEqual(c["stage"], "epic-gap")
        self.assertEqual(c["features"], 2)
        self.assertIn("088-output-first-workflow-vertical-slice", c["citing_specs"])

    def test_epic_with_no_citing_specs_reports_empty_list(self):
        _mk_epic(self.repo, "005-fresh", title="Epic: Fresh", features=1)

        result = cec.check(self.repo)

        self.assertEqual(result["candidates"][0]["citing_specs"], [])

    def test_epic_with_terminal_status_has_no_title_fallback_needed(self):
        _mk_epic(
            self.repo, "006-done", title="Epic: Done", features=1, status="Completed"
        )

        result = cec.check(self.repo)

        c = result["candidates"][0]
        self.assertEqual(c["status"], "Completed")
        self.assertEqual(c["stage"], "epic-complete")

    def test_title_falls_back_to_epic_id_when_no_h1(self):
        path = self.repo / "docs" / "specs" / "epics" / "007-headless.md"
        _write(path, "### Feature 1\nbody\n")

        result = cec.check(self.repo)

        self.assertEqual(result["candidates"][0]["title"], "007-headless")

    def test_non_epic_named_file_is_ignored(self):
        _mk_epic(self.repo, "001-payments", title="Epic: Payments", features=1)
        _write(self.repo / "docs" / "specs" / "epics" / "README.md", "# Epics index\n")

        result = cec.check(self.repo)

        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["epic_id"], "001-payments")

    def test_unreadable_epic_file_is_skipped_not_fatal(self):
        _mk_epic(self.repo, "001-payments", title="Epic: Payments", features=1)
        bad = self.repo / "docs" / "specs" / "epics" / "002-bad.md"
        bad.mkdir()  # a directory named like an epic file -- unreadable as text

        result = cec.check(self.repo)

        self.assertTrue(result["checked"])
        self.assertEqual([c["epic_id"] for c in result["candidates"]], ["001-payments"])


class BuildPendingDecision(unittest.TestCase):
    def test_envelope_shape_and_provenance(self):
        from worktrail.workqueue import decisions

        envelope = cec.build_pending_decision(
            "004-james-agentic-vertical-slice",
            "target/repo",
            run_id="go-20260827-105852",
            dispatch_mode="native-skill",
        )

        self.assertIsNotNone(envelope)
        parsed = decisions.parse_pending_decision_envelope(envelope)
        self.assertEqual(parsed["schema"], decisions.DECISION_ENVELOPE_SCHEMA)
        self.assertEqual(parsed["status"], "pending")
        self.assertTrue(parsed["decision_id"].startswith("dec-"))
        self.assertEqual(parsed["provenance"]["source"], cec.GUARD_SOURCE)
        self.assertEqual(
            parsed["provenance"]["subject"], "004-james-agentic-vertical-slice"
        )
        self.assertEqual(parsed["provenance"]["repo"], "target/repo")
        self.assertEqual(parsed["provenance"]["run_id"], "go-20260827-105852")
        self.assertEqual(parsed["provenance"]["dispatch_mode"], "native-skill")
        self.assertGreaterEqual(len(parsed["options"]), 2)

    def test_identity_is_deterministic_across_re_runs(self):
        from worktrail.workqueue import decisions

        first = cec.build_pending_decision("004-epic", "repo/a")
        second = cec.build_pending_decision("004-epic", "repo/a")

        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(
            first["decision_id"],
            decisions.decision_identity(
                cec.GUARD_SOURCE, "repo/a", "004-epic", cec.DECISION_QUESTION
            ),
        )

    def test_blank_epic_id_or_repo_returns_none(self):
        self.assertIsNone(cec.build_pending_decision("", "repo/a"))
        self.assertIsNone(cec.build_pending_decision("004-epic", ""))

    def test_decision_for_cli_flag(self):
        import contextlib
        import io
        import json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cec.main(
                [
                    "--repo",
                    "target/repo",
                    "--decision-for",
                    "004-epic",
                    "--json",
                ]
            )

        self.assertEqual(rc, 0)
        envelope = json.loads(buf.getvalue())
        self.assertEqual(envelope["provenance"]["subject"], "004-epic")


class CheckEpicCollisionCli(unittest.TestCase):
    def test_main_json_output(self):
        import contextlib
        import io
        import json

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _mk_epic(repo, "001-payments", title="Epic: Payments", features=1)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cec.main(["--repo", str(repo), "--json"])

            self.assertEqual(rc, 0)
            output = json.loads(buf.getvalue())
            self.assertTrue(output["checked"])
            self.assertEqual(output["candidates"][0]["epic_id"], "001-payments")


if __name__ == "__main__":
    unittest.main()
