from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from worktrail.shared.brief_frontmatter import read_frontmatter, validate_brief
from worktrail.workqueue.create_handoff import _slugify, create_handoff, main


def test_create_handoff_writes_valid_brief_and_classifies(tmp_path: Path):
    result = create_handoff(
        "Fix the broken handoff dashboard",
        queue_base=tmp_path,
        repo="/tmp/example",
        base_branch="main",
        suggested_skills=["debugging.skill"],
        approach="Reproduce the dashboard failure and add a regression test.",
    )

    path = Path(result["path"])
    assert path.parent == tmp_path / "queue"
    assert path.is_file()
    assert result["status"] == "created"
    assert result["recommended_route"] == "F"
    assert read_frontmatter(path) == {
        "id": path.stem,
        "created": read_frontmatter(path)["created"],
        "focus": "Fix the broken handoff dashboard",
        "repo": "/tmp/example",
        "remote": None,
        "base-branch": "main",
        "status": "queued",
        "suggested-skills": ["debugging.skill"],
        "recommended-route": "F",
    }
    assert "## Suggested approach" in path.read_text(encoding="utf-8")
    assert validate_brief(path)[0]
    assert os.environ.get("WORK_QUEUE_DIR") != str(tmp_path)


def test_create_handoff_omits_recommended_route_on_zero_signal_focus(tmp_path: Path):
    # A content-free focus string classifies as a zero-signal default (E,
    # low confidence, route_source="no-signal-default"), not a real pick --
    # it must not be persisted as recommended-route frontmatter, the same as
    # the ambiguous-tie omission (20260731-151701).
    result = create_handoff(
        "Standardize the shared helper used by two call sites",
        queue_base=tmp_path,
        repo="/tmp/example",
        base_branch="main",
    )

    path = Path(result["path"])
    assert result["recommended_route"] is None
    assert "recommended-route" not in read_frontmatter(path)
    assert "recommended-route" not in path.read_text(encoding="utf-8")


def test_create_handoff_auto_links_high_confidence_candidate(tmp_path: Path):
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "20260101-000001-auth.md").write_text(
        "---\n"
        "id: 20260101-000001-auth\n"
        "focus: Fix broken auth dashboard access\n"
        "repo: /tmp/example\n"
        "status: queued\n"
        "---\n\n"
        "## Focus\n\nFix broken auth dashboard access\n",
        encoding="utf-8",
    )

    result = create_handoff(
        "Fix broken auth dashboard access",
        queue_base=tmp_path,
        repo="/tmp/example",
    )

    assert "20260101-000001-auth" in result["auto_linked"]
    new_path = Path(result["path"])
    assert "20260101-000001-auth" in read_frontmatter(new_path)["related"]


def test_create_handoff_normalizes_bare_repo_name_against_projects_home(
    tmp_path: Path, monkeypatch
):
    """A bare `--repo devops` must be captured as an absolute path -- a bare
    or `owner/name` value that survives to the queue file makes
    dashboard.py's auto_pick_brief() permanently skip the brief as
    repo-missing (bug: it only ever checked the literal value's is_dir())."""
    projects = tmp_path / "home" / "projects"
    (projects / "devops").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    result = create_handoff("Fix the thing", queue_base=tmp_path / "queue", repo="devops")

    assert read_frontmatter(Path(result["path"]))["repo"] == str((projects / "devops").resolve())


def test_create_handoff_normalizes_owner_slash_name_repo(tmp_path: Path, monkeypatch):
    projects = tmp_path / "home" / "projects"
    (projects / "devops").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    result = create_handoff(
        "Fix the thing", queue_base=tmp_path / "queue", repo="behindthedash/devops"
    )

    assert read_frontmatter(Path(result["path"]))["repo"] == str((projects / "devops").resolve())


