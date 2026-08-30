#!/usr/bin/env python3
"""Tier-Accuracy Report aggregation.

Read-only CLI for the subscription-aware-routing spec: scans task frontmatter
under ``docs/specs`` (or ``--specs-root``) and joins it with existing fan-out
journals under ``<repo>-worktrees/run-*.json`` to produce per-``(complexity,
domain)`` review-pass and fix-strike statistics.

Thresholds:
  - ``MIN_REVIEW_SAMPLES = 2``: pairs with fewer review outcomes are reported
    as ``insufficient data`` instead of a fabricated rate.
  - ``MISSTAMP_RATE_GAP = 0.15``: advisory mis-stamp flag when a lower tier's
    fix-strike rate is at least 15 percentage points above a higher tier's
    rate in the same domain.

The script performs no writes, no network calls, and no LLM invocations.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import split_frontmatter

COMPLEXITY_ORDER = {"trivial": 0, "standard": 1, "hard": 2}
COMPLEXITY_LABELS = tuple(COMPLEXITY_ORDER)
DEFAULT_DOMAIN = "<none>"
MIN_REVIEW_SAMPLES = 2
MISSTAMP_RATE_GAP = 0.15


def _normalize_complexity(value: Any) -> str:
    complexity = str(value or "standard").strip().lower()
    return complexity if complexity in COMPLEXITY_ORDER else "standard"


def _normalize_domain(value: Any) -> str:
    domain = str(value or "").strip()
    return domain if domain else DEFAULT_DOMAIN


def _pair_key(complexity: str, domain: str) -> tuple[int, str, str]:
    return (
        COMPLEXITY_ORDER.get(complexity, COMPLEXITY_ORDER["standard"]),
        domain,
        complexity,
    )


def _pair_label(complexity: str, domain: str) -> str:
    return f"({complexity}, {domain})"


def _iter_task_files(specs_root: Path) -> Iterator[Path]:
    for path in sorted(specs_root.rglob("TASK-*.md")):
        if "reviews" in path.parts:
            continue
        if path.is_file():
            yield path


def _read_task_stamps(specs_root: Path) -> dict[str, dict[str, str]]:
    stamps: dict[str, dict[str, str]] = {}
    for task_file in _iter_task_files(specs_root):
        try:
            text = task_file.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, _ = split_frontmatter(text)
        if not isinstance(frontmatter, dict):
            continue
        task_id = str(frontmatter.get("id") or task_file.stem).strip()
        if not task_id:
            continue
        stamps[task_id] = {
            "task_id": task_id,
            "task_file": str(task_file),
            "complexity": _normalize_complexity(frontmatter.get("complexity")),
            "domain": _normalize_domain(frontmatter.get("domain")),
        }
    return stamps


def _iter_journal_files(worktrees_root: Path) -> Iterator[Path]:
    for path in sorted(worktrees_root.rglob("run-*.json")):
        if not path.is_file():
            continue
        if path.name.endswith(".status.json"):
            continue
        yield path


def _load_journal(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _is_salvage_event(report: dict[str, Any]) -> bool:
    notes = str(report.get("notes") or "")
    return "salvaged from git" in notes


def _is_escalation(report: dict[str, Any]) -> bool:
    terminal_status = str(report.get("terminal_status") or "").strip().lower()
    return terminal_status == "escalated"


def _is_fix_strike_signal(entry: dict[str, Any], report: dict[str, Any]) -> bool:
    review_status = str(report.get("review_status") or "").strip().upper()
    if review_status == "FAILED":
        return True

    role = str(entry.get("role") or entry.get("step") or "").strip().lower()
    if role != "fix":
        return False

    for field in (
        "fix_strike",
        "fix_strike_flag",
        "fix_strike_count",
        "strike",
        "strike_count",
    ):
        value = report.get(field)
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                return True
            continue
        if isinstance(value, (int, float)):
            if value > 0:
                return True
            continue
        if str(value).strip().lower() in {
            "1",
            "true",
            "yes",
            "failed",
            "strike",
            "struck",
        }:
            return True

    return False


def _journal_entries(journal: dict[str, Any]) -> Iterable[dict[str, Any]]:
    entries = journal.get("entries") or []
    if not isinstance(entries, list):
        return []
    return (e for e in entries if isinstance(e, dict))


def aggregate_tier_accuracy(
    *,
    repo_root: Path,
    specs_root: Path | None = None,
    worktrees_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    specs_root = (
        Path(specs_root) if specs_root is not None else repo_root / "docs" / "specs"
    )
    worktrees_root = (
        Path(worktrees_root)
        if worktrees_root is not None
        else repo_root.parent / f"{repo_root.name}-worktrees"
    )

    task_stamps = _read_task_stamps(specs_root) if specs_root.is_dir() else {}
    pair_tasks: dict[tuple[str, str], set[str]] = defaultdict(set)
    task_to_pair: dict[str, tuple[str, str]] = {}
    for task_id, stamp in task_stamps.items():
        pair = (stamp["complexity"], stamp["domain"])
        pair_tasks[pair].add(task_id)
        task_to_pair[task_id] = pair

    pair_stats: dict[tuple[str, str], dict[str, Any]] = {}
    for pair, task_ids in pair_tasks.items():
        pair_stats[pair] = {
            "complexity": pair[0],
            "domain": pair[1],
            "task_count": len(task_ids),
            "review_attempts": 0,
            "review_passes": 0,
            "fix_strikes": 0,
            "escalations": 0,
            "salvage_events": 0,
            "status": "insufficient data",
        }

    parsed_journals = 0
    skipped_journals = 0
    for journal_file in (
        _iter_journal_files(worktrees_root) if worktrees_root.is_dir() else []
    ):
        journal = _load_journal(journal_file)
        if journal is None:
            skipped_journals += 1
            continue
        parsed_journals += 1
        for entry in _journal_entries(journal):
            task_id = str(entry.get("task") or "").strip()
            pair = task_to_pair.get(task_id)
            if pair is None:
                continue
            report = entry.get("report") or {}
            if not isinstance(report, dict):
                continue
            stats = pair_stats[pair]
            review_status = str(report.get("review_status") or "").strip().upper()
            fix_strike_signal = _is_fix_strike_signal(entry, report)
            if review_status in {"PASSED", "FAILED"} or fix_strike_signal:
                stats["review_attempts"] += 1
                if review_status == "PASSED":
                    stats["review_passes"] += 1
                elif review_status == "FAILED" or fix_strike_signal:
                    stats["fix_strikes"] += 1
            if _is_escalation(report):
                stats["escalations"] += 1
            if _is_salvage_event(report):
                stats["salvage_events"] += 1

    for stats in pair_stats.values():
        attempts = stats["review_attempts"]
        if attempts >= MIN_REVIEW_SAMPLES:
            stats["status"] = "ok"
            stats["review_pass_rate"] = stats["review_passes"] / attempts
            stats["fix_strike_rate"] = stats["fix_strikes"] / attempts
        else:
            stats["review_pass_rate"] = None
            stats["fix_strike_rate"] = None

    flags: list[dict[str, Any]] = []
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for stats in pair_stats.values():
        if stats["status"] != "ok":
            continue
        by_domain[stats["domain"]].append(stats)

    for domain, entries in sorted(by_domain.items()):
        ordered = sorted(entries, key=lambda s: COMPLEXITY_ORDER[s["complexity"]])
        for i, lower in enumerate(ordered):
            lower_rate = lower.get("fix_strike_rate")
            if lower_rate is None:
                continue
            for higher in ordered[i + 1 :]:
                higher_rate = higher.get("fix_strike_rate")
                if higher_rate is None:
                    continue
                gap = lower_rate - higher_rate
                if gap >= MISSTAMP_RATE_GAP:
                    flags.append(
                        {
                            "domain": domain,
                            "lower": {
                                "complexity": lower["complexity"],
                                "domain": lower["domain"],
                                "fix_strike_rate": lower_rate,
                            },
                            "higher": {
                                "complexity": higher["complexity"],
                                "domain": higher["domain"],
                                "fix_strike_rate": higher_rate,
                            },
                            "gap": gap,
                            "threshold": MISSTAMP_RATE_GAP,
                            "reason": (
                                f"{lower['complexity']} pair in {domain} has a fix-strike rate "
                                f"{gap:.0%} above {higher['complexity']} by at least "
                                f"{MISSTAMP_RATE_GAP:.0%}"
                            ),
                        }
                    )

    ordered_pairs = sorted(
        pair_stats.values(), key=lambda s: _pair_key(s["complexity"], s["domain"])
    )
    report = {
        "repo_root": str(repo_root),
        "specs_root": str(specs_root),
        "worktrees_root": str(worktrees_root),
        "parsed_journals": parsed_journals,
        "skipped_journals": skipped_journals,
        "min_review_samples": MIN_REVIEW_SAMPLES,
        "misstamp_rate_gap": MISSTAMP_RATE_GAP,
        "pairs": ordered_pairs,
        "misstamp_flags": flags,
    }
    return report


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "Tier-Accuracy Report",
        f"repo root: {report['repo_root']}",
        f"journals: {report['parsed_journals']} parsed, {report['skipped_journals']} skipped",
        f"thresholds: review samples >= {report['min_review_samples']}, "
        f"mis-stamp gap >= {report['misstamp_rate_gap']:.0%}",
        "",
    ]
    for pair in report.get("pairs", []):
        label = _pair_label(pair["complexity"], pair["domain"])
        if pair["status"] == "insufficient data":
            lines.append(
                f"{label}: insufficient data "
                f"({pair['review_attempts']} review outcome(s); need >= {report['min_review_samples']})"
            )
            continue
        pass_rate = pair["review_pass_rate"] or 0.0
        strike_rate = pair["fix_strike_rate"] or 0.0
        lines.append(
            f"{label}: review pass {pair['review_passes']}/{pair['review_attempts']} "
            f"({pass_rate:.0%}), fix-strikes {pair['fix_strikes']} "
            f"({strike_rate:.0%}), escalations {pair['escalations']}, "
            f"salvage events {pair['salvage_events']}"
        )

    if report.get("misstamp_flags"):
        lines.append("")
        lines.append("Mis-stamping advisories")
        for flag in report["misstamp_flags"]:
            lower = flag["lower"]
            higher = flag["higher"]
            lines.append(
                f"{_pair_label(lower['complexity'], lower['domain'])} fix-strike rate "
                f"{lower['fix_strike_rate']:.0%} exceeds "
                f"{_pair_label(higher['complexity'], higher['domain'])} "
                f"({higher['fix_strike_rate']:.0%}) by {flag['gap']:.0%} "
                f"(threshold {flag['threshold']:.0%})"
            )

    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate Tier-Accuracy Report statistics"
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root to scan (default: current working directory)",
    )
    parser.add_argument(
        "--specs-root",
        default=None,
        help="Override the docs/specs directory (default: <repo-root>/docs/specs)",
    )
    parser.add_argument(
        "--worktrees-root",
        default=None,
        help="Override the journal directory (default: <repo-root>-worktrees)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of human-readable text"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    emit_json = "--json" in raw
    argv = [arg for arg in raw if arg != "--json"]
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).expanduser()
    specs_root = Path(args.specs_root).expanduser() if args.specs_root else None
    worktrees_root = (
        Path(args.worktrees_root).expanduser() if args.worktrees_root else None
    )

    if specs_root is None:
        specs_root = repo_root / "docs" / "specs"
    if worktrees_root is None:
        worktrees_root = repo_root.parent / f"{repo_root.name}-worktrees"

    if not repo_root.exists():
        print(f"error: repo root not found: {repo_root}", file=sys.stderr)
        return 2
    if not specs_root.is_dir():
        print(f"error: specs root not found: {specs_root}", file=sys.stderr)
        return 2

    report = aggregate_tier_accuracy(
        repo_root=repo_root, specs_root=specs_root, worktrees_root=worktrees_root
    )

    if emit_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
