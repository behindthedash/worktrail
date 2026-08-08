"""Unit tests for drain.py — no live CLI calls; the spawner is always faked."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from worktrail.drain import drain
from worktrail.drain.drain import (
    MAX_TRANSCRIPT_FILES,
    DrainConfig,
    LoopState,
    Outcome,
    SpawnOutcome,
    acquire_lock,
    build_command,
    build_full_real_resume_command,
    capacity_gated,
    claimed_brief_ids,
    classify_outcome,
    count_ready_briefs,
    decide,
    ensure_pr_risk_label,
    find_resumable_quarantines,
    find_verify_pending_specs,
    newest_run_record,
    parse_run_record,
    release_lock,
    resolve_spec_rel,
    resume_quarantined_budget_exhausted,
    resume_verify_pending,
    select_available_agent,
    write_iteration_transcript,
)


# ---------------------------------------------------------------------------
# build_command


def test_build_command_claude_default_has_no_permission_flags():
    assert build_command("claude", []) == ["claude", "-p", "worktrail-go auto"]


def test_build_command_claude_permission_args_are_explicit_passthrough():
    cmd = build_command("claude", ["--dangerously-skip-permissions"])
    assert cmd == ["claude", "-p", "worktrail-go auto", "--dangerously-skip-permissions"]


def test_build_command_opencode_and_codex_shapes():
    assert build_command("opencode", []) == ["opencode", "run", "worktrail-go auto"]
    assert build_command("codex", []) == [
        "codex", "exec", "-s", "workspace-write", "worktrail-go auto"]


def test_build_command_template_overrides_agent_shape():
    cmd = build_command("claude", ["--ignored-by-template"],
                        template="mycli --oneshot {prompt}")
    assert cmd == ["mycli", "--oneshot", "worktrail-go auto"]


def test_build_command_template_without_prompt_placeholder_rejected():
    with pytest.raises(ValueError):
        build_command("claude", [], template="mycli --oneshot")


def test_build_command_unknown_agent_rejected():
    with pytest.raises(ValueError):
        build_command("gemini", [])


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


# ---------------------------------------------------------------------------
# outcome classification


def test_classify_outcome_success_states():
    for state in ("completed_and_merged", "completed_pr_open",
                  "completed_awaiting_human_approval"):
        out = classify_outcome({"final_status": state}, claimed_delta=1, exit_code=0)
        assert out.kind == "success" and out.state == state


def test_classify_outcome_blocked_and_failed_states():
    assert classify_outcome({"final_status": "blocked_external_dependency"},
                            1, 0).kind == "blocked"
    assert classify_outcome({"final_status": "failed_terminal"}, 1, 0).kind == "failed"


def test_classify_outcome_unfinished_record_is_failure():
    out = classify_outcome({"final_status": None}, claimed_delta=1, exit_code=1)
    assert out.kind == "failed"


def test_classify_outcome_clean_exit_unfinished_record_is_pending_not_failed():
    # Regression (brief 20260806-084302): a Route-C run that stops to ask the
    # operator an implementation-intent question, or a Route-G run that
    # dispatches an async background pipeline and returns, both exit 0
    # without the run record ever reaching a terminal final_status. Neither
    # is the one-shot process crashing, so it must not count as a plain
    # circuit-breaker failure -- but it also never reached a completion
    # state, so (like timeout_after_pr) it must not reset the counter either.
    out = classify_outcome({"final_status": None}, claimed_delta=1, exit_code=0)
    assert out.kind == "pending"


def test_classify_outcome_clean_exit_unfinished_record_attributes_claimed_brief():
    out = classify_outcome({"final_status": None}, claimed_delta=1, exit_code=0,
                           claimed_briefs=["20260806-brief"])
    assert out.kind == "pending" and out.brief_id == "20260806-brief"


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


def test_capacity_gated_partial_gate_does_not_stop():
    cache = {"providers": {
        "claude": {"status": "gated"},
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


def test_decide_pending_outcome_does_not_trip_circuit_breaker():
    # Two consecutive "pending" outcomes (clean-exit, unfinished record) must
    # not read as consecutive_failures -- the drain loop never increments
    # the counter for this kind, so decide() sees it unchanged below threshold.
    d = decide(make_state(last_outcome=Outcome("pending"),
                          consecutive_failures=0), now=0)
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


def test_drain_pending_outcome_does_not_trip_circuit_breaker(tmp_path, monkeypatch):
    # Regression (brief 20260806-084302): a run that exits 0 (the one-shot
    # process itself did not crash) but leaves its run record unfinished --
    # e.g. Route C stopping to ask an implementation-intent question, or
    # Route G returning after dispatching an async background pipeline --
    # must not trip the 2-consecutive-failure breaker the way real crashes do.
    fake = FakeQueue([5])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path, max_items=3, failure_threshold=2)
    calls = {"n": 0}

    def spawner(cmd, timeout):
        calls["n"] += 1
        write_run_record(config.runs_dir, f"go-{calls['n']}", None)
        return SpawnOutcome(0)

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 3
    assert all(i["kind"] == "pending" for i in summary["iterations"])
    assert summary["stopped"].startswith("max_items")


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
    assert seen_cmds == [["opencode", "run", "worktrail-go auto"]]
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
    assert build_command("claude", [], go_repo="ggb") == [
        "claude", "-p", "worktrail-go ggb auto"]
    assert build_command("claude", [], template="x {prompt}",
                         go_repo="ggb") == ["x", "worktrail-go ggb auto"]


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
        tmp_path, None, "claude", 60, spawner, logs.append)
    assert len(calls) == 1
    assert calls[0][:2] == ["worktrail-live", "full-real"]
    assert "--fresh" not in calls[0]
    assert result == [{"repo": "repo-a", "spec_id": "spec-a", "exit_code": 0}]
    assert any("resume-verify-pending" in line for line in logs)


def test_resume_verify_pending_no_hits_is_noop(tmp_path):
    _make_repo(tmp_path, "repo-a")  # no verify-pending spec at all
    calls = []
    result = resume_verify_pending(
        tmp_path, None, "claude", 60, lambda c, t: calls.append(c), lambda _l: None)
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
        tmp_path, None, "claude", 60, spawner, lambda _l: None)
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
        tmp_path, None, "claude", 60, spawner, logs.append)
    assert len(calls) == 1
    assert calls[0][:2] == ["worktrail-live", "full-real"]
    assert "--fresh" not in calls[0]
    assert result == [{"repo": "repo-a", "spec_id": "spec-a", "exit_code": 0}]
    assert any("resume-quarantine" in line for line in logs)


def test_resume_quarantined_budget_exhausted_no_resumable_is_noop(tmp_path):
    _make_repo(tmp_path, "repo-a")  # no journal at all
    calls = []
    result = resume_quarantined_budget_exhausted(
        tmp_path, None, "claude", 60, lambda c, t: calls.append(c), lambda _l: None)
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
