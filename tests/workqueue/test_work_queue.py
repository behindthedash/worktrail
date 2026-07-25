#!/usr/bin/env python3
"""Tests for work_queue.py -- the work-queue lifecycle owner.

Run: python3 -m pytest tests/workqueue/test_work_queue.py -q
"""

from __future__ import annotations

import datetime
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from worktrail.workqueue import work_queue as q


def _brief(
    focus: str,
    brief_id: str = "",
    extra: str = "",
    blocked_by: Optional[list] = None,
    related: Optional[list] = None,
) -> str:
    fm = [f"focus: {focus}", "status: queued"]
    if brief_id:
        fm.append(f"id: {brief_id}")
    if extra:
        fm.append(extra)
    if blocked_by:
        fm.append("blocked-by:")
        for dep in blocked_by:
            fm.append(f"  - {dep}")
    if related:
        fm.append("related:")
        for rel in related:
            fm.append(f"  - {rel}")
    return "---\n" + "\n".join(fm) + "\n---\n\n## Focus\n\n" + focus + "\n"


def _picked_brief(focus: str, status: str = "picked") -> str:
    fm = [f"focus: {focus}", f"status: {status}"]
    return "---\n" + "\n".join(fm) + "\n---\n\n## Focus\n\n" + focus + "\n"


class QueueTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        os.environ["WORK_QUEUE_DIR"] = str(self.base)
        importlib.reload(q)  # pick up the env var
        self.queue = self.base / "queue"
        self.picked = self.base / "picked"
        self.queue.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def write(self, name: str, **kw):
        p = self.queue / name
        p.write_text(_brief(**kw), encoding="utf-8")
        return p


class TestLocation(QueueTestBase):
    def test_base_dir_honors_env(self):
        self.assertEqual(q.base_dir(), self.base)
        self.assertEqual(q.queue_dir(), self.base / "queue")
        self.assertEqual(q.picked_dir(), self.base / "picked")

    def test_default_is_home_work_queue(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        importlib.reload(q)
        self.assertEqual(q.base_dir(), Path("~/work-queue").expanduser())


class TestList(QueueTestBase):
    def test_empty_and_missing(self):
        self.assertEqual(q.list_queue()["briefs"], [])
        # missing dir
        for f in self.queue.iterdir():
            f.unlink()
        self.queue.rmdir()
        self.assertEqual(q.list_queue()["briefs"], [])

    def test_focus_surfaced(self):
        self.write("20260101-000000-a.md", focus="fix the thing")
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["focus"], "fix the thing")

    def test_related_field_present(self):
        """Brief with related in frontmatter surfaces it in list_queue() (AC-004)."""
        self.write("20260101-000000-a.md", focus="some work", related=["id-a", "id-b"])
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["related"], ["id-a", "id-b"])

    def test_related_field_absent(self):
        """Brief without related field has related: [] in list_queue() (AC-004)."""
        self.write("20260101-000000-a.md", focus="some work")
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["related"], [])

    def test_related_stale_id_returned_verbatim(self):
        """Stale related IDs are returned verbatim without error (AC-005)."""
        self.write("20260101-000000-a.md", focus="some work", related=["stale-xyz-999"])
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["related"], ["stale-xyz-999"])
        self.assertFalse(briefs[0]["blocked"])

    def test_related_does_not_affect_blocked_no_blocked_by(self):
        """related set but no blocked-by -> blocked: false (AC-006)."""
        self.write("20260101-000000-a.md", focus="some work", related=["some-id"])
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["blocked"])

    def test_related_does_not_affect_blocked_with_blocked_by_in_queue(self):
        """related + blocked-by dep in queue -> blocked: true; blocked-by drives it (AC-006)."""
        self.write("20260101-000000-dep.md", focus="the dep", brief_id="dep-id")
        self.write(
            "20260101-000001-main.md", focus="needs dep", related=["some-id"], blocked_by=["dep-id"]
        )
        briefs = {b["filename"]: b for b in q.list_queue()["briefs"]}
        self.assertTrue(briefs["20260101-000001-main.md"]["blocked"])
        self.assertEqual(briefs["20260101-000001-main.md"]["related"], ["some-id"])

    def test_related_malformed_scalar_handled_leniently(self):
        """Brief with scalar (malformed) related field is handled without error (AC-005)."""
        p = self.queue / "20260101-000000-a.md"
        p.write_text(
            "---\nfocus: some work\nstatus: queued\nrelated: not-a-list\n---\n\n## Focus\n\nsome work\n",
            encoding="utf-8",
        )
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["related"], [])

    def test_frontmatter_without_trailing_newline_still_parses(self):
        p = self.queue / "20260101-000000-a.md"
        p.write_text("---\nfocus: some work\nstatus: queued\nrelated:\n  - id-a\n---", encoding="utf-8")
        briefs = q.list_queue()["briefs"]
        self.assertEqual(briefs[0]["focus"], "some work")
        self.assertEqual(briefs[0]["related"], ["id-a"])


