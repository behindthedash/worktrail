"""Tests for workqueue/seed_backlog.py — proactive backlog → queue seeding."""

import json
import subprocess
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
        encoding="utf-8",
    )
    return spec_dir


def _mk_epic(
    repo: Path,
    epic_id: str,
    features: int,
    status: str = "Proposed",
    future_spec_ids: dict | None = None,
) -> Path:
    epics = repo / "docs" / "specs" / "epics"
    epics.mkdir(parents=True, exist_ok=True)
    body = [
        f"# Epic: {epic_id}",
        "",
        f"**Epic ID:** {epic_id}",
        f"**Status:** {status}",
        "",
        "## Feature Decomposition",
        "",
    ]
    future_spec_ids = future_spec_ids or {}
    for n in range(1, features + 1):
        body += [f"### Feature {n} — thing {n}", ""]
        if n in future_spec_ids:
            body += [f"**Future spec id:** `{future_spec_ids[n]}`", ""]
        body += [f"Feature {n} body.", ""]
    path = epics / f"{epic_id}.md"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _mk_citing_spec(repo: Path, spec_id: str, epic_id: str) -> None:
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        f"# Spec {spec_id}\n\nOwning epic: {epic_id}\n\n## Tasks note\n",
        encoding="utf-8",
    )
    tasks = spec_dir / "tasks"
    tasks.mkdir()
    (tasks / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: completed\n---\n\nDone.\n", encoding="utf-8"
    )


def _mk_citing_openspec_spec(repo: Path, slug: str, epic_id: str) -> None:
    spec_dir = repo / "openspec" / "specs" / slug
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        f"# {slug}\n\nOwning epic: {epic_id}\n\n## Purpose\n", encoding="utf-8"
    )


def _mk_citing_openspec_change(
    repo: Path, slug: str, epic_id: str, archived: bool = False
) -> None:
    base = repo / "openspec" / "changes"
    change_dir = (
        (base / "archive" / f"2026-08-12-{slug}") if archived else (base / slug)
    )
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        f"# {slug}\n\nOwning epic: {epic_id}\n\n## Why\n", encoding="utf-8"
    )


def _mk_citing_openspec_change_by_prose(
    repo: Path, slug: str, epic_number: str, feature_num: int
) -> None:
    """A change folder that cites its epic only via 'Epic <NNN> Feature <M>'
    prose, never the literal epic id string -- the shape a live epic-002
    change actually used (openspec/changes/work-queue-conservative-
    dependency-resolution/proposal.md)."""
    change_dir = repo / "openspec" / "changes" / slug
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        f"# {slug}\n\nEpic {epic_number} Feature {feature_num} adds this.\n",
        encoding="utf-8",
    )


def _mk_citing_openspec_change_by_future_spec_id(
    repo: Path, slug: str, future_spec_id: str
) -> None:
    """A change folder that cites its epic only via the feature's documented
    future spec id, never the literal epic id string or 'Epic N Feature M'
    prose."""
    change_dir = repo / "openspec" / "changes" / slug
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        f"# {slug}\n\nDelivers {future_spec_id}.\n", encoding="utf-8"
    )


def _mk_openspec_needs_tasks_change(repo: Path, slug: str) -> Path:
    """An OpenSpec change with a proposal but an empty tasks.md — reaches the
    `needs-tasks` dashboard stage (dashboard._safe_detect_openspec: `not tasks`)."""
    change_dir = repo / "openspec" / "changes" / slug
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        f"# {slug}\n\n## Why\ntest\n", encoding="utf-8"
    )
    (change_dir / "tasks.md").write_text("", encoding="utf-8")
    return change_dir


def _mk_openspec_ready_change(repo: Path, slug: str) -> Path:
    """An OpenSpec change with one pending, non-stale task — reaches the
    `ready-to-implement` dashboard stage."""
    change_dir = repo / "openspec" / "changes" / slug
    change_dir.mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        f"# {slug}\n\n## Why\ntest\n", encoding="utf-8"
    )
    (change_dir / "tasks.md").write_text(
        "## 1. Do it\n- [ ] 1.1 step\n", encoding="utf-8"
    )
    return change_dir


