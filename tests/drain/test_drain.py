"""Unit tests for drain.py — no live CLI calls; the spawner is always faked."""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from worktrail.drain.summary_contract import (
    load_nightly_drain_summary_contract,
    stop_semantics,
)

from worktrail.drain import drain
from worktrail.drain.drain import (
    MAX_TRANSCRIPT_FILES,
    PROMPT,
    REMEDIATION_TABLE,
    DrainConfig,
    LoopState,
    Outcome,
    SpawnOutcome,
    StageRemediation,
    acquire_lock,
    acquire_lock_slot,
    archive_openspec_change,
    build_agent_environment,
    build_command,
    build_full_real_resume_command,
    build_sync_command,
    capacity_gated,
    claimed_brief_ids,
    classify_outcome,
    close_stale_bookkeeping,
    count_ready_briefs,
    decide,
    ensure_pr_risk_label,
    find_complete_openspec_changes,
    find_resumable_quarantines,
    find_stale_bookkeeping_specs,
    find_stale_branches,
    find_sync_pending_specs,
    find_verify_pending_specs,
    newest_run_record,
    parse_run_record,
    prune_stale_branch,
    release_lock,
    release_lock_slot,
    resolve_spec_rel,
    resume_quarantined_budget_exhausted,
    resume_sync_pending,
    resume_verify_pending,
    run_one_shot,
    select_available_agent,
    slot_lock_path,
    sweep_remediations,
    validate_agent_runtime,
    worker_scratch_dir,
    write_iteration_transcript,
)


# ---------------------------------------------------------------------------
# build_command


def test_build_command_claude_default_has_no_permission_flags():
    assert build_command("claude", []) == ["claude", "-p", PROMPT]


def test_build_command_claude_permission_args_are_explicit_passthrough():
    cmd = build_command("claude", ["--dangerously-skip-permissions"])
    assert cmd == ["claude", "-p", PROMPT, "--dangerously-skip-permissions"]


def test_build_command_opencode_and_codex_shapes():
    assert build_command("opencode", []) == ["opencode", "run", PROMPT]
    assert build_command("codex", []) == [
        "codex", "exec", "-s", "danger-full-access", PROMPT]


def test_build_command_template_overrides_agent_shape():
    cmd = build_command("claude", ["--ignored-by-template"],
                        template="mycli --oneshot {prompt}")
    assert cmd == ["mycli", "--oneshot", PROMPT]


def test_build_command_template_without_prompt_placeholder_rejected():
    with pytest.raises(ValueError):
        build_command("claude", [], template="mycli --oneshot")


def test_build_command_unknown_agent_rejected():
    with pytest.raises(ValueError):
        build_command("gemini", [])


def test_build_agent_environment_adds_supported_user_runtime_dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    node_bin = home / ".nvm" / "versions" / "node" / "v24.16.0" / "bin"
    node_bin.mkdir(parents=True)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = build_agent_environment(home=home)

    assert env["PATH"].split(os.pathsep) == [
        str(home / ".local" / "bin"),
        str(home / "bin"),
        str(home / ".opencode" / "bin"),
        str(node_bin),
        "/usr/bin",
        "/bin",
    ]


def test_build_agent_environment_chooses_newest_nvm_runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for version in ("v20.9.0", "v24.2.0", "v24.16.0"):
        (home / ".nvm" / "versions" / "node" / version / "bin").mkdir(parents=True)
    monkeypatch.setenv("PATH", "/usr/bin")

    path = build_agent_environment(home=home)["PATH"].split(os.pathsep)

    assert str(home / ".nvm" / "versions" / "node" / "v24.16.0" / "bin") in path
    assert str(home / ".nvm" / "versions" / "node" / "v24.2.0" / "bin") not in path


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode"])
def test_validate_agent_runtime_requires_provider_and_node(agent):
    env = {"PATH": "/minimal"}

    with pytest.raises(RuntimeError, match=rf"{agent}.*node.*PATH=/minimal"):
        validate_agent_runtime(agent, env, which=lambda executable, path: None)


def test_validate_agent_runtime_reports_only_missing_node():
    env = {"PATH": "/minimal"}

    def fake_which(executable, path):
        return f"/minimal/{executable}" if executable == "claude" else None

    with pytest.raises(RuntimeError, match=r"required executable\(s\) unavailable: node"):
        validate_agent_runtime("claude", env, which=fake_which)


# ---------------------------------------------------------------------------
# queue readiness


def test_count_ready_briefs_excludes_blocked_and_not_yet_due():
    queue = {"briefs": [
        {"filename": "a.md", "blocked": False, "not_yet_due": False},
        {"filename": "b.md", "blocked": True, "not_yet_due": False},
        {"filename": "c.md", "blocked": False, "not_yet_due": True},
        {"filename": "d.md"},
    ]}
    assert count_ready_briefs(queue) == 2


def test_count_ready_briefs_empty_and_missing_key():
    assert count_ready_briefs({"briefs": []}) == 0
    assert count_ready_briefs({}) == 0


# ---------------------------------------------------------------------------
# brief-id capture via queue diff


def test_claimed_brief_ids_single_disappearance_strips_md_suffix():
    before = {"briefs": [{"filename": "a.md"}, {"filename": "b.md"}]}
    after = {"briefs": [{"filename": "b.md"}]}
    assert claimed_brief_ids(before, after) == ["a"]


def test_claimed_brief_ids_no_change_is_empty():
    before = {"briefs": [{"filename": "a.md"}]}
    after = {"briefs": [{"filename": "a.md"}]}
    assert claimed_brief_ids(before, after) == []


def test_claimed_brief_ids_multiple_disappearances_all_returned_sorted():
    before = {"briefs": [{"filename": "c.md"}, {"filename": "a.md"}, {"filename": "b.md"}]}
    after = {"briefs": [{"filename": "c.md"}]}
    assert claimed_brief_ids(before, after) == ["a", "b"]


def test_claimed_brief_ids_missing_filename_key_ignored():
    before = {"briefs": [{"filename": "a.md"}, {}]}
    after = {"briefs": []}
    assert claimed_brief_ids(before, after) == ["a"]


def test_claimed_brief_ids_empty_or_missing_briefs_key():
    assert claimed_brief_ids({}, {}) == []
    assert claimed_brief_ids({"briefs": []}, {"briefs": []}) == []


# ---------------------------------------------------------------------------
# run-record parsing


RECORD_FINISHED = """\
run_id: go-20260722-1
status: done
final_status: completed_pr_open
pull_request: "https://github.com/x/y/pull/1"
decisions:
  - something: nested
"""

RECORD_UNFINISHED = """\
run_id: go-20260722-2
status: executing
final_status: null
pull_request: null
"""


def test_parse_run_record_extracts_scalars_and_nulls():
    fields = parse_run_record(RECORD_FINISHED)
    assert fields["final_status"] == "completed_pr_open"
    assert fields["pull_request"] == "https://github.com/x/y/pull/1"
    unfinished = parse_run_record(RECORD_UNFINISHED)
    assert unfinished["final_status"] is None


def test_parse_run_record_skips_nested_lines():
    fields = parse_run_record(RECORD_FINISHED)
    assert "something" not in fields


def test_newest_run_record_excludes_known_paths(tmp_path):
    repo = tmp_path / "repo-a"
    repo.mkdir()
    old = repo / "old.yaml"
    old.write_text("run_id: old\n")
    os.utime(old, (1000, 1000))
    assert newest_run_record(tmp_path) == old
    assert newest_run_record(tmp_path, {old}) is None
    new = repo / "new.yaml"
    new.write_text("run_id: new\n")
    os.utime(new, (2000, 2000))
    assert newest_run_record(tmp_path, {old}) == new
    assert newest_run_record(tmp_path, {old, new}) is None
    assert newest_run_record(tmp_path / "missing") is None