def test_create_handoff_leaves_unresolvable_repo_value_unchanged(tmp_path: Path, monkeypatch):
    """No matching checkout anywhere -- normalization must not fabricate a
    path; leave the value as given (same as today) rather than guess wrong."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    result = create_handoff("Fix the thing", queue_base=tmp_path / "queue", repo="nonexistent-repo")

    assert read_frontmatter(Path(result["path"]))["repo"] == "nonexistent-repo"


def test_create_handoff_infers_repo_from_focus_project_prefix(tmp_path: Path, monkeypatch):
    """`repo: null` hides a brief from same-repo batch detection; a focus that
    opens with `<project>: ` names the checkout, so resolve it at capture."""
    projects = tmp_path / "home" / "projects"
    (projects / "datalena").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    result = create_handoff("datalena: add a CI guard for X", queue_base=tmp_path / "queue")

    assert read_frontmatter(Path(result["path"]))["repo"] == str((projects / "datalena").resolve())


def test_create_handoff_explicit_repo_wins_over_focus_prefix(tmp_path: Path, monkeypatch):
    projects = tmp_path / "home" / "projects"
    (projects / "datalena").mkdir(parents=True)
    (projects / "devops").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    result = create_handoff(
        "datalena: add a CI guard for X", queue_base=tmp_path / "queue", repo="devops"
    )

    assert read_frontmatter(Path(result["path"]))["repo"] == str((projects / "devops").resolve())


def test_create_handoff_focus_prefix_without_matching_checkout_stays_null(
    tmp_path: Path, monkeypatch
):
    """A prefix that is not a project under ~/projects (e.g. "Note: ...") must
    not be guessed into a repo."""
    (tmp_path / "home" / "projects").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    result = create_handoff("Note: fix the thing", queue_base=tmp_path / "queue")

    assert read_frontmatter(Path(result["path"]))["repo"] is None


def test_cli_emits_json_and_accepts_structured_fields(tmp_path: Path, capsys):
    assert main([
        "--focus", "Create a new OpenSpec handoff",
        "--queue-dir", str(tmp_path),
        "--change-kind", "new",
        "--json",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "created"
    assert read_frontmatter(Path(output["path"]))["change-kind"] == "new"


def test_create_handoff_writes_triage_frontmatter(tmp_path: Path):
    result = create_handoff("Fix the thing", queue_base=tmp_path, triage="blocker")
    text = Path(result["path"]).read_text()
    assert "triage: blocker" in text


def test_create_handoff_omits_triage_when_unset(tmp_path: Path):
    result = create_handoff("Fix the thing", queue_base=tmp_path)
    assert "triage:" not in Path(result["path"]).read_text()


def test_create_handoff_rejects_invalid_triage(tmp_path: Path):
    with pytest.raises(ValueError):
        create_handoff("Fix the thing", queue_base=tmp_path, triage="urgent")


def test_create_handoff_preserves_trimmed_blocked_by_order_and_prefixes(tmp_path: Path):
    result = create_handoff(
        "Fix the thing",
        queue_base=tmp_path,
        blocked_by=[" 20260701-000001-alpha ", "20260701-000002 "],
    )

    assert read_frontmatter(Path(result["path"]))["blocked-by"] == [
        "20260701-000001-alpha",
        "20260701-000002",
    ]


def test_cli_repeated_blocked_by_flags_preserve_order(tmp_path: Path, capsys):
    assert (
        main(
            [
                "--focus",
                "Create a handoff with ordered blocked-by refs",
                "--queue-dir",
                str(tmp_path),
                "--blocked-by",
                " 20260701-000001-alpha ",
                "--blocked-by",
                "20260701-000002",
                "--json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert read_frontmatter(Path(output["path"]))["blocked-by"] == [
        "20260701-000001-alpha",
        "20260701-000002",
    ]


def test_create_handoff_rejects_blank_blocked_by_without_touching_queue(tmp_path: Path):
    with pytest.raises(ValueError, match="blocked-by values must be non-empty dependency references"):
        create_handoff("Fix the thing", queue_base=tmp_path, blocked_by=["   "])

    assert not (tmp_path / "queue").exists()
    assert not list(tmp_path.rglob("*.md"))


def test_cli_rejects_comma_joined_blocked_by_with_actionable_guidance_and_no_queue_dir(
    tmp_path: Path, capsys
):
    assert (
        main(
            [
                "--focus",
                "Fix the thing",
                "--queue-dir",
                str(tmp_path),
                "--blocked-by",
                "dep-a,dep-b",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "blocked-by accepts exactly one dependency reference per flag" in captured.err
    assert "repeat --blocked-by for each prerequisite" in captured.err
    assert not (tmp_path / "queue").exists()
    assert not list(tmp_path.rglob("*.md"))


def test_create_handoff_leaves_existing_malformed_queue_brief_untouched_on_success(
    tmp_path: Path,
):
    queue = tmp_path / "queue"
    queue.mkdir()
    malformed = queue / "20260701-000001-broken.md"
    original = "---\nid: 20260701-000001-broken\nfocus: Broken brief\nstatus: queued\n"
    malformed.write_text(original, encoding="utf-8")

    result = create_handoff(
        "Fix the thing",
        queue_base=tmp_path,
        blocked_by=[" 20260701-000001-alpha ", "20260701-000002"],
    )

    assert malformed.read_text(encoding="utf-8") == original
    created = Path(result["path"])
    assert created != malformed
    assert read_frontmatter(created)["blocked-by"] == [
        "20260701-000001-alpha",
        "20260701-000002",
    ]


def test_create_handoff_body_omits_duplicate_focus_section(tmp_path: Path):
    # handoff 20260820-073044: frontmatter `focus:` is the sole source now;
    # the body no longer repeats it under a `## Focus` heading.
    result = create_handoff(
        "Fix the broken handoff dashboard",
        queue_base=tmp_path,
        approach="Reproduce and add a regression test.",
    )
    body = Path(result["path"]).read_text(encoding="utf-8")
    assert "## Focus" not in body
    assert "## Suggested approach" in body
    assert read_frontmatter(Path(result["path"]))["focus"] == "Fix the broken handoff dashboard"


def test_slugify_caps_character_length():
    long_focus = " ".join(["supercalifragilisticexpialidocious"] * 5)
    slug = _slugify(long_focus)
    assert len(slug) <= 60
    assert not slug.endswith("-")


def test_slugify_strips_possessive_apostrophe_instead_of_stray_token():
    slug = _slugify("ci-watch-loop.md's review-thread gate is unreachable")
    # "md's" tokenizes to the single word "md" (no stray "s" token wasting a
    # slot), so the 5-word budget reaches "review" instead of stopping at "s".
    assert slug == "ci-watch-loop-md-review"
    assert "-s-" not in f"-{slug}-"


def test_slugify_filters_single_character_tokens():
    slug = _slugify("a fix for the x y bug")
    words = slug.split("-")
    assert all(len(w) > 1 for w in words)