def _opt_in(repo: Path) -> None:
    worktrail_dir = repo / ".worktrail"
    worktrail_dir.mkdir(parents=True, exist_ok=True)
    (worktrail_dir / "policy.yaml").write_text(
        "allow_seeded_implementation: true\n", encoding="utf-8"
    )


def _mk_ready_spec(repo: Path, spec_id: str, task_id: str = "TASK-001") -> Path:
    """A spec with one pending impl task carrying no `files:` — reaches the
    `ready-to-implement` dashboard stage (the stale-bookkeeping/orchestrator-
    stuck probes both require `files:` or a fanout_failed sidecar to divert
    it elsewhere)."""
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        f"# Spec {spec_id}\n\nApproved spec body, no open questions.\n",
        encoding="utf-8",
    )
    tasks = spec_dir / "tasks"
    tasks.mkdir()
    (tasks / f"{task_id}.md").write_text(
        f"---\nid: {task_id}\nstatus: pending\nkind: impl\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    return spec_dir


def _mark_orchestrator_stuck(repos_root: Path, repo_name: str, spec_id: str) -> None:
    status_dir = repos_root / f"{repo_name}-worktrees"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / f"run-{spec_id}.status.json").write_text(
        json.dumps({"phase": "fanout_failed"}), encoding="utf-8"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _mk_stale_bookkeeping_spec(repo: Path, spec_id: str) -> Path:
    """A spec whose sole pending impl task's `files:` are already committed on
    the base branch — reaches `stale-bookkeeping`, not `ready-to-implement`."""
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(f"# Spec {spec_id}\n", encoding="utf-8")
    tasks = spec_dir / "tasks"
    tasks.mkdir()
    shipped = "src/thing.py"
    (tasks / "TASK-001.md").write_text(
        f"---\nid: TASK-001\nstatus: pending\nkind: impl\nfiles: [{shipped}]\n"
        "dependencies: []\n---\n\nDo it.\n",
        encoding="utf-8",
    )
    target = repo / shipped
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("shipped\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ship")
    return spec_dir


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
        repos_root, queue_base=qbase, log=lambda _m: None
    )

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
        "# Spec\n\n[NEEDS CLARIFICATION] which auth model?\n", encoding="utf-8"
    )

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_spec_with_tasks_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_citing_spec(repo, "012-gamma", "no-epic")  # has a tasks/ DAG

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_openspec_needs_tasks_change_seeds_planning_brief(tmp_path):
    """`find_needs_tasks_specs` scans openspec/ too: `dashboard.scan(repo /
    "docs" / "specs")` derives the repo root from that path and folds in
    OpenSpec change rows (dashboard.py's `scan`), even when `docs/specs`
    doesn't exist on disk at all."""
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_openspec_needs_tasks_change(repo, "013-delta-openspec")
    qbase = tmp_path / "wq"

    findings = seed_backlog.find_needs_tasks_specs(repos_root)
    assert [f["seed_key"] for f in findings] == ["repo-a:spec:013-delta-openspec"]
    assert findings[0]["spec_rel"] == "openspec/changes/013-delta-openspec"

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, log=lambda _m: None
    )
    assert [s["seed_key"] for s in summary["seeded"]] == [
        "repo-a:spec:013-delta-openspec"
    ]
    _path, fm = _queued_briefs(qbase)[0]
    assert fm["recommended-route"] == "C"
    assert fm["target-spec"] == "013-delta-openspec"


# ---------------------------------------------------------------------------
# epics


def test_epic_with_unspecced_features_seeds_brief(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=2)
    _mk_citing_spec(repo, "020-payments-core", "001-payments")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )

    assert [s["seed_key"] for s in summary["seeded"]] == [
        "repo-a:epic:001-payments:cited=1"
    ]
    _path, fm = _queued_briefs(tmp_path / "wq")[0]
    assert fm["recommended-route"] == "C"
    assert fm["implementation-intent"] == "planning-only"


