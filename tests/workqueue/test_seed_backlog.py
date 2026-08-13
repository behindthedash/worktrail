"""Tests for workqueue/seed_backlog.py — proactive backlog → queue seeding."""

import json
from pathlib import Path

from worktrail.shared.brief_frontmatter import split_frontmatter
from worktrail.workqueue import seed_backlog


# ---------------------------------------------------------------------------
# Fixtures


def _mk_repo(repos_root: Path, name: str) -> Path:
    repo = repos_root / name
    (repo / ".git").mkdir(parents=True)
    return repo


def _mk_needs_tasks_spec(repo: Path, spec_id: str) -> Path:
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        f"# Spec {spec_id}\n\nApproved spec body, no open questions.\n",
        encoding="utf-8")
    return spec_dir


def _mk_epic(repo: Path, epic_id: str, features: int,
             status: str = "Proposed") -> Path:
    epics = repo / "docs" / "specs" / "epics"
    epics.mkdir(parents=True, exist_ok=True)
    body = [f"# Epic: {epic_id}", "", f"**Epic ID:** {epic_id}",
            f"**Status:** {status}", "", "## Feature Decomposition", ""]
    for n in range(1, features + 1):
        body += [f"### Feature {n} — thing {n}", "", f"Feature {n} body.", ""]
    path = epics / f"{epic_id}.md"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _mk_citing_spec(repo: Path, spec_id: str, epic_id: str) -> None:
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        f"# Spec {spec_id}\n\nOwning epic: {epic_id}\n\n## Tasks note\n",
        encoding="utf-8")
    tasks = spec_dir / "tasks"
    tasks.mkdir()
    (tasks / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: completed\n---\n\nDone.\n", encoding="utf-8")


def _queued_briefs(queue_base: Path):
    out = []
    for md in sorted((queue_base / "queue").glob("*.md")):
        fm, _body = split_frontmatter(md.read_text(encoding="utf-8"))
        out.append((md, fm))
    return out


# ---------------------------------------------------------------------------
# needs-tasks specs


def test_needs_tasks_spec_seeds_planning_brief(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_needs_tasks_spec(repo, "010-alpha")
    qbase = tmp_path / "wq"

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, log=lambda _m: None)

    assert [s["seed_key"] for s in summary["seeded"]] == ["repo-a:spec:010-alpha"]
    briefs = _queued_briefs(qbase)
    assert len(briefs) == 1
    _path, fm = briefs[0]
    assert fm["seeded-from"] == "repo-a:spec:010-alpha"
    assert fm["recommended-route"] == "C"
    assert fm["implementation-intent"] == "planning-only"
    assert fm["target-spec"] == "010-alpha"
    assert fm["repo"] == "repo-a"
    assert fm["status"] == "queued"


def test_needs_clarification_spec_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    spec_dir = _mk_needs_tasks_spec(repo, "011-beta")
    (spec_dir / "spec.md").write_text(
        "# Spec\n\n[NEEDS CLARIFICATION] which auth model?\n", encoding="utf-8")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None)
    assert summary["seeded"] == []


def test_spec_with_tasks_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_citing_spec(repo, "012-gamma", "no-epic")  # has a tasks/ DAG

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None)
    assert summary["seeded"] == []


# ---------------------------------------------------------------------------
# epics


def test_epic_with_unspecced_features_seeds_brief(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=2)
    _mk_citing_spec(repo, "020-payments-core", "001-payments")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None)

    assert [s["seed_key"] for s in summary["seeded"]] == [
        "repo-a:epic:001-payments:cited=1"]
    _path, fm = _queued_briefs(tmp_path / "wq")[0]
    assert fm["recommended-route"] == "C"
    assert fm["implementation-intent"] == "planning-only"


def test_fully_cited_epic_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=1)
    _mk_citing_spec(repo, "020-payments-core", "001-payments")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None)
    assert summary["seeded"] == []


def test_terminal_status_epic_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "002-legacy", features=3, status="Completed")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None)
    assert summary["seeded"] == []


def test_epic_without_feature_headings_reported_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    epics = repo / "docs" / "specs" / "epics"
    epics.mkdir(parents=True)
    (epics / "003-freeform.md").write_text(
        "# Epic\n\n**Status:** Proposed\n\nProse only, no decomposition.\n",
        encoding="utf-8")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None)
    assert summary["seeded"] == []
    assert summary["unparseable_epics"] == [{"repo": "repo-a", "id": "003-freeform"}]


