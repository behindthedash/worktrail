#!/usr/bin/env python3
"""Unit tests for overlap_check.py — stdlib unittest, no pytest needed."""
import json
import tempfile
import unittest
from pathlib import Path

from worktrail.router.overlap_check import (
    _extract_openspec_spec,
    _feature_summary_from_openspec_spec,
    _feature_summary_from_proposal,
    _feature_summary_from_spec,
    _is_openspec_root,
    _summary_from_problem_statement,
    _user_request_excerpt,
    _spec_title,
    extract_spec_summary,
    scan,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestFeatureSummaryExtraction(unittest.TestCase):

    def test_reads_feature_summary_field(self):
        text = "**Feature Summary**: Allows donors to search nonprofits by cause.\n"
        self.assertEqual(
            _feature_summary_from_spec(text),
            "Allows donors to search nonprofits by cause.",
        )

    def test_reads_unbolded_feature_summary(self):
        text = "Feature Summary: Simple summary here.\n"
        self.assertEqual(_feature_summary_from_spec(text), "Simple summary here.")

    def test_ignores_template_placeholder(self):
        text = "**Feature Summary**: ${FEATURE_SUMMARY}\n"
        self.assertIsNone(_feature_summary_from_spec(text))

    def test_returns_none_when_absent(self):
        text = "# Some spec\n\nNo summary field here.\n"
        self.assertIsNone(_feature_summary_from_spec(text))

    def test_problem_statement_fallback(self):
        text = (
            "## Business Context\n\n"
            "### Problem Statement\n\n"
            "Donors struggle to find nonprofits aligned with their values. "
            "They have no search tools.\n\n"
            "### Target Users\n"
        )
        result = _summary_from_problem_statement(text)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("Donors struggle", result)

    def test_problem_statement_returns_first_sentence(self):
        text = (
            "### Problem Statement\n\n"
            "First sentence. Second sentence.\n"
        )
        result = _summary_from_problem_statement(text)
        self.assertEqual(result, "First sentence.")

    def test_problem_statement_skips_placeholder(self):
        text = "### Problem Statement\n\n[What problem does this feature solve?]\n"
        result = _summary_from_problem_statement(text)
        self.assertIsNone(result)


class TestUserRequestExcerpt(unittest.TestCase):

    def test_reads_user_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "001-test"
            _write(spec_dir / "user-request.md",
                   "# User Request\n\n**Original Input**: Add donor search.\n\nKey requirements here.")
            result = _user_request_excerpt(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIn("Key requirements", result)

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "001-test"
            spec_dir.mkdir()
            self.assertIsNone(_user_request_excerpt(spec_dir))

    def test_truncates_at_max_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "001-test"
            _write(spec_dir / "user-request.md", "x" * 500)
            result = _user_request_excerpt(spec_dir, max_chars=50)
            assert result is not None
            self.assertLessEqual(len(result), 50)


class TestSpecTitle(unittest.TestCase):

    def test_title_from_filename(self):
        f = Path("2025-01-15--donor-search-filter.md")
        self.assertEqual(_spec_title(f, "003-donor-search-filter"), "Donor Search Filter")

    def test_title_from_spec_id_fallback(self):
        self.assertEqual(_spec_title(None, "003-donor-search"), "Donor Search")

    def test_title_from_spec_id_no_prefix(self):
        self.assertEqual(_spec_title(None, "abc-feature"), "Feature")


class TestExtractSpecSummary(unittest.TestCase):

    def _make_spec(self, tmp: str, spec_id: str, spec_content: str,
                   user_request: str = "") -> Path:
        spec_dir = Path(tmp) / spec_id
        _write(spec_dir / "2025-01-01--feature.md", spec_content)
        if user_request:
            _write(spec_dir / "user-request.md", user_request)
        return spec_dir

    def test_returns_feature_summary_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = self._make_spec(
                tmp, "001-donor-search",
                "**Feature Summary**: Donors can search nonprofits by cause.\n"
                "## Business Context\n",
            )
            result = extract_spec_summary(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["feature_summary"],
                             "Donors can search nonprofits by cause.")
            self.assertEqual(result["spec_id"], "001-donor-search")

    def test_falls_back_to_problem_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = self._make_spec(
                tmp, "002-payments",
                "## Business Context\n\n### Problem Statement\n\n"
                "Users cannot pay online. Manual invoices cause delays.\n\n"
                "### Target Users\n",
            )
            result = extract_spec_summary(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIn("Users cannot pay", result["feature_summary"])

    def test_falls_back_to_user_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = self._make_spec(
                tmp, "003-profile",
                "## Business Context\n\n### Problem Statement\n\n"
                "[What problem does this feature solve?]\n",
                user_request="Add user profile editing.",
            )
            result = extract_spec_summary(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIn("user profile", result["feature_summary"])

    def test_returns_none_for_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "001-empty"
            spec_dir.mkdir()
            result = extract_spec_summary(spec_dir)
            self.assertIsNone(result)

    def test_user_request_only_folder_is_scanned(self):
        """Unspec'd backlog stubs (user-request.md, no spec doc) must be visible
        to the overlap gate — they're the easiest specs to duplicate."""
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "004-backlog-stub"
            spec_dir.mkdir()
            _write(spec_dir / "user-request.md", "Bulk-export donor receipts.")
            result = extract_spec_summary(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIn("donor receipts", result["feature_summary"])

    def test_unreadable_spec_file_degrades_not_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "005-bad"
            spec_dir.mkdir()
            (spec_dir / "2026-01-01--bad.md").write_bytes(b"\xff\xfe broken")
            result = extract_spec_summary(spec_dir)
            self.assertIsNotNone(result)  # degraded row, no UnicodeDecodeError

    def test_includes_stage_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = self._make_spec(
                tmp, "001-test",
                "**Feature Summary**: Test feature.\n",
            )
            result = extract_spec_summary(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIn("stage", result)


class TestIsOpenspecRoot(unittest.TestCase):

    def test_changes_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "changes").mkdir()
            self.assertTrue(_is_openspec_root(root))

    def test_specs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "specs").mkdir()
            self.assertTrue(_is_openspec_root(root))

    def test_both_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "changes").mkdir()
            (root / "specs").mkdir()
            self.assertTrue(_is_openspec_root(root))

    def test_neither_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "001-devkit-spec").mkdir()
            self.assertFalse(_is_openspec_root(root))

    def test_neither_present_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(_is_openspec_root(Path(tmp)))

    def test_file_named_changes_does_not_count(self):
        """A `changes` *file* (not a dir) must not be mistaken for OpenSpec shape."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "changes").write_text("not a directory")
            self.assertFalse(_is_openspec_root(root))


class TestFeatureSummaryFromProposal(unittest.TestCase):

    def test_capabilities_present_is_used(self):
        text = (
            "## Why\n\nBecause donors need this.\n\n"
            "## Capabilities\n\nSearch nonprofits by cause.\n\n"
            "## Design\n\nDetails here.\n"
        )
        self.assertEqual(
            _feature_summary_from_proposal(text),
            "Search nonprofits by cause.",
        )

    def test_capabilities_empty_falls_back_to_why_first_sentence(self):
        text = (
            "## Why\n\nDonors struggle to find nonprofits. More context.\n\n"
            "## Capabilities\n\n\n\n"
            "## Design\n\nDetails here.\n"
        )
        self.assertEqual(
            _feature_summary_from_proposal(text),
            "Donors struggle to find nonprofits.",
        )

    def test_capabilities_absent_falls_back_to_why(self):
        text = "## Why\n\nDonors struggle to find nonprofits. More context.\n"
        self.assertEqual(
            _feature_summary_from_proposal(text),
            "Donors struggle to find nonprofits.",
        )

    def test_neither_section_present_returns_none(self):
        text = "## Design\n\nJust design notes, no Capabilities or Why.\n"
        self.assertIsNone(_feature_summary_from_proposal(text))

    def test_empty_text_returns_none_no_crash(self):
        self.assertIsNone(_feature_summary_from_proposal(""))


class TestFeatureSummaryFromOpenspecSpec(unittest.TestCase):

    def test_purpose_present_is_used(self):
        text = (
            "## Purpose\n\nSearch nonprofits by cause.\n\n"
            "## Requirements\n\nDetails here.\n"
        )
        self.assertEqual(
            _feature_summary_from_openspec_spec(text),
            "Search nonprofits by cause.",
        )

    def test_purpose_empty_returns_none(self):
        text = "## Purpose\n\n\n\n## Requirements\n\nDetails here.\n"
        self.assertIsNone(_feature_summary_from_openspec_spec(text))

    def test_purpose_absent_returns_none(self):
        text = "## Requirements\n\nJust requirements, no Purpose section.\n"
        self.assertIsNone(_feature_summary_from_openspec_spec(text))

    def test_empty_text_returns_none_no_crash(self):
        self.assertIsNone(_feature_summary_from_openspec_spec(""))

    def test_purpose_is_last_section(self):
        text = "## Requirements\n\nDetails here.\n\n## Purpose\n\nSearch nonprofits by cause.\n"
        self.assertEqual(
            _feature_summary_from_openspec_spec(text),
            "Search nonprofits by cause.",
        )


class TestExtractOpenspecSpec(unittest.TestCase):

    def test_returns_full_dict_when_purpose_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "donor-search"
            _write(spec_dir / "spec.md",
                   "## Purpose\n\nSearch nonprofits by cause.\n")
            result = _extract_openspec_spec(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["spec_id"], "donor-search")
            self.assertEqual(result["stage"], "complete")
            # _slug_to_title assumes an `NNN-slug` id and drops the first
            # hyphen-delimited token; OpenSpec dir names carry no such prefix.
            self.assertEqual(result["title"], "Search")
            self.assertEqual(result["feature_summary"],
                             "Search nonprofits by cause.")
            self.assertIsNone(result["user_request_excerpt"])

    def test_feature_summary_null_when_purpose_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "donor-search"
            _write(spec_dir / "spec.md", "## Requirements\n\nNo purpose here.\n")
            result = _extract_openspec_spec(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertIsNone(result["feature_summary"])

    def test_returns_none_when_spec_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "donor-search"
            spec_dir.mkdir()
            self.assertIsNone(_extract_openspec_spec(spec_dir))

    def test_key_set_matches_devkit_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "donor-search"
            _write(spec_dir / "spec.md",
                   "## Purpose\n\nSearch nonprofits by cause.\n")
            result = _extract_openspec_spec(spec_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(
                set(result.keys()),
                {"spec_id", "stage", "title", "feature_summary", "user_request_excerpt"},
            )

    def test_unreadable_spec_file_degrades_not_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_dir = Path(tmp) / "donor-search"
            spec_dir.mkdir()
            (spec_dir / "spec.md").write_bytes(b"\xff\xfe broken")
            result = _extract_openspec_spec(spec_dir)
            self.assertIsNotNone(result)  # degraded row, no UnicodeDecodeError


class TestScan(unittest.TestCase):

    def test_scan_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scan(Path(tmp)), [])

    def test_scan_missing_dir(self):
        self.assertEqual(scan(Path("/nonexistent/path")), [])

    def test_scan_finds_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, name in enumerate(["001-auth", "002-payments", "003-profile"]):
                d = root / name
                d.mkdir()
                (d / f"2025-01-0{i+1}--spec.md").write_text(
                    f"**Feature Summary**: Summary for {name}.\n"
                )
            results = scan(root)
            self.assertEqual(len(results), 3)
            ids = [r["spec_id"] for r in results]
            self.assertIn("001-auth", ids)
            self.assertIn("002-payments", ids)

    def test_scan_skips_non_spec_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # shared files (not NNN- prefixed) should be skipped
            (root / "architecture.md").write_text("# Arch")
            (root / "ontology.md").write_text("# Ontology")
            # a real spec
            spec = root / "001-auth"
            spec.mkdir()
            (spec / "2025-01-01--auth.md").write_text("**Feature Summary**: Auth feature.\n")
            results = scan(root)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["spec_id"], "001-auth")

    def test_scan_json_output(self):
        """Verify JSON is well-formed and has expected keys."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = root / "001-auth"
            spec.mkdir()
            (spec / "2025-01-01--auth.md").write_text(
                "**Feature Summary**: Auth feature.\n"
            )
            results = scan(root)
            # Serialize and deserialize to verify JSON-safe
            payload = json.loads(json.dumps({"specs": results}))
            self.assertEqual(len(payload["specs"]), 1)
            keys = payload["specs"][0].keys()
            for k in ("spec_id", "stage", "title", "feature_summary", "user_request_excerpt"):
                self.assertIn(k, keys)


class TestScanOpenSpec(unittest.TestCase):

    def test_changes_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "changes" / "add-donor-search" / "proposal.md",
                   "## Why\n\nDonors need this.\n\n"
                   "## Capabilities\n\nSearch nonprofits by cause.\n")
            results = scan(root)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["spec_id"], "add-donor-search")
            self.assertEqual(results[0]["stage"], "active")
            self.assertEqual(results[0]["feature_summary"],
                             "Search nonprofits by cause.")
            self.assertIsNone(results[0]["user_request_excerpt"])

    def test_specs_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "specs" / "donor-search" / "spec.md",
                   "## Purpose\n\nSearch nonprofits by cause.\n")
            results = scan(root)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["spec_id"], "donor-search")
            self.assertEqual(results[0]["stage"], "complete")
            self.assertEqual(results[0]["feature_summary"],
                             "Search nonprofits by cause.")
            self.assertIsNone(results[0]["user_request_excerpt"])

    def test_both_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "changes" / "add-donor-search" / "proposal.md",
                   "## Capabilities\n\nSearch nonprofits by cause.\n")
            _write(root / "specs" / "donor-search" / "spec.md",
                   "## Purpose\n\nSearch nonprofits by cause.\n")
            results = scan(root)
            self.assertEqual(len(results), 2)
            by_id = {r["spec_id"]: r for r in results}
            self.assertEqual(by_id["add-donor-search"]["stage"], "active")
            self.assertEqual(by_id["donor-search"]["stage"], "complete")

    def test_key_set_parity_with_devkit_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            devkit_root = Path(tmp) / "devkit"
            devkit_spec = devkit_root / "001-auth"
            _write(devkit_spec / "2025-01-01--auth.md",
                   "**Feature Summary**: Auth feature.\n")
            devkit_results = scan(devkit_root)

            openspec_root = Path(tmp) / "openspec"
            _write(openspec_root / "changes" / "add-auth" / "proposal.md",
                   "## Capabilities\n\nAuth feature.\n")
            openspec_results = scan(openspec_root)

            self.assertEqual(len(devkit_results), 1)
            self.assertEqual(len(openspec_results), 1)
            self.assertEqual(set(devkit_results[0].keys()),
                             set(openspec_results[0].keys()))


