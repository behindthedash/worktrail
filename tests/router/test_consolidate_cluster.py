#!/usr/bin/env python3
"""
Tests for consolidate_cluster.py (spec 018, change
2026-07-14--consolidate-cluster-action, TASK-CHG-002).

Covers AC-002 through AC-005 with `tempfile`-backed `queue/`/`picked/`
fixture directories and real `work_queue.py` subprocess invocations
(mirroring `test_cluster_dashboard_e2e.py`'s fixture style).

Run with:
    python3 -m pytest plugins/developer-kit-project-management/skills/devkit-pm-go/scripts/test_consolidate_cluster.py -q
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from worktrail.router import consolidate_cluster as cc

# --------------------------------------------------------------------------- #
# Fixture construction
# --------------------------------------------------------------------------- #


def _write_brief(
    dirpath: Path,
    filename: str,
    *,
    focus: str = "",
    suggested_approach: list[str] | None = None,
) -> str:
    """Write a realistic queued-brief .md file; return its stem (brief id)."""
    lines = [
        "---",
        f"id: {filename[:-3]}",
        f'focus: "{focus}"',
        "status: queued",
        "---",
        "",
    ]
    lines.append("## Focus")
    lines.append("")
    lines.append(focus)
    lines.append("")
    if suggested_approach:
        lines.append("## Suggested approach")
        lines.append("")
        lines.extend(f"{i}. {b}" for i, b in enumerate(suggested_approach, 1))
        lines.append("")
    path = dirpath / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path.stem


class ConsolidateClusterTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.wq_base = self.tmp_path / "wq"
        self.queue_dir = self.wq_base / "queue"
        self.picked_dir = self.wq_base / "picked"
        self.queue_dir.mkdir(parents=True)
        self.picked_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["WORK_QUEUE_DIR"] = str(self.wq_base)
        return env

    def _wq_claim(self, identifier: str) -> dict:
        """Real `work_queue.py claim` subprocess call -- used to simulate an
        out-of-band race between the dashboard snapshot and preview/execute."""
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "worktrail.workqueue.work_queue",
                "claim",
                identifier,
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        return json.loads(r.stdout)

    def _wq_list_json(self) -> str:
        r = subprocess.run(
            [sys.executable, "-m", "worktrail.workqueue.work_queue", "list", "--json"],
            check=False,
            capture_output=True,
            text=True,
            env=self._env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return r.stdout

    def _frontmatter(self, path: Path) -> dict[str, str]:
        return cc._parse_frontmatter(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# resolvable_members() / build_preview()
# --------------------------------------------------------------------------- #


class ResolvableMembersAndPreview(ConsolidateClusterTestCase):
    def test_all_members_resolvable_when_nothing_changed(self):
        """Baseline for AC-005: all member ids still present in queue_dir are
        returned in resolvable_members, excluded_members is empty."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        m3 = _write_brief(
            self.queue_dir, "20260701-100200-gamma.md", focus="Gamma work"
        )

        preview = cc.build_preview([m1, m2, m3], self.queue_dir)

        self.assertEqual(sorted(preview["resolvable_members"]), sorted([m1, m2, m3]))
        self.assertEqual(preview["excluded_members"], [])
        self.assertFalse(preview["withdrawn"])
        self.assertIsNotNone(preview["draft"])

    def test_out_of_band_claim_excludes_member(self):
        """AC-005: a member claimed out-of-band (real work_queue.py claim)
        between cluster formation and build_preview() is excluded."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        m3 = _write_brief(
            self.queue_dir, "20260701-100200-gamma.md", focus="Gamma work"
        )

        claim_result = self._wq_claim(m2)
        self.assertEqual(claim_result["status"], "claimed")

        preview = cc.build_preview([m1, m2, m3], self.queue_dir)

        self.assertIn(m2, preview["excluded_members"])
        self.assertNotIn(m2, preview["resolvable_members"])
        self.assertIn(m1, preview["resolvable_members"])
        self.assertIn(m3, preview["resolvable_members"])

    def test_withdrawn_when_resolvable_drops_below_two(self):
        """AC-005, REQ-CHG-005: when the out-of-band claim(s) above drop
        resolvable membership below 2, build_preview() withdraws (no draft)."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")

        claim_result = self._wq_claim(m2)
        self.assertEqual(claim_result["status"], "claimed")

        preview = cc.build_preview([m1, m2], self.queue_dir)

        self.assertTrue(preview["withdrawn"])
        self.assertIsNone(preview["draft"])
        self.assertEqual(preview["resolvable_members"], [m1])

    def test_draft_merges_focus_and_suggested_approach_from_real_members(self):
        """REQ-CHG-002: draft_consolidated_brief() merges focus: text and
        Suggested approach bullets from >=2 real fixture brief files."""
        m1 = _write_brief(
            self.queue_dir,
            "20260701-100000-alpha.md",
            focus="Fix the alpha widget alignment bug",
            suggested_approach=["Inspect the CSS grid rules", "Add a regression test"],
        )
        m2 = _write_brief(
            self.queue_dir,
            "20260701-100100-beta.md",
            focus="Fix the beta widget spacing bug",
            suggested_approach=["Audit the spacing tokens"],
        )

        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        self.assertIn("Fix the alpha widget alignment bug", draft["focus"])
        self.assertIn("Fix the beta widget spacing bug", draft["focus"])
        self.assertIn("Inspect the CSS grid rules", draft["suggested_approach"])
        self.assertIn("Add a regression test", draft["suggested_approach"])
        self.assertIn("Audit the spacing tokens", draft["suggested_approach"])
        self.assertEqual(sorted(draft["member_ids"]), sorted([m1, m2]))
        self.assertTrue(draft["title"])