def test_newest_run_record_survives_same_tick_mtime_race(tmp_path):
    """Regression for the mtime-comparison race (20260724-163128): two records
    written within the same filesystem mtime-resolution tick used to tie or
    invert under `mtime >= since_epoch`, letting the stale record win
    depending on directory-iteration order. Attribution by before-spawn
    snapshot instead of mtime sidesteps the tie entirely.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    old = repo / "go-1.yaml"
    old.write_text("run_id: old\n")
    os.utime(old, (1000, 1000))
    known_before_iter2 = {old}
    new = repo / "go-2.yaml"
    new.write_text("run_id: new\n")
    os.utime(new, (1000, 1000))  # identical tick as `old`
    assert newest_run_record(tmp_path, known_before_iter2) == new


def test_newest_run_record_repo_filter_scopes_to_one_repo(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    old_a = repo_a / "old.yaml"
    old_a.write_text("run_id: old-a\n")
    os.utime(old_a, (1000, 1000))
    new_a = repo_a / "new.yaml"
    new_a.write_text("run_id: new-a\n")
    os.utime(new_a, (1500, 1500))
    # Newest file overall lives in repo-b -- a repo_filter="repo-a" lookup
    # must ignore it and return the newest file within repo-a instead.
    newer_b = repo_b / "newer.yaml"
    newer_b.write_text("run_id: newer-b\n")
    os.utime(newer_b, (5000, 5000))
    assert newest_run_record(tmp_path, repo_filter="repo-a") == new_a
    assert newest_run_record(tmp_path, repo_filter="repo-b") == newer_b
    assert newest_run_record(tmp_path) == newer_b


def test_newest_run_record_repo_filter_none_matches_unfiltered_default(tmp_path):
    repo = tmp_path / "repo-a"
    repo.mkdir()
    old = repo / "old.yaml"
    old.write_text("run_id: old\n")
    os.utime(old, (1000, 1000))
    new = repo / "new.yaml"
    new.write_text("run_id: new\n")
    os.utime(new, (2000, 2000))
    assert (newest_run_record(tmp_path, repo_filter=None)
            == newest_run_record(tmp_path))
    assert (newest_run_record(tmp_path, {old}, repo_filter=None)
            == newest_run_record(tmp_path, {old}))
    assert (newest_run_record(tmp_path, {old, new}, repo_filter=None)
            == newest_run_record(tmp_path, {old, new}))


# ---------------------------------------------------------------------------
# outcome classification


def test_classify_outcome_success_states():
    for state in ("completed_and_merged", "completed_pr_open",
                  "completed_awaiting_human_approval"):
        out = classify_outcome({"final_status": state}, claimed_delta=1, exit_code=0)
        assert out.kind == "success" and out.state == state


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode"])
def test_provider_commands_require_unattended_terminal_ownership(agent):
    command = drain.build_command(agent, [], go_repo="worktrail")
    prompt = next(part for part in command if part.startswith("worktrail-go "))
    assert prompt.startswith("worktrail-go worktrail auto")
    assert "in the foreground" in prompt
    assert "real final_status" in prompt
    assert "PR checks are pending" in prompt


def test_classify_outcome_blocked_and_failed_states():
    assert classify_outcome({"final_status": "blocked_external_dependency"},
                            1, 0).kind == "blocked"
    assert classify_outcome({"final_status": "failed_terminal"}, 1, 0).kind == "failed"


def test_classify_outcome_unfinished_record_is_failure():
    out = classify_outcome({"final_status": None}, claimed_delta=1, exit_code=1)
    assert out.kind == "failed"


def test_classify_outcome_clean_exit_unfinished_record_is_recoverable_failure():
    # Regression (brief 20260812-091707): a provider one-shot returned 0 while
    # PR checks were pending, abandoning the unfinished run record.
    out = classify_outcome({"final_status": None}, claimed_delta=1, exit_code=0)
    assert out.kind == "failed"
    assert out.state == "failed_recoverable"


def test_classify_outcome_clean_exit_unfinished_record_attributes_claimed_brief():
    out = classify_outcome({"final_status": None}, claimed_delta=1, exit_code=0,
                           claimed_briefs=["20260806-brief"])
    assert out.kind == "failed" and out.brief_id == "20260806-brief"


def test_classify_outcome_no_record_no_claim_clean_exit_is_no_pick():
    assert classify_outcome(None, claimed_delta=0, exit_code=0).kind == "no_pick"


def test_classify_outcome_no_record_nonzero_exit_is_failure():
    assert classify_outcome(None, claimed_delta=0, exit_code=124).kind == "failed"


def test_classify_outcome_attributes_single_claimed_brief():
    out = classify_outcome({"final_status": "completed_pr_open"}, claimed_delta=1,
                           exit_code=0, claimed_briefs=["20260716-171700-x"])
    assert out.brief_id == "20260716-171700-x"


def test_classify_outcome_ambiguous_multi_claim_leaves_brief_unattributed():
    out = classify_outcome({"final_status": "completed_pr_open"}, claimed_delta=2,
                           exit_code=0, claimed_briefs=["a", "b"])
    assert out.brief_id is None


def test_classify_outcome_no_claimed_briefs_leaves_brief_none():
    out = classify_outcome({"final_status": "completed_pr_open"}, claimed_delta=1, exit_code=0)
    assert out.brief_id is None


def test_classify_outcome_no_record_failure_still_attributes_claimed_brief():
    out = classify_outcome(None, claimed_delta=1, exit_code=1, claimed_briefs=["a"])
    assert out.kind == "failed" and out.brief_id == "a"


# ---------------------------------------------------------------------------
# timeout-after-PR classification (defect: iteration-timeout wrap-up killed
# after a PR was already captured used to count as a plain failure)


def test_classify_outcome_timeout_with_pr_is_timeout_after_pr():
    out = classify_outcome(
        {"final_status": None, "pull_request": "https://github.com/x/y/pull/658"},
        claimed_delta=1, exit_code=124, claimed_briefs=["b1"])
    assert out.kind == "timeout_after_pr"
    assert out.pr_url == "https://github.com/x/y/pull/658"
    assert out.brief_id == "b1"


def test_classify_outcome_timeout_without_pr_is_still_failed():
    out = classify_outcome({"final_status": None}, claimed_delta=1, exit_code=124)
    assert out.kind == "failed"


def test_classify_outcome_timeout_without_record_is_still_failed():
    assert classify_outcome(None, claimed_delta=0, exit_code=124).kind == "failed"


def test_classify_outcome_non_timeout_exit_with_pr_but_unfinished_record_is_failed():
    # A PR alone doesn't grant the timeout_after_pr pass — only exit 124 does.
    out = classify_outcome(
        {"final_status": None, "pull_request": "https://github.com/x/y/pull/1"},
        claimed_delta=1, exit_code=1)
    assert out.kind == "failed"


def test_classify_outcome_timeout_with_pr_but_explicit_failed_state_stays_failed():
    # An explicit failed_terminal from the record is a deliberate signal from
    # the agent itself and outranks the timeout+PR heuristic.
    out = classify_outcome(
        {"final_status": "failed_terminal", "pull_request": "https://github.com/x/y/pull/1"},
        claimed_delta=1, exit_code=124)
    assert out.kind == "failed"


# ---------------------------------------------------------------------------
# capacity-classified failures (account-level, not a generic "failed")


def test_classify_outcome_billing_failure_class_is_blocked_not_failed():
    out = classify_outcome(None, claimed_delta=0, exit_code=1, failure_class="billing")
    assert out.kind == "blocked"
    assert out.state == "blocked_capacity_billing"


def test_classify_outcome_auth_failure_class_is_blocked_not_failed():
    out = classify_outcome(None, claimed_delta=0, exit_code=1, failure_class="auth")
    assert out.kind == "blocked"
    assert out.state == "blocked_capacity_auth"


def test_classify_outcome_transport_failure_class_stays_plain_failed():
    # transport/sandbox/startup are not account-level -- unchanged behavior,
    # still counted by the ordinary circuit breaker.
    out = classify_outcome(None, claimed_delta=0, exit_code=1, failure_class="transport")
    assert out.kind == "failed"


def test_classify_outcome_no_failure_class_unchanged_default():
    assert classify_outcome(None, claimed_delta=0, exit_code=1).kind == "failed"


def test_classify_outcome_no_pick_outranks_failure_class():
    # A clean exit with nothing claimed is no_pick even if failure_class were
    # (nonsensically) populated -- exit_code=0 never reaches classify_failure
    # in the real drain() loop, but classify_outcome's own precedence must not
    # depend on that caller discipline.
    out = classify_outcome(None, claimed_delta=0, exit_code=0, failure_class="billing")
    assert out.kind == "no_pick"


# ---------------------------------------------------------------------------
# capacity cache


def test_capacity_gated_all_providers_for_agent_gated():
    cache = {"providers": {
        "claude": {"status": "gated"},
        "claude:opus": {"status": "unavailable"},
        "codex": {"status": "ok"},
    }}
    assert capacity_gated(cache, "claude") is True
    assert capacity_gated(cache, "codex") is False


def test_capacity_gated_bare_agent_gate_overrides_model_history():
    cache = {"providers": {
        "claude": {"status": "gated"},
        "claude:sonnet": {"status": "ok"},
    }}
    assert capacity_gated(cache, "claude") is True


def test_capacity_gated_partial_model_gate_does_not_stop():
    cache = {"providers": {
        "claude:opus": {"status": "unavailable"},
        "claude:sonnet": {"status": "ok"},
    }}
    assert capacity_gated(cache, "claude") is False


def test_capacity_gated_no_entries_or_bad_cache():
    assert capacity_gated({}, "claude") is False
    assert capacity_gated({"providers": "garbage"}, "claude") is False
    # flat layout (no "providers" wrapper)
    assert capacity_gated({"claude": {"status": "gated"}}, "claude") is True


# ---------------------------------------------------------------------------
# agent fallback selection


def test_select_available_agent_prefers_primary_when_ungated():
    cache = {"providers": {"claude": {"status": "gated"}}}
    assert select_available_agent(cache, ["codex", "claude"]) == "codex"


def test_select_available_agent_skips_gated_primary_for_fallback():
    cache = {"providers": {"codex": {"status": "unavailable"}}}
    assert select_available_agent(cache, ["codex", "claude"]) == "claude"


def test_select_available_agent_none_when_every_candidate_gated():
    cache = {"providers": {
        "codex": {"status": "gated"},
        "claude": {"status": "unavailable"},
    }}
    assert select_available_agent(cache, ["codex", "claude"]) is None


def test_select_available_agent_never_tried_counts_as_available():
    assert select_available_agent({}, ["codex", "claude"]) == "codex"


def test_select_available_agent_single_candidate_no_fallback_configured():
    cache = {"providers": {"claude": {"status": "gated"}}}
    assert select_available_agent(cache, ["claude"]) is None


# ---------------------------------------------------------------------------
# iteration transcripts (why did THIS outcome happen, not just what it was)


def test_write_iteration_transcript_none_dir_writes_nothing(tmp_path):
    assert write_iteration_transcript(
        None, 1, "claude", 0, Outcome("no_pick"), "out", "err") is None


def test_write_iteration_transcript_content_and_naming(tmp_path):
    out_dir = tmp_path / "transcripts"
    outcome = Outcome("blocked", "blocked_capacity_billing", "brief-1", "https://pr/1")
    path = write_iteration_transcript(
        out_dir, 3, "codex", 1, outcome, "the stdout body", "the stderr body")
    assert path is not None
    assert path.parent == out_dir
    assert path.name.endswith("-iter3-codex.log")
    text = path.read_text()
    assert "iteration: 3" in text
    assert "agent: codex" in text
    assert "exit_code: 1" in text
    assert "outcome: blocked_capacity_billing" in text
    assert "brief: brief-1" in text
    assert "pr: https://pr/1" in text
    assert "=== STDOUT ===\nthe stdout body" in text
    assert "=== STDERR ===\nthe stderr body" in text


def test_write_iteration_transcript_bounded_retention(tmp_path):
    out_dir = tmp_path / "transcripts"
    for i in range(MAX_TRANSCRIPT_FILES + 5):
        write_iteration_transcript(
            out_dir, i, "claude", 0, Outcome("no_pick"), f"out-{i}", "",
            now=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i))
    remaining = sorted(out_dir.glob("*.log"))
    assert len(remaining) == MAX_TRANSCRIPT_FILES
    assert "iter4-" not in remaining[0].name  # oldest 5 pruned
    assert f"iter{MAX_TRANSCRIPT_FILES + 4}-" in remaining[-1].name


def test_write_iteration_transcript_write_failure_returns_none(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    bad_dir = blocker / "transcripts"  # parent is a file -> mkdir must fail
    assert write_iteration_transcript(
        bad_dir, 1, "claude", 0, Outcome("no_pick"), "out", "err") is None


# ---------------------------------------------------------------------------
# decision function


def make_state(**kw):
    defaults = dict(iteration=0, max_items=0, deadline=None,
                    consecutive_failures=0, failure_threshold=2,
                    ready_count=3, last_outcome=None, agent_capacity_gated=False)
    defaults.update(kw)
    return LoopState(**defaults)


def test_decide_proceeds_with_ready_briefs():
    assert decide(make_state(), now=0).proceed is True


def test_decide_stops_on_empty_queue():
    d = decide(make_state(ready_count=0), now=0)
    assert d.proceed is False and d.reason.startswith("queue_empty")


def test_decide_stops_on_no_pick():
    d = decide(make_state(last_outcome=Outcome("no_pick")), now=0)
    assert d.proceed is False and d.reason.startswith("no_pick")


def test_decide_stops_on_circuit_breaker():
    d = decide(make_state(last_outcome=Outcome("failed"),
                          consecutive_failures=2), now=0)
    assert d.proceed is False and d.reason.startswith("circuit_breaker")


def test_decide_continues_below_failure_threshold():
    d = decide(make_state(last_outcome=Outcome("failed"),
                          consecutive_failures=1), now=0)
    assert d.proceed is True


def test_decide_stops_on_capacity_gate_after_blocked_outcome():
    d = decide(make_state(last_outcome=Outcome("blocked",
                                               "blocked_external_dependency"),
                          consecutive_failures=1, agent_capacity_gated=True), now=0)
    assert d.proceed is False and d.reason.startswith("capacity_gated")


def test_decide_capacity_gate_alone_without_blocked_outcome_continues():
    # A stale gate entry must not stop a drain whose iterations are succeeding.
    d = decide(make_state(last_outcome=Outcome("success", "completed_pr_open"),
                          agent_capacity_gated=True), now=0)
    assert d.proceed is True


def test_decide_stops_on_max_items():
    d = decide(make_state(iteration=3, max_items=3,
                          last_outcome=Outcome("success", "completed_pr_open")), now=0)
    assert d.proceed is False and d.reason.startswith("max_items")


def test_decide_stops_on_budget():
    d = decide(make_state(deadline=100.0), now=101.0)
    assert d.proceed is False and d.reason.startswith("budget_exhausted")


def test_decide_awaiting_approval_continues():
    d = decide(make_state(
        last_outcome=Outcome("success", "completed_awaiting_human_approval")), now=0)
    assert d.proceed is True


# ---------------------------------------------------------------------------
# lockfile


def test_lock_acquire_and_release(tmp_path):
    lock = tmp_path / "drain.lock"
    assert acquire_lock(lock) is True
    assert acquire_lock(lock) is False  # same pid is alive → held
    release_lock(lock)
    assert acquire_lock(lock) is True
    release_lock(lock)


def test_lock_stale_pid_is_replaced(tmp_path):
    lock = tmp_path / "drain.lock"
    lock.write_text(json.dumps({"pid": 999999999, "started": 0}))
    assert acquire_lock(lock) is True
    release_lock(lock)


def test_lock_garbage_content_treated_as_stale(tmp_path):
    lock = tmp_path / "drain.lock"
    lock.write_text("not json")
    assert acquire_lock(lock) is True
    release_lock(lock)


def test_acquire_lock_slot_returns_distinct_slots_then_none(tmp_path):
    lock = tmp_path / "drain.lock"
    assert acquire_lock_slot(lock, max_workers=2) == 0
    assert acquire_lock_slot(lock, max_workers=2) == 1
    assert acquire_lock_slot(lock, max_workers=2) is None


def test_acquire_lock_slot_default_max_workers_acquires_lock_file_unchanged(tmp_path):
    lock = tmp_path / "drain.lock"
    slot = acquire_lock_slot(lock, max_workers=1)
    assert slot == 0
    assert slot_lock_path(lock, slot) == lock
    assert lock.exists()


def test_acquire_lock_slot_takes_over_stale_slot(tmp_path):
    lock = tmp_path / "drain.lock"
    assert acquire_lock_slot(lock, max_workers=2) == 0  # slot 0 held by our live pid
    stale_slot_file = slot_lock_path(lock, 1)
    stale_slot_file.write_text(json.dumps({"pid": 999999999, "started": 0}))
    assert acquire_lock_slot(lock, max_workers=2) == 1
    release_lock_slot(lock, 0)
    release_lock_slot(lock, 1)


# ---------------------------------------------------------------------------
# drain loop end-to-end with a fake spawner


class FakeQueue:
    """Writable stand-in for work_queue.py list output, patched into drain."""

    def __init__(self, ready_counts):
        self.ready_counts = list(ready_counts)
        self.calls = 0

    def next_json(self):
        n = self.ready_counts[min(self.calls, len(self.ready_counts) - 1)]
        self.calls += 1
        return {"briefs": [
            {"filename": f"b{i}.md", "blocked": False, "not_yet_due": False}
            for i in range(n)]}


def make_config(tmp_path, **kw):
    wq = tmp_path / "work_queue.py"
    wq.write_text("# placeholder; list_queue is monkeypatched in tests\n")
    defaults = dict(
        work_queue_py=wq,
        runs_dir=tmp_path / "runs",
        capacity_cache=tmp_path / "capacity.json",
        lock_file=tmp_path / "drain.lock",
        agent="claude",
    )
    defaults.update(kw)
    return DrainConfig(**defaults)


def install_fake_queue(monkeypatch, fake):
    monkeypatch.setattr(drain, "list_queue", lambda *_a, **_k: fake.next_json())


def test_list_queue_runs_installed_module_as_package(tmp_path):
    """Regression: list_queue() must invoke the installed work_queue.py via the
    package (`-m worktrail.workqueue.work_queue`), not as a bare file path.

    work_queue.py imports from `..shared.brief_frontmatter`, a package-relative
    import that breaks under bare-file execution ('attempted relative import
    with no known parent package'). Before the fix drain.py ran it as a plain
    script and every list_queue() call crashed the drain on its first iteration
    (observed live during a datalena unattended drain). This test calls
    list_queue() for real -- no monkeypatching -- against a temp queue.
    """
    installed_module = drain.default_work_queue_py()
    if installed_module is None:
        pytest.skip("installed worktrail.workqueue.work_queue not resolvable")
    queue_base = tmp_path / "work-queue"
    queue_path = queue_base / "queue"
    queue_path.mkdir(parents=True)
    (queue_path / "b1.md").write_text(
        "---\nid: b1\nstatus: queued\n---\n\nbody\n", encoding="utf-8")

    payload = drain.list_queue(installed_module, queue_base)

    filenames = [b["filename"] for b in payload.get("briefs", [])]
    assert "b1.md" in filenames


def test_list_queue_plain_script_override_still_supported(tmp_path):
    """An explicit non-package override path still runs as a plain script."""
    queue_base = tmp_path / "work-queue"
    queue_path = queue_base / "queue"
    queue_path.mkdir(parents=True)
    script = tmp_path / "fake_work_queue.py"
    script.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "def main():\n"
        "    q = Path(os.environ.get('WORK_QUEUE_DIR', '.')) / 'queue'\n"
        "    print(json.dumps({'briefs': [{'filename': p.name} for p in sorted(q.glob('*.md'))]}))\n"
        "main()\n",
        encoding="utf-8",
    )
    (queue_path / "b2.md").write_text(
        "---\nid: b2\nstatus: queued\n---\n\nbody\n", encoding="utf-8")

    payload = drain.list_queue(script, queue_base)

    assert [b["filename"] for b in payload.get("briefs", [])] == ["b2.md"]


def write_run_record(runs_dir, name, final_status, pr=None):
    repo = runs_dir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    lines = [f"run_id: {name}", f"final_status: {final_status or 'null'}"]
    if pr:
        lines.append(f'pull_request: "{pr}"')
    (repo / f"{name}.yaml").write_text("\n".join(lines) + "\n")


def test_drain_two_briefs_then_empty(tmp_path, monkeypatch):
    # ready counts: pre-iter1=2, post-iter1=1, pre-iter2=1, post-iter2=0, pre-iter3=0
    fake = FakeQueue([2, 1, 1, 0, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    n = {"spawned": 0}

    def spawner(cmd, timeout):
        n["spawned"] += 1
        write_run_record(config.runs_dir, f"go-{n['spawned']}",
                         "completed_pr_open", pr=f"https://pr/{n['spawned']}")
        return SpawnOutcome(0)

    logs = []
    summary = drain.drain(config, spawner=spawner, log=logs.append)
    assert n["spawned"] == 2
    assert summary["stopped"].startswith("queue_empty")
    assert [i["state"] for i in summary["iterations"]] == [
        "completed_pr_open", "completed_pr_open"]
    assert not config.lock_file.exists()


def test_drain_passes_distinct_worker_scratch_dir_as_cwd_per_slot(tmp_path, monkeypatch):
    """drain()'s built-in spawner path threads worker_scratch_dir(slot) through
    as run_one_shot's cwd (drain.py task 4.3); two workers configured onto
    different slots of the same lock_file must resolve to distinct scratch
    dirs, not share one."""
    home = tmp_path / "home"
    monkeypatch.setenv("WORKTRAIL_HOME", str(home))
    monkeypatch.setattr(drain, "build_agent_environment", lambda *a, **k: {})
    monkeypatch.setattr(drain, "validate_agent_runtime", lambda *a, **k: None)

    def run_for_slot(expected_slot, run_name):
        fake = FakeQueue([1, 0])
        install_fake_queue(monkeypatch, fake)
        config = make_config(tmp_path, max_workers=2,
                            lock_file=tmp_path / "drain.lock")
        calls = []

        def fake_run_one_shot(cmd, timeout, env=None, cwd=None):
            calls.append(cwd)
            write_run_record(config.runs_dir, run_name,
                             "completed_pr_open", pr=f"https://pr/{run_name}")
            return SpawnOutcome(0)

        monkeypatch.setattr(drain, "run_one_shot", fake_run_one_shot)
        drain.drain(config, log=lambda *_: None)
        assert calls == [worker_scratch_dir(expected_slot, home=home)]
        assert calls[0].is_dir()
        return calls[0]

    # Slot 0 held by a live "other worker" for the duration of this call, so
    # this drain() invocation is forced onto slot 1.
    assert acquire_lock(tmp_path / "drain.lock") is True
    try:
        slot1_cwd = run_for_slot(1, "worker-b")
    finally:
        release_lock(tmp_path / "drain.lock")

    slot0_cwd = run_for_slot(0, "worker-a")

    assert slot0_cwd != slot1_cwd


def test_drain_attributes_outcome_to_claimed_briefs_own_repo_not_other_repos_newer_record(
        tmp_path, monkeypatch):
    """Two workers finish overlapping iterations against different repos: this
    worker's iteration claims a repo-a brief, but a concurrently-running
    worker's repo-b record lands (with a newer mtime) between this
    iteration's pre-spawn snapshot and its post-spawn read. The single
    claimed brief must pin attribution to repo-a's own record, not the
    newer-but-foreign repo-b one (spec `drain-concurrent-workers` scenario
    "Two workers finish overlapping iterations against different repos").
    """
    calls = {"n": 0}

    def fake_list_queue(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            # Pre-spawn snapshot: both briefs still queued.
            return {"briefs": [
                {"filename": "b1.md", "blocked": False, "not_yet_due": False,
                 "repo": "/repos/repo-a"},
                {"filename": "b2.md", "blocked": False, "not_yet_due": False,
                 "repo": "/repos/repo-b"},
            ]}
        if calls["n"] == 2:
            # Post-spawn snapshot: only b1 (repo-a) was claimed this iteration.
            return {"briefs": [
                {"filename": "b2.md", "blocked": False, "not_yet_due": False,
                 "repo": "/repos/repo-b"},
            ]}
        return {"briefs": []}  # next iteration's pre-spawn snapshot: queue empty

    monkeypatch.setattr(drain, "list_queue", fake_list_queue)
    config = make_config(tmp_path)

    def spawner(cmd, timeout):
        repo_a_dir = config.runs_dir / "repo-a"
        repo_a_dir.mkdir(parents=True, exist_ok=True)
        own = repo_a_dir / "go-own.yaml"
        own.write_text(
            "run_id: own\nfinal_status: completed_pr_open\n"
            'pull_request: "https://pr/own"\n')
        os.utime(own, (1000, 1000))
        # The other worker's overlapping iteration, landing a newer record
        # in a different repo's run directory during this same window.
        repo_b_dir = config.runs_dir / "repo-b"
        repo_b_dir.mkdir(parents=True, exist_ok=True)
        other = repo_b_dir / "go-other.yaml"
        other.write_text("run_id: other\nfinal_status: completed_and_merged\n")
        os.utime(other, (9999, 9999))
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    iteration = summary["iterations"][0]
    assert iteration["state"] == "completed_pr_open"
    assert iteration["pr"] == "https://pr/own"


def test_drain_circuit_breaker_after_two_failures(tmp_path, monkeypatch):
    fake = FakeQueue([5])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)

    def spawner(cmd, timeout):
        return SpawnOutcome(1)  # no run record, nonzero exit → failure

    summary = drain.drain(config, spawner=spawner, log=lambda _line: None)
    assert len(summary["iterations"]) == 2
    assert summary["stopped"].startswith("circuit_breaker")


def test_drain_stops_on_no_pick(tmp_path, monkeypatch):
    fake = FakeQueue([3])  # queue never shrinks
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0), log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    assert summary["iterations"][0]["kind"] == "no_pick"
    assert summary["stopped"].startswith("no_pick")


def test_drain_max_items(tmp_path, monkeypatch):
    fake = FakeQueue([9, 8, 8, 7, 7])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, max_items=2)
    n = {"spawned": 0}

    def spawner(cmd, timeout):
        n["spawned"] += 1
        write_run_record(config.runs_dir, f"go-{n['spawned']}", "completed_and_merged")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert n["spawned"] == 2
    assert summary["stopped"].startswith("max_items")


def test_drain_budget_exhausted(tmp_path, monkeypatch):
    fake = FakeQueue([5, 4, 4])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, budget_minutes=1)
    clock_state = {"t": 0.0}

    def clock():
        return clock_state["t"]

    def spawner(cmd, timeout):
        clock_state["t"] += 120.0  # each iteration costs 2 minutes
        write_run_record(config.runs_dir, "go-x", "completed_pr_open")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, clock=clock, log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    assert summary["stopped"].startswith("budget_exhausted")


def test_drain_awaiting_approval_noted_and_continues(tmp_path, monkeypatch):
    fake = FakeQueue([2, 1, 1, 0, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    n = {"spawned": 0}

    def spawner(cmd, timeout):
        n["spawned"] += 1
        state = ("completed_awaiting_human_approval" if n["spawned"] == 1
                 else "completed_pr_open")
        write_run_record(config.runs_dir, f"go-{n['spawned']}", state,
                         pr=f"https://pr/{n['spawned']}")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert n["spawned"] == 2
    assert summary["pending_approvals"] == ["https://pr/1"]
    assert summary["stopped"].startswith("queue_empty")


def test_drain_captures_brief_id_via_queue_diff(tmp_path, monkeypatch):
    # pre-iter1=[b0,b1], post-iter1=[b0] -> iter1 claimed b1
    # pre-iter2=[b0],    post-iter2=[]   -> iter2 claimed b0
    fake = FakeQueue([2, 1, 1, 0, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    n = {"spawned": 0}

    def spawner(cmd, timeout):
        n["spawned"] += 1
        write_run_record(config.runs_dir, f"go-{n['spawned']}",
                         "completed_pr_open", pr=f"https://pr/{n['spawned']}")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert n["spawned"] == 2
    assert [i["brief"] for i in summary["iterations"]] == ["b1", "b0"]


def test_drain_timeout_after_pr_does_not_trip_circuit_breaker(tmp_path, monkeypatch):
    # Queue never shrinks (these iterations don't complete a claim cleanly),
    # so max_items -- not queue_empty -- bounds the loop. What's under test
    # is that 3 consecutive timeout+PR iterations, with a breaker threshold
    # of 2, never trip circuit_breaker the way 3 consecutive plain failures
    # would.
    fake = FakeQueue([5])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, max_items=3, failure_threshold=2)
    calls = {"n": 0}

    def spawner(cmd, timeout):
        calls["n"] += 1
        write_run_record(config.runs_dir, f"go-{calls['n']}", None,
                         pr=f"https://pr/{calls['n']}")
        return SpawnOutcome(124)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 3
    assert all(i["kind"] == "timeout_after_pr" for i in summary["iterations"])
    assert summary["stopped"].startswith("max_items")


def test_drain_unfinished_clean_exit_trips_circuit_breaker(tmp_path, monkeypatch):
    fake = FakeQueue([5])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, max_items=3, failure_threshold=2)
    calls = {"n": 0}

    def spawner(cmd, timeout):
        calls["n"] += 1
        write_run_record(config.runs_dir, f"go-{calls['n']}", None)
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 2
    assert all(i["kind"] == "failed" for i in summary["iterations"])
    assert summary["stopped"].startswith("circuit_breaker")


def test_drain_timeout_without_pr_still_trips_circuit_breaker(tmp_path, monkeypatch):
    fake = FakeQueue([5])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)  # default failure_threshold=2

    def spawner(cmd, timeout):
        return SpawnOutcome(124)  # no run record, no PR captured -> a plain failure

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 2
    assert all(i["kind"] == "failed" for i in summary["iterations"])
    assert summary["stopped"].startswith("circuit_breaker")


def test_drain_capacity_gate_stops_after_blocked_iteration(tmp_path, monkeypatch):
    fake = FakeQueue([4, 4, 4])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    config.capacity_cache.write_text(json.dumps(
        {"providers": {"claude": {"status": "gated"}}}))

    def spawner(cmd, timeout):
        write_run_record(config.runs_dir, "go-1", "blocked_external_dependency")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    assert summary["stopped"].startswith("capacity_gated")


def test_drain_usage_limit_output_becomes_blocked_and_persists_gate(tmp_path, monkeypatch):
    # End-to-end regression for the live incident (2026-08-02): the nightly
    # drain's iteration 2 died in 16s against Codex's usage cap, but the old
    # DEVNULL discipline meant it was indistinguishable from any other bare
    # "failed exit=1" -- no cache entry, no real retry_after, and it counted
    # toward the circuit breaker exactly like a code bug would.
    fake = FakeQueue([3, 3])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, agent="codex")
    usage_limit_text = (
        "ERROR: You've hit your usage limit. Upgrade to Pro "
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more credits "
        "or try again at Aug 8th, 2026 2:17 AM.")

    def spawner(cmd, timeout):
        return SpawnOutcome(1, usage_limit_text, "")

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    assert summary["iterations"][0]["kind"] == "blocked"
    assert summary["iterations"][0]["state"] == "blocked_capacity_billing"
    # circuit breaker never even entered the picture -- capacity_gated fired
    # on the very next iteration check instead.
    assert summary["stopped"].startswith("capacity_gated")

    cache = json.loads(config.capacity_cache.read_text())
    entry = cache["providers"]["codex"]
    assert entry["status"] == "unavailable"
    assert entry["failure_class"] == "billing"
    retry_at = datetime.fromisoformat(entry["retry_after"])
    assert (retry_at.year, retry_at.month, retry_at.day) == (2026, 8, 8)


def test_drain_bare_capacity_gate_overrides_available_model_history(
        tmp_path, monkeypatch):
    fake = FakeQueue([2, 2, 2, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, agent="claude", fallback_agents=["codex"])
    config.capacity_cache.write_text(json.dumps({"providers": {
        "claude:sonnet": {"status": "available"},
    }}))
    seen_cmds = []

    def spawner(cmd, timeout):
        seen_cmds.append(cmd)
        if cmd[0] == "claude":
            return SpawnOutcome(1, "You've hit your weekly limit", "")
        write_run_record(
            config.runs_dir, "go-1", "completed_pr_open", pr="https://pr/1")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)

    assert [cmd[0] for cmd in seen_cmds] == ["claude", "codex"]
    assert [item["kind"] for item in summary["iterations"]] == [
        "blocked", "success",
    ]
    assert summary["stopped"].startswith("queue_empty")


def test_drain_weekly_limit_persists_gate_and_stops_as_capacity_gated(
        tmp_path, monkeypatch):
    """The summary consumed by bridge-health-guard must not look like a fault.

    A stale available model entry must not override the bare-agent gate written
    from a weekly-limit response.  That was the live shape that previously
    advanced to the circuit breaker instead of reporting expected exhaustion.
    """
    fake = FakeQueue([2, 2, 2, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, agent="claude", fallback_agents=["codex"])
    config.capacity_cache.write_text(json.dumps({"providers": {
        "claude:sonnet": {"status": "available"},
        "codex:gpt-5": {"status": "available"},
    }}))
    seen_agents = []

    def spawner(cmd, timeout):
        seen_agents.append(cmd[0])
        return SpawnOutcome(1, "You've hit your weekly limit", "")

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)

    assert seen_agents == ["claude", "codex"]
    assert len(summary["iterations"]) == 2
    assert all(item["kind"] == "blocked" for item in summary["iterations"])
    assert all(item["state"] == "blocked_capacity_billing"
               for item in summary["iterations"])
    assert summary["stopped"].startswith("capacity_gated")

    cache = json.loads(config.capacity_cache.read_text())
    for agent in ("claude", "codex"):
        assert cache["providers"][agent]["status"] == "unavailable"
        assert cache["providers"][agent]["failure_class"] == "billing"


def test_drain_writes_transcript_when_transcript_dir_configured(tmp_path, monkeypatch):
    # End-to-end regression for the live incident (2026-08-03): a no_pick
    # iteration left no trace of what the one-shot actually did, so answering
    # "why" required a fresh manual reproduction. With --transcript-dir set,
    # the raw stdout/stderr survives the iteration.
    fake = FakeQueue([3])
    install_fake_queue(monkeypatch, fake)
    transcript_dir = tmp_path / "transcripts"
    config = make_config(tmp_path, transcript_dir=transcript_dir)

    def spawner(cmd, timeout):
        return SpawnOutcome(0, "here is what the one-shot printed", "")

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert summary["iterations"][0]["kind"] == "no_pick"
    transcript_ref = summary["iterations"][0]["transcript"]
    assert transcript_ref is not None
    transcript_path = Path(transcript_ref)
    assert transcript_path.is_file()
    assert "here is what the one-shot printed" in transcript_path.read_text()


def test_drain_no_transcript_dir_writes_nothing_by_default(tmp_path, monkeypatch):
    fake = FakeQueue([3])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)  # transcript_dir defaults to None

    def spawner(cmd, timeout):
        return SpawnOutcome(0, "output that must not be persisted", "")

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert summary["iterations"][0]["transcript"] is None
    assert not (tmp_path / "transcripts").exists()


def test_drain_generic_failure_stays_plain_failed_with_no_cache_write(tmp_path, monkeypatch):
    # A code bug or transient blip must not be mistaken for an account-level
    # cap: still a plain "failed" iteration, circuit breaker still applies,
    # and nothing is written to the capacity cache.
    fake = FakeQueue([5])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)

    def spawner(cmd, timeout):
        return SpawnOutcome(1, "", "Traceback (most recent call last): ...")

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert all(i["kind"] == "failed" for i in summary["iterations"])
    assert summary["stopped"].startswith("circuit_breaker")
    assert not config.capacity_cache.exists()


def test_drain_falls_back_to_configured_agent_when_primary_gated(tmp_path, monkeypatch):
    fake = FakeQueue([1, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, agent="claude", fallback_agents=["opencode"])
    config.capacity_cache.write_text(json.dumps(
        {"providers": {"claude": {"status": "gated"}}}))
    seen_cmds = []

    def spawner(cmd, timeout):
        seen_cmds.append(cmd)
        write_run_record(config.runs_dir, "go-1", "completed_pr_open", pr="https://pr/1")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    # Selected before the first spawn -- claude never gets a chance to fail.
    assert seen_cmds == [["opencode", "run", PROMPT]]
    assert summary["iterations"][0]["agent"] == "opencode"
    assert summary["stopped"].startswith("queue_empty")


def test_drain_capacity_gated_requires_every_configured_agent_exhausted(tmp_path, monkeypatch):
    fake = FakeQueue([2])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, agent="claude", fallback_agents=["opencode"])
    config.capacity_cache.write_text(json.dumps({"providers": {
        "claude": {"status": "gated"},
        "opencode": {"status": "unavailable"},
    }}))

    def spawner(cmd, timeout):
        write_run_record(config.runs_dir, "go-1", "blocked_external_dependency")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    assert summary["stopped"].startswith("capacity_gated")


def test_drain_refuses_when_lock_held(tmp_path, monkeypatch):
    fake = FakeQueue([1])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    assert acquire_lock(config.lock_file) is True  # our own live pid holds it
    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0), log=lambda _l: None)
    assert summary["stopped"] == "lock_held"
    release_lock(config.lock_file)


def test_drain_dry_run_launches_nothing(tmp_path, monkeypatch):
    fake = FakeQueue([3])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, dry_run=True)

    def spawner(cmd, timeout):
        raise AssertionError("dry-run must not spawn")

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert summary["stopped"] == "dry_run"
    assert summary["iterations"] == []


# ---------------------------------------------------------------------------
# drive() wiring — the loop calls ensure_pr_risk_label per iteration with a PR.
# ensure_pr_risk_label/_current_pr_labels themselves now live in
# router/pr_labels.py (tests/router/test_pr_labels.py) so poll_run.py and the
# worktrail-ensure-pr-label CLI can share the identical correction; drain.py
# still re-exports the name (`from ..router.pr_labels import
# ensure_pr_risk_label`) so `monkeypatch.setattr(drain, ...)` below still works.


def test_drive_calls_ensure_pr_risk_label_and_logs_when_applied(tmp_path, monkeypatch):
    fake = FakeQueue([1, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    seen = []
    monkeypatch.setattr(drain, "ensure_pr_risk_label",
                        lambda repo, pr, risk: seen.append((repo, pr, risk)) or "go:risk-low")

    def spawner(cmd, timeout):
        (config.runs_dir / "repo").mkdir(parents=True, exist_ok=True)
        (config.runs_dir / "repo" / "go-1.yaml").write_text(
            "run_id: go-1\n"
            "final_status: completed_pr_open\n"
            'pull_request: "https://github.com/o/r/pull/9"\n'
            "repository: /home/briank/projects/r\n"
            "risk_level: low\n"
        )
        return SpawnOutcome(0)

    logs = []
    drain.drain(config, spawner=spawner, log=logs.append)
    assert seen == [("/home/briank/projects/r", "https://github.com/o/r/pull/9", "low")]
    assert any("go:risk-low" in line for line in logs)


def test_drive_skips_ensure_pr_risk_label_when_no_pr(tmp_path, monkeypatch):
    fake = FakeQueue([1, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)

    def unexpected(*_a, **_k):
        raise AssertionError("must not be called when the iteration has no PR")

    monkeypatch.setattr(drain, "ensure_pr_risk_label", unexpected)

    def spawner(cmd, timeout):
        write_run_record(config.runs_dir, "go-1", "planned_ready_for_implementation")
        return SpawnOutcome(0)

    drain.drain(config, spawner=spawner, log=lambda _l: None)


def test_build_command_go_repo_scopes_the_prompt():
    scoped = PROMPT.replace("worktrail-go auto", "worktrail-go ggb auto", 1)
    assert build_command("claude", [], go_repo="ggb") == [
        "claude", "-p", scoped]
    assert build_command("claude", [], template="x {prompt}",
                         go_repo="ggb") == ["x", scoped]


# ---------------------------------------------------------------------------
# Resumable quarantine sweep


def _make_repo(repos_root: Path, name: str) -> Path:
    repo = repos_root / name
    (repo / ".git").mkdir(parents=True)
    return repo


def _write_journal(repo: Path, spec_id: str, groups: dict) -> Path:
    worktrees_dir = repo.parent / f"{repo.name}-worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    path = worktrees_dir / f"run-{spec_id}.json"
    path.write_text(json.dumps({"groups": groups}), encoding="utf-8")
    return path


_BUDGET_EXHAUSTED_GROUPS = {
    "1.2": {"state": "QUARANTINED", "pr_url": "", "quarantine_reason": "budget_exhausted"},
}
_TWO_BUDGET_EXHAUSTED_GROUPS = {
    "1.2": {"state": "QUARANTINED", "pr_url": "", "quarantine_reason": "budget_exhausted"},
    "1.3": {"state": "QUARANTINED", "pr_url": "", "quarantine_reason": "budget_exhausted"},
}


def test_resolve_spec_rel_devkit_path():
    repo = Path("/tmp/nonexistent-repo-for-unit-test")
    # Use a real tmp dir instead of a bare literal so .is_dir() checks are real.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "docs" / "specs" / "some-spec").mkdir(parents=True)
        assert resolve_spec_rel(repo, "some-spec") == "docs/specs/some-spec"


def test_resolve_spec_rel_openspec_path(tmp_path):
    (tmp_path / "openspec" / "changes" / "some-change").mkdir(parents=True)
    assert resolve_spec_rel(tmp_path, "some-change") == "openspec/changes/some-change"


def test_resolve_spec_rel_missing_returns_none(tmp_path):
    assert resolve_spec_rel(tmp_path, "ghost-spec") is None


def test_find_resumable_quarantines_discovers_across_repos(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    (repo_a / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo_a, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    repo_b = _make_repo(tmp_path, "repo-b")  # clean repo, no journal at all
    found = find_resumable_quarantines(tmp_path)
    assert [f["repo_name"] for f in found] == ["repo-a"]
    assert found[0]["spec_id"] == "spec-a"
    assert found[0]["spec_rel"] == "docs/specs/spec-a"
    assert found[0]["repo"] == repo_a


def test_find_resumable_quarantines_dedups_multiple_groups_same_spec(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo, "spec-a", _TWO_BUDGET_EXHAUSTED_GROUPS)
    found = find_resumable_quarantines(tmp_path)
    assert len(found) == 1  # one full-real re-run covers both groups' journal


def test_find_resumable_quarantines_skips_spec_with_no_resolvable_path(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    # No docs/specs/spec-a and no openspec/changes/spec-a on disk.
    _write_journal(repo, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    assert find_resumable_quarantines(tmp_path) == []


def test_find_resumable_quarantines_go_repo_filter(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    (repo_a / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo_a, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    repo_b = _make_repo(tmp_path, "repo-b")
    (repo_b / "docs" / "specs" / "spec-b").mkdir(parents=True)
    _write_journal(repo_b, "spec-b", _BUDGET_EXHAUSTED_GROUPS)
    found = find_resumable_quarantines(tmp_path, go_repo="repo-b")
    assert [f["repo_name"] for f in found] == ["repo-b"]


# ---------------------------------------------------------------------------
# Verify-pending sweep


def _write_verify_pending_spec(repo: Path, spec_id: str, pr_url: str) -> None:
    """A spec whose tasks are all completed and whose run journal has
    integrate_complete: true plus a non-MERGED group -- the fixture shape
    dashboard.detect_stage requires to label a spec "verify-pending"
    (mirrors tests/router/test_dashboard.py's own verify-pending fixture)."""
    spec_dir = repo / "docs" / "specs" / spec_id
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (spec_dir / "2026-05-29--feature.md").write_text(
        f"# Feature Specification: X\n\n**ID**: {spec_id}\n\n## Summary\nstuff\n"
    )
    (tasks_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: completed\nkind: impl\ndependencies: []\n---\n# TASK-001\n"
    )
    worktrees_dir = repo.parent / f"{repo.name}-worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    (worktrees_dir / f"run-{spec_id}.json").write_text(json.dumps({
        "integrate_complete": True,
        "groups": {"base": {"pr_url": pr_url, "state": "OPEN"}},
    }))


