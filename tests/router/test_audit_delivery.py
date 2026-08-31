"""Unit + real-git integration tests for router/audit_delivery.py.

Delivery verification (`verify_delivery`) is the correctness-critical part of
this module -- a mocked `subprocess` would happily pass while the real `git
merge-base --is-ancestor`/`cat-file -e` invocations are wrong, so those paths
run against real tmp_path git repos rather than faked subprocess output
(mirrors the `tests/router/test_pre_pr_gate.py` real-git convention)."""

import json
import subprocess
from pathlib import Path

from worktrail.router import audit_delivery as audit


def _run(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    return path


def _commit(repo: Path, filename: str, content: str) -> str:
    (repo / filename).parent.mkdir(parents=True, exist_ok=True)
    (repo / filename).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", f"add {filename}"], check=True
    )
    return _run(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# extract_passed_tasks


def test_extract_passed_tasks_picks_last_passed_review_entry():
    journal = {
        "entries": [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "FAILED", "head_sha": "aaa"},
            },
            {
                "role": "review",
                "task": "1.1",
                "started_at": 2,
                "report": {"review_status": "PASSED", "head_sha": "bbb"},
            },
        ]
    }

    tasks = audit.extract_passed_tasks(journal)

    assert tasks == [{"task": "1.1", "head_sha": "bbb", "started_at": 2}]


def test_extract_passed_tasks_skips_task_with_no_passed_review():
    journal = {
        "entries": [
            {"role": "implement", "task": "1.1", "report": {"head_sha": "aaa"}},
            {
                "role": "review",
                "task": "1.1",
                "report": {"review_status": "FAILED", "head_sha": "aaa"},
            },
        ]
    }

    assert audit.extract_passed_tasks(journal) == []


def test_extract_passed_tasks_ignores_non_review_roles():
    journal = {
        "entries": [
            {
                "role": "cleanup",
                "task": "1.1",
                "report": {"review_status": "PASSED", "head_sha": "should-not-count"},
            }
        ]
    }

    assert audit.extract_passed_tasks(journal) == []


# ---------------------------------------------------------------------------
# discover_journals / load_journal


def test_discover_journals_excludes_status_and_prior_siblings(tmp_path):
    wt_dir = tmp_path / "repo-worktrees"
    wt_dir.mkdir()
    (wt_dir / "run-a.json").write_text("{}")
    (wt_dir / "run-a.status.json").write_text("{}")
    (wt_dir / "run-a.prior.json").write_text("{}")
    (wt_dir / "run-b.json").write_text("{}")

    found = sorted(p.name for p in audit.discover_journals(wt_dir))

    assert found == ["run-a.json", "run-b.json"]


def test_discover_journals_missing_dir_returns_empty(tmp_path):
    assert audit.discover_journals(tmp_path / "nope") == []


def test_discover_journals_finds_nested_per_spec_journal(tmp_path):
    """A spec's own `new`/`modify` pipeline run journal lives one level down,
    under `<repo>-worktrees/<slug>-worktrees/run-<slug>.json` -- not only
    directly under `<repo>-worktrees/`."""
    wt_dir = tmp_path / "repo-worktrees"
    nested = wt_dir / "auto-dod-verification-worktrees"
    nested.mkdir(parents=True)
    (nested / "run-auto-dod-verification.json").write_text("{}")
    (wt_dir / "run-a.json").write_text("{}")

    found = sorted(p.name for p in audit.discover_journals(wt_dir))

    assert found == ["run-a.json", "run-auto-dod-verification.json"]


def test_discover_journals_excludes_runplans_cache_by_schema(tmp_path):
    """`conductor/compile.py`'s cached RunPlan files also match `run-*.json`
    but carry no `entries` -- excluded by `load_journal`'s schema check, not
    a `runplans/`-path special case in `discover_journals` itself."""
    wt_dir = tmp_path / "repo-worktrees"
    runplans = wt_dir / "runplans"
    runplans.mkdir(parents=True)
    runplan_path = runplans / "run-slug-abc123.json"
    runplan_path.write_text(json.dumps({"spec_id": "slug", "tasks": []}))

    assert runplan_path in audit.discover_journals(wt_dir)
    assert audit.load_journal(runplan_path) is None


def test_load_journal_returns_none_on_corrupt_json(tmp_path):
    bad = tmp_path / "run-x.json"
    bad.write_text("{not json")

    assert audit.load_journal(bad) is None


def test_load_journal_returns_none_without_entries_key(tmp_path):
    path = tmp_path / "run-x.json"
    path.write_text(json.dumps({"spec_id": "x", "tasks": []}))

    assert audit.load_journal(path) is None


# ---------------------------------------------------------------------------
# resolve_canonical_repo