class TestResolve(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.write("20260531-141200-auth.md", focus="auth", brief_id="auth-id-1")

    def test_full_stem_prefix_id(self):
        for ident in (
            "20260531-141200-auth.md",
            "20260531-141200-auth",
            "20260531-141200",
            "auth-id-1",
        ):
            self.assertEqual(q.resolve(ident, self.queue)["status"], "match", ident)

    def test_none_and_ambiguous(self):
        self.assertEqual(q.resolve("nope", self.queue)["status"], "none")
        self.write("20260531-999999-other.md", focus="other")
        self.assertEqual(q.resolve("2026", self.queue)["status"], "ambiguous")

    def test_slug_suffix_single_match(self):
        """Slug without its date-time prefix resolves via stem suffix match."""
        self.write("20260604-113700-handoff-related-field-autodetect.md", focus="autodetect")
        res = q.resolve("handoff-related-field-autodetect", self.queue)
        self.assertEqual(res["status"], "match")
        self.assertIn("handoff-related-field-autodetect.md", res["candidates"][0])

    def test_slug_suffix_ambiguous(self):
        """Two files whose stems both end with the same slug → ambiguous (not none)."""
        self.write("20260604-100000-handoff-related-field-autodetect.md", focus="a")
        self.write("20260604-110000-handoff-related-field-autodetect.md", focus="b")
        res = q.resolve("handoff-related-field-autodetect", self.queue)
        self.assertEqual(res["status"], "ambiguous")

    def test_prefix_takes_precedence_over_suffix(self):
        """A prefix match is used; a file that only matches by suffix is not included."""
        # existing setUp file "20260531-141200-auth.md" matches "20260531" by prefix
        # a new file whose stem ENDS WITH "20260531" would also match by suffix,
        # but the prefix pass found a hit so the suffix pass must not run
        self.write("20260601-000000-test-20260531.md", focus="suffix only")
        res = q.resolve("20260531", self.queue)
        self.assertEqual(res["status"], "match")
        self.assertIn("20260531-141200-auth.md", res["candidates"][0])

    def test_existing_forms_regression_with_suffix_candidate(self):
        """Adding a suffix-matchable file does not break the four original match forms."""
        self.write("20260604-113700-handoff-related-field-autodetect.md", focus="slug")
        for ident in (
            "20260531-141200-auth.md",
            "20260531-141200-auth",
            "20260531-141200",
            "auth-id-1",
        ):
            res = q.resolve(ident, self.queue)
            self.assertEqual(res["status"], "match", f"regression: {ident!r} should still match")

    def test_empty_identifier_no_crash(self):
        """Empty string identifier does not raise an exception."""
        try:
            res = q.resolve("", self.queue)
            self.assertIn(res["status"], ("match", "ambiguous", "none"))
        except Exception as exc:
            self.fail(f"resolve('') raised {exc!r}")

    def test_middle_substring_no_match_via_suffix(self):
        """A slug that appears in the middle of a stem (not as suffix) does not match."""
        self.write("20260604-113700-foo-middle-bar.md", focus="test")
        res = q.resolve("middle", self.queue)
        self.assertEqual(res["status"], "none")


class TestClaim(QueueTestBase):
    def test_claim_moves_and_stamps(self):
        self.write("20260531-141200-auth.md", focus="auth")
        res = q.claim("20260531-141200-auth", by="agent-x")
        self.assertEqual(res["status"], "claimed")
        moved = self.picked / "20260531-141200-auth.md"
        self.assertTrue(moved.exists())
        self.assertFalse((self.queue / "20260531-141200-auth.md").exists())
        fm = q._read_frontmatter(moved)
        self.assertEqual(fm["status"], "picked")
        self.assertEqual(fm["claimed-by"], "agent-x")
        self.assertIn("claimed-at", fm)

    def test_claim_none_and_ambiguous(self):
        self.assertEqual(q.claim("missing")["status"], "none")
        self.write("a-1.md", focus="a")
        self.write("a-2.md", focus="b")
        self.assertEqual(q.claim("a-")["status"], "ambiguous")

    def test_second_claim_is_already_claimed(self):
        """The atomic-rename arbiter: once claimed, a second claim cannot win."""
        self.write("20260531-141200-auth.md", focus="auth")
        first = q.claim("20260531-141200-auth")
        self.assertEqual(first["status"], "claimed")
        # the brief now lives in picked/, so a second attempt reports the normal
        # already-claimed condition (not "none", which would surface a red error).
        second = q.claim("20260531-141200-auth")
        self.assertEqual(second["status"], "already-claimed")
        self.assertEqual(second["path"], str(self.picked / "20260531-141200-auth.md"))

    def test_dst_already_present_is_already_claimed(self):
        self.write("dup.md", focus="x")
        self.picked.mkdir(parents=True, exist_ok=True)
        (self.picked / "dup.md").write_text(_brief("already"), encoding="utf-8")
        self.assertEqual(q.claim("dup.md")["status"], "already-claimed")

    def test_claim_of_picked_only_brief_is_already_claimed(self):
        """A brief that exists only in picked/ (never in queue/ for this session)
        resolves to already-claimed, not none -- the /sdd-workflow conductor calls claim on
        briefs a prior session already moved, and must not see a red 'none' error."""
        self.picked.mkdir(parents=True, exist_ok=True)
        (self.picked / "20260603-201545-prior.md").write_text(
            _brief("prior work", brief_id="20260603-201545-prior"), encoding="utf-8"
        )
        res = q.claim("20260603-201545-prior")
        self.assertEqual(res["status"], "already-claimed")
        self.assertEqual(res["path"], str(self.picked / "20260603-201545-prior.md"))


class TestDoneRelease(QueueTestBase):
    def test_done_stamps_in_picked(self):
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        res = q.done("20260531-141200-auth")
        self.assertEqual(res["status"], "done")
        moved = self.picked / "20260531-141200-auth.md"
        self.assertTrue(moved.exists())  # stays in picked/
        fm = q._read_frontmatter(moved)
        self.assertEqual(fm["status"], "done")
        self.assertIn("completed-at", fm)

    def _write_route_c_picked(self, name="20260531-141200-feature.md"):
        self.picked.mkdir(parents=True, exist_ok=True)
        path = self.picked / name
        path.write_text(
            _picked_brief("add the feature").replace(
                "status: picked", "status: picked\nrecommended-route: C"
            ),
            encoding="utf-8",
        )
        return path

    def test_route_c_done_requires_explicit_transition(self):
        path = self._write_route_c_picked()
        res = q.done("20260531-141200-feature")
        self.assertEqual(res["status"], "awaiting_implementation_decision")
        self.assertEqual(q._read_frontmatter(path)["status"], "picked")

    def test_route_c_can_explicitly_stop_planning_only(self):
        path = self._write_route_c_picked()
        res = q.done("20260531-141200-feature", planning_only=True)
        self.assertEqual(res["status"], "done")
        self.assertEqual(q._read_frontmatter(path)["status"], "done")

    def test_route_c_can_complete_after_implementation(self):
        path = self._write_route_c_picked()
        res = q.done("20260531-141200-feature", implementation_complete=True)
        self.assertEqual(res["status"], "done")
        self.assertEqual(q._read_frontmatter(path)["status"], "done")

    def test_release_returns_to_queue(self):
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        res = q.release("20260531-141200-auth")
        self.assertEqual(res["status"], "released")
        back = self.queue / "20260531-141200-auth.md"
        self.assertTrue(back.exists())
        self.assertEqual(q._read_frontmatter(back)["status"], "queued")

    def test_release_with_next_check_after_writes_field(self):
        """release --next-check-after writes the frontmatter date (AC-006)."""
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        res = q.release("20260531-141200-auth", next_check_after="2099-01-01")
        self.assertEqual(res["status"], "released")
        fm = q._read_frontmatter(self.queue / "20260531-141200-auth.md")
        # YAML auto-parses an unquoted ISO date into a date object -- compare via str(),
        # matching how the rest of this codebase handles frontmatter date fields (e.g.
        # dashboard.py's _hours_since_claim casts `claimed_at` the same way).
        self.assertEqual(str(fm["next-check-after"]), "2099-01-01")

    def test_release_without_flag_leaves_field_untouched(self):
        """release with no --next-check-after neither adds nor removes the field (AC-007)."""
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        res = q.release("20260531-141200-auth")
        self.assertEqual(res["status"], "released")
        fm = q._read_frontmatter(self.queue / "20260531-141200-auth.md")
        self.assertNotIn("next-check-after", fm)


class TestDoneNote(QueueTestBase):
    """Tests for the `--note` / `note=` closure-note flag added to `done()` (TASK-003)."""

    def test_done_with_note_stamps_and_appends_closure_note(self):
        """implementation_complete + note stamps status/completed-at and appends a
        ## Closure Note section containing the exact note text (AC-012)."""
        self.picked.mkdir(parents=True, exist_ok=True)
        path = self.picked / "20260531-141200-feature.md"
        path.write_text(_picked_brief("add the feature"), encoding="utf-8")

        res = q.done(
            "20260531-141200-feature",
            implementation_complete=True,
            note="Superseded by spec-042",
        )

        self.assertEqual(res["status"], "done")
        self.assertTrue(path.exists())
        fm = q._read_frontmatter(path)
        self.assertEqual(fm["status"], "done")
        self.assertIn("completed-at", fm)
        body = path.read_text(encoding="utf-8")
        self.assertIn("## Closure Note", body)
        self.assertIn("Superseded by spec-042", body)

    def test_done_closure_note_survives_disk_reread(self):
        """The appended ## Closure Note section is present when the brief is
        re-read from disk after the call returns, not only in the function's
        return value (AC-013)."""
        self.picked.mkdir(parents=True, exist_ok=True)
        path = self.picked / "20260531-141200-feature.md"
        path.write_text(_picked_brief("add the feature"), encoding="utf-8")

        q.done(
            "20260531-141200-feature",
            implementation_complete=True,
            note="Superseded by spec-042",
        )

        # Independent read from disk, not the buffer done() wrote from.
        reread = Path(str(path)).read_text(encoding="utf-8")
        self.assertIn("## Closure Note", reread)
        self.assertIn("Superseded by spec-042", reread)

    def test_done_with_note_no_completion_mode_on_non_route_c_brief(self):
        """note= with no completion-mode flag, on a brief whose recommended-route
        is not C (absent here), stamps status/completed-at exactly like today's
        done() plus the note section (AC-014)."""
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        moved = self.picked / "20260531-141200-auth.md"

        res = q.done("20260531-141200-auth", note="fixed via PR #123")

        self.assertEqual(res["status"], "done")
        fm = q._read_frontmatter(moved)
        self.assertEqual(fm["status"], "done")
        self.assertIn("completed-at", fm)
        body = moved.read_text(encoding="utf-8")
        self.assertIn("## Closure Note", body)
        self.assertIn("fixed via PR #123", body)

    def test_done_without_note_argument_leaves_no_closure_note(self):
        """Omitting note entirely changes nothing about existing done() behavior --
        no ## Closure Note section appears (AC-014)."""
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        moved = self.picked / "20260531-141200-auth.md"

        res = q.done("20260531-141200-auth")

        self.assertEqual(res["status"], "done")
        fm = q._read_frontmatter(moved)
        self.assertEqual(fm["status"], "done")
        self.assertIn("completed-at", fm)
        body = moved.read_text(encoding="utf-8")
        self.assertNotIn("## Closure Note", body)

    def test_done_with_note_rolls_back_completely_on_validation_failure(self):
        """note provided but validate_brief fails post-write (reusing the
        pre-broken-brief fixture) -- the file is rolled back to its exact
        original content, with no partial ## Closure Note section left behind."""
        self.picked.mkdir(parents=True, exist_ok=True)
        p = self.picked / "20260701-000000-broken.md"
        p.write_text(TestWriteVerification._INVALID_YAML, encoding="utf-8")

        res = q.done("20260701-000000-broken", note="should not survive")

        self.assertEqual(res["status"], "write-verification-failed")
        self.assertTrue(p.exists())
        content = p.read_text(encoding="utf-8")
        self.assertEqual(content, TestWriteVerification._INVALID_YAML)
        self.assertNotIn("## Closure Note", content)

    def test_done_with_empty_string_note_appends_nothing(self):
        """--note "" (empty string) is treated as no note -- no ## Closure Note
        section is appended."""
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        moved = self.picked / "20260531-141200-auth.md"

        res = q.done("20260531-141200-auth", note="")

        self.assertEqual(res["status"], "done")
        body = moved.read_text(encoding="utf-8")
        self.assertNotIn("## Closure Note", body)

    def test_done_note_cli_round_trip_matches_direct_call(self):
        """`done <id> --implementation-complete --note "..."` via main() produces
        the same on-disk result as calling done() directly with equivalent
        kwargs (CLI round-trip)."""
        # Pin the clock across both branches (claim + done) so claimed-at/
        # completed-at can't drift a second apart between the two calls and
        # make an otherwise-identical comparison flaky.
        with patch.object(q, "_now_iso", return_value="2026-05-31T14:20:00+00:00"):
            self.write("20260531-141200-cli.md", focus="cli path")
            q.claim("20260531-141200-cli")
            self.write("20260531-141200-direct.md", focus="cli path")
            q.claim("20260531-141200-direct")

            out = io.StringIO()
            with patch("sys.stdout", out):
                code = q.main(
                    [
                        "done",
                        "20260531-141200-cli",
                        "--implementation-complete",
                        "--note",
                        "closed via CLI",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            q.done(
                "20260531-141200-direct",
                implementation_complete=True,
                note="closed via CLI",
            )

        cli_content = (self.picked / "20260531-141200-cli.md").read_text(encoding="utf-8")
        direct_content = (self.picked / "20260531-141200-direct.md").read_text(encoding="utf-8")
        # filenames differ but are not embedded in either file's content, so a
        # byte-for-byte compare is valid once the clock is pinned identically.
        self.assertEqual(cli_content, direct_content)


class TestWriteVerification(QueueTestBase):
    """A mutation that would leave a broken brief on disk fails loudly
    (`status: write-verification-failed`) and rolls back instead of
    reporting a false success -- the systemic gap surfaced by the
    consolidate_cluster.py bug that silently wrote empty/YAML-invalid
    briefs (see the linked root-cause brief)."""

    _INVALID_YAML = '---\nid: broken\nfocus: "unterminated\nstatus: queued\n---\n\nbody\n'
    _MISSING_STATUS = "---\nid: broken\nfocus: no status field\n---\n\nbody\n"

    def test_claim_rejects_pre_broken_yaml_and_leaves_it_in_queue(self):
        p = self.queue / "20260701-000000-broken.md"
        p.write_text(self._INVALID_YAML, encoding="utf-8")

        res = q.claim("20260701-000000-broken")

        self.assertEqual(res["status"], "write-verification-failed")
        self.assertIsNotNone(res["error"])
        self.assertTrue(p.exists(), "must roll back to queue/, not vanish")
        self.assertFalse((self.picked / "20260701-000000-broken.md").exists())
        self.assertEqual(p.read_text(encoding="utf-8"), self._INVALID_YAML)

    def test_claim_always_stamps_status_even_if_source_lacked_it(self):
        """`claim()` unconditionally sets `status: picked` as part of its own
        stamp, so a pre-existing missing-`status` source brief is not a
        validation failure here -- the claim itself supplies the field
        (`validate_brief_text`'s own missing-required-field behavior is
        covered directly in test_brief_frontmatter.py)."""
        p = self.queue / "20260701-000000-nostatus.md"
        p.write_text(self._MISSING_STATUS, encoding="utf-8")

        res = q.claim("20260701-000000-nostatus")

        self.assertEqual(res["status"], "claimed")

    def test_claim_cli_exit_code_is_six(self):
        p = self.queue / "20260701-000000-broken.md"
        p.write_text(self._INVALID_YAML, encoding="utf-8")
        code = q.main(["claim", "20260701-000000-broken", "--json"])
        self.assertEqual(code, 6)

    def test_done_rejects_pre_broken_picked_brief_and_reports_but_leaves_it(self):
        self.picked.mkdir(parents=True, exist_ok=True)
        p = self.picked / "20260701-000000-broken.md"
        p.write_text(self._INVALID_YAML, encoding="utf-8")

        res = q.done("20260701-000000-broken")

        self.assertEqual(res["status"], "write-verification-failed")
        self.assertTrue(p.exists())
        # rolled back to the pre-stamp (still-broken) content, not left
        # half-stamped with status: done on top of invalid YAML.
        self.assertEqual(p.read_text(encoding="utf-8"), self._INVALID_YAML)

    def test_release_rejects_pre_broken_picked_brief_and_leaves_it_in_picked(self):
        self.picked.mkdir(parents=True, exist_ok=True)
        p = self.picked / "20260701-000000-broken.md"
        p.write_text(self._INVALID_YAML, encoding="utf-8")

        res = q.release("20260701-000000-broken")

        self.assertEqual(res["status"], "write-verification-failed")
        self.assertTrue(p.exists(), "must stay in picked/, not move to queue/")
        self.assertFalse((self.queue / "20260701-000000-broken.md").exists())

    def test_link_rejects_when_either_side_is_pre_broken_and_rolls_back_both(self):
        good = self.queue / "20260701-000000-good.md"
        good.write_text(_brief("good side"), encoding="utf-8")
        bad = self.queue / "20260701-000000-bad.md"
        bad.write_text(self._INVALID_YAML, encoding="utf-8")
        good_before = good.read_text(encoding="utf-8")

        res = q.link("20260701-000000-good", "20260701-000000-bad")

        self.assertEqual(res["status"], "write-verification-failed")
        # the good side must not end up half-linked while the bad side failed
        self.assertEqual(good.read_text(encoding="utf-8"), good_before)
        self.assertNotIn("related:", bad.read_text(encoding="utf-8"))


class TestUnterminatedFenceFallback(QueueTestBase):
    """A brief with an opening `---` but no closing fence is a different
    failure shape than invalid-YAML-inside-a-closed-fence (TestWriteVerification
    above): `_FM_RE` never matches at all, so the stamping helpers used to fall
    through to their "no frontmatter -> create a minimal block" branch and
    prepend a fresh, independently well-formed block on top of the broken
    content -- `validate_brief` only re-parses that new block, so it reported
    success while the original `id`/`focus`/etc. fields were demoted to inert
    body text. Confirmed by reproducing this against a real corrupted brief
    from the live queue during the investigation that motivated this fix
    (see docs/specs/research/work-queue-brief-corruption-sweep.md)."""

    _UNTERMINATED_FENCE = (
        "---\nid: broken\nfocus: no closing fence\nstatus: queued\n\n"
        "body text that was never meant to be body text\n"
    )

    def test_set_fm_fields_raises_instead_of_prepending_a_duplicate_block(self):
        p = self.queue / "20260701-000000-nofence.md"
        p.write_text(self._UNTERMINATED_FENCE, encoding="utf-8")

        with self.assertRaises(ValueError):
            q._set_fm_fields(p, {"status": "picked"})

        # nothing was written -- the raise happens before any write
        self.assertEqual(p.read_text(encoding="utf-8"), self._UNTERMINATED_FENCE)

    def test_set_fm_list_field_raises_instead_of_prepending_a_duplicate_block(self):
        p = self.queue / "20260701-000000-nofence2.md"
        p.write_text(self._UNTERMINATED_FENCE, encoding="utf-8")

        with self.assertRaises(ValueError):
            q._set_fm_list_field(p, "related", ["some-other-brief"])

        self.assertEqual(p.read_text(encoding="utf-8"), self._UNTERMINATED_FENCE)

    def test_claim_rejects_unterminated_fence_and_leaves_it_in_queue(self):
        p = self.queue / "20260701-000000-nofence.md"
        p.write_text(self._UNTERMINATED_FENCE, encoding="utf-8")

        res = q.claim("20260701-000000-nofence")

        self.assertEqual(res["status"], "write-verification-failed")
        self.assertTrue(p.exists(), "must roll back to queue/, not vanish")
        self.assertFalse((self.picked / "20260701-000000-nofence.md").exists())
        # exactly the original content -- no duplicate frontmatter block on top
        self.assertEqual(p.read_text(encoding="utf-8"), self._UNTERMINATED_FENCE)
        self.assertEqual(p.read_text(encoding="utf-8").count("---"), 1)

    def test_done_rejects_unterminated_fence_and_leaves_it_untouched(self):
        self.picked.mkdir(parents=True, exist_ok=True)
        p = self.picked / "20260701-000000-nofence.md"
        p.write_text(self._UNTERMINATED_FENCE, encoding="utf-8")

        res = q.done("20260701-000000-nofence")

        self.assertEqual(res["status"], "error")
        self.assertEqual(p.read_text(encoding="utf-8"), self._UNTERMINATED_FENCE)


class TestNotYetDue(QueueTestBase):
    """Tests for the `next-check-after` backoff field (REQ-CHG-001..003, AC-001, AC-005)."""

    def test_future_date_is_not_yet_due(self):
        self.write(
            "20260101-000000-a.md", focus="blocked upstream",
            extra="next-check-after: 2099-01-01",
        )
        briefs = q.list_queue()["briefs"]
        self.assertTrue(briefs[0]["not_yet_due"])

    def test_past_date_is_due(self):
        self.write(
            "20260101-000000-a.md", focus="blocked upstream",
            extra="next-check-after: 2000-01-01",
        )
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["not_yet_due"])

    def test_no_field_is_due(self):
        """No next-check-after field -> not_yet_due False (REQ-CHG-002, backward compatible)."""
        self.write("20260101-000000-a.md", focus="ordinary work")
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["not_yet_due"])

    def test_unparsable_date_is_lenient(self):
        """Malformed next-check-after is treated as due, not an error (AC-005)."""
        self.write(
            "20260101-000000-a.md", focus="blocked upstream",
            extra="next-check-after: not-a-date",
        )
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["not_yet_due"])

    def test_not_yet_due_independent_of_blocked(self):
        """A brief can be not-yet-due without being blocked, and vice versa."""
        self.write(
            "20260101-000000-a.md", focus="blocked upstream",
            extra="next-check-after: 2099-01-01",
        )
        briefs = q.list_queue()["briefs"]
        self.assertTrue(briefs[0]["not_yet_due"])
        self.assertFalse(briefs[0]["blocked"])


