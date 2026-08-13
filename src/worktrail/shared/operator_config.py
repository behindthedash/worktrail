"""Operator-level configuration at `worktrail_home()/config.json`.

One JSON file for machine-wide operator preferences that were previously only
expressible as per-invocation CLI flags. Current schema (all keys optional):

    {
      "drain": {
        "agent": "opencode",
        "fallback_agents": ["claude", "codex"]
      }
    }

Precedence is always CLI > config file > built-in default — an explicit flag
wins, and automation that passes its agents explicitly (e.g. the nightly drain
script) is never affected by this file.

A missing file is an empty config. A file that exists but does not parse, or
does not contain a JSON object, raises `OperatorConfigError` — a malformed
file is stated operator intent that silently falling back to built-in
defaults would invert (the exact failure mode that motivated this module: a
config-less manual drain silently defaulting to a paid provider).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .homedir import worktrail_home


class OperatorConfigError(ValueError):
    """The operator config exists but cannot be honored."""


def config_path() -> Path:
    return worktrail_home() / "config.json"


def load_operator_config() -> Dict[str, Any]:
    path = config_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise OperatorConfigError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise OperatorConfigError(
            f"{path} is not valid JSON ({exc}); fix or remove it -- refusing "
            "to silently ignore an operator preference file") from exc
    if not isinstance(data, dict):
        raise OperatorConfigError(
            f"{path} must contain a JSON object at the top level, "
            f"got {type(data).__name__}")
    return data


def drain_config() -> Dict[str, Any]:
    """The `drain` section, shape-checked: `agent` must be a string if present,
    `fallback_agents` a list of strings. Value validity (is it a supported
    agent?) is the drain CLI's own check, against its `SUPPORTED_AGENTS`."""
    section = load_operator_config().get("drain", {})
    if not isinstance(section, dict):
        raise OperatorConfigError(
            f"{config_path()}: \"drain\" must be an object")
    agent = section.get("agent")
    if agent is not None and not isinstance(agent, str):
        raise OperatorConfigError(
            f"{config_path()}: drain.agent must be a string")
    fallbacks = section.get("fallback_agents", [])
    if not isinstance(fallbacks, list) or any(
            not isinstance(f, str) for f in fallbacks):
        raise OperatorConfigError(
            f"{config_path()}: drain.fallback_agents must be a list of strings")
    return {"agent": agent, "fallback_agents": list(fallbacks)}