def test_find_verify_pending_specs_discovers_across_repos(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _write_verify_pending_spec(
        repo_a, "spec-a", "https://github.com/test/repo/pull/1"
    )
    repo_b = _make_repo(tmp_path, "repo-b")  # clean repo, nothing verify-pending
    found = find_verify_pending_specs(tmp_path)
    assert [f["repo_name"] for f in found] == ["repo-a"]
    assert found[0]["spec_id"] == "spec-a"
    assert found[0]["spec_rel"] == "docs/specs/spec-a"
    assert found[0]["repo"] == repo_a


def _write_verify_pending_openspec_change(repo: Path, change_id: str, pr_url: str) -> None:
    """OpenSpec-format equivalent of `_write_verify_pending_spec`: all tasks
    checked, run journal has integrate_complete: true plus a non-MERGED group.
    Regression fixture for the drain-sweep blind spot where an OpenSpec change
    could never surface as "verify-pending" (dashboard.py's OpenSpec branch had
    no equivalent of that stage)."""
    change_dir = repo / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    (change_dir / "tasks.md").write_text("## 1. Export\n\n- [x] 1.1 Add exporter\n")
    worktrees_dir = repo.parent / f"{repo.name}-worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    (worktrees_dir / f"run-{change_id}.json").write_text(json.dumps({
        "integrate_complete": True,
        "groups": {"base": {"pr_url": pr_url, "state": "OPEN"}},
    }))