class TestRecentlyReleased(QueueTestBase):
    """Tests for the `released-at` recently-released guard."""

    def test_release_stamps_released_at(self):
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth")
        q.release("20260531-141200-auth")
        fm = q._read_frontmatter(self.queue / "20260531-141200-auth.md")
        self.assertIn("released-at", fm)

    def test_claim_release_then_immediate_list_flags_recently_released(self):
        """claim + release then an immediate list -- the exact race the guard
        exists for -- must flag the brief rather than presenting it as an
        ordinary, unremarkable pick."""
        self.write("20260531-141200-auth.md", focus="auth")
        q.claim("20260531-141200-auth", by="agent-a")
        q.release("20260531-141200-auth")
        briefs = q.list_queue()["briefs"]
        self.assertTrue(briefs[0]["recently_released"])
        self.assertEqual(briefs[0]["recently_released_by"], "agent-a")
        self.assertIsNotNone(briefs[0]["recently_released_at"])

    def test_old_released_at_is_not_recently_released(self):
        old = (
            datetime.datetime.now().astimezone()
            - datetime.timedelta(minutes=q.RECENTLY_RELEASED_MINUTES + 5)
        ).isoformat(timespec="seconds")
        self.write("20260531-141200-auth.md", focus="auth", extra=f"released-at: '{old}'")
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["recently_released"])
        self.assertIsNone(briefs[0]["recently_released_by"])

    def test_no_released_at_field_is_not_recently_released(self):
        self.write("20260531-141200-auth.md", focus="auth")
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["recently_released"])

    def test_unparsable_released_at_is_lenient(self):
        self.write("20260531-141200-auth.md", focus="auth", extra="released-at: not-a-timestamp")
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["recently_released"])


