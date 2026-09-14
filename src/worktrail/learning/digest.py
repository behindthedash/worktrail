"""Pure, bounded outcome digest of a run journal (design D1)."""

from __future__ import annotations

from typing import Any

MAX_WORKER_SIGNALS = 50
MAX_TEXT_CHARS = 500
_EXCLUDED_EVENTS = frozenset({"retro", "learned_notes"})


def _truncate(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_TEXT_CHARS]
    if isinstance(value, list):
        return [str(v)[:MAX_TEXT_CHARS] for v in value]
    return value


def _signals(entry: dict) -> list[str]:
    report = entry.get("report") or {}
    role = entry.get("role") or ""
    signals = []
    if report.get("context_quality") == "insufficient":
        signals.append("insufficient_context")
    if (report.get("critical_issues") or 0) > 0:
        signals.append("critical_issues")
    if role.startswith("review") and (report.get("major_issues") or 0) > 0:
        signals.append("major_issues")
    if entry.get("scope_escalated"):
        signals.append("scope_escalated")
    if entry.get("blocked_by"):
        signals.append("blocked")
    if role == "fix":
        signals.append("fix_round")
    return signals


def build_outcome_digest(journal: dict) -> dict:
    groups = journal.get("groups") or {}
    quarantined = [
        {"group": name, "reason": rec.get("quarantine_reason")}
        for name, rec in groups.items()
        if isinstance(rec, dict) and rec.get("state") == "QUARANTINED"
    ]

    worker_signals = []
    events: dict[str, int] = {}
    markers = list(journal.get("entries") or []) + list(
        journal.get("safety_net_events") or []
    )
    for entry in markers:
        if not isinstance(entry, dict):
            continue
        event = entry.get("event")
        if event is not None:
            if event not in _EXCLUDED_EVENTS:
                events[event] = events.get(event, 0) + 1
            continue
        if "role" not in entry:
            continue
        signals = _signals(entry)
        if not signals:
            continue
        report = entry.get("report") or {}
        worker_signals.append(
            {
                "task": entry.get("task"),
                "role": entry.get("role"),
                "agent": entry.get("agent"),
                "signals": signals,
                "notes": _truncate(report.get("notes")),
                "missing_context": _truncate(report.get("missing_context")),
            }
        )

    truncated = len(worker_signals) > MAX_WORKER_SIGNALS
    unreconciled_tail = bool(journal.get("unreconciled_tail_evidence"))
    return {
        "spec_id": journal.get("spec_id"),
        "run_id": journal.get("run_id"),
        "quarantined_groups": quarantined,
        "worker_signals": worker_signals[:MAX_WORKER_SIGNALS],
        "truncated": truncated,
        "events": dict(sorted(events.items())),
        "unreconciled_tail": unreconciled_tail,
        "has_signal": bool(
            quarantined or worker_signals or events or unreconciled_tail
        ),
    }