def test_non_epic_files_in_epics_dir_ignored(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    epics = repo / "docs" / "specs" / "epics"
    epics.mkdir(parents=True)
    (epics / "README.md").write_text("# Index\n", encoding="utf-8")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None)
    assert summary["seeded"] == []
    assert summary["unparseable_epics"] == []


# ---------------------------------------------------------------------------
# dedup + progression


def test_second_run_never_reseeds_same_key(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_needs_tasks_spec(repo, "010-alpha")
    qbase = tmp_path / "wq"

    first = seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)
    assert len(first["seeded"]) == 1
    second = seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)
    assert second["seeded"] == []
    assert second["skipped_existing"] == 1


def test_done_brief_still_blocks_reseed_of_same_key(tmp_path):
    """A finished-but-fruitless seeded brief must not loop a one-shot per
    drain against a stage the previous one failed to clear."""
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_needs_tasks_spec(repo, "010-alpha")
    qbase = tmp_path / "wq"
    seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)

    # Simulate claim+done: move to picked/ with status done, stage unchanged.
    picked = qbase / "picked"
    picked.mkdir()
    brief = next((qbase / "queue").glob("*.md"))
    moved = picked / brief.name
    moved.write_text(
        brief.read_text(encoding="utf-8").replace(
            "status: queued", "status: done"),
        encoding="utf-8")
    brief.unlink()

    again = seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)
    assert again["seeded"] == []
    assert again["skipped_existing"] == 1


def test_epic_progress_rearms_seeding(tmp_path):
    """A new citing spec changes the epic's seed key, so the next sweep seeds
    the next feature even though the previous key is exhausted."""
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=3)
    qbase = tmp_path / "wq"

    first = seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)
    assert [s["seed_key"] for s in first["seeded"]] == [
        "repo-a:epic:001-payments:cited=0"]

    _mk_citing_spec(repo, "020-payments-core", "001-payments")
    second = seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)
    assert [s["seed_key"] for s in second["seeded"]] == [
        "repo-a:epic:001-payments:cited=1"]


# ---------------------------------------------------------------------------
# cap, dry-run, repo restriction


def test_cap_defers_and_reports(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_needs_tasks_spec(repo, "010-alpha")
    _mk_needs_tasks_spec(repo, "011-beta")
    qbase = tmp_path / "wq"

    logs = []
    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, max_seeds=1, log=logs.append)
    assert len(summary["seeded"]) == 1
    assert summary["dropped_over_cap"] == 1
    assert any("deferred to the next sweep" in line for line in logs)
    # deterministic order: lowest spec id first
    assert summary["seeded"][0]["id"] == "010-alpha"


def test_dry_run_creates_nothing(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_needs_tasks_spec(repo, "010-alpha")
    qbase = tmp_path / "wq"

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, dry_run=True, log=lambda _m: None)
    assert len(summary["seeded"]) == 1
    assert summary["seeded"][0]["brief_id"] is None
    assert not (qbase / "queue").is_dir() or not list((qbase / "queue").glob("*.md"))


def test_go_repo_restricts_scan(tmp_path):
    repos_root = tmp_path / "projects"
    _mk_needs_tasks_spec(_mk_repo(repos_root, "repo-a"), "010-alpha")
    _mk_needs_tasks_spec(_mk_repo(repos_root, "repo-b"), "020-other")

    summary = seed_backlog.seed_backlog(
        repos_root, go_repo="repo-b", queue_base=tmp_path / "wq",
        log=lambda _m: None)
    assert [s["repo"] for s in summary["seeded"]] == ["repo-b"]


# ---------------------------------------------------------------------------
# CLI


def test_cli_json_output(tmp_path, capsys):
    repos_root = tmp_path / "projects"
    _mk_needs_tasks_spec(_mk_repo(repos_root, "repo-a"), "010-alpha")

    rc = seed_backlog.main([
        "--repos-root", str(repos_root),
        "--queue-dir", str(tmp_path / "wq"),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["seeded"]) == 1


def test_cli_missing_repos_root_fails(tmp_path, capsys):
    rc = seed_backlog.main(["--repos-root", str(tmp_path / "nope")])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err
