import json
import threading
import time
from datetime import datetime, timedelta, timezone

from worktrail.orchestrator import agent_capacity, spawnlib
from worktrail.runtime.selection import NoExecutionTarget


def _preflight_target(harness, pool="subscription"):
    return {"harness": harness, "pool": pool, "api_opt_in": False, "auth": None}


def _preflight_routing():
    """A two-cell `resolve_routing()`-shaped dict (claude preferred, opencode
    next) for patching `spawnlib.resolve_routing` in a preflight-gating test --
    same shape as `tests/orchestrator/test_spawnlib.py`'s own routing fixtures."""
    return {
        "targets": {
            "claude": _preflight_target("claude"),
            "opencode": _preflight_target("opencode"),
        },
        "tiers": {
            "t2-build": {
                "claude": {"model": "sonnet", "effort": None},
                "opencode": {
                    "model": "opencode/deepseek-v4-flash-free",
                    "effort": None,
                },
            },
        },
        "roles": {},
        "purposes": {},
        "default_tier": "t2-build",
        "drain": {},
    }


def test_malformed_cache_recovers(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text("not-json", encoding="utf-8")

    assert agent_capacity.load(path) == {"version": 1, "providers": {}}


def test_expired_cooldown_allows_provider(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now - timedelta(seconds=1),
        path=path,
        now=now,
    )

    agent_capacity.check("claude", "sonnet", path=path, now=now)


def test_active_cooldown_blocks_provider(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    state = agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=1),
        path=path,
        now=now,
    )

    try:
        agent_capacity.check("claude", "sonnet", path=path, now=now)
    except agent_capacity.ProviderUnavailable as exc:
        assert exc.state == state
    else:
        raise AssertionError("active provider cooldown was ignored")