def test_find_verify_pending_specs_discovers_openspec_changes(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    _write_verify_pending_openspec_change(
        repo, "add-export", "https://github.com/test/repo/pull/1"
    )
    found = find_verify_pending_specs(tmp_path)
    assert [f["repo_name"] for f in found] == ["repo-a"]
    assert found[0]["spec_id"] == "add-export"
    assert found[0]["spec_rel"] == "openspec/changes/add-export"


def _write_ready_to_implement_spec(repo: Path, spec_id: str) -> None:
    """A spec with a pending task and no run journal -- dashboard.detect_stage
    labels this "ready-to-implement", not "verify-pending"."""
    spec_dir = repo / "docs" / "specs" / spec_id
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (spec_dir / "2026-05-29--feature.md").write_text(
        f"# Feature Specification: X\n\n**ID**: {spec_id}\n\n## Summary\nstuff\n"
    )
    (tasks_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: pending\nkind: impl\ndependencies: []\n---\n# TASK-001\n"
    )


def test_find_verify_pending_specs_excludes_non_verify_pending_stages(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    _write_ready_to_implement_spec(repo, "spec-a")
    assert find_verify_pending_specs(tmp_path) == []


def test_find_verify_pending_specs_skips_spec_with_no_resolvable_path(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, "repo-a")
    # dashboard.scan reports a verify-pending row for "spec-a", but no
    # docs/specs/spec-a or openspec/changes/spec-a folder exists on disk --
    # e.g. the spec was since deleted/archived after the scan ran.
    monkeypatch.setattr(
        drain.dashboard, "scan",
        lambda specs_root: [{"id": "spec-a", "stage": "verify-pending"}],
    )
    assert find_verify_pending_specs(tmp_path) == []


def test_find_verify_pending_specs_go_repo_filter(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _write_verify_pending_spec(
        repo_a, "spec-a", "https://github.com/test/repo/pull/1"
    )
    repo_b = _make_repo(tmp_path, "repo-b")
    _write_verify_pending_spec(
        repo_b, "spec-b", "https://github.com/test/repo/pull/2"
    )
    found = find_verify_pending_specs(tmp_path, go_repo="repo-b")
    assert [f["repo_name"] for f in found] == ["repo-b"]


# ---------------------------------------------------------------------------
# Sync-pending sweep


def _write_sync_pending_spec(repo: Path, spec_id: str) -> None:
    """A spec with all tasks completed and no knowledge-graph.json (sync never
    ran) and no run journal at all -- the fixture shape dashboard.detect_stage
    requires to label a spec "sync-pending" (mirrors
    tests/router/test_dashboard.py's test_completed_but_unsynced_is_sync_pending)."""
    spec_dir = repo / "docs" / "specs" / spec_id
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (spec_dir / "2026-05-29--feature.md").write_text(
        f"# Feature Specification: X\n\n**ID**: {spec_id}\n\n## Summary\nstuff\n"
    )
    (tasks_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: completed\nkind: impl\ndependencies: []\n---\n# TASK-001\n"
    )


def _write_openspec_sync_pending_change(repo: Path, change_id: str) -> tuple[Path, Path]:
    change = repo / "openspec" / "changes" / change_id
    delta = change / "specs" / "export" / "spec.md"
    canonical = repo / "openspec" / "specs" / "export" / "spec.md"
    delta.parent.mkdir(parents=True)
    canonical.parent.mkdir(parents=True)
    (change / "tasks.md").write_text("## 1. Export\n\n- [x] 1.1 Add export\n")
    delta.write_text(
        "## ADDED Requirements\n\n### Requirement: CSV export\n\n"
        "#### Scenario: Successful export\n"
    )
    canonical.write_text("# Export\n")
    return delta, canonical


def test_find_sync_pending_specs_discovers_across_repos(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _write_sync_pending_spec(repo_a, "spec-a")
    repo_b = _make_repo(tmp_path, "repo-b")  # clean repo, nothing sync-pending
    found = find_sync_pending_specs(tmp_path)
    assert [f["repo_name"] for f in found] == ["repo-a"]
    assert found[0]["spec_id"] == "spec-a"
    assert found[0]["spec_rel"] == "docs/specs/spec-a"
    assert found[0]["repo"] == repo_a


def test_find_sync_pending_specs_discovers_openspec_until_reconciled(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    _delta, canonical = _write_openspec_sync_pending_change(repo, "add-export")

    found = find_sync_pending_specs(tmp_path)

    assert len(found) == 1
    assert found[0]["spec_id"] == "add-export"
    assert found[0]["spec_rel"] == "openspec/changes/add-export"

    canonical.write_text(
        "### Requirement: CSV export\n\n#### Scenario: Successful export\n"
    )
    assert find_sync_pending_specs(tmp_path) == []


def test_find_sync_pending_specs_excludes_non_sync_pending_stages(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    _write_ready_to_implement_spec(repo, "spec-a")
    assert find_sync_pending_specs(tmp_path) == []


def test_find_sync_pending_specs_skips_spec_with_no_resolvable_path(tmp_path, monkeypatch):
    repo = _make_repo(tmp_path, "repo-a")
    # dashboard.scan reports a sync-pending row for "spec-a", but no
    # docs/specs/spec-a or openspec/changes/spec-a folder exists on disk --
    # e.g. the spec was since deleted/archived after the scan ran.
    monkeypatch.setattr(
        drain.dashboard, "scan",
        lambda specs_root: [{"id": "spec-a", "stage": "sync-pending"}],
    )
    assert find_sync_pending_specs(tmp_path) == []


def test_find_sync_pending_specs_go_repo_filter(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _write_sync_pending_spec(repo_a, "spec-a")
    repo_b = _make_repo(tmp_path, "repo-b")
    _write_sync_pending_spec(repo_b, "spec-b")
    found = find_sync_pending_specs(tmp_path, go_repo="repo-b")
    assert [f["repo_name"] for f in found] == ["repo-b"]


# ---------------------------------------------------------------------------
# OpenSpec archive sweep (complete-stage changes)


def _write_openspec_complete_change(repo: Path, change_id: str) -> None:
    """An OpenSpec change with every task checked and no delta specs -- the
    fixture shape dashboard._safe_detect_openspec requires to label a change
    "complete" (mirrors tests/router/test_dashboard.py's
    test_change_without_delta_specs_is_complete)."""
    change = repo / "openspec" / "changes" / change_id
    change.mkdir(parents=True)
    (change / "tasks.md").write_text("## 1. Export\n\n- [x] 1.1 Add exporter\n")


def _write_devkit_complete_spec(repo: Path, spec_id: str) -> None:
    """A devkit spec with every task completed and a synced knowledge-graph --
    the fixture shape dashboard.detect_stage requires to label a spec
    "complete" (mirrors tests/router/test_dashboard.py's
    test_all_tasks_completed_and_synced_is_complete). Format-less (devkit rows
    carry no "format" key), so this proves find_complete_openspec_changes's
    format=="openspec" guard actually excludes a same-stage devkit spec rather
    than relying on devkit never reaching "complete" at all."""
    spec_dir = repo / "docs" / "specs" / spec_id
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (spec_dir / "2026-05-29--feature.md").write_text(
        f"# Feature Specification: X\n\n**ID**: {spec_id}\n\n## Summary\nstuff\n"
    )
    (tasks_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: completed\nkind: impl\ndependencies: []\n---\n# TASK-001\n"
    )
    (spec_dir / "knowledge-graph.json").write_text(
        '{"metadata": {"spec_id": "%s", "analysis_sources": '
        '[{"agent": "spec-sync", "timestamp": "2026-05-31T10:05:00Z", "mode": "full"}]}}'
        % spec_id
    )


def test_find_complete_openspec_changes_discovers_across_repos(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _write_openspec_complete_change(repo_a, "add-export")
    repo_b = _make_repo(tmp_path, "repo-b")  # clean repo, nothing complete

    found = find_complete_openspec_changes(tmp_path)

    assert [f["repo_name"] for f in found] == ["repo-a"]
    assert found[0]["spec_id"] == "add-export"
    assert found[0]["spec_rel"] == "openspec/changes/add-export"


def test_find_complete_openspec_changes_excludes_devkit_complete_stage(tmp_path):
    # The critical scope guard from design.md: a devkit spec reaching the same
    # "complete" stage label must never be routed into `openspec archive`.
    repo = _make_repo(tmp_path, "repo-a")
    _write_devkit_complete_spec(repo, "spec-a")

    assert find_complete_openspec_changes(tmp_path) == []


def test_find_complete_openspec_changes_returns_empty_when_none_complete(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    _write_openspec_sync_pending_change(repo, "add-export")  # not complete yet

    assert find_complete_openspec_changes(tmp_path) == []


def test_find_complete_openspec_changes_go_repo_filter(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _write_openspec_complete_change(repo_a, "add-export")
    repo_b = _make_repo(tmp_path, "repo-b")
    _write_openspec_complete_change(repo_b, "add-import")

    found = find_complete_openspec_changes(tmp_path, go_repo="repo-b")

    assert [f["repo_name"] for f in found] == ["repo-b"]


def _fake_gh_and_openspec_archive_subprocess_run(pr_url: str):
    """Real `git`/other commands pass through to the real subprocess.run; the
    `openspec archive` and two gh-pr-related calls are faked so the test needs
    no network, `gh` auth, or a real `openspec` CLI. The faked archive call
    writes a marker file into the worktree so the subsequent `git commit` has
    something to commit, mirroring what a real `openspec archive -y` would
    move/write."""
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["openspec", "archive"]:
            Path(kwargs["cwd"], "ARCHIVED.marker").write_text("archived\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="archived\n", stderr="")
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "create":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{pr_url}\n", stderr="")
        return real_run(cmd, **kwargs)

    return fake_run


def test_archive_openspec_change_runs_archive_and_opens_pr(tmp_path, monkeypatch):
    repo = _init_repo_with_origin(tmp_path, "repo-a")
    finding = {"repo": repo, "repo_name": "repo-a", "spec_id": "add-export"}

    monkeypatch.setattr(
        drain.subprocess, "run",
        _fake_gh_and_openspec_archive_subprocess_run("https://example.invalid/pr/9"))

    result = archive_openspec_change(
        finding, "claude", 30, lambda c, t: SpawnOutcome(0), lambda _l: None)

    assert result == {"repo": "repo-a", "spec_id": "add-export",
                       "pr_url": "https://example.invalid/pr/9"}
    committed = subprocess.run(
        ["git", "-C", str(repo), "show", "chore/archive-add-export:ARCHIVED.marker"],
        capture_output=True, text=True, check=True).stdout
    assert committed == "archived\n"


def test_archive_openspec_change_existing_pr_skips_rearchiving(tmp_path, monkeypatch):
    repo = _init_repo_with_origin(tmp_path, "repo-a")
    finding = {"repo": repo, "repo_name": "repo-a", "spec_id": "add-export"}
    calls = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://example.invalid/pr/5\n", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(drain.subprocess, "run", fake_run)

    result = archive_openspec_change(
        finding, "claude", 30, lambda c, t: SpawnOutcome(0), lambda _l: None)

    assert result == {"repo": "repo-a", "spec_id": "add-export",
                       "pr_url": "https://example.invalid/pr/5"}
    assert not any(c[:2] == ["openspec", "archive"] for c in calls)
    assert not any(c[:2] == ["gh", "pr"] and c[2] == "create" for c in calls)


def test_archive_openspec_change_gh_pr_create_failure_raises(tmp_path, monkeypatch):
    repo = _init_repo_with_origin(tmp_path, "repo-a")
    finding = {"repo": repo, "repo_name": "repo-a", "spec_id": "add-export"}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["openspec", "archive"]:
            Path(kwargs["cwd"], "ARCHIVED.marker").write_text("archived\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="archived\n", stderr="")
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "create":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="label not found")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(drain.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="gh pr create failed"):
        archive_openspec_change(
            finding, "claude", 30, lambda c, t: SpawnOutcome(0), lambda _l: None)


def _fake_sync_spawner(calls: list, exit_codes=None):
    """Fake `worktrail-skill-dispatch --skill opsx:sync` spawner: writes a
    marker file into the worktree passed via `--cwd`, mirroring what a real
    `/opsx:sync` run would leave behind for the subsequent `git commit` to
    find (same technique as `_fake_gh_and_openspec_archive_subprocess_run`'s
    ARCHIVED.marker). `exit_codes` supplies one exit code per call in order,
    defaulting to always-0; a non-zero exit code still records the call but
    writes no marker, matching a failed sync producing nothing to land."""
    def spawner(cmd, timeout):
        idx = len(calls)
        calls.append(cmd)
        exit_code = exit_codes[idx] if exit_codes else 0
        if exit_code == 0:
            wt = Path(cmd[cmd.index("--cwd") + 1])
            (wt / "SYNCED.marker").write_text("synced\n")
        return SpawnOutcome(exit_code)
    return spawner


def test_resume_sync_pending_invokes_skill_dispatch_once_per_spec(tmp_path, monkeypatch):
    repo = _init_repo_with_origin(tmp_path, "repo-a")
    _write_sync_pending_spec(repo, "spec-a")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed spec"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "dev"], check=True)

    monkeypatch.setattr(drain.subprocess, "run",
                         _fake_gh_subprocess_run("https://example.invalid/pr/9"))

    calls = []
    logs = []
    result = resume_sync_pending(
        repo.parent, None, ["claude"], tmp_path / "capacity.json", 60,
        _fake_sync_spawner(calls), logs.append)
    assert len(calls) == 1
    wt = repo.parent / "repo-a-worktrees" / "sync-spec-a"
    assert calls[0] == ["worktrail-skill-dispatch", "--agent", "claude",
                         "--skill", "opsx:sync", "--args", "spec-a",
                         "--cwd", str(wt), "--write"]
    assert result == [{"repo": "repo-a", "spec_id": "spec-a", "exit_code": 0,
                        "pr_url": "https://example.invalid/pr/9"}]
    assert any("resume-sync-pending" in line for line in logs)
    # The sync lands on the fix branch (committed, pushed, PR opened) -- the
    # canonical checkout's own `dev` is never written to directly, which is
    # the bug (silent uncommitted write) this test now guards against.
    committed = subprocess.run(
        ["git", "-C", str(repo), "show", "chore/sync-spec-a:SYNCED.marker"],
        capture_output=True, text=True, check=True).stdout
    assert committed == "synced\n"


def test_resume_sync_pending_no_hits_is_noop(tmp_path):
    _make_repo(tmp_path, "repo-a")  # no sync-pending spec at all
    calls = []
    result = resume_sync_pending(
        tmp_path, None, ["claude"], tmp_path / "capacity.json", 60,
        lambda c, t: calls.append(c), lambda _l: None)
    assert calls == []
    assert result == []


def test_resume_sync_pending_one_failure_does_not_block_others(tmp_path, monkeypatch):
    repo_a = _init_repo_with_origin(tmp_path, "repo-a")
    _write_sync_pending_spec(repo_a, "spec-a")
    subprocess.run(["git", "-C", str(repo_a), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo_a), "commit", "-qm", "seed spec"], check=True)
    subprocess.run(["git", "-C", str(repo_a), "push", "-q", "origin", "dev"], check=True)

    repo_b = _init_repo_with_origin(tmp_path, "repo-b")
    _write_sync_pending_spec(repo_b, "spec-b")
    subprocess.run(["git", "-C", str(repo_b), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo_b), "commit", "-qm", "seed spec"], check=True)
    subprocess.run(["git", "-C", str(repo_b), "push", "-q", "origin", "dev"], check=True)

    monkeypatch.setattr(drain.subprocess, "run",
                         _fake_gh_subprocess_run("https://example.invalid/pr/9"))

    calls = []
    result = resume_sync_pending(
        repo_a.parent, None, ["claude"], tmp_path / "capacity.json", 60,
        _fake_sync_spawner(calls, exit_codes=[1, 0]), lambda _l: None)
    assert len(calls) == 2
    assert result == [
        {"repo": "repo-a", "spec_id": "spec-a", "exit_code": 1, "pr_url": None},
        {"repo": "repo-b", "spec_id": "spec-b", "exit_code": 0,
         "pr_url": "https://example.invalid/pr/9"},
    ]


def test_resume_verify_pending_invokes_full_real_once_per_spec(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    _write_verify_pending_spec(
        repo, "spec-a", "https://github.com/test/repo/pull/1"
    )
    calls = []

    def spawner(cmd, timeout):
        calls.append(cmd)
        return SpawnOutcome(0)

    logs = []
    result = resume_verify_pending(
        tmp_path, None, ["claude"], tmp_path / "capacity.json", 60, spawner, logs.append)
    assert len(calls) == 1
    assert calls[0][:2] == ["worktrail-live", "full-real"]
    assert "--fresh" not in calls[0]
    assert result == [{"repo": "repo-a", "spec_id": "spec-a", "exit_code": 0}]
    assert any("resume-verify-pending" in line for line in logs)


def test_resume_verify_pending_no_hits_is_noop(tmp_path):
    _make_repo(tmp_path, "repo-a")  # no verify-pending spec at all
    calls = []
    result = resume_verify_pending(
        tmp_path, None, ["claude"], tmp_path / "capacity.json", 60,
        lambda c, t: calls.append(c), lambda _l: None)
    assert calls == []
    assert result == []


def test_resume_verify_pending_one_failure_does_not_block_others(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _write_verify_pending_spec(
        repo_a, "spec-a", "https://github.com/test/repo/pull/1"
    )
    repo_b = _make_repo(tmp_path, "repo-b")
    _write_verify_pending_spec(
        repo_b, "spec-b", "https://github.com/test/repo/pull/2"
    )
    calls = []

    def spawner(cmd, timeout):
        calls.append(cmd)
        return SpawnOutcome(1 if len(calls) == 1 else 0)

    result = resume_verify_pending(
        tmp_path, None, ["claude"], tmp_path / "capacity.json", 60, spawner, lambda _l: None)
    assert len(calls) == 2
    assert result == [
        {"repo": "repo-a", "spec_id": "spec-a", "exit_code": 1},
        {"repo": "repo-b", "spec_id": "spec-b", "exit_code": 0},
    ]


def test_build_full_real_resume_command_has_no_fresh_flag():
    cmd = build_full_real_resume_command(
        Path("/repo"), "docs/specs/some-spec", "dev", "claude")
    assert cmd == ["worktrail-live", "full-real", "--repo", "/repo",
                   "--spec", "docs/specs/some-spec", "--base", "dev", "--agent", "claude"]
    assert "--fresh" not in cmd  # resume=True is full-real's own default


def test_build_sync_command_uses_opsx_sync_dispatch():
    repo = Path("/tmp/repo-a")
    assert build_sync_command("claude", repo, "spec-a") == [
        "worktrail-skill-dispatch", "--agent", "claude",
        "--skill", "opsx:sync", "--args", "spec-a",
        "--cwd", str(repo), "--write",
    ]


def test_resume_quarantined_budget_exhausted_invokes_full_real_once_per_spec(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo, "spec-a", _TWO_BUDGET_EXHAUSTED_GROUPS)
    calls = []

    def spawner(cmd, timeout):
        calls.append(cmd)
        return SpawnOutcome(0)

    logs = []
    result = resume_quarantined_budget_exhausted(
        tmp_path, None, ["claude"], tmp_path / "capacity.json", 60, spawner, logs.append)
    assert len(calls) == 1
    assert calls[0][:2] == ["worktrail-live", "full-real"]
    assert "--fresh" not in calls[0]
    assert result == [{"repo": "repo-a", "spec_id": "spec-a", "exit_code": 0}]
    assert any("resume-quarantine" in line for line in logs)


def test_resume_quarantined_budget_exhausted_no_resumable_is_noop(tmp_path):
    _make_repo(tmp_path, "repo-a")  # no journal at all
    calls = []
    result = resume_quarantined_budget_exhausted(
        tmp_path, None, ["claude"], tmp_path / "capacity.json", 60,
        lambda c, t: calls.append(c), lambda _l: None)
    assert calls == []
    assert result == []


def test_drain_resumes_budget_exhausted_quarantine_before_queue_empty(tmp_path, monkeypatch):
    # Queue is already empty -- without the sweep this would be a zero-spawn
    # queue_empty stop; the sweep must still fire and invoke full-real once.
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    repos_root = tmp_path / "projects"
    repo = _make_repo(repos_root, "repo-a")
    (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    config = make_config(tmp_path, repos_root=repos_root)

    calls = []

    def spawner(cmd, timeout):
        calls.append(cmd)
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert summary["stopped"].startswith("queue_empty")
    assert summary["iterations"] == []  # no queue work was ever claimed
    assert len(calls) == 1  # the resume call, not a queue-driving iteration
    assert calls[0][:2] == ["worktrail-live", "full-real"]
    assert summary["resumed_quarantines"] == [
        {"repo": "repo-a", "spec_id": "spec-a", "exit_code": 0}]


def test_drain_repos_root_none_by_default_never_sweeps(tmp_path, monkeypatch):
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)  # repos_root defaults to None
    calls = []
    summary = drain.drain(config, spawner=lambda c, t: calls.append(c) or SpawnOutcome(0),
                          log=lambda _l: None)
    assert calls == []
    assert summary["resumed_quarantines"] == []


def test_drain_dry_run_never_sweeps_quarantines(tmp_path, monkeypatch):
    fake = FakeQueue([3])
    install_fake_queue(monkeypatch, fake)
    repos_root = tmp_path / "projects"
    repo = _make_repo(repos_root, "repo-a")
    (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    config = make_config(tmp_path, repos_root=repos_root, dry_run=True)

    def spawner(cmd, timeout):
        raise AssertionError("dry-run must not spawn, including for resumable quarantines")

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert summary["stopped"] == "dry_run"


def test_drain_after_sweep_catches_quarantine_created_by_this_passs_own_iteration(
        tmp_path, monkeypatch):
    # Queue starts with one ready brief (drained in iteration 1); the
    # resumable quarantine only appears on disk once that iteration's spawner
    # runs, simulating a full-real fan-out this pass itself dispatched and
    # that then hit its own --run-budget mid-fan-out. The pre-loop sweep must
    # find nothing; the post-loop sweep must catch it.
    fake = FakeQueue([1, 0])
    install_fake_queue(monkeypatch, fake)
    repos_root = tmp_path / "projects"
    (repos_root / "repo-a" / ".git").mkdir(parents=True)
    config = make_config(tmp_path, repos_root=repos_root)
    calls = []

    def spawner(cmd, timeout):
        calls.append(cmd)
        if cmd[:2] == ["worktrail-live", "full-real"]:
            return SpawnOutcome(0)
        repo = repos_root / "repo-a"
        (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
        _write_journal(repo, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
        write_run_record(config.runs_dir, "go-1", "completed_pr_open", pr="https://pr/1")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    resume_calls = [c for c in calls if c[:2] == ["worktrail-live", "full-real"]]
    assert len(resume_calls) == 1  # only the post-loop sweep found it
    assert summary["resumed_quarantines"] == [
        {"repo": "repo-a", "spec_id": "spec-a", "exit_code": 0}]


def test_drain_sweeps_verify_pending_at_pre_and_post_loop_points(tmp_path, monkeypatch):
    # Mirrors the quarantine sweep's own pre-loop/post-loop wiring test: one
    # queue iteration (satisfying state.iteration > 0) must produce exactly
    # two sweep_remediations calls -- the pre-loop sweep and the post-loop
    # re-sweep -- under the same repos_root/dry_run guards the quarantine and
    # verify-pending sweeps have always shared. drain() now sweeps every
    # REMEDIATION_TABLE row through one shared engine call per point (see
    # drain-stage-remediation-table), so this spies on sweep_remediations
    # itself rather than the single-key resume_verify_pending wrapper.
    fake = FakeQueue([1, 0])
    install_fake_queue(monkeypatch, fake)
    repos_root = tmp_path / "projects"
    _make_repo(repos_root, "repo-a")
    config = make_config(tmp_path, repos_root=repos_root)

    real_sweep_remediations = drain.sweep_remediations
    sweep_calls = []

    def spy_sweep_remediations(*args, **kwargs):
        sweep_calls.append(args)
        return real_sweep_remediations(*args, **kwargs)

    monkeypatch.setattr(drain, "sweep_remediations", spy_sweep_remediations)

    def spawner(cmd, timeout):
        if cmd[:2] == ["worktrail-live", "full-real"]:
            return SpawnOutcome(0)
        write_run_record(config.runs_dir, "go-1", "completed_pr_open", pr="https://pr/1")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)

    assert len(sweep_calls) == 2  # pre-loop sweep + post-loop re-sweep
    for call in sweep_calls:
        assert call[0] == repos_root
        assert call[1] == config.go_repo
    assert "resumed_quarantines" in summary
    assert "resumed_verify_pending" in summary
    assert "resumed_stale_bookkeeping" in summary
    assert "resumed_sync_pending" in summary
    assert "resumed_openspec_archive" in summary


def test_drain_non_leader_slot_never_sweeps_or_seeds_backlog(tmp_path, monkeypatch):
    # A worker landing on any slot other than 0 must skip both the
    # sweep_remediations and seed_backlog leader-only blocks entirely (task
    # 5.1), even with repos_root set, seed_backlog enabled, and ready briefs
    # in the queue -- those are the exact conditions that trigger both blocks
    # for a slot-0 worker in the tests above/below.
    monkeypatch.setattr(drain, "acquire_lock_slot", lambda *a, **k: 1)
    fake = FakeQueue([1, 0])
    install_fake_queue(monkeypatch, fake)
    repos_root = tmp_path / "projects"
    _make_repo(repos_root, "repo-a")
    config = make_config(tmp_path, repos_root=repos_root)

    sweep_calls = []
    monkeypatch.setattr(
        drain, "sweep_remediations",
        lambda *a, **k: sweep_calls.append((a, k)) or {})
    seed_calls = []
    monkeypatch.setattr(
        drain.seed_backlog_mod, "seed_backlog",
        lambda *a, **k: seed_calls.append((a, k)) or {})

    def spawner(cmd, timeout):
        write_run_record(config.runs_dir, "go-1", "completed_pr_open", pr="https://pr/1")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)

    assert sweep_calls == []
    assert seed_calls == []
    assert summary["resumed_quarantines"] == []
    assert summary["seeded_backlog"] == {}


# ---------------------------------------------------------------------------
# stuck-remediation detector integration


def test_drain_stuck_remediations_empty_when_no_identity_recurs(tmp_path, monkeypatch):
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    repos_root = tmp_path / "projects"
    repo = _make_repo(repos_root, "repo-a")
    (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    history_path = tmp_path / "history.json"
    config = make_config(tmp_path, repos_root=repos_root, stuck_history_path=history_path)

    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0), log=lambda _l: None)

    # A single sweep only advances the identity's streak to 1, below the
    # default stuck_threshold of 3, so nothing is flagged yet.
    assert summary["stuck_remediations"] == []


def test_drain_flags_identity_recurring_across_stuck_threshold_calls(tmp_path, monkeypatch):
    repos_root = tmp_path / "projects"
    repo = _make_repo(repos_root, "repo-a")
    (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    history_path = tmp_path / "history.json"
    threshold = 2

    summaries = []
    for _ in range(threshold):
        fake = FakeQueue([0])
        install_fake_queue(monkeypatch, fake)
        config = make_config(tmp_path, repos_root=repos_root,
                             stuck_history_path=history_path, stuck_threshold=threshold)
        summaries.append(drain.drain(
            config, spawner=lambda c, t: SpawnOutcome(0), log=lambda _l: None))

    # The journal is untouched by the faked spawner, so the same
    # quarantined-budget-exhausted finding re-affirms on every call, building
    # its streak by one per drain() invocation sharing `history_path`.
    assert summaries[0]["stuck_remediations"] == []
    assert summaries[-1]["stuck_remediations"] == [
        {"key": "quarantined_budget_exhausted", "repo_name": "repo-a",
         "spec_id": "spec-a", "streak": threshold}]


def test_drain_dry_run_never_writes_stuck_history(tmp_path, monkeypatch):
    fake = FakeQueue([3])
    install_fake_queue(monkeypatch, fake)
    repos_root = tmp_path / "projects"
    repo = _make_repo(repos_root, "repo-a")
    (repo / "docs" / "specs" / "spec-a").mkdir(parents=True)
    _write_journal(repo, "spec-a", _BUDGET_EXHAUSTED_GROUPS)
    history_path = tmp_path / "history.json"
    config = make_config(tmp_path, repos_root=repos_root, dry_run=True,
                         stuck_history_path=history_path)

    def spawner(cmd, timeout):
        raise AssertionError("dry-run must not spawn, including for the stuck-remediation sweep")

    drain.drain(config, spawner=spawner, log=lambda _l: None)

    assert not history_path.exists()


def test_drain_repos_root_none_never_writes_stuck_history(tmp_path, monkeypatch):
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    history_path = tmp_path / "history.json"
    config = make_config(tmp_path, stuck_history_path=history_path)  # repos_root defaults to None

    drain.drain(config, spawner=lambda c, t: SpawnOutcome(0), log=lambda _l: None)

    assert not history_path.exists()


# ---------------------------------------------------------------------------
# sweep_remediations engine


def test_sweep_remediations_runs_every_table_row(monkeypatch, tmp_path):
    calls = []

    def make_row(key):
        def finder(repos_root, go_repo):
            return [{"repo_name": key, "spec_id": "spec-a"}]

        def action(finding, agent, timeout, spawner, log):
            calls.append((key, finding["repo_name"]))
            return {"repo": finding["repo_name"], "spec_id": finding["spec_id"]}

        return StageRemediation(key, f"label-{key}", finder, action)

    fake_table = [make_row("row-a"), make_row("row-b")]
    monkeypatch.setattr(drain, "REMEDIATION_TABLE", fake_table)

    results = sweep_remediations(
        Path("/fake/root"), None, ["claude"], tmp_path / "capacity.json", 60,
        lambda c, t: SpawnOutcome(0), lambda _l: None)

    assert set(results) == {"row-a", "row-b"}
    assert results["row-a"] == [{"repo": "row-a", "spec_id": "spec-a"}]
    assert results["row-b"] == [{"repo": "row-b", "spec_id": "spec-a"}]
    assert set(calls) == {("row-a", "row-a"), ("row-b", "row-b")}


def test_sweep_remediations_isolates_per_finding_failure(monkeypatch, tmp_path):
    def failing_finder(repos_root, go_repo):
        return [{"repo_name": "repo-a", "spec_id": "spec-a"},
                {"repo_name": "repo-b", "spec_id": "spec-b"}]

    def failing_action(finding, agent, timeout, spawner, log):
        if finding["repo_name"] == "repo-a":
            raise RuntimeError("boom")
        return {"repo": finding["repo_name"], "spec_id": finding["spec_id"]}

    def ok_finder(repos_root, go_repo):
        return [{"repo_name": "repo-c", "spec_id": "spec-c"}]

    def ok_action(finding, agent, timeout, spawner, log):
        return {"repo": finding["repo_name"], "spec_id": finding["spec_id"]}

    def openspec_archive_finder(repos_root, go_repo):
        return [{"repo_name": "repo-d", "spec_id": "add-export"}]

    def openspec_archive_action(finding, agent, timeout, spawner, log):
        return {"repo": finding["repo_name"], "spec_id": finding["spec_id"],
                "pr_url": "https://example.invalid/pr/1"}

    logs = []
    fake_table = [
        StageRemediation("flaky", "flaky-label", failing_finder, failing_action),
        StageRemediation("ok", "ok-label", ok_finder, ok_action),
        StageRemediation("openspec_archive", "archive-openspec-change",
                          openspec_archive_finder, openspec_archive_action),
    ]
    monkeypatch.setattr(drain, "REMEDIATION_TABLE", fake_table)

    results = sweep_remediations(
        Path("/fake/root"), None, ["claude"], tmp_path / "capacity.json", 60,
        lambda c, t: SpawnOutcome(0), logs.append)

    # repo-a's failure is caught and logged; repo-b (same row) still runs.
    assert results["flaky"] == [{"repo": "repo-b", "spec_id": "spec-b"}]
    assert any("flaky-label error" in line and "repo-a" in line for line in logs)
    # The other rows are unaffected by the first row's failure, including the
    # new openspec_archive row.
    assert results["ok"] == [{"repo": "repo-c", "spec_id": "spec-c"}]
    assert results["openspec_archive"] == [
        {"repo": "repo-d", "spec_id": "add-export", "pr_url": "https://example.invalid/pr/1"}]


def test_sweep_remediations_keys_filter_restricts_rows(monkeypatch, tmp_path):
    called_finders = []

    def make_row(key):
        def finder(repos_root, go_repo):
            called_finders.append(key)
            return []

        return StageRemediation(key, f"label-{key}", finder, lambda *a, **k: {})

    fake_table = [make_row("row-a"), make_row("row-b")]
    monkeypatch.setattr(drain, "REMEDIATION_TABLE", fake_table)

    results = sweep_remediations(
        Path("/fake/root"), None, ["claude"], tmp_path / "capacity.json", 60,
        lambda c, t: SpawnOutcome(0), lambda _l: None,
        keys=["row-b"])

    assert set(results) == {"row-b"}
    assert called_finders == ["row-b"]  # row-a's finder never ran


def test_remediation_table_excludes_orchestrator_stuck():
    keys = {row.key for row in REMEDIATION_TABLE}
    assert "orchestrator_stuck" not in keys
    assert "fanout_failed" not in keys
    assert keys == {
        "quarantined_budget_exhausted", "verify_pending",
        "stale_bookkeeping", "sync_pending", "openspec_archive",
        "stale_branches",
    }


def test_sweep_remediations_falls_back_when_primary_agent_gated(monkeypatch, tmp_path):
    # Before this fix, every finding in a sweep was locked to whatever single
    # `agent` string the caller passed in, with no ability to fall back to a
    # lower-priority candidate the way the main drain loop already can via
    # `select_available_agent`. Gate "claude" up front and prove the sweep
    # picks "codex" (the next candidate) instead of stalling or erroring.
    capacity_cache = tmp_path / "capacity.json"
    capacity_cache.write_text(json.dumps({"claude": {"status": "gated"}}))

    used_agents = []

    def finder(repos_root, go_repo):
        return [{"repo_name": "repo-a", "spec_id": "spec-a"}]

    def action(finding, agent, timeout, spawner, log):
        used_agents.append(agent)
        return {"repo": finding["repo_name"], "spec_id": finding["spec_id"]}

    fake_table = [StageRemediation("row-a", "label-a", finder, action)]
    monkeypatch.setattr(drain, "REMEDIATION_TABLE", fake_table)

    logs = []
    results = sweep_remediations(
        Path("/fake/root"), None, ["claude", "codex"], capacity_cache, 60,
        lambda c, t: SpawnOutcome(0), logs.append)

    assert used_agents == ["codex"]
    assert results["row-a"] == [{"repo": "repo-a", "spec_id": "spec-a"}]
    assert any("agent switch: claude -> codex (capacity)" in line for line in logs)


def test_sweep_remediations_skips_finding_when_every_candidate_gated(monkeypatch, tmp_path):
    capacity_cache = tmp_path / "capacity.json"
    capacity_cache.write_text(json.dumps({
        "claude": {"status": "gated"}, "codex": {"status": "gated"},
    }))

    def finder(repos_root, go_repo):
        return [{"repo_name": "repo-a", "spec_id": "spec-a"}]

    def action(finding, agent, timeout, spawner, log):
        raise AssertionError("action must not run when every candidate is gated")

    fake_table = [StageRemediation("row-a", "label-a", finder, action)]
    monkeypatch.setattr(drain, "REMEDIATION_TABLE", fake_table)

    logs = []
    results = sweep_remediations(
        Path("/fake/root"), None, ["claude", "codex"], capacity_cache, 60,
        lambda c, t: SpawnOutcome(0), logs.append)

    assert results["row-a"] == []
    assert any("capacity-gated" in line for line in logs)


def test_sweep_remediations_re_reads_capacity_between_findings(monkeypatch, tmp_path):
    # A long sweep can outlive a capacity gate that was active when it
    # started -- the second finding should pick up the higher-priority agent
    # the instant the cache reports it's no longer gated, without needing the
    # whole sweep (or drain process) to restart.
    capacity_cache = tmp_path / "capacity.json"
    capacity_cache.write_text(json.dumps({"claude": {"status": "gated"}}))

    def finder(repos_root, go_repo):
        return [{"repo_name": "repo-a", "spec_id": "spec-a"},
                {"repo_name": "repo-b", "spec_id": "spec-b"}]

    used_agents = []

    def action(finding, agent, timeout, spawner, log):
        used_agents.append(agent)
        if finding["repo_name"] == "repo-a":
            capacity_cache.write_text(json.dumps({}))  # gate clears mid-sweep
        return {"repo": finding["repo_name"], "spec_id": finding["spec_id"]}

    fake_table = [StageRemediation("row-a", "label-a", finder, action)]
    monkeypatch.setattr(drain, "REMEDIATION_TABLE", fake_table)

    sweep_remediations(
        Path("/fake/root"), None, ["claude", "codex"], capacity_cache, 60,
        lambda c, t: SpawnOutcome(0), lambda _l: None)

    assert used_agents == ["codex", "claude"]


def test_run_one_shot_kills_whole_process_group_on_timeout(tmp_path):
    # Regression for the orphaned-grandchild leak: `worktrail-skill-dispatch`
    # spawns the real provider CLI without redirecting its own stdout/stderr,
    # so that grandchild inherits the immediate child's pipe fds and (before
    # this fix) its process group. A plain `subprocess.run(timeout=...)` only
    # SIGKILLs the immediate child, leaving a still-alive grandchild running
    # forever. This spawns a short-lived parent that backgrounds a
    # long-lived, non-child-reaped grandchild sharing its own process group
    # (mirroring "opencode run" being un-redirected under
    # worktrail-skill-dispatch), and proves the whole group is gone shortly
    # after the configured timeout fires -- not just the immediate PID.
    marker = tmp_path / "grandchild_still_running_after_timeout"
    script = tmp_path / "spawn_and_hang.sh"
    script.write_text(
        "#!/bin/sh\n"
        # Backgrounded grandchild: touches a marker file every 0.2s forever,
        # inheriting this script's own (unredirected) stdout/stderr and
        # process group -- exactly what worktrail-skill-dispatch's own
        # un-redirected Popen call produces for the real provider CLI.
        f"( while true; do touch '{marker}'; sleep 0.2; done ) &\n"
        "GRANDCHILD_PID=$!\n"
        f"echo $GRANDCHILD_PID > '{tmp_path}/grandchild.pid'\n"
        # The immediate child itself then hangs (simulating a stuck
        # worktrail-skill-dispatch waiting on the provider CLI).
        "while true; do sleep 1; done\n"
    )
    script.chmod(0o755)

    outcome = run_one_shot([str(script)], timeout=1)
    assert outcome.exit_code == 124

    grandchild_pid = int((tmp_path / "grandchild.pid").read_text().strip())
    # Give the OS a brief moment to actually reap the killed processes, then
    # confirm the grandchild is gone -- both by PID and by it no longer
    # touching the marker file. Bounded, not a real-timeout-length wait.
    import time as _time
    deadline = _time.time() + 5
    grandchild_alive = True
    while _time.time() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            grandchild_alive = False
            break
        _time.sleep(0.1)
    assert not grandchild_alive, (
        "grandchild process outlived the parent's timeout -- process-group "
        "kill did not reach it")


# ---------------------------------------------------------------------------
# Stale-bookkeeping finder


def _write_stale_bookkeeping_spec(repo: Path, spec_id: str, task_id: str = "TASK-001") -> None:
    """A spec with one pending impl task whose `files:` are already git-tracked
    on `repo`'s current branch -- the fixture shape dashboard.detect_stage
    requires to label a spec "stale-bookkeeping" (mirrors
    tests/router/test_dashboard.py's StaleBookkeeping fixture)."""
    spec_dir = repo / "docs" / "specs" / spec_id
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (spec_dir / "2026-05-29--feature.md").write_text(
        f"# Feature Specification: X\n\n**ID**: {spec_id}\n\n## Summary\nstuff\n"
    )
    shipped_rel = f"src/{spec_id}/shipped.py"
    (tasks_dir / f"{task_id}.md").write_text(
        f"---\nid: {task_id}\nstatus: pending\nkind: impl\n"
        f"files: [{shipped_rel}]\ndependencies: []\n---\n# {task_id}\n"
    )
    shipped = repo / shipped_rel
    shipped.parent.mkdir(parents=True, exist_ok=True)
    shipped.write_text("shipped\n")
    for cmd in (["add", shipped_rel], ["commit", "-qm", "ship"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True)


def _init_git_repo(path: Path) -> None:
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *cmd], check=True)


def test_find_stale_bookkeeping_specs_discovers_across_repos(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _init_git_repo(repo_a)
    _write_stale_bookkeeping_spec(repo_a, "spec-a")
    repo_b = _make_repo(tmp_path, "repo-b")
    _init_git_repo(repo_b)  # clean repo, no stale-bookkeeping spec

    found = find_stale_bookkeeping_specs(tmp_path)

    assert [f["repo_name"] for f in found] == ["repo-a"]
    assert found[0]["spec_id"] == "spec-a"
    assert found[0]["stale_task_ids"] == ["TASK-001"]


def test_find_stale_bookkeeping_specs_excludes_non_stale_stage(tmp_path):
    repo = _make_repo(tmp_path, "repo-a")
    _init_git_repo(repo)
    # A verify-pending spec is a different stage entirely -- must not be
    # picked up by the stale-bookkeeping finder.
    _write_verify_pending_spec(repo, "spec-a", "https://github.com/test/repo/pull/1")

    assert find_stale_bookkeeping_specs(tmp_path) == []


def test_find_stale_bookkeeping_specs_go_repo_filter(tmp_path):
    repo_a = _make_repo(tmp_path, "repo-a")
    _init_git_repo(repo_a)
    _write_stale_bookkeeping_spec(repo_a, "spec-a")
    repo_b = _make_repo(tmp_path, "repo-b")
    _init_git_repo(repo_b)
    _write_stale_bookkeeping_spec(repo_b, "spec-b")

    found = find_stale_bookkeeping_specs(tmp_path, go_repo="repo-b")

    assert [f["repo_name"] for f in found] == ["repo-b"]


# ---------------------------------------------------------------------------
# close_stale_bookkeeping action


def _init_repo_with_origin(tmp_path: Path, name: str) -> Path:
    """A real repo with a real (bare, local-filesystem) `origin` remote on
    branch `dev`, so `close_stale_bookkeeping`'s `git push` succeeds without
    any network access -- `_base_branch_for` falls back to "dev" when no
    go-policy.yaml is present, so the fixture's default branch must match."""
    bare = tmp_path / f"{name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = tmp_path / "projects" / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "dev"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "dev"], check=True)
    return repo


def _fake_gh_subprocess_run(pr_url: str):
    """Real `git`/other commands pass through to the real subprocess.run;
    the two gh-pr-related calls are faked so the test needs no network or
    `gh` auth. Mirrors test_pr_creation_callsite_enforcement_coverage.py's
    drain.py proof fake."""
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "create":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{pr_url}\n", stderr="")
        return real_run(cmd, **kwargs)

    return fake_run


def test_close_stale_bookkeeping_flips_status_and_opens_pr(tmp_path, monkeypatch):
    repo = _init_repo_with_origin(tmp_path, "repo-a")
    _write_stale_bookkeeping_spec(repo, "spec-a")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed spec"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "dev"], check=True)

    finding = {"repo": repo, "repo_name": "repo-a", "spec_id": "spec-a",
               "stale_task_ids": ["TASK-001"]}

    monkeypatch.setattr(drain.subprocess, "run",
                         _fake_gh_subprocess_run("https://example.invalid/pr/9"))

    result = close_stale_bookkeeping(
        finding, "claude", 30, lambda c, t: SpawnOutcome(0), lambda _l: None)

    assert result == {"repo": "repo-a", "spec_id": "spec-a",
                       "task_ids": ["TASK-001"], "pr_url": "https://example.invalid/pr/9"}
    # The flip lands on the fix branch (pushed, PR opened), not on `repo`'s
    # own checked-out `dev` -- that branch is never touched by the action.
    flipped = subprocess.run(
        ["git", "-C", str(repo), "show", "fix/close-stale-spec-a:docs/specs/spec-a/tasks/TASK-001.md"],
        capture_output=True, text=True, check=True).stdout
    assert "status: completed" in flipped


def test_close_stale_bookkeeping_missing_task_file_raises(tmp_path):
    repo = _init_repo_with_origin(tmp_path, "repo-a")
    finding = {"repo": repo, "repo_name": "repo-a", "spec_id": "spec-a",
               "stale_task_ids": ["TASK-GHOST"]}

    with pytest.raises(RuntimeError, match="no TASK-\\*.md found"):
        close_stale_bookkeeping(
            finding, "claude", 30, lambda c, t: SpawnOutcome(0), lambda _l: None)


def test_close_stale_bookkeeping_gh_pr_create_failure_raises(tmp_path, monkeypatch):
    repo = _init_repo_with_origin(tmp_path, "repo-a")
    _write_stale_bookkeeping_spec(repo, "spec-a")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed spec"], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "dev"], check=True)

    finding = {"repo": repo, "repo_name": "repo-a", "spec_id": "spec-a",
               "stale_task_ids": ["TASK-001"]}

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "list":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["gh", "pr"] and cmd[2] == "create":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="label not found")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(drain.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="gh pr create failed"):
        close_stale_bookkeeping(
            finding, "claude", 30, lambda c, t: SpawnOutcome(0), lambda _l: None)


def test_nightly_drain_summary_contract_distinguishes_capacity_and_breaker():
    contract = load_nightly_drain_summary_contract()

    assert contract["contract"] == "worktrail.nightly-drain-summary"
    assert contract["version"] == 1
    assert stop_semantics(
        "capacity_gated: provider capacity gate persisted for claude"
    ) == {"kind": "capacity_gated", "operator_alert": False}
    assert stop_semantics(
        "circuit_breaker: 2 consecutive failed iterations"
    ) == {"kind": "circuit_breaker", "operator_alert": True}


# ---------------------------------------------------------------------------
# Backlog seeding (pre-loop queue top-up)


def _make_needs_tasks_repo(repos_root, name, spec_id):
    repo = repos_root / name
    (repo / ".git").mkdir(parents=True)
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Spec\n\nApproved, no markers.\n")
    return repo


def test_drain_seeds_backlog_before_loop(tmp_path, monkeypatch):
    repos_root = tmp_path / "projects"
    _make_needs_tasks_repo(repos_root, "repo-a", "010-alpha")
    queue_base = tmp_path / "wq"
    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue_base))
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, repos_root=repos_root)

    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0),
                          log=lambda _l: None)

    seeded = summary["seeded_backlog"]["seeded"]
    assert [s["seed_key"] for s in seeded] == ["repo-a:spec:010-alpha"]
    assert len(list((queue_base / "queue").glob("*.md"))) == 1


def _make_ready_to_implement_repo(repos_root, name, spec_id):
    repo = repos_root / name
    (repo / ".git").mkdir(parents=True)
    specs_dir = repo / "docs" / "specs"
    spec_dir = specs_dir / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "user-request.md").write_text("# User Request\n")
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: pending\nkind: impl\n---\n")
    (specs_dir / "go-policy.yaml").write_text("allow_seeded_implementation: true\n")
    return repo


def test_drain_seeds_ready_to_implement_backlog_for_opted_in_repo(tmp_path, monkeypatch):
    # Regression guard for task 3.2's "no drain.py code changes needed" claim:
    # a Route D implementation brief for a ready-to-implement spec must reach
    # the run summary's seeded_backlog.seeded through the existing wiring.
    repos_root = tmp_path / "projects"
    _make_ready_to_implement_repo(repos_root, "repo-a", "020-beta")
    queue_base = tmp_path / "wq"
    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue_base))
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, repos_root=repos_root)

    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0),
                          log=lambda _l: None)

    seeded = summary["seeded_backlog"]["seeded"]
    assert [s["seed_key"] for s in seeded] == ["repo-a:impl:020-beta"]
    assert seeded[0]["kind"] == "ready-to-implement"
    brief_files = list((queue_base / "queue").glob("*.md"))
    assert len(brief_files) == 1
    assert "recommended-route: D" in brief_files[0].read_text()


