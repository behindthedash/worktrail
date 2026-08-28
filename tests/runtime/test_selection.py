"""Tests for `select_cell()` / `Cell` (design D3). Run: python3 -m pytest tests/runtime/test_selection.py"""
import pytest

from worktrail.runtime.selection import Cell, NoExecutionTarget, select_cell


def _routing(targets, tiers):
    return {"targets": targets, "tiers": tiers, "roles": {}, "purposes": {},
            "default_tier": None, "drain": {}}


def _target(harness, pool="subscription", api_opt_in=False, auth=None):
    return {"harness": harness, "pool": pool, "api_opt_in": api_opt_in, "auth": auth}


def _cell(model, effort=None):
    return {"model": model, "effort": effort}


def _deny_all(target, model, now=None):
    return False, {"failure_class": "billing", "retry_after": "2026-08-28T00:00:00+00:00"}


def _available_only_for(*names):
    def check(target, model, now=None):
        return target in names, {"failure_class": "transport", "retry_after": "later"}
    return check


class TestPreferReorder:
    def test_prefer_moves_target_to_front_of_attempt_order(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
                "opencode-free": _target("opencode", pool="free"),
            },
            tiers={"t1-deep": {
                "claude-sub": _cell("opus"),
                "codex-sub": _cell("gpt-5"),
                "opencode-free": _cell("zen/free"),
            }},
        )
        with pytest.raises(NoExecutionTarget) as excinfo:
            select_cell(routing, "t1-deep", prefer="opencode-free", capacity=_deny_all)
        attempted_targets = [item[0] for item in excinfo.value.attempted]
        assert attempted_targets == ["opencode-free", "claude-sub", "codex-sub"]

    def test_prefer_naming_target_without_cell_in_row_is_a_no_op(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
            },
            tiers={"t1-deep": {"claude-sub": _cell("opus")}},
        )
        with pytest.raises(NoExecutionTarget) as excinfo:
            select_cell(routing, "t1-deep", prefer="codex-sub", capacity=_deny_all)
        # codex-sub has no cell in this row, so it's dropped entirely -- not
        # moved to the front and not attempted.
        assert [item[0] for item in excinfo.value.attempted] == ["claude-sub"]

    def test_preferred_cell_wins_when_it_has_capacity(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
            },
            tiers={"t1-deep": {
                "claude-sub": _cell("opus"),
                "codex-sub": _cell("gpt-5"),
            }},
        )
        cell = select_cell(
            routing, "t1-deep", prefer="codex-sub",
            capacity=_available_only_for("claude-sub", "codex-sub"))
        assert cell == Cell(target="codex-sub", harness="codex", model="gpt-5",
                             effort=None, pool="subscription", auth=None)


class TestApiOptInSkip:
    def test_api_pool_target_without_opt_in_is_skipped(self):
        routing = _routing(
            targets={
                "openrouter": _target("opencode", pool="api", api_opt_in=False),
                "codex-sub": _target("codex"),
            },
            tiers={"t1-deep": {
                "openrouter": _cell("zen"),
                "codex-sub": _cell("gpt-5"),
            }},
        )
        cell = select_cell(routing, "t1-deep", capacity=_available_only_for("openrouter", "codex-sub"))
        # openrouter has capacity but is never attempted -- codex-sub wins.
        assert cell.target == "codex-sub"

    def test_api_pool_target_with_opt_in_is_attempted(self):
        routing = _routing(
            targets={
                "claude-api": _target("claude", pool="api", api_opt_in=True,
                                       auth={"env": "ANTHROPIC_API_KEY"}),
            },
            tiers={"t1-deep": {"claude-api": _cell("opus", effort="high")}},
        )
        cell = select_cell(routing, "t1-deep", capacity=_available_only_for("claude-api"))
        assert cell == Cell(target="claude-api", harness="claude", model="opus",
                             effort="high", pool="api", auth={"env": "ANTHROPIC_API_KEY"})


class TestMissingCell:
    def test_target_with_no_cell_in_row_is_skipped(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
            },
            tiers={
                "t1-deep": {"codex-sub": _cell("gpt-5")},
                "t4-trivia": {"claude-sub": _cell("haiku")},
            },
        )
        # claude-sub has no cell in t1-deep -- can't serve this tier at all.
        cell = select_cell(routing, "t1-deep", capacity=_available_only_for("claude-sub", "codex-sub"))
        assert cell.target == "codex-sub"

    def test_row_absent_entirely_raises_no_execution_target(self):
        routing = _routing(
            targets={"claude-sub": _target("claude")},
            tiers={"t4-trivia": {"claude-sub": _cell("haiku")}},
        )
        with pytest.raises(NoExecutionTarget) as excinfo:
            select_cell(routing, "unknown-tier", capacity=_available_only_for("claude-sub"))
        assert excinfo.value.attempted == ()


class TestSoftHarnessExclusion:
    def test_excluded_harness_moved_last_not_dropped(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
            },
            tiers={"t1-deep": {
                "claude-sub": _cell("opus"),
                "codex-sub": _cell("gpt-5"),
            }},
        )
        with pytest.raises(NoExecutionTarget) as excinfo:
            select_cell(routing, "t1-deep", exclude_harness="claude", capacity=_deny_all)
        # codex-sub (declared second) is attempted before claude-sub, the
        # excluded harness -- but claude-sub is still attempted (soft, not
        # a hard drop).
        assert [item[0] for item in excinfo.value.attempted] == ["codex-sub", "claude-sub"]

    def test_excluded_harness_still_wins_as_last_resort(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
            },
            tiers={"t1-deep": {
                "claude-sub": _cell("opus"),
                "codex-sub": _cell("gpt-5"),
            }},
        )
        # Only the excluded harness's target has capacity.
        cell = select_cell(routing, "t1-deep", exclude_harness="claude",
                            capacity=_available_only_for("claude-sub"))
        assert cell.target == "claude-sub"


class TestExhaustion:
    def test_no_execution_target_lists_every_cell_with_gate_class_and_retry(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
            },
            tiers={"t1-deep": {
                "claude-sub": _cell("opus"),
                "codex-sub": _cell("gpt-5"),
            }},
        )
        with pytest.raises(NoExecutionTarget) as excinfo:
            select_cell(routing, "t1-deep", capacity=_deny_all)
        message = str(excinfo.value)
        assert "claude-sub" in message
        assert "codex-sub" in message
        assert "billing" in message
        assert "2026-08-28T00:00:00+00:00" in message
        assert len(excinfo.value.attempted) == 2

    def test_no_capacity_gate_means_first_cell_always_wins(self):
        routing = _routing(
            targets={
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
            },
            tiers={"t1-deep": {
                "claude-sub": _cell("opus"),
                "codex-sub": _cell("gpt-5"),
            }},
        )
        cell = select_cell(routing, "t1-deep", capacity=None)
        assert cell.target == "claude-sub"