def test_save_is_atomic_and_json(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.record("codex", "gpt", outcome="available", path=path)

    assert (
        json.loads(path.read_text(encoding="utf-8"))["providers"]["codex:gpt"]["status"]
        == "available"
    )
    assert list(tmp_path.glob(".capacity.json.*")) == []


def test_unknown_transport_does_not_get_a_reset_time():
    assert agent_capacity.classify_failure(1, "", "network disconnected") == "transport"


def test_usage_limit_wording_classifies_as_billing():
    # Live reproduction 2026-08-02 (drain-nightly iteration 2): Codex's own
    # usage-cap wording used to fall through to the generic "transport" class
    # (30s cooldown) instead of the multi-hour/day reset a real account-level
    # cap needs.
    stdout = (
        "ERROR: You've hit your usage limit. Upgrade to Pro "
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more "
        "credits or try again at Aug 8th, 2026 2:17 AM."
    )
    assert agent_capacity.classify_failure(1, stdout, "") == "billing"


def test_session_limit_wording_also_classifies_as_billing():
    assert (
        agent_capacity.classify_failure(
            1, "", "You've hit your session limit. Your limit resets at 3:00pm."
        )
        == "billing"
    )


def test_weekly_limit_wording_also_classifies_as_billing():
    # Live reproduction 2026-08-05 (drain-nightly 2026-08-05T09-17-01Z): Claude's
    # own weekly-cap wording used to fall through to "transport", so two
    # consecutive weekly-limit hits tripped the circuit breaker as plain
    # failures instead of gating the provider as a capacity issue.
    assert (
        agent_capacity.classify_failure(
            1, "You've hit your weekly limit · resets 2pm (America/Los_Angeles)", ""
        )
        == "billing"
    )


def test_parse_explicit_reset_extracts_codex_notice():
    stdout = (
        "ERROR: You've hit your usage limit. Upgrade to Pro "
        "(https://chatgpt.com/explore/pro), visit "
        "https://chatgpt.com/codex/settings/usage to purchase more "
        "credits or try again at Aug 8th, 2026 2:17 AM."
    )
    reset = agent_capacity.parse_explicit_reset(stdout)
    assert reset is not None
    assert (reset.year, reset.month, reset.day) == (2026, 8, 8)
    local_naive = datetime(2026, 8, 8, 2, 17)  # noqa: DTZ001
    assert reset == local_naive.astimezone(timezone.utc)


def test_parse_explicit_reset_accepts_full_month_name_no_ordinal():
    reset = agent_capacity.parse_explicit_reset("try again at August 8, 2026 2:17AM.")
    assert reset is not None and (reset.year, reset.month, reset.day) == (2026, 8, 8)


def test_parse_explicit_reset_returns_none_without_a_timestamp():
    assert agent_capacity.parse_explicit_reset("some unrelated crash text") is None
    assert agent_capacity.parse_explicit_reset("") is None


def test_model_not_found_wording_classifies_as_model_unavailable():
    assert (
        agent_capacity.classify_failure(1, "", "Error: model not found")
        == "model_unavailable"
    )


def test_model_unavailable_default_cooldown_is_one_day():
    assert agent_capacity.DEFAULT_COOLDOWNS["model_unavailable"] == 86400


def test_opencode_generic_error_event_classifies_as_transport():
    # Real shape from a live reproduction (handoff 20260722-152514,
    # /tmp/opencode-error-repro.jsonl): opencode's own error surface only exposed
    # a generic "UnknownError" for what was almost certainly a free-tier rate
    # limit -- no distinguishable rate-limit signal was observed, so per the
    # no-guessing rule this must fall through to the existing generic "transport"
    # class (short retry/backoff) rather than a fabricated "rate_limit" class.
    stdout = json.dumps(
        {
            "type": "error",
            "error": {
                "name": "UnknownError",
                "data": {
                    "message": "Unexpected server error. Check server logs for details.",
                    "ref": "err_55ac1dc1",
                },
            },
        }
    )
    assert agent_capacity.classify_failure(0, stdout, "") == "transport"


def test_opencode_error_event_is_recognized_as_infra_failure():
    # Cross-module regression for the actual bug: exit 0 + non-empty stdout used
    # to be treated as a clean success (agent_capacity.record(outcome="available"))
    # even though the provider itself failed.
    stdout = json.dumps(
        {
            "type": "error",
            "error": {
                "name": "UnknownError",
                "data": {"message": "Unexpected server error."},
            },
        }
    )
    assert spawnlib.is_infra_failure(0, stdout) is True


def test_gate_snapshot_reports_retry_class_and_all_gated(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    for agent, model, failure_class in (
        ("claude", "sonnet", "transport"),
        ("opencode", "safe/model", "auth"),
    ):
        agent_capacity.record(
            agent,
            model,
            outcome="unavailable",
            failure_class=failure_class,
            retry_after=now + timedelta(minutes=5),
            path=path,
            now=now,
        )

    snapshot = agent_capacity.gate_snapshot(
        [
            agent_capacity.provider_key("claude", "sonnet"),
            agent_capacity.provider_key("opencode", "safe/model"),
        ],
        path=path,
        now=now,
    )

    assert snapshot["all_gated"] is True
    assert snapshot["retry_after"] == "2026-07-20T20:05:00+00:00"
    assert [item["failure_class"] for item in snapshot["gated"]] == [
        "transport",
        "auth",
    ]


def test_gate_snapshot_reports_model_unavailable_failure_class(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude-heavy",
        "retired-model",
        outcome="unavailable",
        failure_class="model_unavailable",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=now,
    )

    snapshot = agent_capacity.gate_snapshot(
        [agent_capacity.provider_key("claude-heavy", "retired-model")],
        path=path,
        now=now,
    )

    assert snapshot["gated"] == [
        {
            "provider": "claude-heavy:retired-model",
            "failure_class": "model_unavailable",
            "retry_after": "2026-07-20T21:00:00+00:00",
        }
    ]


def test_record_and_check_key_on_target_names(tmp_path):
    # provider_key/check/record are keyed on routing target names, not
    # harness/agent names -- two different targets sharing the same model
    # must not collide.
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude-heavy",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )

    agent_capacity.check("claude-light", "sonnet", path=path, now=now)
    try:
        agent_capacity.check("claude-heavy", "sonnet", path=path, now=now)
    except agent_capacity.ProviderUnavailable:
        pass
    else:
        raise AssertionError(
            "gated target was not distinguished from a differently named target"
        )


def test_gate_snapshot_ignores_stale_configured_providers_key(tmp_path):
    # Task 7.4: a stale `configured_providers` key left behind in a cache file
    # from before this consolidation (or hand-edited) must never gate or
    # ungate anything -- only the caller's explicit routing-derived
    # `providers` argument decides what gate_snapshot evaluates.
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    data = json.loads(path.read_text())
    # A stale key naming a DIFFERENT provider -- if gate_snapshot ever fell
    # back to reading it, "claude:sonnet" would be dropped from `configured`
    # entirely and never show up as gated.
    data["configured_providers"] = ["codex:gpt"]
    path.write_text(json.dumps(data))

    snapshot = agent_capacity.gate_snapshot(
        [agent_capacity.provider_key("claude", "sonnet")],
        path=path,
        now=now,
    )

    assert snapshot["configured"] == ["claude:sonnet"]
    assert snapshot["all_gated"] is True


def test_preflight_uses_fallback_when_primary_is_gated(tmp_path, monkeypatch):
    path = tmp_path / "capacity.json"
    now = datetime.now(timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=1),
        path=path,
        now=now,
    )
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(path))
    monkeypatch.setattr(
        spawnlib, "resolve_routing", lambda *a, **kw: _preflight_routing()
    )
    monkeypatch.setattr(
        spawnlib.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"result","result":"ok","usage":{}}\n',
                "stderr": "",
            },
        )(),
    )

    result = spawnlib.spawn_agent(
        "prompt", tmp_path, tier="t2-build", sleep=lambda *_: None
    )

    assert result.text == "ok"