def test_drain_no_seed_backlog_opt_out(tmp_path, monkeypatch):
    repos_root = tmp_path / "projects"
    _make_needs_tasks_repo(repos_root, "repo-a", "010-alpha")
    queue_base = tmp_path / "wq"
    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue_base))
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, repos_root=repos_root, seed_backlog=False)

    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0),
                          log=lambda _l: None)

    assert summary["seeded_backlog"] == {}
    assert not (queue_base / "queue").is_dir()


def test_drain_seed_backlog_failure_never_aborts(tmp_path, monkeypatch):
    repos_root = tmp_path / "projects"
    (repos_root / "repo-a" / ".git").mkdir(parents=True)
    fake = FakeQueue([0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, repos_root=repos_root)

    def boom(*_a, **_k):
        raise RuntimeError("queue dir unwritable")

    monkeypatch.setattr(drain.seed_backlog_mod, "seed_backlog", boom)
    logs = []
    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0),
                          log=logs.append)
    assert summary["stopped"].startswith("queue_empty")
    assert any("seed-backlog error" in line for line in logs)


def test_drain_dry_run_never_seeds(tmp_path, monkeypatch):
    repos_root = tmp_path / "projects"
    _make_needs_tasks_repo(repos_root, "repo-a", "010-alpha")
    queue_base = tmp_path / "wq"
    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue_base))
    fake = FakeQueue([1])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, repos_root=repos_root, dry_run=True)

    summary = drain.drain(config, spawner=lambda c, t: SpawnOutcome(0),
                          log=lambda _l: None)
    assert summary["seeded_backlog"] == {}
    assert not (queue_base / "queue").is_dir()