def test_sequencing_gated_epic_logs_one_line_and_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(
        repo,
        "001-payments",
        features=3,
        future_spec_ids={1: "020-payments-core"},
    )
    epic_path = repo / "docs" / "specs" / "epics" / "001-payments.md"
    epic_path.write_text(
        epic_path.read_text(encoding="utf-8")
        + "\nFeature 2 depends on Feature 1's contract.\n",
        encoding="utf-8",
    )
    # cited=1 (unrelated spec citing the epic id) so next_n = 2, which the
    # appended prose above gates on Feature 1 (still unspecced -- open).
    _mk_citing_spec(repo, "030-unrelated", "001-payments")

    messages: list[str] = []
    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=messages.append
    )

    assert summary["seeded"] == []
    gated_lines = [m for m in messages if "epic-sequencing-gated" in m]
    assert len(gated_lines) == 1
    line = gated_lines[0]
    assert "001-payments" in line
    assert "Feature 2" in line
    assert "Feature 1" in line
    assert "020-payments-core" in line or "not yet specced" in line


def test_openspec_spec_citation_counts_toward_epic(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=1)
    _mk_citing_openspec_spec(repo, "payments-core", "001-payments")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_openspec_change_citation_counts_toward_epic(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=1)
    _mk_citing_openspec_change(repo, "payments-core", "001-payments")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_openspec_archived_change_citation_counts_toward_epic(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=1)
    _mk_citing_openspec_change(repo, "payments-core", "001-payments", archived=True)

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_mixed_format_citations_are_not_undercounted(tmp_path):
    # Regression for the live 2026-08-15 devops incident: an epic whose
    # features ship across both spec formats must not be re-seeded just
    # because the OpenSpec-shipped features are invisible to the scan.
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=2)
    _mk_citing_spec(repo, "020-payments-core", "001-payments")
    _mk_citing_openspec_change(repo, "payments-refunds", "001-payments", archived=True)

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_epic_number_feature_prose_citation_counts_toward_epic(tmp_path):
    # Regression for the live 2026-08-19 false-stale incident: brief
    # 20260819-021834-epic-002-safe-work-queue claimed "only 1 spec cites it"
    # for epic 002 even though Feature 2's change (work-queue-conservative-
    # dependency-resolution) was already implemented and merged -- its
    # proposal.md cites the epic only via "Epic 002 Feature 2" prose, never
    # the literal epic id string.
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "002-payments", features=2)
    _mk_citing_spec(repo, "020-payments-core", "002-payments")
    _mk_citing_openspec_change_by_prose(repo, "payments-refunds", "002", 2)

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_future_spec_id_citation_counts_toward_epic(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(
        repo, "001-payments", features=1, future_spec_ids={1: "payments-core-ledger"}
    )
    _mk_citing_openspec_change_by_future_spec_id(
        repo, "payments-ledger", "payments-core-ledger"
    )

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_bare_epic_number_mention_is_not_a_citation(tmp_path):
    # A bare mention of the epic's leading number, with no "Epic"/"Feature"
    # framing and no literal epic id or future-spec-id, must not count --
    # otherwise any unrelated "002" mention (e.g. "see PR 002") would count.
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "002-payments", features=1)
    spec_dir = repo / "docs" / "specs" / "020-unrelated"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(
        "# Spec 020-unrelated\n\nSee PR 002 open items.\n", encoding="utf-8"
    )

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert "repo-a:epic:002-payments:cited=0" in [
        s["seed_key"] for s in summary["seeded"]
    ]


def test_fully_cited_epic_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "001-payments", features=1)
    _mk_citing_spec(repo, "020-payments-core", "001-payments")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_terminal_status_epic_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_epic(repo, "002-legacy", features=3, status="Completed")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_epic_without_feature_headings_reported_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    epics = repo / "docs" / "specs" / "epics"
    epics.mkdir(parents=True)
    (epics / "003-freeform.md").write_text(
        "# Epic\n\n**Status:** Proposed\n\nProse only, no decomposition.\n",
        encoding="utf-8",
    )

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []
    assert summary["unparseable_epics"] == [{"repo": "repo-a", "id": "003-freeform"}]


