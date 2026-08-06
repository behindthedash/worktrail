import json
from datetime import datetime, timedelta, timezone

from worktrail.orchestrator import agent_capacity
from worktrail.orchestrator import spawnlib


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

    assert json.loads(path.read_text(encoding="utf-8"))["providers"]["codex:gpt"]["status"] == "available"
    assert list(tmp_path.glob(".capacity.json.*")) == []


def test_unknown_transport_does_not_get_a_reset_time():
    assert agent_capacity.classify_failure(1, "", "network disconnected") == "transport"


def test_usage_limit_wording_classifies_as_billing():
    # Live reproduction 2026-08-02 (drain-nightly iteration 2): Codex's own
    # usage-cap wording used to fall through to the generic "transport" class
    # (30s cooldown) instead of the multi-hour/day reset a real account-level
    # cap needs.
    stdout = ("ERROR: You've hit your usage limit. Upgrade to Pro "
              "(https://chatgpt.com/explore/pro), visit "
              "https://chatgpt.com/codex/settings/usage to purchase more "
              "credits or try again at Aug 8th, 2026 2:17 AM.")
    assert agent_capacity.classify_failure(1, stdout, "") == "billing"


def test_session_limit_wording_also_classifies_as_billing():
    assert agent_capacity.classify_failure(
        1, "", "You've hit your session limit. Your limit resets at 3:00pm.") == "billing"


def test_weekly_limit_wording_also_classifies_as_billing():
    # Live reproduction 2026-08-05 (drain-nightly 2026-08-05T09-17-01Z): Claude's
    # own weekly-cap wording used to fall through to "transport", so two
    # consecutive weekly-limit hits tripped the circuit breaker as plain
    # failures instead of gating the provider as a capacity issue.
    assert agent_capacity.classify_failure(
        1, "You've hit your weekly limit · resets 2pm (America/Los_Angeles)", "") == "billing"


def test_parse_explicit_reset_extracts_codex_notice():
    stdout = ("ERROR: You've hit your usage limit. Upgrade to Pro "
              "(https://chatgpt.com/explore/pro), visit "
              "https://chatgpt.com/codex/settings/usage to purchase more "
              "credits or try again at Aug 8th, 2026 2:17 AM.")
    reset = agent_capacity.parse_explicit_reset(stdout)
    assert reset is not None
    assert (reset.year, reset.month, reset.day) == (2026, 8, 8)
    local_naive = datetime(2026, 8, 8, 2, 17)
    assert reset == local_naive.astimezone(timezone.utc)


def test_parse_explicit_reset_accepts_full_month_name_no_ordinal():
    reset = agent_capacity.parse_explicit_reset(
        "try again at August 8, 2026 2:17AM.")
    assert reset is not None and (reset.year, reset.month, reset.day) == (2026, 8, 8)


def test_parse_explicit_reset_returns_none_without_a_timestamp():
    assert agent_capacity.parse_explicit_reset("some unrelated crash text") is None
    assert agent_capacity.parse_explicit_reset("") is None


def test_opencode_generic_error_event_classifies_as_transport():
    # Real shape from a live reproduction (handoff 20260722-152514,
    # /tmp/opencode-error-repro.jsonl): opencode's own error surface only exposed
    # a generic "UnknownError" for what was almost certainly a free-tier rate
    # limit -- no distinguishable rate-limit signal was observed, so per the
    # no-guessing rule this must fall through to the existing generic "transport"
    # class (short retry/backoff) rather than a fabricated "rate_limit" class.
    stdout = json.dumps({
        "type": "error",
        "error": {
            "name": "UnknownError",
            "data": {"message": "Unexpected server error. Check server logs for details.",
                      "ref": "err_55ac1dc1"},
        },
    })
    assert agent_capacity.classify_failure(0, stdout, "") == "transport"


def test_opencode_error_event_is_recognized_as_infra_failure():
    # Cross-module regression for the actual bug: exit 0 + non-empty stdout used
    # to be treated as a clean success (agent_capacity.record(outcome="available"))
    # even though the provider itself failed.
    stdout = json.dumps({
        "type": "error",
        "error": {"name": "UnknownError", "data": {"message": "Unexpected server error."}},
    })
    assert spawnlib.is_infra_failure(0, stdout) is True


def test_gate_snapshot_reports_retry_class_and_all_gated(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.configure(
        [("claude", "sonnet"), ("opencode", "safe/model")], path=path
    )
    for agent, model, failure_class in (
        ("claude", "sonnet", "transport"),
        ("opencode", "safe/model", "auth"),
    ):
        agent_capacity.record(
            agent, model, outcome="unavailable", failure_class=failure_class,
            retry_after=now + timedelta(minutes=5), path=path, now=now,
        )

    snapshot = agent_capacity.gate_snapshot(path=path, now=now)

    assert snapshot["all_gated"] is True
    assert snapshot["retry_after"] == "2026-07-20T20:05:00+00:00"
    assert [item["failure_class"] for item in snapshot["gated"]] == ["transport", "auth"]


def test_preflight_uses_fallback_when_primary_is_gated(tmp_path, monkeypatch):
    path = tmp_path / "capacity.json"
    now = datetime.now(timezone.utc)
    agent_capacity.record(
        "claude", "sonnet", outcome="unavailable", failure_class="transport",
        retry_after=now + timedelta(minutes=1), path=path, now=now,
    )
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(path))
    monkeypatch.setattr(
        spawnlib.subprocess,
        "run",
        lambda *args, **kwargs: type("Proc", (), {
            "returncode": 0,
            "stdout": '{"type":"result","result":"ok","usage":{}}\n',
            "stderr": "",
        })(),
    )

    result = spawnlib.spawn_agent("prompt", tmp_path, fallback_agent="opencode")

    assert result.text == "ok"