class FullFidelityConsolidation(ConsolidateClusterTestCase):
    """Regression coverage for the lossy flat-merge bug: a flat focus/bullets
    merge silently dropped Key artifacts, Open questions, and route info,
    and `_extract_suggested_approach` truncated soft-wrapped bullets at
    their first line. `draft_consolidated_brief` must now preserve a
    member's full body verbatim (via the `members` field), and
    `_extract_suggested_approach`'s standalone bullets must survive a
    line-wrapped continuation too."""

    def _write_raw_brief(self, filename: str, frontmatter_extra: str, body: str) -> str:
        path = self.queue_dir / filename
        content = (
            "---\n"
            f"id: {filename[:-3]}\n"
            'focus: "synthetic fidelity test"\n'
            "status: queued\n"
            f"{frontmatter_extra}"
            "---\n\n"
            f"{body}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path.stem

    def test_key_artifacts_and_open_questions_survive_into_consolidated_body(self):
        m1 = self._write_raw_brief(
            "20260701-100000-alpha.md",
            "recommended-route: F\nchange-kind: bugfix\n",
            "## Focus\n\nFix the alpha widget\n\n"
            "## Key artifacts\n\n"
            "`alpha.py` (`AlphaWidget.render`), `test_alpha.py`\n\n"
            "## Open questions\n\nShould this also cover the legacy widget path?\n",
        )
        m2 = self._write_raw_brief(
            "20260701-100100-beta.md",
            "recommended-route: H\nchange-kind: delta\n",
            "## Focus\n\nFix the beta widget\n\n"
            "## Key artifacts\n\n`beta.py` (`BetaWidget.render`)\n",
        )

        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)
        _, content = cc._build_consolidated_brief_content(draft)

        # Route/kind reach the mechanically-derived summary table.
        self.assertIn("| 1 |", content)
        self.assertIn("| F | bugfix |", content)
        self.assertIn("| H | delta |", content)

        # Full per-member body -- previously dropped entirely by the flat
        # focus/bullets merge -- survives verbatim (nested one level deeper).
        self.assertIn("### Key artifacts", content)
        self.assertIn("`AlphaWidget.render`", content)
        self.assertIn("`BetaWidget.render`", content)
        self.assertIn("### Open questions", content)
        self.assertIn("Should this also cover the legacy widget path?", content)

    def test_wrapped_bullet_continuation_survives(self):
        m1 = self._write_raw_brief(
            "20260701-100000-alpha.md",
            "",
            "## Focus\n\nFix the alpha widget\n\n"
            "## Suggested approach\n\n"
            "1. Inspect the CSS grid rules across every breakpoint\n"
            "   that the responsive layout system defines\n"
            "2. Add a regression test\n",
        )
        m2 = self._write_raw_brief(
            "20260701-100100-beta.md",
            "",
            "## Focus\n\nFix the beta widget\n\n"
            "## Suggested approach\n\n1. Audit the spacing tokens\n",
        )

        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        # draft["suggested_approach"] (the flat, backward-compatible field):
        # the continuation line is merged onto its bullet, not dropped.
        self.assertIn(
            "Inspect the CSS grid rules across every breakpoint that the responsive "
            "layout system defines",
            draft["suggested_approach"],
        )

        # The rendered body embeds each member's original text verbatim, so
        # the continuation line is never even at risk of the extraction
        # bug -- it survives in its original (still valid markdown) form.
        _, content = cc._build_consolidated_brief_content(draft)
        self.assertIn("across every breakpoint", content)
        self.assertIn("that the responsive layout system defines", content)


# --------------------------------------------------------------------------- #
# execute_consolidation()
# --------------------------------------------------------------------------- #