# ---------------------------------------------------------------------------
# Decision-queue awareness


def _file_open_decision(queue_base, name):
    d = queue_base / "decisions" / "open"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        "---\nid: " + name + "\nstatus: open\n---\n\n## Question\n\nQ?\n")


def test_drain_decision_filed_block_skips_circuit_breaker(tmp_path, monkeypatch):
    queue_base = tmp_path / "wq"
    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue_base))
    fake = FakeQueue([3, 2, 2, 1, 1, 0, 0])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    n = {"spawned": 0}

    def spawner(cmd, timeout):
        n["spawned"] += 1
        _file_open_decision(queue_base, f"20260813-12000{n['spawned']}-q")
        write_run_record(config.runs_dir, f"go-{n['spawned']}",
                         "blocked_product_decision")
        return SpawnOutcome(0)

    logs = []
    summary = drain.drain(config, spawner=spawner, log=logs.append)
    # threshold is 2: two decision-less blocked iterations would trip the
    # breaker; decision-filed blocks must not.
    assert n["spawned"] == 3
    assert summary["stopped"].startswith("queue_empty")
    assert all(i["decisions_filed"] for i in summary["iterations"])
    assert any("decision filed for a human" in line for line in logs)
    assert summary["decisions_open"] == 3
    assert any("decisions awaiting a human: 3" in line for line in logs)