class TestClaimBatch(QueueTestBase):
    """Tests for claim-batch -- batch consumption of related briefs."""

    def test_batch_claims_primary_and_companions(self):
        self.write("20260701-000001-primary.md", focus="primary work")
        self.write("20260701-000002-comp-a.md", focus="companion a")
        self.write("20260701-000003-comp-b.md", focus="companion b")
        res = q.claim_batch(
            "20260701-000001-primary",
            ["20260701-000002-comp-a", "20260701-000003-comp-b"],
            by="agent-x",
        )
        self.assertEqual(res["status"], "claimed")
        self.assertEqual([c["status"] for c in res["companions"]], ["claimed", "claimed"])
        fm = q._read_frontmatter(self.picked / "20260701-000001-primary.md")
        self.assertEqual(fm["status"], "picked")
        self.assertEqual(fm["batch"], ["20260701-000002-comp-a", "20260701-000003-comp-b"])
        for comp in ("20260701-000002-comp-a", "20260701-000003-comp-b"):
            cfm = q._read_frontmatter(self.picked / f"{comp}.md")
            self.assertEqual(cfm["status"], "picked")
            self.assertEqual(cfm["batch-primary"], "20260701-000001-primary")
            self.assertEqual(cfm["claimed-by"], "agent-x")

    def test_companion_failure_never_aborts_batch(self):
        self.write("20260701-000001-primary.md", focus="primary work")
        self.write("20260701-000002-comp-a.md", focus="companion a")
        res = q.claim_batch(
            "20260701-000001-primary",
            ["20260701-000002-comp-a", "20260701-999999-missing"],
        )
        self.assertEqual(res["status"], "claimed")
        statuses = {c["identifier"]: c["status"] for c in res["companions"]}
        self.assertEqual(statuses["20260701-000002-comp-a"], "claimed")
        self.assertEqual(statuses["20260701-999999-missing"], "none")
        # only the actually-claimed companion is recorded on the primary
        fm = q._read_frontmatter(self.picked / "20260701-000001-primary.md")
        self.assertEqual(fm["batch"], ["20260701-000002-comp-a"])

    def test_failed_primary_aborts_batch_companions_untouched(self):
        self.write("20260701-000002-comp-a.md", focus="companion a")
        res = q.claim_batch("20260701-000001-missing", ["20260701-000002-comp-a"])
        self.assertEqual(res["status"], "none")
        self.assertEqual(res["companions"], [])
        self.assertTrue((self.queue / "20260701-000002-comp-a.md").exists())

    def test_release_strips_batch_primary(self):
        self.write("20260701-000001-primary.md", focus="primary work")
        self.write("20260701-000002-comp-a.md", focus="companion a")
        q.claim_batch("20260701-000001-primary", ["20260701-000002-comp-a"])
        res = q.release("20260701-000002-comp-a")
        self.assertEqual(res["status"], "released")
        fm = q._read_frontmatter(self.queue / "20260701-000002-comp-a.md")
        self.assertEqual(fm["status"], "queued")
        self.assertNotIn("batch-primary", fm)

    def test_cli_exit_codes_keyed_off_primary(self):
        self.write("20260701-000001-primary.md", focus="p")
        self.write("20260701-000002-comp.md", focus="c")
        self.assertEqual(
            q.main(["claim-batch", "20260701-000001-primary", "20260701-000002-comp", "--json"]),
            0,
        )
        # primary already in picked/ -> exit 4, same contract as single claim
        self.assertEqual(q.main(["claim-batch", "20260701-000001-primary", "--json"]), 4)