def test_non_epic_files_in_epics_dir_ignored(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    epics = repo / "docs" / "specs" / "epics"
    epics.mkdir(parents=True)
    (epics / "README.md").write_text("# Index\n", encoding="utf-8")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []
    assert summary["unparseable_epics"] == []


# ---------------------------------------------------------------------------
# ready-to-implement (Route D) — find_ready_specs / seeded-implementation gate


def test_find_ready_specs_without_optin_returns_nothing(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_ready_spec(repo, "030-delta")

    assert seed_backlog.find_ready_specs(repos_root) == []


def test_ready_spec_without_optin_is_not_seeded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_ready_spec(repo, "030-delta")

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert summary["seeded"] == []


def test_ready_spec_optin_seeds_route_d_brief(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _opt_in(repo)
    _mk_ready_spec(repo, "030-delta")
    qbase = tmp_path / "wq"

    findings = seed_backlog.find_ready_specs(repos_root)
    assert [f["seed_key"] for f in findings] == ["repo-a:impl:030-delta"]
    assert findings[0]["kind"] == "ready-to-implement"

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, log=lambda _m: None
    )
    assert [s["seed_key"] for s in summary["seeded"]] == ["repo-a:impl:030-delta"]
    _path, fm = _queued_briefs(qbase)[0]
    assert fm["seeded-from"] == "repo-a:impl:030-delta"
    assert fm["recommended-route"] == "D"
    assert fm["implementation-intent"] == "requested"
    assert fm["target-spec"] == "030-delta"
    assert fm["repo"] == "repo-a"
    assert fm["status"] == "queued"


def test_openspec_ready_change_optin_seeds_route_d_brief(tmp_path):
    """`find_ready_specs` scans openspec/ too, same mechanism as
    `find_needs_tasks_specs` above."""
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _opt_in(repo)
    _mk_openspec_ready_change(repo, "031-echo-openspec")
    qbase = tmp_path / "wq"

    findings = seed_backlog.find_ready_specs(repos_root)
    assert [f["seed_key"] for f in findings] == ["repo-a:impl:031-echo-openspec"]
    assert findings[0]["spec_rel"] == "openspec/changes/031-echo-openspec"

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, log=lambda _m: None
    )
    assert [s["seed_key"] for s in summary["seeded"]] == [
        "repo-a:impl:031-echo-openspec"
    ]
    _path, fm = _queued_briefs(qbase)[0]
    assert fm["recommended-route"] == "D"
    assert fm["target-spec"] == "031-echo-openspec"


def test_stale_bookkeeping_spec_is_excluded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _opt_in(repo)
    _mk_stale_bookkeeping_spec(repo, "031-echo")

    assert seed_backlog.find_ready_specs(repos_root) == []


def test_orchestrator_stuck_spec_is_excluded(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _opt_in(repo)
    _mk_ready_spec(repo, "032-foxtrot")
    _mark_orchestrator_stuck(repos_root, "repo-a", "032-foxtrot")

    assert seed_backlog.find_ready_specs(repos_root) == []


def test_ready_spec_done_brief_still_blocks_reseed(tmp_path):
    """The seed key for a ready-to-implement spec is stable (no cited-count
    suffix like the epic finder), so more pending work never re-arms it."""
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _opt_in(repo)
    spec_dir = _mk_ready_spec(repo, "030-delta")
    qbase = tmp_path / "wq"
    seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)

    picked = qbase / "picked"
    picked.mkdir()
    brief = next((qbase / "queue").glob("*.md"))
    moved = picked / brief.name
    moved.write_text(
        brief.read_text(encoding="utf-8").replace("status: queued", "status: done"),
        encoding="utf-8",
    )
    brief.unlink()
    (spec_dir / "tasks" / "TASK-002.md").write_text(
        "---\nid: TASK-002\nstatus: pending\nkind: impl\n---\n\nMore.\n",
        encoding="utf-8",
    )

    again = seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)
    assert again["seeded"] == []
    assert again["skipped_existing"] == 1


def test_ready_spec_dry_run_creates_nothing(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _opt_in(repo)
    _mk_ready_spec(repo, "030-delta")
    qbase = tmp_path / "wq"

    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, dry_run=True, log=lambda _m: None
    )
    assert len(summary["seeded"]) == 1
    assert summary["seeded"][0]["brief_id"] is None
    assert not (qbase / "queue").is_dir() or not list((qbase / "queue").glob("*.md"))