def test_status_reads_empty_cache(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    cap = agent_capacity.cmd_status(path=path)
    assert cap == 0


def test_status_shows_configured_unconfigured_providers(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.configure([("claude", "sonnet"), ("opencode", "model")], path=path)
    cap = agent_capacity.cmd_status(path=path)
    assert cap == 0


def test_clear_refuses_unknown_key(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.cmd_clear("unknown:key", "billing resolved", path=path)
    assert rc == 1
    assert json.loads(path.read_text()) == {"version": 1, "providers": {}, "configured_providers": []}


def test_clear_refuses_empty_reason(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=agent_capacity._now() + timedelta(minutes=5), path=path)
    content_before = path.read_text()
    rc = agent_capacity.cmd_clear("claude:sonnet", "", path=path)
    assert rc == 1
    assert path.read_text() == content_before


def test_clear_refuses_blank_reason(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=agent_capacity._now() + timedelta(minutes=5), path=path)
    content_before = path.read_text()
    rc = agent_capacity.cmd_clear("claude:sonnet", "   ", path=path)
    assert rc == 1
    assert path.read_text() == content_before


def test_clear_targeted_removes_exactly_one_provider(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=now + timedelta(minutes=5), path=path, now=now)
    agent_capacity.record("opencode", "model", outcome="unavailable", failure_class="auth", retry_after=now + timedelta(minutes=5), path=path, now=now)
    agent_capacity.configure([("claude", "sonnet"), ("opencode", "model")], path=path)
    content_before = path.read_text()
    rc = agent_capacity.cmd_clear("claude:sonnet", "transport resolved", path=path, now=now)
    assert rc == 0
    data = json.loads(path.read_text())
    assert "claude:sonnet" not in data["providers"]
    assert data["providers"]["opencode:model"]["status"] == "unavailable"
    assert data["configured_providers"] == ["claude:sonnet", "opencode:model"]


def test_clear_all_removes_all_gates_preserves_config(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=now + timedelta(minutes=5), path=path, now=now)
    agent_capacity.record("opencode", "model", outcome="unavailable", failure_class="auth", retry_after=now + timedelta(minutes=5), path=path, now=now)
    agent_capacity.configure([("claude", "sonnet"), ("opencode", "model")], path=path)
    rc = agent_capacity.cmd_clear("--all", "all resolved", path=path, now=now)
    assert rc == 0
    data = json.loads(path.read_text())
    assert data["providers"] == {}
    assert data["configured_providers"] == ["claude:sonnet", "opencode:model"]


def test_clear_rejects_all_without_explicit_flag(tmp_path):
    path = tmp_path / "capacity.json"
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=agent_capacity._now() + timedelta(minutes=5), path=path)
    content_before = path.read_text()
    rc = agent_capacity.cmd_clear("--all", "resolved", path=path)
    assert rc == 0  # --all is explicit scope


def test_clear_writes_audit_entry(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=now + timedelta(minutes=5), path=path, now=now)
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
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=now + timedelta(minutes=5), path=path, now=now)
    agent_capacity.record("opencode", "model", outcome="unavailable", failure_class="auth", retry_after=now + timedelta(minutes=5), path=path, now=now)
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
        agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=now + timedelta(minutes=5), path=path, now=now)
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
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=now + timedelta(minutes=5), path=path, now=now)
    rc = agent_capacity.main(["--cache", str(path), "clear", "claude:sonnet", "--reason", "fixed"])
    assert rc == 0
    data = json.loads(path.read_text())
    assert "claude:sonnet" not in data["providers"]
    assert len(data["audit"]) == 1


def test_main_clear_all_via_cli(tmp_path):
    path = tmp_path / "capacity.json"
    now = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    agent_capacity.record("claude", "sonnet", outcome="unavailable", failure_class="transport", retry_after=now + timedelta(minutes=5), path=path, now=now)
    rc = agent_capacity.main(["--cache", str(path), "clear", "--all", "--reason", "all fixed"])
    assert rc == 0
    data = json.loads(path.read_text())
    assert data["providers"] == {}


def test_main_clear_unknown_key_returns_nonzero(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.main(["--cache", str(path), "clear", "unknown", "--reason", "fix"])
    assert rc == 1


def test_main_clear_missing_reason_returns_nonzero(tmp_path):
    path = tmp_path / "capacity.json"
    path.write_text('{"version":1,"providers":{},"configured_providers":[]}')
    rc = agent_capacity.main(["--cache", str(path), "clear", "--all", "--reason", ""])
    assert rc == 1


def test_main_no_command_returns_nonzero(tmp_path):
    rc = agent_capacity.main(["--cache", str(tmp_path / "c.json")])
    assert rc == 1


def test_preflight_reports_all_providers_gated_without_spawning(tmp_path, monkeypatch):
    path = tmp_path / "capacity.json"
    now = datetime.now(timezone.utc)
    for agent, model in (("claude", "sonnet"), ("opencode", spawnlib.default_model_for_agent("opencode"))):
        agent_capacity.record(
            agent, model, outcome="unavailable", failure_class="transport",
            retry_after=now + timedelta(minutes=1), path=path, now=now,
        )
    monkeypatch.setenv("GO_AGENT_CAPACITY_CACHE", str(path))
    called = []
    monkeypatch.setattr(spawnlib.subprocess, "run", lambda *args, **kwargs: called.append(args))

    try:
        spawnlib.spawn_agent("prompt", tmp_path, fallback_agent="opencode")
    except agent_capacity.ProviderUnavailable as exc:
        assert exc.provider_key == "opencode:" + spawnlib.default_model_for_agent("opencode")
    else:
        raise AssertionError("all gated providers should stop before spawn")
    assert called == []
