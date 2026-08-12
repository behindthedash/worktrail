"""Versioned producer/consumer contract for nightly-drain stop summaries."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

CONTRACT_RESOURCE = ".fixtures/contracts/nightly-drain-summary-v1.json"


def load_nightly_drain_summary_contract() -> dict[str, Any]:
    """Load the packaged v1 contract shared with drain-summary consumers."""
    resource = files("worktrail").joinpath(CONTRACT_RESOURCE)
    return json.loads(resource.read_text(encoding="utf-8"))


def stop_semantics(stopped: object) -> dict[str, object] | None:
    """Return the contract semantics for a recognized ``stopped`` reason."""
    if not isinstance(stopped, str):
        return None
    contract = load_nightly_drain_summary_contract()
    for kind, semantics in contract["stop_reasons"].items():
        if stopped.startswith(kind):
            return {"kind": kind, "operator_alert": semantics["operator_alert"]}
    return None