class ExecuteConsolidationDecline(ConsolidateClusterTestCase):
    def test_declined_performs_zero_writes(self):
        """AC-002, AC-004: confirmed=False writes nothing to queue_dir/
        picked_dir and never invokes the work_queue CLI."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        queue_before = sorted(f.name for f in self.queue_dir.iterdir())
        picked_before = sorted(f.name for f in self.picked_dir.iterdir())

        calls: list[list[str]] = []
        real_run = subprocess.run

        def _spy_run(args, *a, **kw):
            calls.append(args)
            return real_run(args, *a, **kw)

        subprocess.run = _spy_run
        try:
            result = cc.execute_consolidation(
                False, [m1, m2], draft, self.queue_dir, self.picked_dir
            )
        finally:
            subprocess.run = real_run

        self.assertEqual(result["status"], "declined")
        self.assertEqual(calls, [])
        self.assertEqual(sorted(f.name for f in self.queue_dir.iterdir()), queue_before)
        self.assertEqual(
            sorted(f.name for f in self.picked_dir.iterdir()), picked_before
        )

    def test_declined_list_json_snapshot_byte_identical(self):
        """AC-004: a work_queue.py list --json snapshot before and after a
        confirmed=False call is byte-for-byte identical."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        before = self._wq_list_json()
        cc.execute_consolidation(
            False, [m1, m2], draft, self.queue_dir, self.picked_dir
        )
        after = self._wq_list_json()

        self.assertEqual(before, after)


class ExecuteConsolidationConfirm(ConsolidateClusterTestCase):
    def test_confirmed_writes_one_brief_and_stamps_superseded(self):
        """AC-003: confirmed=True with N resolvable members results in
        exactly one new brief in queue_dir; each member ends up in picked_dir
        with status: done, carries a ## Superseded note referencing the new
        brief's id, and is not deleted."""
        m1 = _write_brief(
            self.queue_dir,
            "20260701-100000-alpha.md",
            focus="Alpha work",
            suggested_approach=["Do the alpha thing"],
        )
        m2 = _write_brief(
            self.queue_dir,
            "20260701-100100-beta.md",
            focus="Beta work",
            suggested_approach=["Do the beta thing"],
        )
        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        result = cc.execute_consolidation(
            True, [m1, m2], draft, self.queue_dir, self.picked_dir
        )

        self.assertEqual(result["status"], "written")
        self.assertEqual(sorted(result["members_completed"]), sorted([m1, m2]))
        self.assertEqual(result["members_skipped"], [])

        new_brief_files = list(self.queue_dir.glob("*.md"))
        self.assertEqual(len(new_brief_files), 1)
        new_brief_path = new_brief_files[0]
        self.assertEqual(str(new_brief_path), result["new_brief_path"])
        new_fm = self._frontmatter(new_brief_path)
        self.assertEqual(new_fm.get("status"), "queued")
        new_brief_id = new_fm.get("id")
        self.assertIsNotNone(new_brief_id)

        for member_id in (m1, m2):
            picked_path = self.picked_dir / f"{member_id}.md"
            self.assertTrue(
                picked_path.exists(), msg=f"{member_id} missing from picked/"
            )
            fm = self._frontmatter(picked_path)
            self.assertEqual(fm.get("status"), "done")
            body = picked_path.read_text(encoding="utf-8")
            self.assertIn("## Superseded", body)
            self.assertIn(new_brief_id, body)

        # No deletion: each member's file still exists on disk in picked_dir
        # (already asserted above via .exists()).

    def test_race_lost_member_recorded_in_members_skipped(self):
        """AC-005's execute-time half: a member claimed out-of-band
        immediately before execute_consolidation is recorded in
        members_skipped, not members_completed, and the remaining members
        are still processed normally (batch is not aborted)."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        claim_result = self._wq_claim(m1)  # race m1 away before execute
        self.assertEqual(claim_result["status"], "claimed")

        result = cc.execute_consolidation(
            True, [m1, m2], draft, self.queue_dir, self.picked_dir
        )

        self.assertEqual(result["status"], "written")
        self.assertIn(m1, result["members_skipped"])
        self.assertNotIn(m1, result["members_completed"])
        self.assertIn(m2, result["members_completed"])

        m2_picked = self.picked_dir / f"{m2}.md"
        self.assertTrue(m2_picked.exists())
        self.assertEqual(self._frontmatter(m2_picked).get("status"), "done")

    def test_empty_resolvable_ids_no_zero_provenance_brief(self):
        """Edge case: confirmed=True with an empty resolvable_ids list does
        not write a consolidated brief with zero provenance, and returns a
        result distinguishable from a normal 'written' outcome."""
        draft = {
            "title": "Consolidated",
            "focus": "",
            "suggested_approach": [],
            "member_ids": [],
        }

        result = cc.execute_consolidation(
            True, [], draft, self.queue_dir, self.picked_dir
        )

        self.assertEqual(result["members_completed"], [])
        self.assertNotEqual(result["status"], "written")
        self.assertEqual(list(self.queue_dir.glob("*.md")), [])

    def test_new_brief_status_is_queued(self):
        """Edge case: the consolidated brief's status: frontmatter field is
        queued immediately after execute_consolidation writes it (claimable,
        not accidentally already-done/picked)."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        result = cc.execute_consolidation(
            True, [m1, m2], draft, self.queue_dir, self.picked_dir
        )

        new_brief_path = Path(result["new_brief_path"])
        fm = self._frontmatter(new_brief_path)
        self.assertEqual(fm.get("status"), "queued")