def test_ready_spec_go_repo_restricts_scan(tmp_path):
    repos_root = tmp_path / "projects"
    repo_a = _mk_repo(repos_root, "repo-a")
    _opt_in(repo_a)
    _mk_ready_spec(repo_a, "030-delta")
    repo_b = _mk_repo(repos_root, "repo-b")
    _opt_in(repo_b)
    _mk_ready_spec(repo_b, "040-echo")

    summary = seed_backlog.seed_backlog(
        repos_root, go_repo="repo-b", queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert [s["repo"] for s in summary["seeded"]] == ["repo-b"]


def test_cap_defers_across_needs_tasks_epic_and_ready(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _opt_in(repo)
    _mk_needs_tasks_spec(repo, "010-alpha")
    _mk_epic(repo, "001-payments", features=1)
    _mk_ready_spec(repo, "030-delta")
    qbase = tmp_path / "wq"

    logs = []
    summary = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, max_seeds=2, log=logs.append
    )
    assert len(summary["seeded"]) == 2
    assert summary["dropped_over_cap"] == 1
    assert any("deferred to the next sweep" in line for line in logs)
    # deterministic order: needs-tasks first, then epics, then ready-to-implement
    assert [s["kind"] for s in summary["seeded"]] == ["needs-tasks", "epic"]

    second = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, log=lambda _m: None
    )
    assert [s["kind"] for s in second["seeded"]] == ["ready-to-implement"]
    assert [s["seed_key"] for s in second["seeded"]] == ["repo-a:impl:030-delta"]


# ---------------------------------------------------------------------------
# dedup + progression


def test_second_run_never_reseeds_same_key(tmp_path):
    repos_root = tmp_path / "projects"
    repo = _mk_repo(repos_root, "repo-a")
    _mk_needs_tasks_spec(repo, "010-alpha")
    qbase = tmp_path / "wq"

    first = seed_backlog.seed_backlog(repos_root, queue_base=qbase, log=lambda _m: None)
    assert len(first["seeded"]) == 1
    second = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, log=lambda _m: None
    )
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
        brief.read_text(encoding="utf-8").replace("status: queued", "status: done"),
        encoding="utf-8",
    )
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
        "repo-a:epic:001-payments:cited=0"
    ]

    _mk_citing_spec(repo, "020-payments-core", "001-payments")
    second = seed_backlog.seed_backlog(
        repos_root, queue_base=qbase, log=lambda _m: None
    )
    assert [s["seed_key"] for s in second["seeded"]] == [
        "repo-a:epic:001-payments:cited=1"
    ]


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
        repos_root, queue_base=qbase, max_seeds=1, log=logs.append
    )
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
        repos_root, queue_base=qbase, dry_run=True, log=lambda _m: None
    )
    assert len(summary["seeded"]) == 1
    assert summary["seeded"][0]["brief_id"] is None
    assert not (qbase / "queue").is_dir() or not list((qbase / "queue").glob("*.md"))


def test_go_repo_restricts_scan(tmp_path):
    repos_root = tmp_path / "projects"
    _mk_needs_tasks_spec(_mk_repo(repos_root, "repo-a"), "010-alpha")
    _mk_needs_tasks_spec(_mk_repo(repos_root, "repo-b"), "020-other")

    summary = seed_backlog.seed_backlog(
        repos_root, go_repo="repo-b", queue_base=tmp_path / "wq", log=lambda _m: None
    )
    assert [s["repo"] for s in summary["seeded"]] == ["repo-b"]


# ---------------------------------------------------------------------------
# CLI


def test_cli_json_output(tmp_path, capsys):
    repos_root = tmp_path / "projects"
    _mk_needs_tasks_spec(_mk_repo(repos_root, "repo-a"), "010-alpha")

    rc = seed_backlog.main(
        [
            "--repos-root",
            str(repos_root),
            "--queue-dir",
            str(tmp_path / "wq"),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["seeded"]) == 1


def test_cli_missing_repos_root_fails(tmp_path, capsys):
    rc = seed_backlog.main(["--repos-root", str(tmp_path / "nope")])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err