class TestCli(QueueTestBase):
    def test_claim_exit_codes(self):
        self.write("20260531-141200-auth.md", focus="auth")
        self.assertEqual(q.main(["claim", "20260531-141200-auth", "--json"]), 0)
        self.assertEqual(
            q.main(["claim", "20260531-141200-auth", "--json"]), 4
        )  # now in picked/ -> already-claimed
        self.assertEqual(q.main(["list", "--json"]), 0)


class TestBlockedBy(QueueTestBase):
    """Tests for the blocked-by dependency tracking feature."""

    def test_list_not_blocked_when_no_deps(self):
        self.write("20260101-000000-a.md", focus="no deps")
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["blocked"])

    def test_list_blocked_when_dep_in_queue(self):
        self.write("20260101-000000-dep.md", focus="the prereq", brief_id="dep-id")
        self.write("20260101-000001-main.md", focus="needs dep", blocked_by=["dep-id"])
        briefs = {b["filename"]: b for b in q.list_queue()["briefs"]}
        self.assertFalse(briefs["20260101-000000-dep.md"]["blocked"])
        self.assertTrue(briefs["20260101-000001-main.md"]["blocked"])

    def test_list_blocked_when_dep_picked_but_not_done(self):
        self.picked.mkdir(parents=True, exist_ok=True)
        (self.picked / "dep.md").write_text(_picked_brief("dep", status="picked"), encoding="utf-8")
        self.write("20260101-000001-main.md", focus="needs dep", blocked_by=["dep"])
        briefs = q.list_queue()["briefs"]
        self.assertTrue(briefs[0]["blocked"])

    def test_list_not_blocked_when_dep_done(self):
        self.picked.mkdir(parents=True, exist_ok=True)
        (self.picked / "dep.md").write_text(_picked_brief("dep", status="done"), encoding="utf-8")
        self.write("20260101-000001-main.md", focus="needs dep", blocked_by=["dep"])
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["blocked"])

    def test_list_not_blocked_when_dep_not_found(self):
        """Lenient: a missing (stale) dep ID is treated as satisfied."""
        self.write("20260101-000001-main.md", focus="needs dep", blocked_by=["stale-id-xyz"])
        briefs = q.list_queue()["briefs"]
        self.assertFalse(briefs[0]["blocked"])

    def test_claim_warns_when_dep_unresolved(self):
        self.write("20260101-000000-dep.md", focus="the prereq", brief_id="dep-id")
        self.write("20260101-000001-main.md", focus="needs dep", blocked_by=["dep-id"])
        res = q.claim("20260101-000001-main")
        self.assertEqual(res["status"], "claimed")
        self.assertIn("blocked by dep-id", res["warnings"])

    def test_claim_no_warnings_when_dep_done(self):
        self.picked.mkdir(parents=True, exist_ok=True)
        (self.picked / "dep.md").write_text(_picked_brief("dep", status="done"), encoding="utf-8")
        self.write("20260101-000001-main.md", focus="needs dep", blocked_by=["dep"])
        res = q.claim("20260101-000001-main")
        self.assertEqual(res["status"], "claimed")
        self.assertEqual(res["warnings"], [])

    def test_claim_no_warnings_when_dep_not_found(self):
        """Lenient: stale dep IDs do not produce warnings."""
        self.write("20260101-000001-main.md", focus="needs dep", blocked_by=["stale-id-xyz"])
        res = q.claim("20260101-000001-main")
        self.assertEqual(res["status"], "claimed")
        self.assertEqual(res["warnings"], [])

    def test_claim_always_has_warnings_key(self):
        """All claim outcomes include a warnings key."""
        self.write("20260101-000000-a.md", focus="plain")
        res = q.claim("20260101-000000-a")
        self.assertIn("warnings", res)
        self.assertEqual(res["warnings"], [])