# --------------------------------------------------------------------------- #
# Write verification (root-cause bug regression: --draft contract mismatch,
# unescaped focus quotes, slug collisions -- see the linked root-cause brief
# 20260716-171900-consolidate-cluster-draft-arg-and-yaml-escape-bugs)
# --------------------------------------------------------------------------- #


class BuildConsolidatedBriefContent(unittest.TestCase):
    def test_focus_with_embedded_quote_round_trips(self):
        """Bug #2: a focus containing a literal `"` (pulled verbatim from a
        source brief's title) must not break the frontmatter's YAML."""
        draft = {
            "title": 'Consolidated: failed in "Wait for Postgres"',
            "focus": 'failed in "Wait for Postgres" step',
            "suggested_approach": [],
            "member_ids": ["m1", "m2"],
        }
        _, content = cc._build_consolidated_brief_content(draft)

        ok, err = cc.validate_brief_text(content, required=("id", "status", "focus"))
        self.assertTrue(ok, msg=err)
        # Real YAML parsing (not this module's intentionally-naive local
        # `_parse_frontmatter`, which doesn't unescape `\"`) confirms the
        # round-trip a downstream reader like work_queue.py actually sees.
        raw_yaml = content.split("---", 2)[1]
        fm = yaml.safe_load(raw_yaml)
        self.assertEqual(fm["focus"], 'failed in "Wait for Postgres" step')

    def test_different_member_sets_with_same_generic_title_get_different_ids(self):
        """Bug #3: two unrelated clusters whose auto-generated titles both
        slugify to the same generic stem must not collide on brief id."""
        draft_a = {
            "title": "Consolidated",
            "focus": "a",
            "suggested_approach": [],
            "member_ids": ["m1", "m2"],
        }
        draft_b = {
            "title": "Consolidated",
            "focus": "b",
            "suggested_approach": [],
            "member_ids": ["m3", "m4"],
        }

        id_a, _ = cc._build_consolidated_brief_content(draft_a)
        id_b, _ = cc._build_consolidated_brief_content(draft_b)

        self.assertNotEqual(id_a, id_b)

    def test_same_member_set_is_deterministic(self):
        draft = {
            "title": "Consolidated",
            "focus": "a",
            "suggested_approach": [],
            "member_ids": ["m2", "m1"],
        }
        id_1, _ = cc._build_consolidated_brief_content(draft)
        id_2, _ = cc._build_consolidated_brief_content(draft)
        # same second (patched-free here, real clock) -- compare the slug+hash
        # suffix portion only, which is order-independent of member_ids.
        self.assertEqual(id_1.rsplit("-", 1)[-1], id_2.rsplit("-", 1)[-1])