def test_resolve_canonical_repo_strips_worktrees_suffix(tmp_path):
    wt_dir = tmp_path / "myrepo-worktrees"

    assert audit.resolve_canonical_repo(wt_dir) == tmp_path / "myrepo"


# ---------------------------------------------------------------------------
# verify_delivery / touched_files -- real git repos


def test_verify_delivery_delivered_when_sha_is_ancestor(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    sha = _commit(repo, "a.txt", "one")
    _commit(repo, "b.txt", "two")

    assert audit.verify_delivery(repo, "main", sha) == "delivered"


def test_verify_delivery_confirmed_dropped_when_object_present_not_ancestor(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    dropped_sha = _commit(repo, "dropped.txt", "never merged")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    assert audit.verify_delivery(repo, "main", dropped_sha) == "confirmed_dropped"


def test_verify_delivery_unverifiable_when_object_missing(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")

    assert (
        audit.verify_delivery(repo, "main", "0" * 40)
        == "unverifiable"
    )


def test_touched_files_lists_paths_from_commit(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    sha = _commit(repo, "a.txt", "one")

    assert audit.touched_files(repo, sha) == ["a.txt"]


def test_touched_files_empty_for_unknown_sha(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")

    assert audit.touched_files(repo, "0" * 40) == []


# ---------------------------------------------------------------------------
# shippable_files


def test_shippable_files_excludes_review_scratch_and_compile_marker():
    files = [
        "src/a.py",
        "docs/specs/003-x/reviews/TASK-1-review.md",
        "openspec/changes/y/.compile-ok",
        "tests/test_a.py",
    ]

    assert audit.shippable_files(files) == ["src/a.py", "tests/test_a.py"]


def test_shippable_files_no_filtering_when_nothing_matches():
    files = ["src/a.py", "tests/test_a.py"]

    assert audit.shippable_files(files) == files


# ---------------------------------------------------------------------------
# content_delivered_via_rewrite


def test_content_delivered_via_rewrite_true_when_blob_matches_a_base_commit(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(repo, "b.txt", "squashed content")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    # The squash-merge rewrites the same content under a new sha.
    (repo / "b.txt").write_text("squashed content")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "squash: base 1.1"], check=True
    )

    assert audit.content_delivered_via_rewrite(repo, "main", task_sha, ["b.txt"]) is True


def test_content_delivered_via_rewrite_false_when_content_never_lands(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(repo, "dropped.txt", "never merged")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    assert (
        audit.content_delivered_via_rewrite(repo, "main", task_sha, ["dropped.txt"])
        is False
    )


def test_content_delivered_via_rewrite_false_when_only_some_files_match(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    (repo / "b.txt").write_text("delivered")
    (repo / "c.txt").write_text("never delivered")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt", "c.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "task"], check=True)
    task_sha = _run(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    (repo / "b.txt").write_text("delivered")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "squash: b only"], check=True)

    assert (
        audit.content_delivered_via_rewrite(repo, "main", task_sha, ["b.txt", "c.txt"])
        is False
    )


def test_content_delivered_via_rewrite_true_when_file_further_edited_after_landing(
    tmp_path,
):
    """Two tasks squashed into the same commit both touch pyproject.toml;
    later commits keep editing it (version bumps). Task 1's added line must
    still be recognized as delivered even though the file no longer
    byte-matches task 1's isolated diff."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "pyproject.toml").write_text("version = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    (repo / "pyproject.toml").write_text("version = 1\nfoo = task1\n")
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "task 1"], check=True)
    task_sha = _run(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    # Squash commit combines task 1's addition with a second task's edit.
    (repo / "pyproject.toml").write_text("version = 1\nfoo = task1\nbar = task2\n")
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "squash: 1, 2"], check=True)
    # A later, unrelated version bump keeps editing the same file.
    (repo / "pyproject.toml").write_text("version = 2\nfoo = task1\nbar = task2\n")
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "bump version"], check=True)

    assert (
        audit.content_delivered_via_rewrite(repo, "main", task_sha, ["pyproject.toml"])
        is True
    )


def test_content_delivered_via_rewrite_true_when_one_line_later_touched_up(tmp_path):
    """A delivered task whose file gets one later incidental edit (e.g. a
    follow-up commit reworded one assertion) must still count as delivered --
    reproduces the live worktrail sample (task 3.2,
    backlog-seeding-epic-sequencing-gate: 29/30 added lines intact)."""
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    lines = [f"line{i}" for i in range(10)]
    (repo / "b.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "task"], check=True)
    task_sha = _run(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    # Squash-lands the file, then one later commit tweaks a single line.
    lines[0] = "line0-reworded"
    (repo / "b.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "squash + touch-up"], check=True)

    assert audit.content_delivered_via_rewrite(repo, "main", task_sha, ["b.txt"]) is True


def test_content_delivered_via_rewrite_false_when_most_lines_differ(tmp_path):
    """Only a minority of added lines surviving is not "lightly touched up" --
    it stays a genuine not-delivered candidate."""
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    lines = [f"line{i}" for i in range(10)]
    (repo / "b.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "task"], check=True)
    task_sha = _run(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    other_lines = [f"other{i}" for i in range(10)]
    (repo / "b.txt").write_text("\n".join(other_lines) + "\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "different content"], check=True)

    assert audit.content_delivered_via_rewrite(repo, "main", task_sha, ["b.txt"]) is False


def test_content_delivered_via_rewrite_false_with_no_files():
    assert (
        audit.content_delivered_via_rewrite(Path("/nonexistent"), "main", "abc", [])
        is False
    )


# ---------------------------------------------------------------------------
# identifiers_survive_elsewhere


def test_identifiers_survive_elsewhere_true_when_module_renamed(tmp_path):
    """The task's file was renamed/reorganized during later implementation;
    the same function definition now lives under a different path."""
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(
        repo,
        "src/stale_bookkeeping_check.py",
        "def check_stale_bookkeeping(repo):\n    return True\n",
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    _commit(
        repo,
        "src/sweep_check.py",
        "def check_stale_bookkeeping(repo):\n    return True\n",
    )

    assert (
        audit.identifiers_survive_elsewhere(
            repo, "main", task_sha, ["src/stale_bookkeeping_check.py"]
        )
        is True
    )


def test_identifiers_survive_elsewhere_false_when_identifier_absent(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(
        repo, "src/x.py", "def truly_dropped_function(repo):\n    return True\n"
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    assert (
        audit.identifiers_survive_elsewhere(repo, "main", task_sha, ["src/x.py"]) is False
    )


def test_identifiers_survive_elsewhere_false_with_no_extractable_identifiers(tmp_path):
    """A config/data-only diff has no def/class-shaped additions -- silence
    is never treated as survival."""
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(repo, "config.yaml", "key: value\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    assert (
        audit.identifiers_survive_elsewhere(repo, "main", task_sha, ["config.yaml"]) is False
    )


def test_identifiers_survive_elsewhere_requires_all_identifiers_present(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(
        repo,
        "src/x.py",
        "def function_one(repo):\n    return True\n\n\n"
        "def function_two_missing(repo):\n    return False\n",
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    _commit(repo, "src/y.py", "def function_one(repo):\n    return True\n")

    assert (
        audit.identifiers_survive_elsewhere(repo, "main", task_sha, ["src/x.py"]) is False
    )


# ---------------------------------------------------------------------------
# resolve_base_ref -- real remote


def test_resolve_base_ref_reads_head_branch_from_remote(tmp_path):
    upstream = _init_repo(tmp_path / "upstream")
    _commit(upstream, "a.txt", "one")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(clone)], check=True
    )

    assert audit.resolve_base_ref(clone) == "origin/main"


def test_resolve_base_ref_none_when_no_remote(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    _commit(repo, "a.txt", "one")

    assert audit.resolve_base_ref(repo) is None


# ---------------------------------------------------------------------------
# audit_repo end-to-end


def _write_journal(wt_dir: Path, name: str, run_id: str, entries: list) -> None:
    wt_dir.mkdir(parents=True, exist_ok=True)
    (wt_dir / name).write_text(
        json.dumps({"run_id": run_id, "spec_id": "001-example", "entries": entries})
    )


def test_audit_repo_flags_confirmed_dropped_task(tmp_path):
    repo = _init_repo(tmp_path / "myrepo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    dropped_sha = _commit(repo, "dropped.txt", "never merged")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    wt_dir = tmp_path / "myrepo-worktrees"
    _write_journal(
        wt_dir,
        "run-full-1.json",
        "full-1",
        [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "PASSED", "head_sha": dropped_sha},
            }
        ],
    )

    result = audit.audit_repo("myrepo", wt_dir, base_ref="main")

    assert result["journals_scanned"] == 1
    assert result["tasks_checked"] == 1
    assert len(result["confirmed_dropped"]) == 1
    finding = result["confirmed_dropped"][0]
    assert finding["run_id"] == "full-1"
    assert finding["task"] == "1.1"
    assert finding["head_sha"] == dropped_sha
    assert finding["files"] == ["dropped.txt"]
    assert result["unverifiable"] == []


def test_audit_repo_buckets_squash_rewritten_task_separately_from_dropped(tmp_path):
    repo = _init_repo(tmp_path / "myrepo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(repo, "b.txt", "squashed content")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    (repo / "b.txt").write_text("squashed content")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "squash: base 1.1"], check=True
    )

    wt_dir = tmp_path / "myrepo-worktrees"
    _write_journal(
        wt_dir,
        "run-full-1.json",
        "full-1",
        [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "PASSED", "head_sha": task_sha},
            }
        ],
    )

    result = audit.audit_repo("myrepo", wt_dir, base_ref="main")

    assert result["confirmed_dropped"] == []
    assert len(result["content_delivered_via_rewrite"]) == 1
    assert result["content_delivered_via_rewrite"][0]["task"] == "1.1"


def test_audit_repo_buckets_policy_excluded_only_file_separately(tmp_path):
    repo = _init_repo(tmp_path / "myrepo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    task_sha = _commit(
        repo, "docs/specs/x/reviews/TASK-1-review.md", "review scratch, never shipped"
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    wt_dir = tmp_path / "myrepo-worktrees"
    _write_journal(
        wt_dir,
        "run-full-1.json",
        "full-1",
        [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "PASSED", "head_sha": task_sha},
            }
        ],
    )

    result = audit.audit_repo("myrepo", wt_dir, base_ref="main")

    assert result["confirmed_dropped"] == []
    assert result["content_delivered_via_rewrite"] == []
    assert len(result["never_shipped_by_policy"]) == 1
    assert result["never_shipped_by_policy"][0]["task"] == "1.1"


def test_audit_repo_no_findings_when_task_delivered(tmp_path):
    repo = _init_repo(tmp_path / "myrepo")
    _commit(repo, "a.txt", "one")
    sha = _commit(repo, "b.txt", "two")

    wt_dir = tmp_path / "myrepo-worktrees"
    _write_journal(
        wt_dir,
        "run-full-1.json",
        "full-1",
        [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "PASSED", "head_sha": sha},
            }
        ],
    )

    result = audit.audit_repo("myrepo", wt_dir, base_ref="main")

    assert result["confirmed_dropped"] == []
    assert result["unverifiable"] == []
    assert result["tasks_checked"] == 1


def test_audit_repo_reports_unverifiable_never_as_confirmed_dropped(tmp_path):
    repo = _init_repo(tmp_path / "myrepo")
    _commit(repo, "a.txt", "one")

    wt_dir = tmp_path / "myrepo-worktrees"
    _write_journal(
        wt_dir,
        "run-full-1.json",
        "full-1",
        [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "PASSED", "head_sha": "f" * 40},
            }
        ],
    )

    result = audit.audit_repo("myrepo", wt_dir, base_ref="main")

    assert result["confirmed_dropped"] == []
    assert len(result["unverifiable"]) == 1
    assert result["unverifiable"][0]["head_sha"] == "f" * 40


def test_audit_repo_missing_canonical_repo_reports_error(tmp_path):
    wt_dir = tmp_path / "ghost-worktrees"
    wt_dir.mkdir()

    result = audit.audit_repo("ghost", wt_dir)

    assert "error" in result
    assert result["confirmed_dropped"] == []


def test_audit_repo_skips_corrupt_journal(tmp_path):
    repo = _init_repo(tmp_path / "myrepo")
    _commit(repo, "a.txt", "one")
    wt_dir = tmp_path / "myrepo-worktrees"
    wt_dir.mkdir()
    (wt_dir / "run-bad.json").write_text("{not json")

    result = audit.audit_repo("myrepo", wt_dir, base_ref="main")

    assert result["journals_scanned"] == 0
    assert result["confirmed_dropped"] == []


# ---------------------------------------------------------------------------
# CLI


def test_main_json_output_exit_code_reflects_findings(tmp_path, capsys):
    repo = _init_repo(tmp_path / "myrepo")
    _commit(repo, "a.txt", "one")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side"], check=True)
    dropped_sha = _commit(repo, "dropped.txt", "never merged")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    wt_dir = tmp_path / "myrepo-worktrees"
    _write_journal(
        wt_dir,
        "run-full-1.json",
        "full-1",
        [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "PASSED", "head_sha": dropped_sha},
            }
        ],
    )

    exit_code = audit.main(
        [
            "--repos-root",
            str(tmp_path),
            "--repo",
            "myrepo",
            "--base",
            "main",
            "--json",
        ]
    )

    out = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert len(out["results"][0]["confirmed_dropped"]) == 1


def test_main_exit_zero_when_clean(tmp_path, capsys):
    repo = _init_repo(tmp_path / "myrepo")
    sha = _commit(repo, "a.txt", "one")
    wt_dir = tmp_path / "myrepo-worktrees"
    _write_journal(
        wt_dir,
        "run-full-1.json",
        "full-1",
        [
            {
                "role": "review",
                "task": "1.1",
                "started_at": 1,
                "report": {"review_status": "PASSED", "head_sha": sha},
            }
        ],
    )

    exit_code = audit.main(
        ["--repos-root", str(tmp_path), "--repo", "myrepo", "--base", "main"]
    )

    capsys.readouterr()
    assert exit_code == 0