class TestPremiseDriftWarning(QueueTestBase):
    """Tests for the claim-time premise-drift age warning."""

    def _created(self, days_ago: float) -> str:
        when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
        return when.isoformat()

    def test_claim_warns_when_brief_older_than_threshold(self):
        self.write(
            "20260101-000000-old.md",
            focus="stale ask",
            extra=f"created: {self._created(q.PREMISE_DRIFT_THRESHOLD_DAYS + 1)}",
        )
        res = q.claim("20260101-000000-old")
        self.assertEqual(res["status"], "claimed")
        self.assertTrue(any("premise may have drifted" in w for w in res["warnings"]))

    def test_claim_no_warning_when_brief_recent(self):
        self.write(
            "20260101-000000-fresh.md",
            focus="new ask",
            extra=f"created: {self._created(1)}",
        )
        res = q.claim("20260101-000000-fresh")
        self.assertEqual(res["status"], "claimed")
        self.assertEqual(res["warnings"], [])

    def test_claim_no_warning_when_created_missing(self):
        """Lenient: no created field never produces a drift warning."""
        self.write("20260101-000000-nodate.md", focus="no created field")
        res = q.claim("20260101-000000-nodate")
        self.assertEqual(res["status"], "claimed")
        self.assertEqual(res["warnings"], [])

    def test_claim_no_warning_when_created_unparsable(self):
        """Lenient: a malformed created value never produces a drift warning."""
        self.write(
            "20260101-000000-badd.md",
            focus="bad date",
            extra="created: not-a-date",
        )
        res = q.claim("20260101-000000-badd")
        self.assertEqual(res["status"], "claimed")
        self.assertEqual(res["warnings"], [])


