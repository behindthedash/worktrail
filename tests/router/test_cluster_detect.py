#!/usr/bin/env python3
"""Unit tests for cluster_detect.py's signal extraction and pairwise matching
(spec 018 TASK-001). Uses a small local frontmatter parser (block-sequence and
inline-list aware, mirroring the loader/`_parse_fm` shape dashboard.py injects
in production) so tests exercise real `.md` fixtures rather than hand-built
signal dicts. Run with:
    python3 -m pytest plugins/.../go/scripts/test_cluster_detect.py -q
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from worktrail.router import cluster_detect as _cluster_detect_mod
from worktrail.router.cluster_detect import (
    NEAR_IDENTICAL_THRESHOLD,
    OVERLAP_THRESHOLD,
    _assemble_clusters,
    _connected_components,
    _Edge,
    _extract_signal,
    _filter_reportable,
    _overlap_coefficient,
    _signal_matches,
    _slug,
    _tokenize,
    compute_clusters,
)

_FM_RE = re.compile(r"^---\r?\n(.*?)\n---\r?\n", re.DOTALL)


def _fake_parse_frontmatter(text: str) -> Dict[str, Any]:
    """Minimal stand-in for the loader-backed parser dashboard.py injects in
    production: handles scalars, null, quoted strings, inline `[a, b]` lists,
    and multi-line block-sequence lists."""
    m = _FM_RE.match(text)
    if not m:
        return {}
    result: Dict[str, Any] = {}
    lines = m.group(1).split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        kv = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*):\s*(.*)", line)
        if not kv:
            i += 1
            continue
        key, val = kv.group(1), kv.group(2).strip()
        if not val:
            items: List[str] = []
            i += 1
            while i < len(lines) and re.match(r"^\s+-", lines[i]):
                item_m = re.match(r"^\s+-\s+(.*)", lines[i])
                if item_m:
                    items.append(item_m.group(1).strip())
                i += 1
            result[key] = items
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            result[key] = [x.strip().strip("\"'") for x in inner.split(",")] if inner else []
        elif val in ("null", "~"):
            result[key] = None
        elif val.startswith('"') and val.endswith('"') and len(val) >= 2:
            result[key] = val[1:-1]
        elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
            result[key] = val[1:-1]
        else:
            result[key] = val
        i += 1
    return result


def _write_brief(
    dirpath: Path,
    filename: str,
    *,
    repo: Optional[str] = None,
    target_spec: Optional[str] = None,
    related: Union[None, str, List[str]] = None,
    blocked_by: Union[None, str, List[str]] = None,
    focus: str = "",
) -> Path:
    lines = ["---"]
    if repo is not None:
        lines.append(f"repo: {repo}")
    if target_spec is not None:
        lines.append(f"target-spec: {target_spec}")
    if related is not None:
        if isinstance(related, str):
            lines.append(f"related: {related}")
        else:
            lines.append("related:")
            lines.extend(f"  - {r}" for r in related)
    if blocked_by is not None:
        if isinstance(blocked_by, str):
            lines.append(f"blocked-by: {blocked_by}")
        else:
            lines.append("blocked-by:")
            lines.extend(f"  - {b}" for b in blocked_by)
    lines.append(f'focus: "{focus}"')
    lines.append("---")
    lines.append("")
    lines.append("Body text.")
    path = dirpath / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class SlugTests(unittest.TestCase):
    def test_strips_timestamp_prefix(self):
        self.assertEqual(_slug("20260610-093000-fix-auth-flow"), "fix-auth-flow")

    def test_no_timestamp_prefix_returned_unchanged(self):
        self.assertEqual(_slug("fix-auth-flow"), "fix-auth-flow")
        self.assertEqual(_slug("not-a-timestamp-12345"), "not-a-timestamp-12345")


class TokenizeOverlapTests(unittest.TestCase):
    def test_tokens_lowercased_alnum_min_length_3(self):
        tokens = _tokenize("Fix Auth-Flow: OAuth2 bug (v2)!")
        self.assertEqual(tokens, {"fix", "auth", "flow", "oauth2", "bug"})

    def test_tokenize_empty_text(self):
        self.assertEqual(_tokenize(""), set())

    def test_overlap_coefficient_formula(self):
        a = {"fix", "auth", "flow", "delta"}
        b = {"auth", "flow", "epsilon", "zeta"}
        self.assertAlmostEqual(_overlap_coefficient(a, b), 2 / 4)

    def test_overlap_coefficient_empty_sets_never_divides_by_zero(self):
        self.assertEqual(_overlap_coefficient(set(), set()), 0.0)
        self.assertEqual(_overlap_coefficient(set(), {"x"}), 0.0)
        self.assertEqual(_overlap_coefficient({"x"}, set()), 0.0)


class ExtractSignalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_well_formed_brief_yields_full_signal(self):
        path = _write_brief(
            self.dir,
            "20260610-093000-fix-auth-flow.md",
            repo="proj/repo-a",
            target_spec="018",
            related=["some-other-id"],
            blocked_by=["blocking-id"],
            focus="fix auth flow",
        )
        sig = _extract_signal(path, _fake_parse_frontmatter)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig["repo"], "proj/repo-a")
        self.assertEqual(sig["target_spec"], "018")
        self.assertEqual(sig["related"], ["some-other-id"])
        self.assertEqual(sig["blocked_by"], ["blocking-id"])
        self.assertEqual(sig["slug"], "fix-auth-flow")
        self.assertEqual(sig["focus_tokens"], {"fix", "auth", "flow"})

    def test_malformed_frontmatter_yields_none(self):
        path = self.dir / "20260610-093000-broken.md"
        path.write_text("no frontmatter here, just body text.\n", encoding="utf-8")
        self.assertIsNone(_extract_signal(path, _fake_parse_frontmatter))

    def test_unreadable_file_yields_none(self):
        missing = self.dir / "does-not-exist.md"
        self.assertIsNone(_extract_signal(missing, _fake_parse_frontmatter))

    def test_parser_exception_yields_none(self):
        path = _write_brief(self.dir, "20260610-093000-alpha.md", repo="repo-a", focus="x")

        def _raising_parser(_text: str) -> Dict[str, Any]:
            raise ValueError("boom")

        self.assertIsNone(_extract_signal(path, _raising_parser))

    def test_empty_focus_yields_empty_focus_tokens(self):
        path = _write_brief(self.dir, "20260610-093000-no-focus.md", repo="repo-a", focus="")
        sig = _extract_signal(path, _fake_parse_frontmatter)
        assert sig is not None
        self.assertEqual(sig["focus_tokens"], set())

    def test_related_as_single_string_is_coerced_to_list(self):
        path = _write_brief(
            self.dir,
            "20260610-093000-single-related.md",
            repo="repo-a",
            related="some-other-id",
        )
        sig = _extract_signal(path, _fake_parse_frontmatter)
        assert sig is not None
        self.assertEqual(sig["related"], ["some-other-id"])


class SignalMatchesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _sig(self, filename: str, **kwargs) -> Dict[str, Any]:
        path = _write_brief(self.dir, filename, **kwargs)
        sig = _extract_signal(path, _fake_parse_frontmatter)
        assert sig is not None
        return sig

    def test_duplicate_slug_matches_across_different_repos(self):
        a = self._sig("20260610-093000-fix-auth-flow.md", repo="repo-a", focus="")
        b = self._sig("20260611-101500-fix-auth-flow.md", repo="repo-b", focus="")
        matches = dict(_signal_matches(a, b))
        self.assertIn("duplicate-slug", matches)

    def test_same_repo_same_target_spec_matches(self):
        a = self._sig("20260610-093000-alpha.md", repo="repo-a", target_spec="018", focus="")
        b = self._sig("20260611-101500-beta.md", repo="repo-a", target_spec="018", focus="")
        matches = dict(_signal_matches(a, b))
        self.assertIn("same-target-spec", matches)

    def test_related_link_matches_forward_direction(self):
        b = self._sig("20260611-101500-beta.md", repo="repo-a", focus="")
        a = self._sig("20260610-093000-alpha.md", repo="repo-a", related=[b["stem"]], focus="")
        matches = dict(_signal_matches(a, b))
        self.assertIn("related-link", matches)

    def test_related_link_matches_reverse_direction(self):
        c = self._sig("20260610-093000-gamma.md", repo="repo-a", focus="")
        d = self._sig("20260611-101500-delta.md", repo="repo-a", related=[c["stem"]], focus="")
        # d names c; querying (c, d) must still surface the link.
        matches = dict(_signal_matches(c, d))
        self.assertIn("related-link", matches)

    def test_focus_overlap_at_or_above_threshold_matches(self):
        a = self._sig("20260610-093000-alpha.md", repo="repo-a", focus="alpha beta gamma delta")
        b = self._sig("20260611-101500-beta.md", repo="repo-a", focus="alpha beta gamma epsilon")
        score = _overlap_coefficient(a["focus_tokens"], b["focus_tokens"])
        self.assertGreaterEqual(score, OVERLAP_THRESHOLD)
        matches = dict(_signal_matches(a, b))
        self.assertIn("focus-overlap", matches)
        overlap_score = matches["focus-overlap"]
        assert overlap_score is not None
        self.assertAlmostEqual(overlap_score, score)

    def test_focus_overlap_below_threshold_no_match(self):
        a = self._sig("20260610-093000-alpha.md", repo="repo-a", focus="alpha beta gamma delta")
        b = self._sig("20260611-101500-beta.md", repo="repo-a", focus="alpha zeta eta theta")
        score = _overlap_coefficient(a["focus_tokens"], b["focus_tokens"])
        self.assertLess(score, OVERLAP_THRESHOLD)
        matches = dict(_signal_matches(a, b))
        self.assertNotIn("focus-overlap", matches)

    def test_blocked_by_excludes_every_signal_even_identical_slugs(self):
        a = self._sig(
            "20260610-093000-fix-auth-flow.md",
            repo="repo-a",
            target_spec="018",
            focus="alpha beta gamma",
        )
        b = self._sig(
            "20260611-101500-fix-auth-flow.md",
            repo="repo-a",
            target_spec="018",
            related=[a["stem"]],
            blocked_by=[a["stem"]],
            focus="alpha beta gamma",
        )
        self.assertEqual(_signal_matches(a, b), [])

    def test_blocked_by_excludes_reverse_direction_too(self):
        a = self._sig(
            "20260610-093000-fix-auth-flow.md",
            repo="repo-a",
            blocked_by=None,
            focus="",
        )
        b = self._sig(
            "20260611-101500-fix-auth-flow.md",
            repo="repo-a",
            blocked_by=[a["stem"]],
            focus="",
        )
        self.assertEqual(_signal_matches(a, b), [])

    def test_different_nonnull_repos_block_all_repo_scoped_signals(self):
        a = self._sig(
            "20260610-093000-alpha.md",
            repo="repo-a",
            target_spec="018",
            related=["20260611-101500-beta"],
            focus="alpha beta gamma delta",
        )
        b = self._sig(
            "20260611-101500-beta.md",
            repo="repo-b",
            target_spec="018",
            focus="alpha beta gamma delta",
        )
        matches = dict(_signal_matches(a, b))
        self.assertNotIn("same-target-spec", matches)
        self.assertNotIn("related-link", matches)
        self.assertNotIn("focus-overlap", matches)

    def test_one_null_repo_blocks_repo_scoped_signals(self):
        # A one-null-one-real-repo pair is a genuine mismatch (unlike
        # both-null, which the same-repo gate now treats as "same repo") —
        # it must still block every repo-scoped signal.
        a = self._sig(
            "20260610-093000-alpha.md",
            repo=None,
            target_spec="018",
            related=["20260611-101500-beta"],
            focus="alpha beta gamma delta",
        )
        b = self._sig(
            "20260611-101500-beta.md",
            repo="repo-b",
            target_spec="018",
            focus="alpha beta gamma delta",
        )
        matches = dict(_signal_matches(a, b))
        self.assertNotIn("same-target-spec", matches)
        self.assertNotIn("related-link", matches)
        self.assertNotIn("focus-overlap", matches)

    def test_both_null_repos_still_match_duplicate_slug(self):
        # Per the same-repo gate's null-vs-null carve-out, a pair where both
        # briefs carry a null repo is treated as same-repo, so
        # same-target-spec, related-link, and focus-overlap all apply
        # alongside the repo-independent duplicate-slug match.
        a = self._sig(
            "20260610-093000-fix-auth-flow.md",
            repo=None,
            target_spec="018",
            related=["20260611-101500-fix-auth-flow"],
            focus="alpha beta gamma delta",
        )
        b = self._sig(
            "20260611-101500-fix-auth-flow.md",
            repo=None,
            target_spec="018",
            focus="alpha beta gamma delta",
        )
        matches = dict(_signal_matches(a, b))
        self.assertIn("duplicate-slug", matches)
        self.assertIn("same-target-spec", matches)
        self.assertIn("related-link", matches)
        self.assertIn("focus-overlap", matches)

    def test_related_link_matches_via_slug_only_identifier(self):
        # `related:` naming just the descriptive slug (no timestamp prefix) —
        # _id_matches's endswith fallback still resolves it to the full stem.
        b = self._sig("20260611-101500-beta.md", repo="repo-a", focus="")
        a = self._sig("20260610-093000-alpha.md", repo="repo-a", related=["beta"], focus="")
        matches = dict(_signal_matches(a, b))
        self.assertIn("related-link", matches)

    def test_blocked_by_matches_via_timestamp_prefix_identifier(self):
        # `blocked-by:` naming just the timestamp prefix (no slug) —
        # _id_matches's startswith fallback still resolves it to the full stem.
        a = self._sig("20260610-093000-alpha.md", repo="repo-a", focus="")
        b = self._sig(
            "20260611-101500-beta.md",
            repo="repo-a",
            blocked_by=["20260610-093000"],
            focus="",
        )
        self.assertEqual(_signal_matches(a, b), [])

    def test_empty_focus_produces_no_false_overlap_match(self):
        a = self._sig("20260610-093000-alpha.md", repo="repo-a", focus="")
        b = self._sig("20260611-101500-beta.md", repo="repo-a", focus="")
        matches = dict(_signal_matches(a, b))
        self.assertNotIn("focus-overlap", matches)
        self.assertEqual(matches, {})


class ConnectedComponentsTests(unittest.TestCase):
    def test_three_pairwise_connected_form_one_cluster(self):
        edges: List[_Edge] = [
            ("a", "b", [("same-target-spec", None)]),
            ("b", "c", [("related-link", None)]),
            ("a", "c", [("related-link", None)]),
        ]
        comps = _connected_components(edges)
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["members"], ["a", "b", "c"])
        self.assertEqual(comps[0]["size"], 3)

    def test_transitivity_via_shared_neighbor(self):
        # a-b and a-c match; b-c does not. Still one component of size 3.
        edges: List[_Edge] = [
            ("a", "b", [("related-link", None)]),
            ("a", "c", [("same-target-spec", None)]),
        ]
        comps = _connected_components(edges)
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["members"], ["a", "b", "c"])
        self.assertEqual(comps[0]["size"], 3)

    def test_unmatched_pair_never_joins_a_component(self):
        edges = [
            ("a", "b", []),
            ("c", "d", [("related-link", None)]),
        ]
        comps = _connected_components(edges)
        member_sets = [c["members"] for c in comps]
        self.assertNotIn(["a", "b"], member_sets)
        self.assertIn(["c", "d"], member_sets)

    def test_two_unmatched_pairs_and_unrelated_third_brief_span_no_cluster(self):
        edges = [
            ("a", "b", []),
            ("a", "c", []),
            ("b", "c", []),
        ]
        self.assertEqual(_connected_components(edges), [])

    def test_signals_list_contains_only_distinct_present_labels(self):
        edges: List[_Edge] = [
            ("a", "b", [("same-target-spec", None)]),
            ("b", "c", [("related-link", None)]),
            ("a", "c", [("same-target-spec", None), ("related-link", None)]),
        ]
        comps = _connected_components(edges)
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["signals"], ["related-link", "same-target-spec"])

    def test_empty_edges_yields_empty_components(self):
        self.assertEqual(_connected_components([]), [])


class FilterReportableTests(unittest.TestCase):
    def _component(self, members, signals, edges):
        return {
            "members": sorted(members),
            "signals": sorted(signals),
            "size": len(members),
            "_edges": edges,
        }

    def test_size_three_always_surfaced(self):
        comp = self._component(
            ["a", "b", "c"],
            ["related-link"],
            [("a", "b", [("related-link", None)]), ("b", "c", [("related-link", None)])],
        )
        reportable = _filter_reportable([comp])
        self.assertEqual(len(reportable), 1)
        self.assertNotIn("_edges", reportable[0])

    def test_size_two_duplicate_slug_surfaced(self):
        comp = self._component(
            ["a", "b"], ["duplicate-slug"], [("a", "b", [("duplicate-slug", None)])]
        )
        reportable = _filter_reportable([comp])
        self.assertEqual(len(reportable), 1)

    def test_size_two_focus_overlap_at_or_above_near_identical_surfaced(self):
        comp = self._component(
            ["a", "b"],
            ["focus-overlap"],
            [("a", "b", [("focus-overlap", NEAR_IDENTICAL_THRESHOLD)])],
        )
        reportable = _filter_reportable([comp])
        self.assertEqual(len(reportable), 1)

    def test_size_two_focus_overlap_below_near_identical_not_surfaced(self):
        below = NEAR_IDENTICAL_THRESHOLD - 0.01
        self.assertGreaterEqual(below, OVERLAP_THRESHOLD)
        comp = self._component(
            ["a", "b"], ["focus-overlap"], [("a", "b", [("focus-overlap", below)])]
        )
        self.assertEqual(_filter_reportable([comp]), [])

    def test_size_two_focus_overlap_between_old_and_new_threshold_now_surfaced(self):
        # Between the lowered NEAR_IDENTICAL_THRESHOLD (0.50) and the old one
        # (0.75): dropped before the threshold change, surfaced now.
        score = 0.60
        self.assertGreaterEqual(score, NEAR_IDENTICAL_THRESHOLD)
        self.assertLess(score, 0.75)
        comp = self._component(
            ["a", "b"], ["focus-overlap"], [("a", "b", [("focus-overlap", score)])]
        )
        reportable = _filter_reportable([comp])
        self.assertEqual(len(reportable), 1)

    def test_size_two_same_target_spec_only_not_surfaced(self):
        comp = self._component(
            ["a", "b"], ["same-target-spec"], [("a", "b", [("same-target-spec", None)])]
        )
        self.assertEqual(_filter_reportable([comp]), [])

    def test_size_two_related_link_only_not_surfaced(self):
        comp = self._component(["a", "b"], ["related-link"], [("a", "b", [("related-link", None)])])
        self.assertEqual(_filter_reportable([comp]), [])

    def test_size_one_never_surfaced(self):
        comp = self._component(["a"], [], [])
        self.assertEqual(_filter_reportable([comp]), [])


class AssembleClustersTests(unittest.TestCase):
    def test_empty_signal_matches_produces_empty_cluster_list_not_an_error(self):
        self.assertEqual(_assemble_clusters([]), [])

    def test_end_to_end_three_brief_cluster_surfaced_as_one(self):
        edges: List[_Edge] = [
            ("a", "b", [("same-target-spec", None)]),
            ("b", "c", [("related-link", None)]),
        ]
        clusters = _assemble_clusters(edges)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["members"], ["a", "b", "c"])
        self.assertEqual(clusters[0]["signals"], ["related-link", "same-target-spec"])
        self.assertEqual(clusters[0]["size"], 3)

    def test_end_to_end_ordinary_size_two_pair_dropped_alongside_surfaced_trio(self):
        edges: List[_Edge] = [
            ("a", "b", [("same-target-spec", None)]),
            ("b", "c", [("related-link", None)]),
            ("d", "e", [("related-link", None)]),
        ]
        clusters = _assemble_clusters(edges)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["members"], ["a", "b", "c"])

    def test_end_to_end_duplicate_slug_pair_surfaced(self):
        edges: List[_Edge] = [("a", "b", [("duplicate-slug", None)])]
        clusters = _assemble_clusters(edges)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["members"], ["a", "b"])


class ComputeClustersTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _members_containing(
        self, clusters: List[Dict[str, Any]], stem: str
    ) -> Optional[Dict[str, Any]]:
        for cluster in clusters:
            if stem in cluster["members"]:
                return cluster
        return None

    def test_malformed_brief_excluded_but_clustering_continues(self):
        a = _write_brief(self.dir, "20260610-090000-fix-auth-flow.md", repo="repo-a", focus="")
        b = _write_brief(self.dir, "20260610-091000-fix-auth-flow.md", repo="repo-b", focus="")
        broken = self.dir / "20260610-092000-broken.md"
        broken.write_text("no frontmatter here, just body text.\n", encoding="utf-8")

        clusters = compute_clusters(self.dir, _fake_parse_frontmatter)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["members"], sorted([a.stem, b.stem]))
        self.assertIn("duplicate-slug", clusters[0]["signals"])

    def test_missing_queue_dir_returns_empty_list(self):
        missing = self.dir / "does-not-exist"
        self.assertEqual(compute_clusters(missing, _fake_parse_frontmatter), [])

    def test_empty_queue_dir_returns_empty_list(self):
        self.assertEqual(compute_clusters(self.dir, _fake_parse_frontmatter), [])

    def test_parser_exception_for_one_file_does_not_abort_others(self):
        a = _write_brief(self.dir, "20260610-090000-fix-auth-flow.md", repo="repo-a", focus="")
        b = _write_brief(self.dir, "20260610-091000-fix-auth-flow.md", repo="repo-b", focus="")
        boom = _write_brief(self.dir, "20260610-092000-boom.md", repo="repo-c", focus="")

        # Raise only for the "boom" brief by inspecting its own frontmatter marker.
        def _selective_raising_parser(text: str) -> Dict[str, Any]:
            fm = _fake_parse_frontmatter(text)
            if fm.get("repo") == "repo-c":
                raise RuntimeError("unexpected internal failure")
            return fm

        clusters = compute_clusters(self.dir, _selective_raising_parser)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["members"], sorted([a.stem, b.stem]))
        self.assertNotIn(boom.stem, clusters[0]["members"])

    def test_full_realistic_multi_brief_scenario(self):
        # duplicate-slug pair, different repos -> size-2 near-identical, surfaced.
        dup_a = _write_brief(self.dir, "20260610-090000-fix-auth-flow.md", repo="repo-a", focus="")
        dup_b = _write_brief(self.dir, "20260610-091000-fix-auth-flow.md", repo="repo-b", focus="")

        # same-target-spec + related-link trio -> size-3, surfaced unconditionally.
        alpha = _write_brief(
            self.dir,
            "20260610-092000-alpha.md",
            repo="repo-c",
            target_spec="020",
            focus="",
        )
        _write_brief(
            self.dir,
            "20260610-093000-beta.md",
            repo="repo-c",
            target_spec="020",
            focus="",
        )
        _write_brief(
            self.dir,
            "20260610-094000-gamma.md",
            repo="repo-c",
            related=[alpha.stem],
            focus="",
        )

        # blocked-by-excluded pair: same slug + same target-spec, but blocked-by
        # suppresses every signal between them, so no edge at all.
        login_a = _write_brief(
            self.dir,
            "20260610-095000-fix-login.md",
            repo="repo-d",
            target_spec="030",
            focus="fix login flow",
        )
        _write_brief(
            self.dir,
            "20260610-096000-fix-login.md",
            repo="repo-d",
            target_spec="030",
            blocked_by=[login_a.stem],
            focus="fix login flow",
        )

        # ordinary (non-near-identical) focus-overlap pair, size-2 -> dropped.
        # 5 shared / 11 tokens each ~= 0.4545: above OVERLAP_THRESHOLD (0.45)
        # so a focus-overlap match exists, but below the lowered
        # NEAR_IDENTICAL_THRESHOLD (0.50) so it stays non-qualifying.
        _write_brief(
            self.dir,
            "20260610-097000-delta.md",
            repo="repo-e",
            focus=(
                "apple banana cherry date fig "
                "grape honey kiwi lemon mango nectarine"
            ),
        )
        _write_brief(
            self.dir,
            "20260610-098000-epsilon.md",
            repo="repo-e",
            focus=(
                "apple banana cherry date fig "
                "quince plum peach pear grapefruit papaya"
            ),
        )

        # unrelated brief matching nothing.
        _write_brief(self.dir, "20260610-099000-zeta.md", repo="repo-f", focus="zzz random text")

        # malformed brief must be skipped without raising.
        (self.dir / "20260610-100000-broken.md").write_text(
            "no frontmatter here, just body text.\n", encoding="utf-8"
        )

        clusters = compute_clusters(self.dir, _fake_parse_frontmatter)

        self.assertEqual(len(clusters), 2)

        dup_cluster = self._members_containing(clusters, dup_a.stem)
        self.assertIsNotNone(dup_cluster)
        assert dup_cluster is not None
        self.assertEqual(dup_cluster["members"], sorted([dup_a.stem, dup_b.stem]))
        self.assertEqual(dup_cluster["signals"], ["duplicate-slug"])

        trio_cluster = self._members_containing(clusters, alpha.stem)
        self.assertIsNotNone(trio_cluster)
        assert trio_cluster is not None
        self.assertEqual(trio_cluster["size"], 3)
        self.assertEqual(trio_cluster["signals"], ["related-link", "same-target-spec"])

    def test_integration_real_tmpdir_queue_fixture(self):
        queue_dir = self.dir / "queue"
        queue_dir.mkdir()

        a = _write_brief(queue_dir, "20260610-090000-fix-auth-flow.md", repo="repo-a", focus="")
        b = _write_brief(queue_dir, "20260610-091000-fix-auth-flow.md", repo="repo-b", focus="")
        _write_brief(
            queue_dir, "20260610-092000-unrelated.md", repo="repo-c", focus="nothing shared"
        )

        clusters = compute_clusters(queue_dir, _fake_parse_frontmatter)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["members"], sorted([a.stem, b.stem]))
        self.assertIn("duplicate-slug", clusters[0]["signals"])


class NoFilesystemWritesOrNetworkCallsTests(unittest.TestCase):
    def test_source_contains_no_write_open_or_network_imports(self):
        source = Path(_cluster_detect_mod.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r'open\([^)]*["\']a["\']')
        self.assertNotRegex(source, r'open\([^)]*["\']w["\']')
        for forbidden in ("socket", "urllib", "http.client", "requests"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