class ExecuteConsolidationValidatesBeforeMutating(ConsolidateClusterTestCase):
    def test_broken_draft_aborts_before_any_member_mutation(self):
        """A draft that would build empty/invalid content (e.g. the
        `--draft` wrapper-vs-bare-dict contract mismatch, unwrapped upstream
        into `{}`) must fail validation and return before claiming or
        marking any member done -- never strand claimed members with
        nothing to supersede them."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        broken_draft: dict = {}  # mirrors the wrapper-unwrap bug's effective payload

        result = cc.execute_consolidation(
            True, [m1, m2], broken_draft, self.queue_dir, self.picked_dir
        )

        self.assertEqual(result["status"], "write-verification-failed")
        self.assertIsNone(result["new_brief_path"])
        self.assertEqual(result["members_completed"], [])
        self.assertEqual(result["members_skipped"], [])
        # neither member was touched: still sitting, unclaimed, in queue_dir/
        self.assertTrue((self.queue_dir / "20260701-100000-alpha.md").exists())
        self.assertTrue((self.queue_dir / "20260701-100100-beta.md").exists())
        self.assertEqual(list(self.picked_dir.glob("*.md")), [])
        # and no broken consolidated brief was written either
        remaining = [f for f in self.queue_dir.glob("*.md")]
        self.assertEqual(len(remaining), 2)  # just the two original members


class ResolveDraftPayload(unittest.TestCase):
    """Bug #1: `execute --draft` must accept either `preview --json`'s full
    wrapper or a bare draft dict -- the natural pipe
    (`preview --json | ... | execute --draft <that output>`) passes the
    whole wrapper, and previously that silently produced an empty brief."""

    def test_accepts_bare_draft_dict(self):
        draft = {
            "title": "t",
            "focus": "f",
            "suggested_approach": [],
            "member_ids": ["m1", "m2"],
        }
        resolved = cc._resolve_draft_payload(json.dumps(draft))
        self.assertEqual(resolved, draft)

    def test_unwraps_full_preview_payload(self):
        draft = {
            "title": "t",
            "focus": "f",
            "suggested_approach": [],
            "member_ids": ["m1", "m2"],
        }
        wrapper = {
            "resolvable_members": ["m1", "m2"],
            "excluded_members": [],
            "withdrawn": False,
            "draft": draft,
        }
        resolved = cc._resolve_draft_payload(json.dumps(wrapper))
        self.assertEqual(resolved, draft)

    def test_withdrawn_wrapper_with_null_draft_raises_clear_error(self):
        wrapper = {
            "resolvable_members": [],
            "excluded_members": ["m1"],
            "withdrawn": True,
            "draft": None,
        }
        with self.assertRaises(ValueError) as ctx:
            cc._resolve_draft_payload(json.dumps(wrapper))
        self.assertIn("member_ids", str(ctx.exception))

    def test_invalid_json_raises_clear_error(self):
        with self.assertRaises(ValueError):
            cc._resolve_draft_payload("{not json")

    def test_non_object_json_raises_clear_error(self):
        with self.assertRaises(ValueError):
            cc._resolve_draft_payload("[1, 2, 3]")

    def test_empty_member_ids_raises_clear_error(self):
        draft = {"title": "t", "focus": "f", "suggested_approach": [], "member_ids": []}
        with self.assertRaises(ValueError):
            cc._resolve_draft_payload(json.dumps(draft))


class MainCliDraftContract(ConsolidateClusterTestCase):
    def test_execute_with_full_preview_payload_writes_real_brief(self):
        """End-to-end regression for the documented SKILL.md usage
        (`execute <members...> --draft '<preview JSON>' --confirm`), which
        passes the FULL preview payload, not the bare inner draft."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        preview = cc.build_preview([m1, m2], self.queue_dir)
        self.assertFalse(preview["withdrawn"])

        code = cc.main(
            [
                "execute",
                m1,
                m2,
                "--draft",
                json.dumps(preview),
                "--confirm",
                "--json",
                "--queue-dir",
                str(self.queue_dir),
                "--picked-dir",
                str(self.picked_dir),
            ]
        )

        self.assertEqual(code, 0)
        new_brief_files = [
            f for f in self.queue_dir.glob("*.md") if f.stem not in (m1, m2)
        ]
        self.assertEqual(len(new_brief_files), 1)
        fm = self._frontmatter(new_brief_files[0])
        self.assertTrue(fm.get("focus"))

    def test_execute_with_withdrawn_draft_exits_nonzero(self):
        code = cc.main(
            [
                "execute",
                "m1",
                "--draft",
                json.dumps(
                    {
                        "resolvable_members": [],
                        "excluded_members": ["m1"],
                        "withdrawn": True,
                        "draft": None,
                    }
                ),
                "--confirm",
                "--json",
                "--queue-dir",
                str(self.queue_dir),
                "--picked-dir",
                str(self.picked_dir),
            ]
        )
        self.assertEqual(code, 1)


# --------------------------------------------------------------------------- #
# Cluster-3 recovery bugs (brief 20260826-144144-consolidate-cluster-brief-
# filename-and): unbounded slug length, argv MAX_ARG_STRLEN on --draft, and
# the nested-consolidation-batch closure evidence gate.
# --------------------------------------------------------------------------- #


class SlugLengthCap(unittest.TestCase):
    def test_long_title_produces_filesystem_safe_filename(self):
        """A cluster whose first member's title is a long focus sentence
        must not produce a filename over the 255-byte filesystem limit --
        `write_text()` previously raised `OSError: [Errno 36] File name too
        long` after the member claim/done loop had already run."""
        long_title = "Consolidated: " + ("word " * 60)
        draft = {
            "title": long_title,
            "focus": "x",
            "suggested_approach": [],
            "member_ids": ["m1", "m2"],
        }
        new_id, _content = cc._build_consolidated_brief_content(draft)
        self.assertLessEqual(len((new_id + ".md").encode("utf-8")), 255)

    def test_slug_never_ends_in_a_hyphen_after_truncation(self):
        long_title = "a-" * 200  # truncation boundary lands mid-hyphen-run
        self.assertFalse(cc._slugify(long_title).endswith("-"))