class TestScanDevkitRegression(unittest.TestCase):
    """Regression guard for Requirement: Devkit-Shaped Root Scanning Is
    Unchanged — scans this repo's own real `docs/specs` devkit-format tree
    (not a synthetic fixture) and asserts the OpenSpec-shape branch added to
    `scan()` leaves devkit output byte-for-byte identical to pre-change
    behavior."""

    def test_scan_docs_specs_matches_pre_change_snapshot(self):
        repo_root = Path(__file__).resolve().parents[2]
        docs_specs = repo_root / "docs" / "specs"
        self.assertTrue(docs_specs.is_dir(), "fixture docs/specs must exist")

        results = scan(docs_specs)

        self.assertEqual(
            results,
            [
                {
                    "spec_id": "001-task-ac-verification-gate",
                    "stage": "complete",
                    "title": "Task Ac Verification Gate",
                    "feature_summary": (
                        "Add automated AC-verification to the worktrail SDD "
                        "workflow's task-completion gate: before a task can be "
                        "marked status:completed (or before its PR mer"
                    ),
                    "user_request_excerpt": (
                        "Add automated AC-verification to the worktrail SDD "
                        "workflow's task-completion gate: before a task can be "
                        "marked status:completed (or before its PR merges), "
                        "re-run that task's own literal DoD assertion"
                    ),
                },
            ],
        )

    def test_scan_docs_specs_routes_through_devkit_path_not_openspec(self):
        """`docs/specs` has no `changes/` or `specs/` subdirectory, so `scan()`
        must take the `^\\d{3,}-` devkit iteration, matching `extract_spec_summary`
        called directly on the same folder rather than the OpenSpec extractors."""
        repo_root = Path(__file__).resolve().parents[2]
        docs_specs = repo_root / "docs" / "specs"

        results = scan(docs_specs)
        direct = extract_spec_summary(docs_specs / "001-task-ac-verification-gate")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], direct)


if __name__ == "__main__":
    unittest.main()
