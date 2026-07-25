"""Unit tests for drain.py — no live CLI calls; the spawner is always faked."""

import json
import os

import pytest

from worktrail.drain import drain
from worktrail.drain.drain import (
    DrainConfig,
    LoopState,
    Outcome,
    acquire_lock,
    build_command,
    capacity_gated,
    classify_outcome,
    count_ready_briefs,
    decide,
    newest_run_record,
    parse_run_record,
    release_lock,
)


# ---------------------------------------------------------------------------
# build_command


def test_build_command_claude_default_has_no_permission_flags():
    assert build_command("claude", []) == ["claude", "-p", "/go auto"]


def test_build_command_claude_permission_args_are_explicit_passthrough():
    cmd = build_command("claude", ["--dangerously-skip-permissions"])
    assert cmd == ["claude", "-p", "/go auto", "--dangerously-skip-permissions"]


def test_build_command_opencode_and_codex_shapes():
    assert build_command("opencode", []) == ["opencode", "run", "/go auto"]
    assert build_command("codex", []) == [
        "codex", "exec", "-s", "workspace-write", "/go auto"]


def test_build_command_template_overrides_agent_shape():
    cmd = build_command("claude", ["--ignored-by-template"],
                        template="mycli --oneshot {prompt}")
    assert cmd == ["mycli", "--oneshot", "/go auto"]


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


def test_classify_outcome_no_record_no_claim_clean_exit_is_no_pick():
    assert classify_outcome(None, claimed_delta=0, exit_code=0).kind == "no_pick"


def test_classify_outcome_no_record_nonzero_exit_is_failure():
    assert classify_outcome(None, claimed_delta=0, exit_code=124).kind == "failed"


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
        return 0

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
        return 1  # no run record, nonzero exit → failure

    summary = drain.drain(config, spawner=spawner, log=lambda _line: None)
    assert len(summary["iterations"]) == 2
    assert summary["stopped"].startswith("circuit_breaker")


def test_drain_stops_on_no_pick(tmp_path, monkeypatch):
    fake = FakeQueue([3])  # queue never shrinks
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    summary = drain.drain(config, spawner=lambda c, t: 0, log=lambda _l: None)
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
        return 0

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
        return 0

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
        return 0

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert n["spawned"] == 2
    assert summary["pending_approvals"] == ["https://pr/1"]
    assert summary["stopped"].startswith("queue_empty")


def test_drain_capacity_gate_stops_after_blocked_iteration(tmp_path, monkeypatch):
    fake = FakeQueue([4, 4, 4])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    config.capacity_cache.write_text(json.dumps(
        {"providers": {"claude": {"status": "gated"}}}))

    def spawner(cmd, timeout):
        write_run_record(config.runs_dir, "go-1", "blocked_external_dependency")
        return 0

    summary = drain.drain(config, spawner=spawner, log=lambda _l: None)
    assert len(summary["iterations"]) == 1
    assert summary["stopped"].startswith("capacity_gated")


def test_drain_refuses_when_lock_held(tmp_path, monkeypatch):
    fake = FakeQueue([1])
    install_fake_queue(monkeypatch, fake)
    config = make_config(tmp_path)
    assert acquire_lock(config.lock_file) is True  # our own live pid holds it
    summary = drain.drain(config, spawner=lambda c, t: 0, log=lambda _l: None)
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


def test_build_command_go_repo_scopes_the_prompt():
    assert build_command("claude", [], go_repo="ggb") == [
        "claude", "-p", "/go ggb auto"]
    assert build_command("claude", [], template="x {prompt}",
                         go_repo="ggb") == ["x", "/go ggb auto"]