class TestSetFmListField(QueueTestBase):
    """Tests for the _set_fm_list_field helper."""

    def test_write_new_list_field(self):
        """Writing a new list field to a brief that has none adds it to frontmatter."""
        p = self.write("20260101-000000-a.md", focus="some work")
        q._set_fm_list_field(p, "related", ["id-a", "id-b"])
        fm = q._read_frontmatter(p)
        self.assertEqual(fm.get("related"), ["id-a", "id-b"])

    def test_replace_existing_list_field(self):
        """Writing to a brief that already has a related list replaces it (idempotent)."""
        p = self.write("20260101-000000-a.md", focus="some work", related=["old-id"])
        q._set_fm_list_field(p, "related", ["new-id"])
        fm = q._read_frontmatter(p)
        self.assertEqual(fm.get("related"), ["new-id"])

    def test_idempotent_same_values(self):
        """Writing the same values twice produces no duplicates."""
        p = self.write("20260101-000000-a.md", focus="some work", related=["id-a"])
        q._set_fm_list_field(p, "related", ["id-a"])
        q._set_fm_list_field(p, "related", ["id-a"])
        fm = q._read_frontmatter(p)
        self.assertEqual(fm.get("related"), ["id-a"])

    def test_preserves_other_fields_and_body(self):
        """Writing leaves all other frontmatter fields and body unchanged."""
        p = self.write("20260101-000000-a.md", focus="some work", blocked_by=["dep-id"])
        q._set_fm_list_field(p, "related", ["id-a"])
        fm = q._read_frontmatter(p)
        self.assertEqual(fm.get("related"), ["id-a"])
        self.assertEqual(fm.get("blocked-by"), ["dep-id"])
        self.assertEqual(fm.get("focus"), "some work")
        content = p.read_text(encoding="utf-8")
        self.assertIn("## Focus", content)
        self.assertIn("some work", content)

    def test_empty_list_omits_field_when_present(self):
        """Writing an empty list removes a previously present field."""
        p = self.write("20260101-000000-a.md", focus="some work", related=["id-a"])
        q._set_fm_list_field(p, "related", [])
        fm = q._read_frontmatter(p)
        self.assertNotIn("related", fm)

    def test_empty_list_no_field_is_noop(self):
        """Writing an empty list when field is absent leaves frontmatter unchanged."""
        p = self.write("20260101-000000-a.md", focus="some work")
        q._set_fm_list_field(p, "related", [])
        fm = q._read_frontmatter(p)
        self.assertNotIn("related", fm)
        self.assertEqual(fm.get("focus"), "some work")

    def test_does_not_clobber_blocked_by(self):
        """Writing related list does not touch blocked-by entries."""
        p = self.write(
            "20260101-000000-a.md", focus="some work", blocked_by=["dep-id"], related=["old-id"]
        )
        q._set_fm_list_field(p, "related", ["new-id"])
        fm = q._read_frontmatter(p)
        self.assertEqual(fm.get("related"), ["new-id"])
        self.assertEqual(fm.get("blocked-by"), ["dep-id"])


