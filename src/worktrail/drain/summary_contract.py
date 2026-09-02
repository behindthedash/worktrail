"""Versioned producer/consumer contract for nightly-drain stop summaries."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

CONTRACT_RESOURCE = ".fixtures/contracts/nightly-drain-summary-v1.json"

# First-class stop kind for the pending-user-decision handoff (the drain
# surface of the provider-neutral `worktrail.pending-decision` contract).
# Recognized in code because the packaged v1 contract predates it: a
# pending decision means a human must answer before the work can resume,
# which is exactly the condition a sleeping operator must be alerted to.
PENDING_USER_DECISION = "pending_user_decision"
PENDING_USER_DECISION_SEMANTICS: dict[str, object] = {
    "kind": PENDING_USER_DECISION,
    "operator_alert": True,
}

# Optional summary blocks the drain pre-passes contribute (task
# intake-to-spec-triage 4.1/4.2), documented here in Python rather than in
# the packaged v1 fixture -- same reasoning as PENDING_USER_DECISION_SEMANTICS
# above: both pre-passes postdate the frozen v1 contract, and each block is
# present in a drain summary only when its own flag was set, never zero-filled.
SUMMARY_BLOCKS: dict[str, dict[str, object]] = {
    "intake_triage": {
        "flag": "--intake-triage",
        "fields": (
            "briefs_evaluated",
            "verdict_counts",
            "pull_requests_opened",
            "briefs_held_by_cap",
        ),
    },
    "seed_backlog": {
        "flag": "--seed-backlog",
        "fields": ("seeds_captured",),
    },
}


def load_nightly_drain_summary_contract() -> dict[str, Any]:
    """Load the packaged v1 contract shared with drain-summary consumers."""
    resource = files("worktrail").joinpath(CONTRACT_RESOURCE)
    return json.loads(resource.read_text(encoding="utf-8"))


def summary_block_contract(block: str) -> dict[str, object] | None:
    """Flag/field contract for an optional drain summary block, or `None` for
    an unrecognized `block` name -- the `SUMMARY_BLOCKS` entry, copied so a
    caller can't mutate the shared registry."""
    entry = SUMMARY_BLOCKS.get(block)
    return dict(entry) if entry is not None else None


def stop_semantics(stopped: object) -> dict[str, object] | None:
    """Return the contract semantics for a recognized ``stopped`` reason."""
    if not isinstance(stopped, str):
        return None
    if stopped.startswith(PENDING_USER_DECISION):
        return dict(PENDING_USER_DECISION_SEMANTICS)
    contract = load_nightly_drain_summary_contract()
    for kind, semantics in contract["stop_reasons"].items():
        if stopped.startswith(kind):
            return {"kind": kind, "operator_alert": semantics["operator_alert"]}
    return None