def test_drain_decisionless_block_still_trips_breaker(tmp_path, monkeypatch):
    queue_base = tmp_path / "wq"
    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue_base))
    fake = FakeQueue([3, 2, 2, 1])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    n = {"spawned": 0}

    def spawner(cmd, timeout):
        n["spawned"] += 1
        write_run_record(config.runs_dir, f"go-{n['spawned']}",
                         "blocked_product_decision")
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert n["spawned"] == 2
    assert summary["stopped"].startswith("circuit_breaker")
    assert summary["decisions_open"] == 0


# ---------------------------------------------------------------------------
# Operator-config agent defaults (CLI > config file > built-in)


def _run_main(tmp_path, monkeypatch, argv_extra=(), config_payload=None):
    home = tmp_path / "wt-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WORKTRAIL_HOME", str(home))
    if config_payload is not None:
        (home / "config.json").write_text(config_payload, encoding="utf-8")
    wq = tmp_path / "work_queue.py"
    wq.write_text("# placeholder\n")
    captured = {}

    def fake_drain(config, **_kw):
        captured["config"] = config
        return {"stopped": "queue_empty", "iterations": []}

    monkeypatch.setattr(drain, "drain", fake_drain)
    rc = drain.main(["--work-queue-py", str(wq), *argv_extra])
    return rc, captured.get("config")


