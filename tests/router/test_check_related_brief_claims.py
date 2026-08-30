#!/usr/bin/env python3
"""Unit tests for the `/go` Phase 5.5 related-brief collision guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from worktrail.router import check_related_brief_claims as crbc
from worktrail.workqueue import decisions


def _write_brief(
    path: Path,
    *,
    related=None,
    status: str | None = "queued",
    claimed_by: str | None = None,
    claimed_at: str | None = None,
    repo: str | None = None,
    focus: str | None = "Some focus text.",
    brief_id: str | None = None,
) -> Path:
    """Write a brief-shaped markdown file with the given frontmatter fields."""
    lines = ["---"]
    if brief_id is not None:
        lines.append(f"id: {brief_id}")
    if status is not None:
        lines.append(f"status: {status}")
    if claimed_by is not None:
        lines.append(f"claimed-by: {claimed_by}")
    if claimed_at is not None:
        lines.append(f"claimed-at: '{claimed_at}'")
    if repo is not None:
        lines.append(f"repo: {repo}")
    if focus is not None:
        lines.append(f"focus: {focus}")
    if related is not None:
        if isinstance(related, str):
            lines.append(f"related: {related}")
        else:
            lines.append("related:")
            for r in related:
                lines.append(f"  - {r}")
    lines.append("---")
    lines.append("")
    lines.append("## Focus")
    lines.append("")
    lines.append(focus or "")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.fixture()
def queue_dirs(tmp_path: Path):
    picked_dir = tmp_path / "picked"
    queue_dir = tmp_path / "queue"
    picked_dir.mkdir()
    queue_dir.mkdir()
    return picked_dir, queue_dir


# --------------------------------------------------------------------------- #
# Related-id resolution
# --------------------------------------------------------------------------- #
class TestRelatedIdResolution:
    def test_single_match_in_picked_is_resolved(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        _write_brief(
            picked_dir / "20260101-000000-other-brief.md",
            status="picked",
            claimed_by="somehost:123",
            claimed_at="2026-01-01T00:00:00",
            repo="some/repo",
        )
        claimed = _write_brief(
            tmp_path / "claimed.md",
            related=["other-brief"],
        )

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert len(res["active"]) == 1
        assert res["active"][0]["id"] == "other-brief"

    def test_zero_matches_is_skipped_not_fatal(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        claimed = _write_brief(tmp_path / "claimed.md", related=["nonexistent-brief"])

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"] == []

    def test_ambiguous_match_is_skipped_not_fatal(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        _write_brief(picked_dir / "20260101-000000-dupe-brief.md", status="picked")
        _write_brief(picked_dir / "20260102-000000-dupe-brief.md", status="picked")
        claimed = _write_brief(tmp_path / "claimed.md", related=["dupe-brief"])

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"] == []

    def test_one_resolvable_and_one_unresolvable_id_still_reports_the_resolvable_one(
        self, tmp_path, queue_dirs
    ):
        picked_dir, queue_dir = queue_dirs
        _write_brief(
            picked_dir / "20260101-000000-real-brief.md",
            status="picked",
            claimed_by="somehost:123",
        )
        claimed = _write_brief(
            tmp_path / "claimed.md",
            related=["real-brief", "missing-brief"],
        )

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert [m["id"] for m in res["active"]] == ["real-brief"]


# --------------------------------------------------------------------------- #
# Active vs done vs still-queued status determination
# --------------------------------------------------------------------------- #
class TestStatusDetermination:
    def test_picked_status_is_active(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        _write_brief(
            picked_dir / "20260101-000000-active-brief.md",
            status="picked",
            claimed_by="somehost:123",
            claimed_at="2026-01-01T00:00:00",
            repo="some/repo",
        )
        claimed = _write_brief(tmp_path / "claimed.md", related=["active-brief"])

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert len(res["active"]) == 1
        match = res["active"][0]
        assert match["claimed-by"] == "somehost:123"
        assert match["repo"] == "some/repo"

    def test_done_status_in_picked_dir_is_not_active(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        _write_brief(
            picked_dir / "20260101-000000-done-brief.md",
            status="done",
            claimed_by="somehost:123",
        )
        claimed = _write_brief(tmp_path / "claimed.md", related=["done-brief"])

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["active"] == []

    def test_still_queued_brief_is_not_active(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        _write_brief(queue_dir / "20260101-000000-queued-brief.md", status="queued")
        claimed = _write_brief(tmp_path / "claimed.md", related=["queued-brief"])

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["active"] == []


# --------------------------------------------------------------------------- #
# No `related:` field short-circuit
# --------------------------------------------------------------------------- #
class TestNoRelatedField:
    def test_missing_related_field_short_circuits_without_touching_queue_dirs(
        self, tmp_path
    ):
        claimed = _write_brief(tmp_path / "claimed.md", related=None)
        picked_dir = tmp_path / "picked-does-not-exist"
        queue_dir = tmp_path / "queue-does-not-exist"

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"] == []
        assert res["warning"] is None

    def test_empty_related_list_short_circuits(self, tmp_path):
        claimed = _write_brief(tmp_path / "claimed.md", related=[])
        picked_dir = tmp_path / "picked-does-not-exist"
        queue_dir = tmp_path / "queue-does-not-exist"

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"] == []


# --------------------------------------------------------------------------- #
# Run-record enrichment present/absent
# --------------------------------------------------------------------------- #
class TestRunRecordEnrichment:
    def test_matching_run_record_is_attached_when_claimed_by_is_this_agent(
        self, tmp_path, queue_dirs
    ):
        picked_dir, queue_dir = queue_dirs
        agent_label = "thishost:456"
        _write_brief(
            picked_dir / "20260101-000000-related-brief.md",
            status="picked",
            claimed_by=agent_label,
            repo="some-repo",
        )
        claimed = _write_brief(tmp_path / "claimed.md", related=["related-brief"])

        runs_dir = tmp_path / "runs"
        repo_dir = runs_dir / "some-repo"
        repo_dir.mkdir(parents=True)
        record = repo_dir / "20260101-000000.yaml"
        record.write_text(
            "brief: related-brief\nstatus: in_progress\n", encoding="utf-8"
        )

        res = crbc.check(
            claimed, picked_dir, queue_dir, agent_label=agent_label, runs_dir=runs_dir
        )

        assert len(res["active"]) == 1
        assert res["active"][0]["run_record"] == str(record)

    def test_no_matching_run_record_still_reports_match_without_run_record_key(
        self, tmp_path, queue_dirs
    ):
        picked_dir, queue_dir = queue_dirs
        agent_label = "thishost:456"
        _write_brief(
            picked_dir / "20260101-000000-related-brief.md",
            status="picked",
            claimed_by=agent_label,
            repo="some-repo",
        )
        claimed = _write_brief(tmp_path / "claimed.md", related=["related-brief"])

        runs_dir = tmp_path / "runs-empty"
        runs_dir.mkdir()

        res = crbc.check(
            claimed, picked_dir, queue_dir, agent_label=agent_label, runs_dir=runs_dir
        )

        assert len(res["active"]) == 1
        assert "run_record" not in res["active"][0]

    def test_claimed_by_different_agent_is_not_enriched(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        _write_brief(
            picked_dir / "20260101-000000-related-brief.md",
            status="picked",
            claimed_by="otherhost:789",
            repo="some-repo",
        )
        claimed = _write_brief(tmp_path / "claimed.md", related=["related-brief"])

        runs_dir = tmp_path / "runs"
        repo_dir = runs_dir / "some-repo"
        repo_dir.mkdir(parents=True)
        (repo_dir / "20260101-000000.yaml").write_text(
            "brief: related-brief\n", encoding="utf-8"
        )

        res = crbc.check(
            claimed,
            picked_dir,
            queue_dir,
            agent_label="thishost:456",
            runs_dir=runs_dir,
        )

        assert len(res["active"]) == 1
        assert "run_record" not in res["active"][0]


# --------------------------------------------------------------------------- #
# Fail-open paths
# --------------------------------------------------------------------------- #
class TestFailsOpen:
    def test_unreadable_claimed_brief_yields_checked_false(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        missing_brief = tmp_path / "does-not-exist.md"

        res = crbc.check(missing_brief, picked_dir, queue_dir)

        assert res["checked"] is False
        assert res["active"] == []
        assert res["warning"] is not None

    def test_missing_picked_dir_does_not_raise_and_is_skipped(self, tmp_path):
        picked_dir = tmp_path / "picked-does-not-exist"
        queue_dir = tmp_path / "queue"
        queue_dir.mkdir()
        claimed = _write_brief(tmp_path / "claimed.md", related=["some-brief"])

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"] == []

    def test_directory_claimed_path_is_checked_false_not_a_silent_pass(
        self, tmp_path, queue_dirs
    ):
        """A picked-brief directory passes a bare `.stat()`, so without the
        shape check this used to fall through to `read_frontmatter`
        (swallowing the resulting OSError into `{}`) and report
        `checked: true, active: []` -- indistinguishable from "nothing
        collides" even though the input itself was wrong."""
        picked_dir, queue_dir = queue_dirs
        claimed_dir = tmp_path / "20260101-000000-some-brief"
        claimed_dir.mkdir()

        res = crbc.check(claimed_dir, picked_dir, queue_dir)

        assert res["checked"] is False
        assert res["active"] == []
        assert "directory" in res["warning"]

    def test_check_never_raises_for_a_variety_of_degraded_inputs(self, tmp_path):
        cases = [
            Path("/nonexistent/path/claimed.md"),
            Path("/nonexistent/dir/also-missing.md"),
        ]
        picked_dir = tmp_path / "picked-missing"
        queue_dir = tmp_path / "queue-missing"
        for claimed in cases:
            res = crbc.check(claimed, picked_dir, queue_dir)
            assert "checked" in res
            assert res["checked"] is False