def test_status_reads_empty_cache(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    cap = agent_capacity.cmd_status(path=path)
    assert cap == 0


def test_status_lists_all_recorded_providers(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    agent_capacity.record("opencode", "model", outcome="available", path=path, now=now)
    cap = agent_capacity.cmd_status(path=path, now=now)
    assert cap == 0


def test_clear_refuses_unknown_key(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.cmd_clear("unknown:key", "billing resolved", path=path)
    assert rc == 1
    assert json.loads(path.read_text()) == {
        "version": 1,
        "providers": {},
        "configured_providers": [],
    }


def test_clear_refuses_empty_reason(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=agent_capacity._now() + timedelta(minutes=5),
        path=path,
    )
    content_before = path.read_text()
    rc = agent_capacity.cmd_clear("claude:sonnet", "", path=path)
    assert rc == 1
    assert path.read_text() == content_before


def test_clear_refuses_blank_reason(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=agent_capacity._now() + timedelta(minutes=5),
        path=path,
    )
    content_before = path.read_text()
    rc = agent_capacity.cmd_clear("claude:sonnet", "   ", path=path)
    assert rc == 1
    assert path.read_text() == content_before


def test_clear_targeted_removes_exactly_one_provider(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    agent_capacity.record(
        "opencode",
        "model",
        outcome="unavailable",
        failure_class="auth",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    rc = agent_capacity.cmd_clear(
        "claude:sonnet", "transport resolved", path=path, now=now
    )
    assert rc == 0
    data = json.loads(path.read_text())
    assert "claude:sonnet" not in data["providers"]
    assert data["providers"]["opencode:model"]["status"] == "unavailable"


def test_clear_all_removes_all_gates(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    agent_capacity.record(
        "opencode",
        "model",
        outcome="unavailable",
        failure_class="auth",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    rc = agent_capacity.cmd_clear("--all", "all resolved", path=path, now=now)
    assert rc == 0
    data = json.loads(path.read_text())
    assert data["providers"] == {}


def test_clear_rejects_all_without_explicit_flag(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=agent_capacity._now() + timedelta(minutes=5),
        path=path,
    )
    path.read_text()
    rc = agent_capacity.cmd_clear("--all", "resolved", path=path)
    assert rc == 0  # --all is explicit scope


def test_clear_writes_audit_entry(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    rc = agent_capacity.cmd_clear("claude:sonnet", "billing fixed", path=path, now=now)
    assert rc == 0
    data = json.loads(path.read_text())
    assert len(data["audit"]) == 1
    entry = data["audit"][0]
    assert entry["action"] == "clear"
    assert entry["scope"] == "provider"
    assert entry["providers"] == ["claude:sonnet"]
    assert entry["reason"] == "billing fixed"
    assert entry["at"] == "2026-07-20T20:00:00+00:00"


def test_clear_all_writes_audit_entry(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    agent_capacity.record(
        "opencode",
        "model",
        outcome="unavailable",
        failure_class="auth",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    rc = agent_capacity.cmd_clear("--all", "all fixed", path=path, now=now)
    assert rc == 0
    data = json.loads(path.read_text())
    assert len(data["audit"]) == 1
    assert data["audit"][0]["providers"] == ["claude:sonnet", "opencode:model"]


def test_audit_bounds_at_max_entries(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    for i in range(agent_capacity.MAX_AUDIT_ENTRIES + 10):
        agent_capacity.record(
            "claude",
            "sonnet",
            outcome="unavailable",
            failure_class="transport",
            retry_after=now + timedelta(minutes=5),
            path=path,
            now=now,
        )
        agent_capacity.cmd_clear("claude:sonnet", f"fix {i}", path=path, now=now)
    data = json.loads(path.read_text())
    assert len(data["audit"]) == agent_capacity.MAX_AUDIT_ENTRIES


def test_clear_on_malformed_cache_fails_safely(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text("not-json", encoding="utf-8")
    rc = agent_capacity.cmd_clear("claude:sonnet", "fix", path=path)
    assert rc == 1
    assert path.read_text() == "not-json"


def test_main_status_via_cli(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.main(["--cache", str(path), "status"])
    assert rc == 0


def test_main_clear_targeted_via_cli(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    rc = agent_capacity.main(
        ["--cache", str(path), "clear", "claude:sonnet", "--reason", "fixed"]
    )
    assert rc == 0
    data = json.loads(path.read_text())
    assert "claude:sonnet" not in data["providers"]
    assert len(data["audit"]) == 1


def test_main_clear_all_via_cli(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    rc = agent_capacity.main(
        ["--cache", str(path), "clear", "--all", "--reason", "all fixed"]
    )
    assert rc == 0
    data = json.loads(path.read_text())
    assert data["providers"] == {}


def test_main_clear_unknown_key_returns_nonzero(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.main(
        ["--cache", str(path), "clear", "unknown", "--reason", "fix"]
    )
    assert rc == 1


def test_main_clear_missing_reason_returns_nonzero(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.main(["--cache", str(path), "clear", "--all", "--reason", ""])
    assert rc == 1


def test_main_no_command_returns_nonzero(tmp_path):
    rc = agent_capacity.main(["--cache", str(tmp_path / "c.json")])
    assert rc == 1


def _run_racing_writers(tmp_path, monkeypatch, writer_a, writer_b):
    """Race two writer callables against one cache with a widened read window.

    Wraps ``load`` with a post-read sleep so both writers are guaranteed to
    read the same snapshot before either saves -- the exact lost-update
    interleaving two concurrent workers hit at task completion. With
    ``_write_lock`` serializing load->mutate->save, the sleep happens under
    the lock and the second writer sees the first writer's state.
    """
    real_load = agent_capacity.load

    def slow_load(path=None):
        data = real_load(path)
        time.sleep(0.05)
        return data

    monkeypatch.setattr(agent_capacity, "load", slow_load)
    barrier = threading.Barrier(2)
    errors = []

    def run(writer):
        try:
            barrier.wait(timeout=5)
            writer()
        except Exception as exc:  # noqa: BLE001 -- pragma: no cover, failure reporting
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(w,)) for w in (writer_a, writer_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []


def test_concurrent_record_calls_both_survive(tmp_path, monkeypatch):
    # Regression: record() used to load-mutate-save with no lock, so two
    # workers finishing close together (full-real default max_workers=3) could
    # both load the same snapshot and the second os.replace silently dropped
    # the first worker's capacity-gate write.
    path = tmp_path / "capacity.json"
    now = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
    _run_racing_writers(
        tmp_path,
        monkeypatch,
        lambda: agent_capacity.record(
            "claude",
            "sonnet",
            outcome="unavailable",
            failure_class="billing",
            retry_after=now + timedelta(hours=1),
            path=path,
            now=now,
        ),
        lambda: agent_capacity.record(
            "codex",
            "gpt",
            outcome="available",
            path=path,
            now=now,
        ),
    )

    providers = json.loads(path.read_text(encoding="utf-8"))["providers"]
    assert providers["claude:sonnet"]["status"] == "unavailable"
    assert providers["codex:gpt"]["status"] == "available"


def test_concurrent_record_and_clear_both_survive(tmp_path, monkeypatch):
    # record() and cmd_clear() both load-mutate-save the same `providers` dict
    # from different entry points; without the shared lock either save can
    # clobber the other's.
    path = tmp_path / "capacity.json"
    now = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "codex",
        "gpt",
        outcome="unavailable",
        failure_class="transport",
        retry_after=now + timedelta(minutes=5),
        path=path,
        now=now,
    )
    _run_racing_writers(
        tmp_path,
        monkeypatch,
        lambda: agent_capacity.record(
            "claude",
            "sonnet",
            outcome="unavailable",
            failure_class="billing",
            retry_after=now + timedelta(hours=1),
            path=path,
            now=now,
        ),
        lambda: agent_capacity.cmd_clear("codex:gpt", "resolved", path=path, now=now),
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["providers"]["claude:sonnet"]["status"] == "unavailable"
    assert "codex:gpt" not in data["providers"]


def test_concurrent_record_capacity_gate_calls_both_survive(tmp_path, monkeypatch):
    # drain.py's record_capacity_gate does its own load-mutate-save under
    # agent_capacity.write_lock; two workers finishing close together for
    # different agents must not let one save clobber the other's gate.
    from worktrail.drain import drain

    path = tmp_path / "capacity.json"
    # Real clock: record_capacity_gate prunes drain-owned entries whose
    # retry window has already passed, so a fixed past date would let the
    # second writer legitimately drop the first writer's expired gate.
    now = datetime.now(timezone.utc)
    _run_racing_writers(
        tmp_path,
        monkeypatch,
        lambda: drain.record_capacity_gate(
            path, "claude", "billing", now + timedelta(hours=1)
        ),
        lambda: drain.record_capacity_gate(
            path, "codex", "transport", now + timedelta(minutes=5)
        ),
    )

    providers = json.loads(path.read_text(encoding="utf-8"))["providers"]
    assert providers["claude"]["status"] == "unavailable"
    assert providers["claude"]["failure_class"] == "billing"
    assert providers["codex"]["status"] == "unavailable"
    assert providers["codex"]["failure_class"] == "transport"


def test_preflight_reports_all_providers_gated_without_spawning(tmp_path, monkeypatch):
    path = tmp_path / "capacity.json"
    now = datetime.now(timezone.utc)
    for target, model in (
        ("claude", "sonnet"),
        ("opencode", "opencode/deepseek-v4-flash-free"),
    ):
        agent_capacity.record(
            target,
            model,
            outcome="unavailable",
            failure_class="transport",
            retry_after=now + timedelta(minutes=1),
            path=path,
            now=now,
        )
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(path))
    monkeypatch.setattr(
        spawnlib, "resolve_routing", lambda *a, **kw: _preflight_routing()
    )
    called = []
    monkeypatch.setattr(
        spawnlib.subprocess, "run", lambda *args, **kwargs: called.append(args)
    )

    try:
        spawnlib.spawn_agent("prompt", tmp_path, tier="t2-build", sleep=lambda *_: None)
    except NoExecutionTarget:
        pass
    else:
        raise AssertionError("all gated providers should stop before spawn")
    assert called == []


def _routing_fixture():
    return {
        "targets": {
            "claude-sub": {"harness": "claude", "pool": "subscription"},
            "codex-sub": {"harness": "codex", "pool": "subscription"},
        },
        "tiers": {
            "t2-build": {
                "claude-sub": {"model": "sonnet"},
                "codex-sub": {"model": "gpt-5.6-terra"},
            }
        },
        "default_tier": "t2-build",
    }


def test_gate_for_agent_returns_none_when_available(tmp_path):
    path = tmp_path / "capacity.json"
    assert (
        agent_capacity.gate_for_agent(_routing_fixture(), "claude", path=path) is None
    )


def test_gate_for_agent_returns_gate_when_resolved_target_is_unavailable(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude-sub",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=now,
    )
    gate = agent_capacity.gate_for_agent(
        _routing_fixture(), "claude", path=path, now=now
    )
    assert gate is not None
    assert gate["target"] == "claude-sub"
    assert gate["failure_class"] == "billing"


def test_gate_for_agent_never_substitutes_a_different_target(tmp_path):
    """codex-sub sitting healthy must never mask claude-sub's own gate --
    this check answers "is claude gated," not "is anything available"."""
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude-sub",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=now,
    )
    agent_capacity.record(
        "codex-sub", "gpt-5.6-terra", outcome="available", path=path, now=now
    )
    gate = agent_capacity.gate_for_agent(
        _routing_fixture(), "claude", path=path, now=now
    )
    assert gate is not None and gate["target"] == "claude-sub"


def test_gate_for_agent_no_matching_target_degrades_to_proceed(tmp_path):
    path = tmp_path / "capacity.json"
    routing = {"targets": {}, "tiers": {}, "default_tier": None}
    assert agent_capacity.gate_for_agent(routing, "claude", path=path) is None


def test_check_agent_cli_exits_zero_when_available(tmp_path):
    path = tmp_path / "capacity.json"
    rc = agent_capacity.main(
        [
            "--cache",
            str(path),
            "check-agent",
            "--agent",
            "claude",
            "--routing",
            json.dumps(_routing_fixture()),
        ]
    )
    assert rc == 0


def test_check_agent_cli_exits_one_when_gated(tmp_path, capsys):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record(
        "claude-sub",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=now,
    )
    rc = agent_capacity.cmd_check_agent(
        "claude", json.dumps(_routing_fixture()), None, path=path, now=now
    )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["gated"] is True
    assert out["target"] == "claude-sub"


def test_consumed_refresh_token_wording_classifies_as_auth():
    # Live reproduction 2026-09-01 (brief 20260901-175101): codex-sub's ChatGPT
    # refresh token had been consumed. The 401 line already matched "unauthorized";
    # the bare `ERROR:` lines codex also emits must classify as auth on their own.
    stderr = (
        "ERROR: Your access token could not be refreshed because your refresh "
        "token was already used. Please log out and sign in again."
    )
    assert agent_capacity.classify_failure(1, "", stderr) == "auth"


def test_auth_default_cooldown_is_one_day():
    # An auth failure never self-heals (it needs `codex login` / a new key), so a
    # one-hour gate just re-burned the per-spawn retry budget every hour of a
    # multi-hour run. Match model_unavailable's one-day default; operators clear
    # it explicitly once auth is fixed.
    assert agent_capacity.DEFAULT_COOLDOWNS["auth"] == 86400


def _seed_gate(path, key, retry_at, source="drain", status="unavailable"):
    raw = json.loads(path.read_text()) if path.exists() else {"version": 1}
    providers = raw.setdefault("providers", {})
    providers[key] = {
        "status": status,
        "failure_class": "capacity",
        "retry_after": retry_at.isoformat(),
        "source": source,
        "checked_at": retry_at.isoformat(),
    }
    path.write_text(json.dumps(raw))


def test_status_labels_expired_and_active_gates(tmp_path, capsys):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    _seed_gate(path, "claude", now - timedelta(minutes=5))
    _seed_gate(path, "claude-sub:opus", now + timedelta(minutes=5), source="spawn")
    before = path.read_bytes()
    rc = agent_capacity.cmd_status(path=path, now=now)
    assert rc == 0
    out = capsys.readouterr().out
    assert "  claude  unavailable  (expired)" in out
    assert "  claude-sub:opus  unavailable  (active)" in out
    assert path.read_bytes() == before


def test_clear_expired_removes_only_expired_gates(tmp_path, capsys):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    _seed_gate(path, "claude", now - timedelta(minutes=5))
    _seed_gate(path, "codex", now + timedelta(minutes=5))
    agent_capacity.record("opencode", "model", outcome="available", path=path, now=now)
    rc = agent_capacity.cmd_clear("--expired", "hygiene", path=path, now=now)
    assert rc == 0
    data = json.loads(path.read_text())
    assert "claude" not in data["providers"]
    assert "codex" in data["providers"]
    assert "opencode:model" in data["providers"]
    assert len(data["audit"]) == 1
    assert data["audit"][0]["scope"] == "expired"
    assert "cleared: claude" in capsys.readouterr().out


def test_clear_expired_noop_when_nothing_expired(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    _seed_gate(path, "codex", now + timedelta(minutes=5))
    before = path.read_bytes()
    rc = agent_capacity.cmd_clear("--expired", "hygiene", path=path, now=now)
    assert rc == 0
    assert path.read_bytes() == before


def test_main_clear_expired_requires_reason_and_rejects_all(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.main(["--cache", str(path), "clear", "--expired"])
    assert rc == 1
    rc = agent_capacity.main(
        ["--cache", str(path), "clear", "--expired", "--all", "--reason", "x"]
    )
    assert rc == 1
    rc = agent_capacity.main(
        ["--cache", str(path), "clear", "--expired", "--reason", "hygiene"]
    )
    assert rc == 0


def test_probe_lets_first_check_through_after_probe_interval(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(minutes=20)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=checked_at,
    )

    agent_capacity.check("claude", "sonnet", path=path, now=now)

    data = json.loads(path.read_text())
    state = data["providers"]["claude:sonnet"]
    assert state["probe_at"] == now.isoformat()

    try:
        agent_capacity.check(
            "claude", "sonnet", path=path, now=now + timedelta(seconds=1)
        )
    except agent_capacity.ProviderUnavailable:
        pass
    else:
        raise AssertionError("second check right after a probe should still gate")


def test_check_resolves_default_path_through_probe_branch(tmp_path, monkeypatch):
    path = tmp_path / "capacity.json"
    monkeypatch.setenv("WORKTRAIL_AGENT_CAPACITY_CACHE", str(path))
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(minutes=20)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=checked_at,
    )

    agent_capacity.check("claude", "sonnet", now=now)

    data = json.loads(path.read_text())
    state = data["providers"]["claude:sonnet"]
    assert state["probe_at"] == now.isoformat()


def test_probe_does_not_fire_before_the_interval_elapses(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(minutes=5)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=checked_at,
    )
    before = path.read_bytes()

    try:
        agent_capacity.check("claude", "sonnet", path=path, now=now)
    except agent_capacity.ProviderUnavailable:
        pass
    else:
        raise AssertionError("gate within the probe interval should still raise")

    assert path.read_bytes() == before


def test_provider_reset_source_is_never_probed(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(minutes=20)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        reset_source="provider",
        path=path,
        now=checked_at,
    )
    before = path.read_bytes()

    try:
        agent_capacity.check("claude", "sonnet", path=path, now=now)
    except agent_capacity.ProviderUnavailable:
        pass
    else:
        raise AssertionError("provider-stated reset should never be probed through")

    assert path.read_bytes() == before


def test_model_unavailable_is_never_probed(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(minutes=20)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="model_unavailable",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=checked_at,
    )
    before = path.read_bytes()

    try:
        agent_capacity.check("claude", "sonnet", path=path, now=now)
    except agent_capacity.ProviderUnavailable:
        pass
    else:
        raise AssertionError("model_unavailable should never be probed through")

    assert path.read_bytes() == before


def test_entry_without_reset_source_field_is_probeable(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(minutes=20)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=checked_at,
    )
    data = json.loads(path.read_text())
    del data["providers"]["claude:sonnet"]["reset_source"]
    path.write_text(json.dumps(data))

    agent_capacity.check("claude", "sonnet", path=path, now=now)

    data = json.loads(path.read_text())
    assert "probe_at" in data["providers"]["claude:sonnet"]


def test_record_stores_reset_source_and_defaults_to_cooldown(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    state = agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=now,
    )
    assert state["reset_source"] == "cooldown"

    state = agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        reset_source="provider",
        path=path,
        now=now,
    )
    assert state["reset_source"] == "provider"


def test_cmd_status_prints_probed_for_entry_with_probe_at(tmp_path, capsys):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(minutes=20)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=checked_at,
    )
    agent_capacity.check("claude", "sonnet", path=path, now=now)

    rc = agent_capacity.cmd_status(path=path, now=now)
    assert rc == 0
    out = capsys.readouterr().out
    assert "probed:" in out


def test_probe_interval_env_var_overrides_cadence(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_capacity, "PROBE_INTERVAL_S", 60)
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    checked_at = now - timedelta(seconds=61)
    agent_capacity.record(
        "claude",
        "sonnet",
        outcome="unavailable",
        failure_class="billing",
        retry_after=now + timedelta(hours=1),
        path=path,
        now=checked_at,
    )

    agent_capacity.check("claude", "sonnet", path=path, now=now)

    data = json.loads(path.read_text())
    assert "probe_at" in data["providers"]["claude:sonnet"]


def test_probe_interval_reads_env_var_at_import(monkeypatch):
    monkeypatch.setenv("GO_AGENT_GATE_PROBE_INTERVAL", "60")
    import importlib

    reloaded = importlib.reload(agent_capacity)
    try:
        assert reloaded.PROBE_INTERVAL_S == 60
    finally:
        monkeypatch.delenv("GO_AGENT_GATE_PROBE_INTERVAL", raising=False)
        importlib.reload(agent_capacity)


_WEEKLY_NOTICE = "You've hit your weekly limit · resets 2pm (America/Los_Angeles)"


def _pacific():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/Los_Angeles")


def test_lenient_reset_resolves_next_occurrence_in_stated_zone():
    tz = _pacific()
    now = datetime(2026, 8, 5, 9, 0, tzinfo=tz)

    reset = agent_capacity.parse_explicit_reset(_WEEKLY_NOTICE, now)

    assert reset is not None and reset.tzinfo is not None
    assert reset == datetime(2026, 8, 5, 14, 0, tzinfo=tz).astimezone(timezone.utc)


def test_lenient_reset_rolls_to_tomorrow_when_time_has_passed():
    tz = _pacific()
    now = datetime(2026, 8, 5, 16, 0, tzinfo=tz)

    reset = agent_capacity.parse_explicit_reset(_WEEKLY_NOTICE, now)

    assert reset == datetime(2026, 8, 6, 14, 0, tzinfo=tz).astimezone(timezone.utc)


def test_lenient_reset_accepts_at_and_minute_and_spaced_meridiem():
    now = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)

    a = agent_capacity.parse_explicit_reset("resets at 3:00pm", now)
    b = agent_capacity.parse_explicit_reset("resets 3:00 PM", now)

    assert a is not None and b is not None and a == b
    local_now = now.astimezone()
    expected = local_now.replace(hour=15, minute=0, second=0, microsecond=0)
    if expected <= local_now:
        expected += timedelta(days=1)
    assert a == expected.astimezone(timezone.utc)


def test_lenient_reset_matches_capitalised_wording():
    tz = _pacific()
    now = datetime(2026, 8, 5, 9, 0, tzinfo=tz)

    assert agent_capacity.parse_explicit_reset(
        "Resets 2pm (America/Los_Angeles)", now
    ) == datetime(2026, 8, 5, 14, 0, tzinfo=tz).astimezone(timezone.utc)
    assert agent_capacity.parse_explicit_reset(
        "RESETS AT 3:00PM (America/Los_Angeles)", now
    ) == datetime(2026, 8, 5, 15, 0, tzinfo=tz).astimezone(timezone.utc)


def test_lenient_reset_falls_back_to_local_time_without_usable_zone():
    now = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)

    unresolvable = agent_capacity.parse_explicit_reset("resets 2pm (PT)", now)
    zoneless = agent_capacity.parse_explicit_reset("resets 2pm", now)

    assert unresolvable is not None and zoneless is not None
    assert unresolvable == zoneless
    local_now = now.astimezone()
    expected = local_now.replace(hour=14, minute=0, second=0, microsecond=0)
    if expected <= local_now:
        expected += timedelta(days=1)
    assert zoneless == expected.astimezone(timezone.utc)


def test_lenient_reset_is_ignored_in_text_longer_than_notice_bound():
    filler = "x" * (agent_capacity._NOTICE_MAX_CHARS + 1)
    assert agent_capacity.parse_explicit_reset(f"{filler} {_WEEKLY_NOTICE}") is None


def test_dated_reset_still_parses_in_long_text_and_wins_over_lenient():
    filler = "x" * (agent_capacity._NOTICE_MAX_CHARS + 1)
    long_text = f"{filler} try again at Aug 8th, 2026 2:17 AM."

    reset = agent_capacity.parse_explicit_reset(long_text)
    assert reset is not None and (reset.year, reset.month, reset.day) == (2026, 8, 8)

    both = agent_capacity.parse_explicit_reset(
        f"{_WEEKLY_NOTICE} try again at Aug 8th, 2026 2:17 AM."
    )
    assert both == datetime(2026, 8, 8, 2, 17).astimezone(timezone.utc)


def test_weekly_limit_spawn_records_provider_derived_gate(tmp_path, monkeypatch):
    path = tmp_path / "capacity.json"
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(path))
    monkeypatch.setattr(
        spawnlib, "resolve_routing", lambda *a, **kw: _preflight_routing()
    )
    monkeypatch.setattr(
        spawnlib.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {"returncode": 1, "stdout": "", "stderr": _WEEKLY_NOTICE},
        )(),
    )

    try:
        spawnlib.spawn_agent("prompt", tmp_path, tier="t2-build", sleep=lambda *_: None)
    except Exception:
        pass

    state = agent_capacity.load(path)["providers"]["claude:sonnet"]
    assert state["failure_class"] == "billing"
    assert state["reset_source"] == "provider"
    retry_after = datetime.fromisoformat(state["retry_after"])
    # The billing cooldown is 1h; a parsed 2pm-Pacific reset is not that.
    assert retry_after > datetime.now(timezone.utc)
    assert retry_after.astimezone(_pacific()).hour == 14
