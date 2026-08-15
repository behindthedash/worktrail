"""Tests for shared/operator_config.py."""

import json

import pytest

from worktrail.shared import operator_config


def _write(tmp_path, monkeypatch, payload):
    home = tmp_path / "wt-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WORKTRAIL_HOME", str(home))
    path = home / "config.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8")
    return path


def test_missing_file_is_empty_config(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "nowhere"))
    assert operator_config.load_operator_config() == {}
    assert operator_config.drain_config() == {
        "agent": None, "fallback_agents": [], "max_workers": 2}


def test_drain_section_round_trip(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           {"drain": {"agent": "opencode", "fallback_agents": ["claude", "codex"]}})
    assert operator_config.drain_config() == {
        "agent": "opencode", "fallback_agents": ["claude", "codex"], "max_workers": 2}


def test_max_workers_defaults_to_two(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"drain": {}})
    assert operator_config.drain_config()["max_workers"] == 2


def test_max_workers_configured_value(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"drain": {"max_workers": 5}})
    assert operator_config.drain_config()["max_workers"] == 5


def test_malformed_json_raises_not_silently_ignored(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch, "{not json")
    with pytest.raises(operator_config.OperatorConfigError, match=str(path)):
        operator_config.load_operator_config()


def test_non_object_top_level_raises(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "[1, 2]")
    with pytest.raises(operator_config.OperatorConfigError, match="JSON object"):
        operator_config.load_operator_config()


@pytest.mark.parametrize("section", [
    {"drain": "opencode"},
    {"drain": {"agent": 3}},
    {"drain": {"fallback_agents": "claude"}},
    {"drain": {"fallback_agents": [1]}},
    {"drain": {"max_workers": 0}},
    {"drain": {"max_workers": -1}},
    {"drain": {"max_workers": "2"}},
    {"drain": {"max_workers": True}},
])
def test_bad_drain_shapes_raise(tmp_path, monkeypatch, section):
    path = _write(tmp_path, monkeypatch, section)
    with pytest.raises(operator_config.OperatorConfigError, match=str(path)):
        operator_config.drain_config()