class ExecuteDraftFileAlternative(ConsolidateClusterTestCase):
    def test_draft_file_produces_same_result_as_draft_argv(self):
        """`--draft-file` is a drop-in alternative to `--draft`: reading the
        identical payload from a file writes the same consolidated brief."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = _write_brief(self.queue_dir, "20260701-100100-beta.md", focus="Beta work")
        preview = cc.build_preview([m1, m2], self.queue_dir)
        draft_file = self.tmp_path / "draft.json"
        draft_file.write_text(json.dumps(preview), encoding="utf-8")

        code = cc.main(
            [
                "execute",
                m1,
                m2,
                "--draft-file",
                str(draft_file),
                "--confirm",
                "--json",
                "--queue-dir",
                str(self.queue_dir),
                "--picked-dir",
                str(self.picked_dir),
            ]
        )

        self.assertEqual(code, 0)
        new_brief_files = [
            f for f in self.queue_dir.glob("*.md") if f.stem not in (m1, m2)
        ]
        self.assertEqual(len(new_brief_files), 1)

    def test_oversized_draft_would_overflow_argv_but_draft_file_succeeds(self):
        """Regression for the exact failure mode: a draft payload larger
        than the kernel's MAX_ARG_STRLEN (~128KB) fails `--draft` at
        exec() with `OSError: Argument list too long`; `--draft-file`
        reads the same payload from disk instead."""
        big_focus = "x" * 140_000
        big_draft = {
            "draft": {
                "title": "Consolidated: big",
                "focus": big_focus,
                "suggested_approach": [],
                "member_ids": ["m1"],
            }
        }
        payload = json.dumps(big_draft)
        self.assertGreater(len(payload.encode("utf-8")), 128_000)

        draft_file = self.tmp_path / "big_draft.json"
        draft_file.write_text(payload, encoding="utf-8")

        code = cc.main(
            [
                "execute",
                "m1",
                "--draft-file",
                str(draft_file),
                "--decline",
                "--json",
                "--queue-dir",
                str(self.queue_dir),
                "--picked-dir",
                str(self.picked_dir),
            ]
        )
        self.assertEqual(code, 0)

    def test_draft_file_missing_reports_invalid_draft(self):
        code = cc.main(
            [
                "execute",
                "m1",
                "--draft-file",
                str(self.tmp_path / "does-not-exist.json"),
                "--decline",
                "--json",
                "--queue-dir",
                str(self.queue_dir),
                "--picked-dir",
                str(self.picked_dir),
            ]
        )
        self.assertEqual(code, 1)

    def test_draft_and_draft_file_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            cc.main(
                [
                    "execute",
                    "m1",
                    "--draft",
                    "{}",
                    "--draft-file",
                    str(self.tmp_path / "x.json"),
                    "--decline",
                ]
            )


class NestedConsolidationClosureEvidence(ConsolidateClusterTestCase):
    def _write_nested_batch_member(
        self, filename: str, *, focus: str, sub_ids: list[str]
    ) -> str:
        """Write a member brief that is itself a consolidation-batch brief
        (carries its own '## Consolidated from' section), simulating a
        cluster re-consolidating an already-consolidated brief."""
        lines = [
            "---",
            f"id: {filename[:-3]}",
            f'focus: "{focus}"',
            "status: queued",
            "---",
            "",
        ]
        lines.append("## Focus")
        lines.append("")
        lines.append(focus)
        lines.append("")
        lines.append("## Consolidated from")
        lines.append("")
        lines.extend(f"- {sub_id}" for sub_id in sub_ids)
        lines.append("")
        path = self.queue_dir / filename
        path.write_text("\n".join(lines), encoding="utf-8")
        return path.stem

    def test_nested_consolidation_member_closes_instead_of_sticking_at_picked(self):
        """A member that is itself a nested consolidation-batch brief must
        close (status: done) like its siblings, not get stuck at
        status: picked on work_queue.py's unverified_consolidation_closure
        gate."""
        nested = self._write_nested_batch_member(
            "20260801-000000-nested.md",
            focus="nested batch",
            sub_ids=["20260701-100000-sub-a", "20260701-100100-sub-b"],
        )
        plain = _write_brief(self.queue_dir, "20260801-000100-plain.md", focus="plain")
        draft = cc.draft_consolidated_brief([nested, plain], self.queue_dir)

        result = cc.execute_consolidation(
            True, [nested, plain], draft, self.queue_dir, self.picked_dir
        )

        self.assertEqual(result["status"], "written")
        self.assertEqual(sorted(result["members_completed"]), sorted([nested, plain]))
        self.assertEqual(result["members_skipped"], [])

        nested_fm = self._frontmatter(self.picked_dir / f"{nested}.md")
        self.assertEqual(nested_fm.get("status"), "done")

    def test_ordinary_member_gets_no_closure_note(self):
        """An ordinary (non-nested-batch) member is marked done with no
        `--note` at all -- `_build_nested_consolidation_note` must not
        fire for the common case."""
        plain = _write_brief(self.queue_dir, "20260801-000200-plain.md", focus="plain")
        self.assertIsNone(
            cc._build_nested_consolidation_note(
                (self.queue_dir / f"{plain}.md").read_text(encoding="utf-8")
            )
        )


# --------------------------------------------------------------------------- #
# original_created propagation (stale-brief-precheck-consolidation-original-
# created): the consolidated draft's original_created tracks the earliest
# resolvable member's created:/original-created: timestamp, and that value
# round-trips into the written brief's original-created: frontmatter line.
# --------------------------------------------------------------------------- #


class OriginalCreatedPropagation(ConsolidateClusterTestCase):
    def _write_created_brief(
        self, filename: str, *, focus: str, created: str, quoted: bool = True
    ) -> str:
        """Write a brief whose `created:` line is either a quoted string
        (parses as `str`) or bare (parses as PyYAML's native `datetime`)."""
        created_line = f'created: "{created}"' if quoted else f"created: {created}"
        path = self.queue_dir / filename
        path.write_text(
            "---\n"
            f"id: {filename[:-3]}\n"
            f'focus: "{focus}"\n'
            f"{created_line}\n"
            "status: queued\n"
            "---\n\n"
            f"## Focus\n\n{focus}\n",
            encoding="utf-8",
        )
        return path.stem

    def test_draft_original_created_is_earliest_member_timestamp(self):
        """A multi-member draft whose members have different created:
        timestamps yields original_created equal to the earliest one."""
        m1 = self._write_created_brief(
            "20260701-100000-alpha.md",
            focus="Alpha work",
            created="2026-07-05T10:00:00Z",
        )
        m2 = self._write_created_brief(
            "20260701-100100-beta.md", focus="Beta work", created="2026-07-01T09:00:00Z"
        )
        m3 = self._write_created_brief(
            "20260701-100200-gamma.md",
            focus="Gamma work",
            created="2026-07-10T12:00:00Z",
        )

        draft = cc.draft_consolidated_brief([m1, m2, m3], self.queue_dir)

        self.assertEqual(
            draft["original_created"],
            datetime.datetime(
                2026, 7, 1, 9, 0, 0, tzinfo=datetime.timezone.utc
            ).isoformat(),
        )

    def test_native_pyyaml_datetime_created_contributes_correctly(self):
        """A member whose created: value is an unquoted (bare) ISO timestamp
        -- parsed by PyYAML natively as a datetime.datetime, not a str --
        still contributes to the earliest-timestamp computation."""
        m1 = self._write_created_brief(
            "20260701-100000-alpha.md",
            focus="Alpha work",
            created="2026-07-01T09:00:00",
            quoted=False,
        )
        m2 = self._write_created_brief(
            "20260701-100100-beta.md", focus="Beta work", created="2026-07-05T10:00:00Z"
        )

        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        self.assertEqual(
            draft["original_created"],
            datetime.datetime(
                2026, 7, 1, 9, 0, 0, tzinfo=datetime.timezone.utc
            ).isoformat(),
        )

    def test_missing_or_unparseable_created_is_skipped_without_raising(self):
        """A member with a missing created: field, and a member with an
        unparseable created: value, are both skipped -- the surviving
        member's timestamp still wins, and no exception propagates."""
        m1 = _write_brief(
            self.queue_dir, "20260701-100000-alpha.md", focus="Alpha work"
        )
        m2 = self._write_created_brief(
            "20260701-100100-beta.md", focus="Beta work", created="not-a-real-timestamp"
        )
        m3 = self._write_created_brief(
            "20260701-100200-gamma.md",
            focus="Gamma work",
            created="2026-07-03T08:00:00Z",
        )

        draft = cc.draft_consolidated_brief([m1, m2, m3], self.queue_dir)

        self.assertEqual(sorted(draft["member_ids"]), sorted([m1, m2, m3]))
        self.assertEqual(
            draft["original_created"],
            datetime.datetime(
                2026, 7, 3, 8, 0, 0, tzinfo=datetime.timezone.utc
            ).isoformat(),
        )

    def test_written_brief_carries_original_created_when_draft_has_it(self):
        m1 = self._write_created_brief(
            "20260701-100000-alpha.md",
            focus="Alpha work",
            created="2026-07-01T09:00:00Z",
        )
        m2 = self._write_created_brief(
            "20260701-100100-beta.md", focus="Beta work", created="2026-07-05T10:00:00Z"
        )
        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)
        self.assertIn("original_created", draft)

        _, content = cc._build_consolidated_brief_content(draft)

        self.assertIn(f"original-created: '{draft['original_created']}'", content)
        raw_yaml = content.split("---", 2)[1]
        fm = yaml.safe_load(raw_yaml)
        # Written through the shared canonical serializer, which single-quotes
        # an ISO timestamp string (same as `created:`) rather than leaving it
        # bare -- it round-trips as `str`, not a native datetime, but
        # `_member_created_at()`'s `isinstance(value, str)` branch already
        # handles that (it must, for create_handoff.py's own quoted `created:`).
        self.assertEqual(fm["original-created"], draft["original_created"])

    def test_written_brief_omits_original_created_when_draft_lacks_it(self):
        """Unchanged from current behavior: no member's created: parsed (or
        no members at all), so the field is omitted entirely."""
        draft = {
            "title": "Consolidated",
            "focus": "no timestamps here",
            "suggested_approach": [],
            "member_ids": ["m1", "m2"],
        }
        self.assertNotIn("original_created", draft)

        _, content = cc._build_consolidated_brief_content(draft)

        raw_yaml = content.split("---", 2)[1]
        fm = yaml.safe_load(raw_yaml)
        self.assertNotIn("original-created", fm)
        self.assertNotIn("original-created:", content)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# Block-scalar / folded-scalar focus parsing (live incident 2026-08-13: five