class TestLink(QueueTestBase):
    """Tests for work_queue.link() — AC-007..011."""

    def test_symmetric_write(self):
        """link(a, b) writes b into a.related and a into b.related (AC-007)."""
        self.write("20260101-000001-alpha.md", focus="alpha task")
        self.write("20260101-000002-beta.md", focus="beta task")
        result = q.link("20260101-000001-alpha", "20260101-000002-beta")
        self.assertEqual(result["status"], "linked")
        self.assertEqual(len(result["paths"]), 2)
        fm_a = q._read_frontmatter(self.queue / "20260101-000001-alpha.md")
        fm_b = q._read_frontmatter(self.queue / "20260101-000002-beta.md")
        self.assertIn("20260101-000002-beta", fm_a.get("related", []))
        self.assertIn("20260101-000001-alpha", fm_b.get("related", []))

    def test_idempotent(self):
        """Calling link twice produces no duplicates and both calls succeed (AC-008)."""
        self.write("20260101-000001-alpha.md", focus="alpha task")
        self.write("20260101-000002-beta.md", focus="beta task")
        r1 = q.link("20260101-000001-alpha", "20260101-000002-beta")
        r2 = q.link("20260101-000001-alpha", "20260101-000002-beta")
        self.assertEqual(r1["status"], "linked")
        self.assertEqual(r2["status"], "linked")
        fm_a = q._read_frontmatter(self.queue / "20260101-000001-alpha.md")
        fm_b = q._read_frontmatter(self.queue / "20260101-000002-beta.md")
        self.assertEqual(fm_a.get("related", []).count("20260101-000002-beta"), 1)
        self.assertEqual(fm_b.get("related", []).count("20260101-000001-alpha"), 1)

    def test_no_clobber_existing_related(self):
        """Pre-existing related entries are preserved alongside the new link (AC-009)."""
        self.write("20260101-000001-alpha.md", focus="alpha", extra="related:\n  - existing-id")
        self.write("20260101-000002-beta.md", focus="beta")
        q.link("20260101-000001-alpha", "20260101-000002-beta")
        fm_a = q._read_frontmatter(self.queue / "20260101-000001-alpha.md")
        related_a = fm_a.get("related", [])
        self.assertIn("existing-id", related_a)
        self.assertIn("20260101-000002-beta", related_a)

    def test_no_clobber_blocked_by(self):
        """blocked-by entries on both files are unchanged by link (AC-009)."""
        self.write("20260101-000001-alpha.md", focus="alpha", blocked_by=["prereq-id"])
        self.write("20260101-000002-beta.md", focus="beta", blocked_by=["other-prereq"])
        q.link("20260101-000001-alpha", "20260101-000002-beta")
        fm_a = q._read_frontmatter(self.queue / "20260101-000001-alpha.md")
        fm_b = q._read_frontmatter(self.queue / "20260101-000002-beta.md")
        self.assertEqual(fm_a.get("blocked-by"), ["prereq-id"])
        self.assertEqual(fm_b.get("blocked-by"), ["other-prereq"])

    def test_none_identifier(self):
        """link with a missing identifier returns none and makes no changes (AC-010)."""
        self.write("20260101-000002-beta.md", focus="beta")
        result = q.link("missing-id-xyz", "20260101-000002-beta")
        self.assertEqual(result["status"], "none")
        self.assertEqual(result["paths"], [])
        fm_b = q._read_frontmatter(self.queue / "20260101-000002-beta.md")
        self.assertFalse(fm_b.get("related"))

    def test_ambiguous_identifier(self):
        """link with an ambiguous identifier returns ambiguous and makes no changes (AC-010)."""
        self.write("20260101-000001-shared-prefix-a.md", focus="a")
        self.write("20260101-000001-shared-prefix-b.md", focus="b")
        self.write("20260101-000002-target.md", focus="target")
        result = q.link("20260101-000001-shared-prefix", "20260101-000002-target")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["paths"], [])
        fm_t = q._read_frontmatter(self.queue / "20260101-000002-target.md")
        self.assertFalse(fm_t.get("related"))

    def test_resolve_across_queue_and_picked(self):
        """link resolves a in queue/ and b in picked/ (AC-007 across locations)."""
        self.write("20260101-000001-alpha.md", focus="alpha")
        self.picked.mkdir(parents=True, exist_ok=True)
        (self.picked / "20260101-000002-beta.md").write_text(
            _picked_brief("beta", status="picked"), encoding="utf-8"
        )
        result = q.link("20260101-000001-alpha", "20260101-000002-beta")
        self.assertEqual(result["status"], "linked")
        fm_a = q._read_frontmatter(self.queue / "20260101-000001-alpha.md")
        fm_b = q._read_frontmatter(self.picked / "20260101-000002-beta.md")
        self.assertIn("20260101-000002-beta", fm_a.get("related", []))
        self.assertIn("20260101-000001-alpha", fm_b.get("related", []))

    def test_cli_json_linked(self):
        """--json output has status=linked and two paths on success (AC-011)."""
        self.write("20260101-000001-alpha.md", focus="alpha")
        self.write("20260101-000002-beta.md", focus="beta")
        out = io.StringIO()
        with patch("sys.stdout", out):
            code = q.main(["link", "20260101-000001-alpha", "20260101-000002-beta", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["status"], "linked")
        self.assertEqual(len(data["paths"]), 2)
        self.assertIsNone(data["error"])

    def test_cli_json_none(self):
        """--json output has status=none and exit code 1 when identifier missing (AC-011)."""
        self.write("20260101-000002-beta.md", focus="beta")
        out = io.StringIO()
        with patch("sys.stdout", out):
            code = q.main(["link", "missing-id-xyz", "20260101-000002-beta", "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        self.assertEqual(data["status"], "none")

    def test_self_link_noop(self):
        """Self-link (same brief both args) is a no-op success — writes nothing."""
        self.write("20260101-000001-alpha.md", focus="alpha")
        result = q.link("20260101-000001-alpha", "20260101-000001-alpha")
        self.assertEqual(result["status"], "linked")
        fm = q._read_frontmatter(self.queue / "20260101-000001-alpha.md")
        self.assertNotIn("20260101-000001-alpha", fm.get("related") or [])

    def test_picked_done_still_resolvable(self):
        """A picked brief with status=done can still be found and linked."""
        self.write("20260101-000001-alpha.md", focus="alpha")
        self.picked.mkdir(parents=True, exist_ok=True)
        (self.picked / "20260101-000002-done-brief.md").write_text(
            _picked_brief("done work", status="done"), encoding="utf-8"
        )
        result = q.link("20260101-000001-alpha", "20260101-000002-done-brief")
        self.assertEqual(result["status"], "linked")
        fm_a = q._read_frontmatter(self.queue / "20260101-000001-alpha.md")
        self.assertIn("20260101-000002-done-brief", fm_a.get("related", []))


class TestRelatedAndLink(QueueTestBase):
    """AC-021: test scenarios for the related field not covered by TestLink."""

    def test_stale_id_lenience_in_list(self):
        """list_queue() returns a brief with a stale related ID without error;
        the stale ID appears in the related array and blocked is false (AC-021)."""
        self.write("20260101-000001-alpha.md", focus="alpha", related=["stale-xyz-999"])
        briefs = q.list_queue()["briefs"]
        self.assertEqual(len(briefs), 1)
        self.assertFalse(briefs[0]["blocked"])
        self.assertEqual(briefs[0].get("related"), ["stale-xyz-999"])

    def test_list_json_related_array_empty_when_absent(self):
        """`list --json` emits related: [] for a brief with no related field (AC-021)."""
        self.write("20260101-000001-alpha.md", focus="alpha")
        briefs = q.list_queue()["briefs"]
        self.assertEqual(len(briefs), 1)
        self.assertEqual(briefs[0].get("related"), [])

    def test_list_json_related_array_present_when_set(self):
        """`list --json` emits the stored related IDs for a brief with related set (AC-021)."""
        self.write("20260101-000001-alpha.md", focus="alpha", related=["id-x", "id-y"])
        briefs = q.list_queue()["briefs"]
        self.assertEqual(len(briefs), 1)
        self.assertEqual(briefs[0].get("related"), ["id-x", "id-y"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
