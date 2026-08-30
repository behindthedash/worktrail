#!/usr/bin/env python3
"""Tests for score_candidates.py — the Create-time auto-detect scoring helper.

Run: python3 -m pytest tests/workqueue/test_score_candidates.py -q
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from worktrail.workqueue import score_candidates as sc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _brief(
    focus: str,
    repo: str = "null",
    status: str = "queued",
    blocked_by: list | None = None,
    extra_body: str = "",
    target_spec: str | None = None,
    related: list | None = None,
) -> str:
    fm_lines = [
        f"focus: {focus}",
        f"repo: {repo}",
        f"status: {status}",
    ]
    if target_spec:
        fm_lines.append(f"target-spec: {target_spec}")
    if related:
        fm_lines.append("related:")
        for rel in related:
            fm_lines.append(f"  - {rel}")
    if blocked_by:
        fm_lines.append("blocked-by:")
        for dep in blocked_by:
            fm_lines.append(f"  - {dep}")
    body = f"\n## Focus\n\n{focus}\n"
    if extra_body:
        body += f"\n## Key artifacts\n\n{extra_body}\n"
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body


class ScoreCandidatesTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.queue = self.base / "queue"
        self.picked = self.base / "picked"
        self.queue.mkdir(parents=True, exist_ok=True)
        self.picked.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write_queue(self, name: str, **kw) -> Path:
        p = self.queue / name
        p.write_text(_brief(**kw), encoding="utf-8")
        return p

    def write_picked(self, name: str, **kw) -> Path:
        p = self.picked / name
        p.write_text(_brief(**kw), encoding="utf-8")
        return p

    def write_new_brief(self, name: str, **kw) -> Path:
        p = self.queue / name
        p.write_text(_brief(**kw), encoding="utf-8")
        return p

    def score(self, new_brief_path: Path) -> dict:
        return sc.score_candidates(new_brief_path, self.base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSameRepoBoost(ScoreCandidatesTestBase):
    """AC-013: same-repo briefs score higher than cross-repo briefs."""

    def test_same_repo_scores_higher_than_different_repo(self):
        repo = "/home/user/projects/myapp"
        focus_new = "sharp cloudinary migration slot bake"
        focus_shared = "sharp cloudinary bake writeback missing"

        new = self.write_new_brief("20260604-160000-new.md", focus=focus_new, repo=repo)
        self.write_queue("20260604-112107-same-repo.md", focus=focus_shared, repo=repo)
        self.write_queue(
            "20260604-154500-diff-repo.md",
            focus=focus_shared,
            repo="/home/user/projects/other",
        )

        result = self.score(new)
        all_candidates = result["auto_link"] + result["confirm"]
        ids = [c["id"] for c in all_candidates]

        # Both should appear (they share tokens with the new brief)
        self.assertIn("20260604-112107-same-repo", ids)
        self.assertIn("20260604-154500-diff-repo", ids)

        # Find their scores
        same_score = next(
            c["total_score"]
            for c in self._score_raw(new)
            if c["id"] == "20260604-112107-same-repo"
        )
        diff_score = next(
            c["total_score"]
            for c in self._score_raw(new)
            if c["id"] == "20260604-154500-diff-repo"
        )
        self.assertGreater(same_score, diff_score)

    def _score_raw(self, new_path: Path):
        """Internal helper to get raw scored list before split into auto/confirm."""
        from worktrail.workqueue.score_candidates import (
            MIN_OVERLAP,
            SAME_REPO_BOOST,
            _is_blocked_by_pair,
            _md_files,
            _normalize_repo,
            _overlap_coefficient,
            _read_brief,
            _tokenize,
        )

        new_fm, new_body = _read_brief(new_path)
        new_stem = new_path.stem
        new_repo = _normalize_repo(new_fm.get("repo"))
        new_focus_tokens = _tokenize(str(new_fm.get("focus") or ""))
        new_body_tokens = _tokenize(new_body or "")

        scored = []
        for subdir in ("queue", "picked"):
            for f in _md_files(self.base / subdir):
                if f.resolve() == new_path.resolve():
                    continue
                cand_fm, cand_body = _read_brief(f)
                if cand_fm is None or cand_fm.get("status") == "done":
                    continue
                if _is_blocked_by_pair(new_fm, new_stem, cand_fm, f.stem):
                    continue
                cand_focus_tokens = _tokenize(str(cand_fm.get("focus") or ""))
                cand_body_tokens = _tokenize(cand_body or "")
                fs = _overlap_coefficient(new_focus_tokens, cand_focus_tokens)
                bs = _overlap_coefficient(new_body_tokens, cand_body_tokens)
                base = fs * 0.7 + bs * 0.3
                cand_repo = _normalize_repo(cand_fm.get("repo"))
                same = cand_repo is not None and cand_repo == new_repo
                total = base + (SAME_REPO_BOOST if same else 0.0)
                if total >= MIN_OVERLAP:
                    scored.append({"id": f.stem, "total_score": total})
        return scored


class TestBlockedByExclusion(ScoreCandidatesTestBase):
    """AC-016: blocked-by pairs (either direction) are excluded."""

    def test_new_brief_blocked_by_candidate_excluded(self):
        repo = "/home/user/projects/myapp"
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
            blocked_by=["20260604-161500-prereq"],
        )
        # This is the dep-id that the new brief is blocked by
        self.write_queue(
            "20260604-161500-prereq.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
        )
        result = self.score(new)
        all_ids = [c["id"] for c in result["auto_link"] + result["confirm"]]
        self.assertNotIn("20260604-161500-prereq", all_ids)

    def test_candidate_blocked_by_new_brief_excluded(self):
        repo = "/home/user/projects/myapp"
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
        )
        # Candidate depends on the new brief (other direction)
        self.write_queue(
            "20260604-161500-dependant.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
            blocked_by=["20260604-160000-new"],
        )
        result = self.score(new)
        all_ids = [c["id"] for c in result["auto_link"] + result["confirm"]]
        self.assertNotIn("20260604-161500-dependant", all_ids)


class TestMinimumThreshold(ScoreCandidatesTestBase):
    """AC-013, AC-017: briefs below threshold are absent from output."""

    def test_no_shared_tokens_different_repo_excluded(self):
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo="/home/user/projects/myapp",
        )
        # Completely unrelated brief
        self.write_queue(
            "20260604-100000-unrelated.md",
            focus="database postgres schema migrations",
            repo="/home/user/projects/other",
        )
        result = self.score(new)
        all_ids = [c["id"] for c in result["auto_link"] + result["confirm"]]
        self.assertNotIn("20260604-100000-unrelated", all_ids)


class TestCapToTopThree(ScoreCandidatesTestBase):
    """AC-013: candidate set capped to top ~3."""

    def test_five_above_threshold_capped_at_three(self):
        repo = "/home/user/projects/myapp"
        focus_new = "sharp cloudinary migration bake slot crop resize"
        new = self.write_new_brief("20260604-160000-new.md", focus=focus_new, repo=repo)

        # Create 5 candidates with overlapping focus
        for i in range(1, 6):
            self.write_queue(
                f"20260604-10000{i}-cand{i}.md",
                focus=f"sharp cloudinary migration bake slot crop cand{i}",
                repo=repo,
            )

        result = self.score(new)
        total = len(result["auto_link"]) + len(result["confirm"])
        self.assertLessEqual(total, sc.TOP_N)


class TestNoCandidates(ScoreCandidatesTestBase):
    """AC-017: when no candidate passes threshold, both lists are empty."""

    def test_all_below_threshold_returns_empty(self):
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo="/home/user/projects/myapp",
        )
        # Unrelated brief
        self.write_queue(
            "20260604-100000-unrelated.md",
            focus="database postgres schema",
            repo="/home/user/projects/other",
        )
        result = self.score(new)
        self.assertEqual(result["auto_link"], [])
        self.assertEqual(result["confirm"], [])


class TestDoneExclusion(ScoreCandidatesTestBase):
    """AC-012: picked briefs with status: done are excluded."""

    def test_done_brief_in_picked_excluded(self):
        repo = "/home/user/projects/myapp"
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
        )
        # Done brief with same focus
        self.write_picked(
            "20260604-112107-done-brief.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
            status="done",
        )
        result = self.score(new)
        all_ids = [c["id"] for c in result["auto_link"] + result["confirm"]]
        self.assertNotIn("20260604-112107-done-brief", all_ids)


class TestHighConfidenceAutoLink(ScoreCandidatesTestBase):
    """AC-014: same-repo + high-confidence candidates appear in auto_link."""

    def test_same_repo_high_confidence_goes_to_auto_link(self):
        repo = "/home/user/projects/myapp"
        # High overlap: 5 shared tokens out of 6 → focus_overlap = 5/6 ≈ 0.83
        # base_score ≈ 0.83 * 0.70 = 0.58; total = 0.58 + 0.20 = 0.78 > HIGH_CONFIDENCE
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary slot crop bake resize migration",
            repo=repo,
        )
        self.write_queue(
            "20260604-112107-high-conf.md",
            focus="sharp cloudinary slot crop bake resize process",
            repo=repo,
        )
        result = self.score(new)
        auto_ids = [c["id"] for c in result["auto_link"]]
        self.assertIn("20260604-112107-high-conf", auto_ids)
        # Must NOT be in confirm
        confirm_ids = [c["id"] for c in result["confirm"]]
        self.assertNotIn("20260604-112107-high-conf", confirm_ids)

    def test_cross_repo_goes_to_confirm_not_auto_link(self):
        focus = "sharp cloudinary slot crop bake resize migration"
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus=focus,
            repo="/home/user/projects/myapp",
        )
        self.write_queue(
            "20260604-112107-cross-repo.md",
            focus="sharp cloudinary slot crop bake resize process",
            repo="/home/user/projects/other",
        )
        result = self.score(new)
        auto_ids = [c["id"] for c in result["auto_link"]]
        confirm_ids = [c["id"] for c in result["confirm"]]
        self.assertNotIn("20260604-112107-cross-repo", auto_ids)
        self.assertIn("20260604-112107-cross-repo", confirm_ids)


class TestEdgeCases(ScoreCandidatesTestBase):
    """AC-017: edge cases must not crash or block creation."""

    def test_empty_queue_and_picked_returns_empty(self):
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration",
            repo="/home/user/projects/myapp",
        )
        # Remove the new brief from queue to simulate empty dirs
        # (the dirs exist but have no candidates other than new itself)
        result = self.score(new)
        self.assertEqual(result["auto_link"], [])
        self.assertEqual(result["confirm"], [])

    def test_missing_queue_and_picked_dirs_returns_empty(self):
        # Write the new brief outside queue/picked, then remove those dirs
        new = self.base / "20260604-160000-new.md"
        new.write_text(
            _brief(
                focus="sharp cloudinary migration", repo="/home/user/projects/myapp"
            ),
            encoding="utf-8",
        )
        # Remove queue/ and picked/ so scorer sees no candidate dirs
        self.queue.rmdir()
        self.picked.rmdir()
        result = sc.score_candidates(new, self.base)
        self.assertEqual(result["auto_link"], [])
        self.assertEqual(result["confirm"], [])

    def test_malformed_frontmatter_skipped_without_crash(self):
        repo = "/home/user/projects/myapp"
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
        )
        # Malformed brief (no frontmatter at all)
        bad = self.queue / "20260604-111111-bad.md"
        bad.write_text(
            "This is not a valid brief — no frontmatter.\n", encoding="utf-8"
        )

        # Good candidate (should still be found)
        self.write_queue(
            "20260604-112107-good.md",
            focus="sharp cloudinary migration bake slot resize",
            repo=repo,
        )
        # Must not crash
        result = self.score(new)
        # The malformed brief should not appear
        all_ids = [c["id"] for c in result["auto_link"] + result["confirm"]]
        self.assertNotIn("20260604-111111-bad", all_ids)
        # The good candidate should appear
        self.assertIn("20260604-112107-good", all_ids)

    def test_self_not_included_in_candidates(self):
        repo = "/home/user/projects/myapp"
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
        )
        result = self.score(new)
        all_ids = [c["id"] for c in result["auto_link"] + result["confirm"]]
        self.assertNotIn("20260604-160000-new", all_ids)

    def test_null_repo_never_same_repo_matches(self):
        """Two briefs with repo: null must not be treated as same-repo."""
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo="null",
        )
        self.write_queue(
            "20260604-112107-other.md",
            focus="sharp cloudinary migration bake slot resize",
            repo="null",
        )
        result = self.score(new)
        # Both have null repo — should go to confirm (not auto_link)
        auto_ids = [c["id"] for c in result["auto_link"]]
        self.assertNotIn("20260604-112107-other", auto_ids)


class TestOutputShape(ScoreCandidatesTestBase):
    """Verify output JSON shape matches the contract."""

    def test_output_has_path_id_focus_keys(self):
        repo = "/home/user/projects/myapp"
        new = self.write_new_brief(
            "20260604-160000-new.md",
            focus="sharp cloudinary migration bake slot",
            repo=repo,
        )
        self.write_queue(
            "20260604-112107-cand.md",
            focus="sharp cloudinary migration bake resize",
            repo=repo,
        )
        result = self.score(new)
        all_entries = result["auto_link"] + result["confirm"]
        self.assertTrue(len(all_entries) > 0)
        for entry in all_entries:
            self.assertIn("path", entry)
            self.assertIn("id", entry)
            self.assertIn("focus", entry)


class TestBatchMode(ScoreCandidatesTestBase):
    """Consume-time batch candidate detection (--mode batch)."""

    REPO = "/home/user/projects/myapp"

    def batch(self, brief_path: Path) -> dict:
        return sc.batch_candidates(brief_path, self.base)

    def test_same_repo_same_target_spec_included(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="tenant scope capability registry gate",
            repo=self.REPO,
            target_spec="063-capability-provider-registry",
        )
        self.write_queue(
            "20260701-000002-cand.md",
            focus="provider resolution workspace isolation",
            repo=self.REPO,
            target_spec="063-capability-provider-registry",
        )
        result = self.batch(primary)
        self.assertEqual(len(result["batch"]), 1)
        self.assertEqual(result["batch"][0]["id"], "20260701-000002-cand")
        self.assertEqual(result["batch"][0]["reason"], "same-target-spec")

    def test_different_repo_excluded_even_with_identical_focus(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="sharp cloudinary migration bake slot",
            repo=self.REPO,
        )
        self.write_queue(
            "20260701-000002-cand.md",
            focus="sharp cloudinary migration bake slot",
            repo="/home/user/projects/otherapp",
        )
        self.assertEqual(self.batch(primary)["batch"], [])

    def test_related_link_included_despite_low_score(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="alpha bravo charlie",
            repo=self.REPO,
            related=["20260701-000002-unrelated-words"],
        )
        self.write_queue(
            "20260701-000002-unrelated-words.md",
            focus="delta echo foxtrot",
            repo=self.REPO,
        )
        result = self.batch(primary)
        self.assertEqual(len(result["batch"]), 1)
        self.assertEqual(result["batch"][0]["reason"], "related-link")

    def test_blocked_by_pair_excluded(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="tenant scope capability registry gate",
            repo=self.REPO,
            blocked_by=["20260701-000002-cand"],
        )
        self.write_queue(
            "20260701-000002-cand.md",
            focus="tenant scope capability registry gate",
            repo=self.REPO,
        )
        self.assertEqual(self.batch(primary)["batch"], [])

    def test_picked_briefs_never_batch_candidates(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="tenant scope capability registry gate",
            repo=self.REPO,
        )
        self.write_picked(
            "20260701-000002-cand.md",
            focus="tenant scope capability registry gate",
            repo=self.REPO,
            status="picked",
        )
        self.assertEqual(self.batch(primary)["batch"], [])

    def test_null_repo_returns_empty(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="tenant scope capability registry gate",
            repo="null",
        )
        self.write_queue(
            "20260701-000002-cand.md",
            focus="tenant scope capability registry gate",
            repo="null",
        )
        self.assertEqual(self.batch(primary)["batch"], [])

    def test_cap_and_related_first_ordering(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="tenant scope capability registry gate",
            repo=self.REPO,
            related=["20260701-000005-linked"],
        )
        for i in (2, 3, 4):
            self.write_queue(
                f"20260701-00000{i}-cand.md",
                focus="tenant scope capability registry gate hardening",
                repo=self.REPO,
            )
        self.write_queue(
            "20260701-000005-linked.md",
            focus="totally different words here",
            repo=self.REPO,
        )
        result = self.batch(primary)
        self.assertEqual(len(result["batch"]), sc.BATCH_TOP_N)
        self.assertEqual(result["batch"][0]["id"], "20260701-000005-linked")

    def test_cli_batch_mode_shape(self):
        primary = self.write_queue(
            "20260701-000001-primary.md",
            focus="tenant scope capability registry gate",
            repo=self.REPO,
            target_spec="063-capability-provider-registry",
        )
        self.write_queue(
            "20260701-000002-cand.md",
            focus="capability registry follow-up",
            repo=self.REPO,
            target_spec="063-capability-provider-registry",
        )
        result = sc.batch_candidates(primary, self.base)
        json.dumps(result)  # serialisable
        for entry in result["batch"]:
            self.assertEqual(set(entry), {"path", "id", "focus", "reason"})


class TestBatchModeIdentifierOverlap(ScoreCandidatesTestBase):
    """Regression for brief 20260821-172334: three datalena CI-guard briefs
    shared a concrete filename/job-name identifier each but scored 0.32/0.42/0.36
    against BATCH_MIN (0.45) because the overlap coefficient treats every token
    (plain word or identifier) the same, letting long prose dilute a strong,
    precise identifier match into noise. Focus text below is trimmed but keeps
    the exact shared identifiers from the live queue briefs so the reproduced
    scores match what was observed (verified 2026-08-21)."""

    REPO = "/home/briank/projects/datalena"

    # Verbatim focus text from the live queue briefs (20260820-191437,
    # 20260821-070329, 20260821-130630) that reproduced 0.42/0.36/0.32 against
    # BATCH_MIN — trimmed synthetic text under-reproduces the dilution effect
    # since fewer total tokens raises the overlap ratio artificially.
    B1_FOCUS = (
        "datalena: add a CI guard (AST-based, e.g. an ast.walk check similar to "
        "scripts/ci/check_origin_referer_allowlist.py) that fails the build when "
        "record_audit()/record_tenant_scope_denial() is followed by a bare "
        "session.commit() not wrapped in db/session_context.py's "
        "commit_tenant_scope_denial() -- the commit-clears-tenant-scoping-GUCs "
        "regression has now shipped independently 2-3 times in embed_grants.py "
        "alone (PR #2406, #2418, plus the organization_id/workspace_id FK bug "
        "found in PR for 20260820-164131), and the new shared helper only "
        "prevents recurrence if every future deny-branch author actually reaches "
        "for it. Without an automated guard, the helper is a convention, not the "
        "structural guarantee its own docstring claims."
    )
    B2_FOCUS = (
        "datalena: promote ci-guardrails to a required status check in "
        ".github/rulesets/protect-dev.json (and stg/prd if they mirror dev's "
        "required list) now that it reports 0 violations (PR #2435 remediated "
        "the 18 pre-existing timeout-minutes/continue-on-error findings). "
        "Currently ci-guardrails runs on every PR but is advisory-only -- "
        "nothing blocks a future PR from reintroducing an unguarded "
        "continue-on-error step or a required-check job with no "
        "timeout-minutes, which is exactly the regression class that produced "
        "the 18-violation backlog PR #2435 just cleared. Suggested approach: "
        "add the ci-guardrails job's check name to required_status_checks in "
        "protect-dev.json (and any mirrored stg/prd rulesets per "
        "CLAUDE.repo.md's job/ruleset sync rule), apply the ruleset via gh api, "
        "and verify with the rulesets_drift_guard workflow."
    )
    B3_FOCUS = (
        "datalena: add a deterministic CI guardrail (rg/AST-based check, "
        "similar to check_origin_referer_allowlist.py's pattern) that flags "
        "any FastAPI route parameter named limit/offset/page/size declared as "
        "a bare 'int = <literal>' (or 'int' with no Query bound) instead of "
        "Annotated[int, Query(ge=..., le=...)]. Motivation: PR #2438 found and "
        "fixed 14 such sites across 10 routers (s3_watchers, managed_datasets, "
        "schedules, email_channels, delivery_destinations, merge_runs, "
        "ingestion_credentials, dashboard_releases, schedule_runs, "
        "source_arrivals) that were unreachable by any existing lint rule -- "
        "only Schemathesis's expensive nightly fuzz-and-manually-triage cycle "
        "caught them, and only 9 of the 14 vulnerable sites had actually been "
        "exercised by a fuzzer run yet. Without a structural guard, the exact "
        "same bug class (unbounded pagination params -> unhandled 500 from "
        "adversarial/malformed input) can reappear in any new endpoint written "
        "after this fix, silently, until the next multi-hour fuzz-triage cycle "
        "finds it again. A cheap static check run in CI (mirroring the "
        "existing ci-guardrails job's shape) closes this permanently for "
        "near-zero ongoing cost, converting a recurring expensive detection "
        "loop into a one-time prevention."
    )

    def _write_brief(self, name: str, focus_text: str) -> Path:
        # Block-scalar style (`focus: |-`) — the format the handoff capture
        # flow actually writes (see TestBatchModeBlockScalarFocusRegression
        # above). The plain-scalar `_brief()` helper breaks on this text's
        # colons/brackets, which isn't the defect under test here.
        content = (
            f"---\nfocus: |-\n  {focus_text}\nrepo: {self.REPO}\nstatus: queued\n---\n"
        )
        p = self.queue / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_shared_filename_identifier_crosses_batch_min(self):
        primary = self._write_brief("20260820-191437-b1.md", self.B1_FOCUS)
        self._write_brief("20260821-130630-b3.md", self.B3_FOCUS)

        result = sc.batch_candidates(primary, self.base)
        ids = [c["id"] for c in result["batch"]]
        self.assertIn("20260821-130630-b3", ids)

    def test_shared_job_name_identifier_crosses_batch_min(self):
        primary = self._write_brief("20260821-070329-b2.md", self.B2_FOCUS)
        self._write_brief("20260821-130630-b3.md", self.B3_FOCUS)

        result = sc.batch_candidates(primary, self.base)
        ids = [c["id"] for c in result["batch"]]
        self.assertIn("20260821-130630-b3", ids)
        entry = next(c for c in result["batch"] if c["id"] == "20260821-130630-b3")
        self.assertEqual(entry["reason"], "identifier-overlap")

    def test_no_shared_identifier_stays_unbatched(self):
        """b1 and b2 share no identifier-shaped token — legitimately different
        topics (audit-commit fix vs. ruleset promotion) — so they should NOT be
        forced together just because both mention 'CI guard' generically."""
        primary = self._write_brief("20260820-191437-b1.md", self.B1_FOCUS)
        self._write_brief("20260821-070329-b2.md", self.B2_FOCUS)

        result = sc.batch_candidates(primary, self.base)
        ids = [c["id"] for c in result["batch"]]
        self.assertNotIn("20260821-070329-b2", ids)

    def test_eg_abbreviation_is_not_an_identifier(self):
        """ "e.g" (from "e.g." in prose) matches the compound-token shape
        (letter + '.' + letter) but is not an identifier — a live-queue false
        positive found while validating this fix: two otherwise-unrelated
        datalena briefs both used "e.g." and were spuriously batched on it."""
        self.assertEqual(sc._identifier_tokens("similar to X (e.g. a widget)"), set())
        self.assertEqual(sc._identifier_tokens("i.e. this one specifically"), set())
        # Real two-char-segment identifiers must still match.
        self.assertIn("ast.walk", sc._identifier_tokens("an ast.walk check"))
        self.assertIn(
            "s3_watchers", sc._identifier_tokens("across s3_watchers and others")
        )


class TestReadBriefYamlParsing(unittest.TestCase):
    """Coverage for _read_brief's frontmatter parsing (delegated to the
    shared, PyYAML-backed worktrail.shared.brief_frontmatter parser since
    brief 20260807-121604 replaced the hand-rolled _parse_fm regex parser).

    The block-scalar cases are regression coverage carried over from brief
    20260807-114939-worktrail-score-candidates-batch-mode (a `focus: |-`
    field — the common multi-line capture format, ~18% of a real queue — was
    previously parsed as the literal indicator string "|-" instead of its
    text, silently zeroing focus-token overlap in both capture and batch
    scoring). The flow-list and quoted-string cases are new: they prove the
    migration eliminates the whole class of YAML constructs the regex parser
    could not special-case, not just the one reported instance.
    """

    def _fm(self, tmp_path: Path, content: str):
        p = tmp_path / "brief.md"
        p.write_text(content, encoding="utf-8")
        fm, _ = sc._read_brief(p)
        return fm

    def test_dash_style_block_scalar(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "---\nfocus: |-\n  the actual focus text here\nrepo: null\n---\n"
            fm = self._fm(Path(tmp), content)
        self.assertEqual(fm["focus"], "the actual focus text here")

    def test_multiline_dash_style_joins_with_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "---\nfocus: |-\n  line one\n  line two\nrepo: null\n---\n"
            fm = self._fm(Path(tmp), content)
        self.assertEqual(fm["focus"], "line one\nline two")

    def test_folded_style_joins_with_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "---\nfocus: >-\n  line one\n  line two\nrepo: null\n---\n"
            fm = self._fm(Path(tmp), content)
        self.assertEqual(fm["focus"], "line one line two")

    def test_block_scalar_stops_at_next_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\nfocus: |-\n  the focus text\nrepo: /a/b\nstatus: queued\n---\n"
            )
            fm = self._fm(Path(tmp), content)
        self.assertEqual(fm["focus"], "the focus text")
        self.assertEqual(fm["repo"], "/a/b")
        self.assertEqual(fm["status"], "queued")

    def test_flow_style_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "---\nfocus: flow list test\nrelated: [a, b, c]\n---\n"
            fm = self._fm(Path(tmp), content)
        self.assertEqual(fm["related"], ["a", "b", "c"])

    def test_quoted_string_with_embedded_colon(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = '---\nfocus: "a value: with a colon in it"\nrepo: null\n---\n'
            fm = self._fm(Path(tmp), content)
        self.assertEqual(fm["focus"], "a value: with a colon in it")


class TestBatchModeBlockScalarFocusRegression(ScoreCandidatesTestBase):
    """End-to-end regression for the reported batch-mode miss (brief
    20260807-114939): two same-repo briefs with substantially overlapping
    `focus: |-` text were not surfaced as batch candidates because the block
    scalar was parsed as the literal "|-" instead of the real text.
    """

    REPO = "/home/user/projects/datalena"

    def _write_block_scalar_brief(self, name: str, focus_text: str, repo: str) -> Path:
        content = (
            "---\n"
            "focus: |-\n"
            f"  {focus_text}\n"
            f"repo: {repo}\n"
            "status: queued\n"
            "---\n"
            f"\n## Focus\n\n{focus_text}\n"
        )
        p = self.queue / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_near_identical_block_scalar_focus_briefs_now_batch(self):
        primary_focus = (
            "Enable datalena's migration-fold safety net: add migration_path_patterns "
            "to docs/specs/go-policy.yaml for the orchestrator group-isolation fix so a "
            "quarantined migration group does not block unrelated consumer groups."
        )
        companion_focus = (
            "Configure datalena's docs/specs/go-policy.yaml with migration_path_patterns "
            "so the orchestrator group-isolation fix actually takes effect for datalena's "
            "own parallel-orchestrator runs, preventing a quarantined migration group."
        )
        primary = self._write_block_scalar_brief(
            "20260701-000001-primary.md", primary_focus, self.REPO
        )
        self._write_block_scalar_brief(
            "20260701-000002-cand.md", companion_focus, self.REPO
        )

        result = sc.batch_candidates(primary, self.base)
        self.assertEqual([c["id"] for c in result["batch"]], ["20260701-000002-cand"])


class TestPrecheckDuplicate(ScoreCandidatesTestBase):
    """precheck_duplicate() -- the pre-write counterpart to score_candidates()'s
    post-write auto_link tier (same repo AND total_score >= HIGH_CONFIDENCE)."""

    REPO = "/home/user/projects/myapp"

    def test_finds_high_confidence_same_repo_match(self):
        focus = "handoff capture dedup gap durable artifact overlap score candidates"
        self.write_queue(
            "20260830-090000-existing.md",
            focus=focus,
            repo=self.REPO,
        )
        match = sc.precheck_duplicate(focus, "", self.REPO, self.base)
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "20260830-090000-existing")
        self.assertGreaterEqual(match["total_score"], sc.HIGH_CONFIDENCE)

    def test_returns_none_below_threshold(self):
        self.write_queue(
            "20260830-090000-existing.md",
            focus="totally unrelated database migration script",
            repo=self.REPO,
        )
        match = sc.precheck_duplicate(
            "handoff capture dedup gap overlap scoring", "", self.REPO, self.base
        )
        self.assertIsNone(match)

    def test_returns_none_for_cross_repo_match(self):
        focus = "handoff capture dedup gap durable artifact overlap score candidates"
        self.write_queue(
            "20260830-090000-existing.md", focus=focus, repo="/home/user/projects/other"
        )
        match = sc.precheck_duplicate(focus, "", self.REPO, self.base)
        self.assertIsNone(match)

    def test_returns_none_for_null_repo(self):
        """Matches score_candidates()'s own behavior: a null-repo new brief
        can never satisfy same_repo, so precheck never matches either."""
        focus = "handoff capture dedup gap durable artifact overlap score candidates"
        self.write_queue("20260830-090000-existing.md", focus=focus, repo="null")
        match = sc.precheck_duplicate(focus, "", None, self.base)
        self.assertIsNone(match)

    def test_agrees_with_post_write_auto_link_tier(self):
        """precheck_duplicate() run before writing agrees with score_candidates()
        run after writing the identical content -- the refactor that shares
        _score_against_queue() between them must not change either's answer."""
        focus = "handoff capture dedup gap durable artifact overlap score candidates"
        body = "\n## Discovery context\n\ncreate_handoff.py and work_queue.py\n"
        self.write_queue("20260830-090000-existing.md", focus=focus, repo=self.REPO)

        precheck = sc.precheck_duplicate(focus, body, self.REPO, self.base)
        self.assertIsNotNone(precheck)

        new_path = self.queue / "20260830-151114-new.md"
        new_path.write_text(
            f"---\nfocus: {focus}\nrepo: {self.REPO}\nstatus: queued\n---\n" + body,
            encoding="utf-8",
        )
        post_write = sc.score_candidates(new_path, self.base)
        auto_link_ids = [c["id"] for c in post_write["auto_link"]]
        self.assertIn("20260830-090000-existing", auto_link_ids)
        self.assertEqual(precheck["id"], "20260830-090000-existing")


class TestCLIOutput(unittest.TestCase):
    """Verify the CLI emits valid JSON."""

    def test_cli_emits_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "queue").mkdir()
            (base / "picked").mkdir()
            new = base / "queue" / "20260604-160000-new.md"
            new.write_text(
                _brief(
                    focus="sharp cloudinary migration", repo="/home/user/projects/myapp"
                ),
                encoding="utf-8",
            )
            result = sc.score_candidates(new, base)
            # Verify it serialises to valid JSON
            json.dumps(result)
            self.assertIn("auto_link", result)
            self.assertIn("confirm", result)


if __name__ == "__main__":
    unittest.main()