# consolidated briefs shipped with focus: "|- / |-" because the old
# line-oriented _parse_frontmatter read YAML style markers as values)
# --------------------------------------------------------------------------- #


class BlockScalarFocusParsing(ConsolidateClusterTestCase):
    BLOCK_FOCUS_1 = (
        "Verify bridge-health-guard's queue_growth trend "
        "reverses: watch history for ~5 days"
    )
    BLOCK_FOCUS_2 = (
        "Add a fail-closed datalena PR target guard: reject shared-branch pushes"
    )

    def _write_block_scalar_member(self, filename: str, focus: str) -> str:
        """Exactly the shape create_handoff emits for any focus containing
        ': ' or an apostrophe: a literal block scalar."""
        indented = "\n".join(f"  {line}" for line in focus.split("\n"))
        path = self.queue_dir / filename
        path.write_text(
            "---\n"
            f"id: {filename[:-3]}\n"
            f"focus: |-\n{indented}\n"
            "status: queued\n"
            "---\n\n"
            f"## Focus\n\n{focus}\n",
            encoding="utf-8",
        )
        return filename[:-3]

    def test_parse_frontmatter_reads_block_scalar_value(self):
        self._write_block_scalar_member("20260813-100000-m1.md", self.BLOCK_FOCUS_1)
        fm = cc._parse_frontmatter(
            (self.queue_dir / "20260813-100000-m1.md").read_text(encoding="utf-8")
        )
        self.assertEqual(fm["focus"], self.BLOCK_FOCUS_1)

    def test_parse_frontmatter_reads_folded_quoted_scalar(self):
        """The old writer's own output: yaml.safe_dump default width folds a
        long double-quoted focus across continuation lines."""
        long_focus = "Reconcile ROOT_ORGANIZATION_ID consumers " * 8
        folded = yaml.safe_dump(long_focus.strip(), default_style='"').strip()
        self.assertIn("\n", folded)  # the fixture must actually fold
        path = self.queue_dir / "20260813-100001-m2.md"
        path.write_text(
            f"---\nid: m2\nfocus: {folded}\nstatus: queued\n---\n\nbody\n",
            encoding="utf-8",
        )
        fm = cc._parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fm["focus"], long_focus.strip())

    def test_draft_from_block_scalar_members_has_no_style_markers(self):
        m1 = self._write_block_scalar_member(
            "20260813-100002-m1.md", self.BLOCK_FOCUS_1
        )
        m2 = self._write_block_scalar_member(
            "20260813-100003-m2.md", self.BLOCK_FOCUS_2
        )

        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)

        self.assertEqual(draft["focus"], f"{self.BLOCK_FOCUS_1} / {self.BLOCK_FOCUS_2}")
        self.assertEqual(draft["title"], f"Consolidated: {self.BLOCK_FOCUS_1}")
        self.assertNotIn("|-", draft["focus"])
        for member in draft["members"]:
            self.assertNotIn("|-", member["focus"])

        _, content = cc._build_consolidated_brief_content(draft)
        self.assertNotIn("|- / |-", content)
        raw_yaml = content.split("---", 2)[1]
        fm = yaml.safe_load(raw_yaml)
        self.assertEqual(fm["focus"], draft["focus"])

    def test_multiline_block_focus_collapses_to_one_line_in_draft(self):
        multiline = "First line of the focus\ncontinues on a second line"
        m1 = self._write_block_scalar_member("20260813-100004-m1.md", multiline)
        m2 = self._write_block_scalar_member(
            "20260813-100005-m2.md", self.BLOCK_FOCUS_2
        )

        draft = cc.draft_consolidated_brief([m1, m2], self.queue_dir)
        self.assertIn(
            "First line of the focus continues on a second line", draft["focus"]
        )
        self.assertNotIn("\n", draft["focus"])

    def test_written_focus_frontmatter_never_folds(self):
        """A very long joined focus must stay a single frontmatter line —
        folded continuations are exactly what line-oriented readers truncate."""
        draft = {
            "title": "Consolidated: long",
            "focus": "Reconcile every remaining consumer " * 20,
            "suggested_approach": [],
            "member_ids": ["m1", "m2"],
        }
        _, content = cc._build_consolidated_brief_content(draft)
        raw_yaml = content.split("---", 2)[1]
        focus_lines = [ln for ln in raw_yaml.splitlines() if ln.startswith("focus:")]
        self.assertEqual(len(focus_lines), 1)
        # The canonical serializer strips insignificant trailing whitespace
        # (PyYAML cannot represent it unambiguously in `|` block style), so
        # compare against the stripped form, not the raw draft input.
        self.assertEqual(yaml.safe_load(raw_yaml)["focus"], draft["focus"].strip())
        # Never a folded double-quoted continuation (`\<newline>  \ `) --
        # canonical style renders `focus:` as a `|-` block scalar, whose
        # content is one contiguous indented line rather than backslash-
        # folded fragments.
        self.assertNotIn("\\\n", raw_yaml)
        self.assertIn("\n  " + draft["focus"].strip() + "\n", raw_yaml)