def test_main_defaults_to_claude_without_config(tmp_path, monkeypatch):
    rc, config = _run_main(tmp_path, monkeypatch)
    assert rc == 0
    assert config.agent == "claude"
    assert config.fallback_agents == []


def test_main_honors_operator_config_agents(tmp_path, monkeypatch):
    rc, config = _run_main(
        tmp_path, monkeypatch,
        config_payload='{"drain": {"agent": "opencode", '
                       '"fallback_agents": ["claude", "codex"]}}')
    assert rc == 0
    assert config.agent == "opencode"
    assert config.fallback_agents == ["claude", "codex"]


def test_main_cli_flags_override_operator_config(tmp_path, monkeypatch):
    rc, config = _run_main(
        tmp_path, monkeypatch,
        argv_extra=["--agent", "codex", "--fallback-agent", "claude"],
        config_payload='{"drain": {"agent": "opencode", '
                       '"fallback_agents": ["codex"]}}')
    assert rc == 0
    assert config.agent == "codex"
    assert config.fallback_agents == ["claude"]


def test_main_rejects_unsupported_config_agent(tmp_path, monkeypatch, capsys):
    rc, config = _run_main(
        tmp_path, monkeypatch,
        config_payload='{"drain": {"agent": "deepseek"}}')
    assert rc == 2
    assert config is None
    err = capsys.readouterr().err
    assert "deepseek" in err and "config.json" in err


def test_main_fails_loud_on_malformed_config(tmp_path, monkeypatch, capsys):
    rc, config = _run_main(tmp_path, monkeypatch, config_payload="{broken")
    assert rc == 2
    assert config is None
    assert "not valid JSON" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Operator-config max_workers defaults (CLI > config file > built-in)


def test_main_max_workers_flag_overrides_config(tmp_path, monkeypatch):
    rc, config = _run_main(
        tmp_path, monkeypatch,
        argv_extra=["--max-workers", "4"],
        config_payload='{"drain": {"max_workers": 3}}')
    assert rc == 0
    assert config.max_workers == 4


def test_main_max_workers_uses_config_when_flag_omitted(tmp_path, monkeypatch):
    rc, config = _run_main(
        tmp_path, monkeypatch,
        config_payload='{"drain": {"max_workers": 3}}')
    assert rc == 0
    assert config.max_workers == 3


def test_main_max_workers_builtin_default_when_neither_present(tmp_path, monkeypatch):
    rc, config = _run_main(tmp_path, monkeypatch)
    assert rc == 0
    assert config.max_workers == 2


# ---------------------------------------------------------------------------
# Stale-branch finder + prune action


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                    capture_output=True, text=True)


def _init_branch_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "--allow-empty", "-m", "init")
    return repo


def _merged_branch(repo: Path, branch: str) -> None:
    _git(repo, "checkout", "-b", branch, "main")
    (repo / f"{branch.replace('/', '-')}.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"{branch} work")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", branch, "-m", f"merge {branch}")


def _no_gh(fn, *args, **kwargs):
    """Runs `fn` with `gh` calls forced to \"no merged PR\" so ancestry/cherry
    stays the only signal in play -- every fixture below uses plain `--no-ff`
    merges, which ancestry alone already proves."""
    real_run = subprocess.run

    def _side_effect(cmd, *a, **k):
        if cmd and cmd[0] == "gh":
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="")
        return real_run(cmd, *a, **k)

    with mock.patch("subprocess.run", side_effect=_side_effect):
        return fn(*args, **kwargs)


def test_find_stale_branches_discovers_merged_branch_across_repos(tmp_path):
    repo_a = _init_branch_repo(tmp_path, "repo-a")
    _merged_branch(repo_a, "topic")
    repo_b = _init_branch_repo(tmp_path, "repo-b")  # nothing merged

    found = _no_gh(find_stale_branches, tmp_path)

    assert [f["repo_name"] for f in found] == ["repo-a"]
    assert found[0]["branch"] == "topic"
    assert found[0]["method"] == "ancestry"
    assert found[0]["worktree_path"] is None


def test_find_stale_branches_go_repo_filter(tmp_path):
    repo_a = _init_branch_repo(tmp_path, "repo-a")
    _merged_branch(repo_a, "topic")
    repo_b = _init_branch_repo(tmp_path, "repo-b")
    _merged_branch(repo_b, "topic")

    found = _no_gh(find_stale_branches, tmp_path, go_repo="repo-b")

    assert [f["repo_name"] for f in found] == ["repo-b"]


def test_find_stale_branches_excludes_unmerged(tmp_path):
    repo = _init_branch_repo(tmp_path, "repo-a")
    _git(repo, "checkout", "-b", "topic", "main")
    (repo / "topic.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "topic.txt")
    _git(repo, "commit", "-m", "topic work")
    _git(repo, "checkout", "main")

    assert _no_gh(find_stale_branches, tmp_path) == []


def test_prune_stale_branch_deletes_branch_with_no_worktree():
    tmp_path = Path(tempfile.mkdtemp())
    repo = _init_branch_repo(tmp_path, "repo-a")
    _merged_branch(repo, "topic")
    finding = {"repo": repo, "repo_name": "repo-a", "branch": "topic",
               "worktree_path": None, "method": "ancestry"}

    result = prune_stale_branch(finding, "claude", 30, None, lambda _l: None)

    assert result == {"repo": "repo-a", "branch": "topic",
                       "method": "ancestry", "pruned": True}
    remaining = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "topic" not in remaining


def test_prune_stale_branch_removes_worktree_first():
    tmp_path = Path(tempfile.mkdtemp())
    repo = _init_branch_repo(tmp_path, "repo-a")
    _merged_branch(repo, "topic")
    wt = tmp_path / "repo-a-worktrees" / "topic"
    _git(repo, "worktree", "add", str(wt), "topic")
    finding = {"repo": repo, "repo_name": "repo-a", "branch": "topic",
               "worktree_path": str(wt), "method": "ancestry"}

    result = prune_stale_branch(finding, "claude", 30, None, lambda _l: None)

    assert result["pruned"] is True
    assert not wt.exists()
    remaining = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "topic" not in remaining


def test_prune_stale_branch_raises_when_worktree_dirty():
    """A branch that went dirty between the finder's read and this action's
    write must not be force-removed -- `git worktree remove` (no `--force`)
    raises, the sweep engine's per-finding try/except catches it, and the
    branch survives for the next sweep to re-evaluate."""
    tmp_path = Path(tempfile.mkdtemp())
    repo = _init_branch_repo(tmp_path, "repo-a")
    _merged_branch(repo, "topic")
    wt = tmp_path / "repo-a-worktrees" / "topic"
    _git(repo, "worktree", "add", str(wt), "topic")
    (wt / "uncommitted.txt").write_text("wip\n", encoding="utf-8")
    finding = {"repo": repo, "repo_name": "repo-a", "branch": "topic",
               "worktree_path": str(wt), "method": "ancestry"}

    with pytest.raises(RuntimeError):
        prune_stale_branch(finding, "claude", 30, None, lambda _l: None)

    assert wt.exists()
    remaining = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "topic" in remaining