# --------------------------------------------------------------------------- #
# Provider-neutral pending-decision envelope (pending-user-decision-dispatch-contract 2.1)
# --------------------------------------------------------------------------- #
class TestPendingDecisionEnvelope:
    def _active_claim(
        self, tmp_path: Path, queue_dirs, brief_id="20260101-000000-other"
    ):
        picked_dir, _queue_dir = queue_dirs
        _write_brief(
            picked_dir / f"{brief_id}.md",
            status="picked",
            claimed_by="somehost:123",
            claimed_at="2026-01-01T00:00:00",
            repo="some/repo",
        )
        return _write_brief(
            tmp_path / "claimed.md",
            related=["other"],
            brief_id="20260202-000000-claimed",
            repo="target/repo",
        )

    def test_active_claims_carry_valid_envelope(self, tmp_path, queue_dirs):
        claimed = self._active_claim(tmp_path, queue_dirs)
        picked_dir, queue_dir = queue_dirs

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"]
        envelope = res["pending_decision"]
        assert envelope is not None
        parsed = decisions.parse_pending_decision_envelope(envelope)
        assert parsed["schema"] == decisions.DECISION_ENVELOPE_SCHEMA
        assert parsed["version"] == decisions.DECISION_ENVELOPE_VERSION
        assert parsed["status"] == "pending"
        assert parsed["decision_id"].startswith("dec-")
        assert parsed["provenance"]["source"] == crbc.GUARD_SOURCE
        assert parsed["provenance"]["subject"] == "20260202-000000-claimed"
        assert parsed["provenance"]["repo"] == "target/repo"
        assert len(parsed["options"]) >= 2

    def test_identity_is_deterministic_across_re_runs(self, tmp_path, queue_dirs):
        claimed = self._active_claim(tmp_path, queue_dirs)
        picked_dir, queue_dir = queue_dirs

        first = crbc.check(claimed, picked_dir, queue_dir)["pending_decision"]
        second = crbc.check(claimed, picked_dir, queue_dir)["pending_decision"]
        assert first["decision_id"] == second["decision_id"]
        assert first["decision_id"] == decisions.decision_identity(
            crbc.GUARD_SOURCE,
            "target/repo",
            "20260202-000000-claimed",
            crbc.DECISION_QUESTION,
        )

    def test_subject_falls_back_to_stem_without_frontmatter_id(
        self, tmp_path, queue_dirs
    ):
        picked_dir, queue_dir = queue_dirs
        _write_brief(picked_dir / "20260101-000000-other.md", status="picked")
        claimed = _write_brief(
            tmp_path / "20260202-000000-claimed.md", related=["other"]
        )

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert (
            res["pending_decision"]["provenance"]["subject"]
            == "20260202-000000-claimed"
        )

    def test_repo_provenance_falls_back_then_to_unspecified(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        # no repo anywhere -> the stable placeholder, never a dropped envelope
        _write_brief(picked_dir / "20260101-000000-other.md", status="picked")
        claimed = _write_brief(tmp_path / "claimed.md", related=["other"])
        res = crbc.check(claimed, picked_dir, queue_dir)
        assert res["pending_decision"]["provenance"]["repo"] == "unspecified"

        # claimed brief lacks repo, but the active claim carries one
        _write_brief(
            picked_dir / "20260103-000000-other3.md",
            status="picked",
            claimed_by="h:1",
            repo="active/repo",
        )
        claimed2 = _write_brief(tmp_path / "claimed2.md", related=["other3"])
        res2 = crbc.check(claimed2, picked_dir, queue_dir)
        assert res2["pending_decision"]["provenance"]["repo"] == "active/repo"

    def test_provenance_threads_run_id_and_dispatch_mode(self, tmp_path, queue_dirs):
        claimed = self._active_claim(tmp_path, queue_dirs)
        picked_dir, queue_dir = queue_dirs

        res = crbc.check(
            claimed,
            picked_dir,
            queue_dir,
            run_id="go-20260825-101010",
            dispatch_mode="adapter",
        )

        prov = res["pending_decision"]["provenance"]
        assert prov["run_id"] == "go-20260825-101010"
        assert prov["dispatch_mode"] == "adapter"

    def test_no_active_claims_leaves_pending_decision_none(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs
        claimed = _write_brief(tmp_path / "claimed.md", related=["missing-brief"])

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"] == []
        assert res["pending_decision"] is None

    def test_checked_false_leaves_pending_decision_none(self, tmp_path, queue_dirs):
        picked_dir, queue_dir = queue_dirs

        res = crbc.check(tmp_path / "does-not-exist.md", picked_dir, queue_dir)

        assert res["checked"] is False
        assert res["pending_decision"] is None

    def test_unavailable_decision_primitives_degrade_without_raising(
        self, tmp_path, queue_dirs, monkeypatch
    ):
        claimed = self._active_claim(tmp_path, queue_dirs)
        picked_dir, queue_dir = queue_dirs
        monkeypatch.setattr(crbc, "_decision_helpers", lambda: (None, None))

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["checked"] is True
        assert res["active"]
        assert res["pending_decision"] is None

    def test_format_human_names_the_decision_id_when_present(
        self, tmp_path, queue_dirs
    ):
        claimed = self._active_claim(tmp_path, queue_dirs)
        picked_dir, queue_dir = queue_dirs

        res = crbc.check(claimed, picked_dir, queue_dir)

        assert res["pending_decision"]["decision_id"] in crbc._format_human(res)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
