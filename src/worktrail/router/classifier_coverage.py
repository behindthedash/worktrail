#!/usr/bin/env python3
"""Classifier Coverage Audit — replay `classify.py` over historical briefs.

Read-only CLI that answers one question: **where does the route classifier
disagree with the route a human or agent actually recorded?**

Corpus: handoff briefs under the work queue (``queue/`` + ``picked/``). For
each brief the audit resolves an *expected* route from one of two sources,
in precedence order:

  1. ``actual`` — a run record whose ``handoffs_consumed`` names this brief,
     read from its ``selected_route``. This is what was really executed, so
     it outranks the recommendation.
  2. ``recommended`` — the brief's own ``recommended-route`` frontmatter.

It then replays ``classify.classify()`` over the brief's ``focus`` text and
clusters the disagreements by ``(expected, predicted)`` pair.

**The replay deliberately withholds the ``handoff_route`` hint.** Passing a
brief's ``recommended-route`` into the classifier and then scoring the answer
against that same value is partly tautological — ``classify()`` can return
the hint verbatim via its ``handoff-recommended-override`` path. Withholding
it measures the organic text signal, which is the thing a coverage audit can
act on. The report header states this so a reader never mistakes the numbers
for end-to-end front-door accuracy.

Replay inputs that production reads live are pinned to explicit, documented
values so two runs over the same corpus produce the same report:

  - ``state`` — defaults to ``REPLAY_STATE``; override with ``--state``.
  - ``resumable_state`` — defaults to ``None`` ("unknown", leaving Route E's
    scoring untouched); ``--resumable-state false`` models the modern
    front-door pre-check that disqualifies E outright.

Disagreements are split by cause, because the two have different fixes:

  - **no-signal** — ``classify()`` scored nothing at all (empty ``scores``)
    and fell through to its default route. The brief's vocabulary is simply
    absent from the signal tables; the fix is coverage, not re-weighting.
    Note the default itself moves: no-signal resolves to ``E`` normally, but
    to ``A`` once ``resumable_state=False`` disqualifies ``E`` — so the same
    gap surfaces under different route pairs depending on replay settings,
    which is exactly why it is counted separately from them.
  - **mis-weighted** — signals did fire and still lost to another route. The
    fix is weights or precedence in the signal tables.

Thresholds:
  - ``DEFAULT_LIMIT = 500``: the corpus is bounded by default (newest first);
    ``--limit 0`` scans everything.
  - ``MIN_ACTIONABLE_CONFIDENT = 2``: a cluster is flagged ``actionable``
    only once at least this many of its disagreements were predicted at
    ``high`` confidence — a confidently wrong route is a signal-table bug,
    while a low-confidence miss is usually just thin input text. A no-signal
    disagreement is always low confidence, so it can never raise this flag.
  - ``MAX_SAMPLES = 5``: sample brief ids carried per cluster.

The script performs no writes, no network calls, and no LLM invocations. It
never imports or mutates production routing state; ``classify()`` is pure
given its inputs, and ``pr_states`` is left ``None`` so no ``gh`` lookup can
happen.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from ..shared.brief_frontmatter import split_frontmatter
from ..shared.homedir import worktrail_home
from .classify import ROUTE_NAMES, classify

DEFAULT_LIMIT = 500
MIN_ACTIONABLE_CONFIDENT = 2
MAX_SAMPLES = 5

# Pinned replay state. Both values are non-zero on purpose: `active_specs == 0`
# trips classify()'s D-demotion and `handoff_queue == 0` suppresses its E bump,
# so a zeroed state would silently audit a configuration the front door almost
# never actually dispatches under.
REPLAY_STATE: dict[str, Any] = {"active_specs": 1, "handoff_queue": 1}

SKIP_NO_FOCUS = "no focus text"
SKIP_NO_EXPECTED = "no recorded route"
SKIP_BAD_ROUTE = "unrecognized route value"


# --------------------------------------------------------------------------- #
# Corpus loading
# --------------------------------------------------------------------------- #


def default_queue_dir() -> Path:
    """The work-queue root: ``$WORK_QUEUE_DIR`` or ``~/work-queue``.

    Mirrors ``work_queue.base_dir()`` without importing it — this module must
    stay free of the queue's mutating lifecycle code.
    """
    return Path(os.environ.get("WORK_QUEUE_DIR", "~/work-queue")).expanduser()


def _iter_brief_files(queue_root: Path) -> Iterator[Path]:
    for folder in ("queue", "picked"):
        directory = queue_root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.is_file():
                yield path


def _normalize_route(value: Any) -> str | None:
    """Return a canonical ``A``-``J`` letter, or None if it isn't one.

    Tolerates the trailing-rationale form authors use in practice
    (``recommended-route: F   # overriding classify.py's H``) — YAML already
    strips the ``#`` comment, so only case and whitespace remain here. A
    non-route value (observed in the wild: ``verify``) returns None rather
    than being coerced into a neighbouring letter.
    """
    route = str(value or "").strip().upper()
    return route if route in ROUTE_NAMES else None


def _focus_text(frontmatter: dict[str, Any], body: str) -> str:
    """The text the audit classifies: the brief's ``focus``.

    Falls back to the ``## Focus`` body section for briefs written before
    ``focus`` became frontmatter. Deliberately excludes the suggested-approach
    prose: ``focus`` is the one-line intent the dashboard and front door
    surface, so it is the input a coverage number should be about.
    """
    focus = str(frontmatter.get("focus") or "").strip()
    if focus:
        return focus

    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() != "## focus":
            continue
        collected: list[str] = []
        for following in lines[index + 1 :]:
            if following.startswith("## "):
                break
            collected.append(following)
        return "\n".join(collected).strip()
    return ""


def load_briefs(queue_root: Path) -> list[dict[str, Any]]:
    """Read every brief in the queue, newest-first by ``created`` then id."""
    briefs: list[dict[str, Any]] = []
    for path in _iter_brief_files(queue_root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, body = split_frontmatter(text)
        if not isinstance(frontmatter, dict):
            frontmatter = {}
        brief_id = str(frontmatter.get("id") or path.stem).strip()
        briefs.append(
            {
                "brief_id": brief_id,
                "path": str(path),
                "created": str(frontmatter.get("created") or ""),
                "focus": _focus_text(frontmatter, body),
                "recommended_route": _normalize_route(
                    frontmatter.get("recommended-route")
                ),
                "raw_recommended": frontmatter.get("recommended-route"),
            }
        )
    briefs.sort(key=lambda b: (b["created"], b["brief_id"]), reverse=True)
    return briefs


# --------------------------------------------------------------------------- #
# Actual-route join (run records)
# --------------------------------------------------------------------------- #


def _consumed_ids(value: Any) -> list[str]:
    """Normalize ``handoffs_consumed`` — written as both a string and a list."""
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return []


def load_actual_routes(runs_root: Path) -> dict[str, str]:
    """Map ``brief_id -> selected_route`` from run records that consumed it.

    Run records live at ``<runs_root>/<repo-name>/<run-id>.yaml``. When more
    than one run consumed the same brief, the newest run id wins — a later
    dispatch supersedes an earlier abandoned one.
    """
    if not runs_root.is_dir():
        return {}

    newest: dict[str, tuple[str, str]] = {}
    for path in sorted(runs_root.glob("*/*.yaml")):
        try:
            record = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(record, dict):
            continue
        route = _normalize_route(record.get("selected_route"))
        if not route:
            continue
        run_id = str(record.get("run_id") or path.stem)
        for brief_id in _consumed_ids(record.get("handoffs_consumed")):
            previous = newest.get(brief_id)
            if previous is None or run_id > previous[0]:
                newest[brief_id] = (run_id, route)
    return {brief_id: route for brief_id, (_, route) in newest.items()}


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def audit_coverage(
    queue_root: Path,
    runs_root: Path,
    limit: int = DEFAULT_LIMIT,
    since: str | None = None,
    state: dict[str, Any] | None = None,
    resumable_state: bool | None = None,
) -> dict[str, Any]:
    """Replay the classifier over the brief corpus and cluster disagreements."""
    replay_state = REPLAY_STATE if state is None else state
    briefs = load_briefs(queue_root)
    actual_routes = load_actual_routes(runs_root)

    if since:
        briefs = [b for b in briefs if b["created"] and b["created"][:10] >= since]
    scanned = len(briefs)
    if limit and limit > 0:
        briefs = briefs[:limit]

    comparisons: list[dict[str, Any]] = []
    skipped: dict[str, int] = defaultdict(int)

    for brief in briefs:
        focus = brief["focus"]
        if not focus:
            skipped[SKIP_NO_FOCUS] += 1
            continue

        actual = actual_routes.get(brief["brief_id"])
        recommended = brief["recommended_route"]
        if actual:
            expected, source = actual, "actual"
        elif recommended:
            expected, source = recommended, "recommended"
        elif brief["raw_recommended"] is not None:
            skipped[SKIP_BAD_ROUTE] += 1
            continue
        else:
            skipped[SKIP_NO_EXPECTED] += 1
            continue

        result = classify(
            focus,
            state=replay_state,
            handoff_route=None,
            pr_states=None,
            resumable_state=resumable_state,
        )
        comparisons.append(
            {
                "brief_id": brief["brief_id"],
                "expected": expected,
                "expected_source": source,
                "predicted": result["route"],
                "confidence": result["confidence"],
                "agrees": result["route"] == expected,
                "no_signal": _scored_nothing(
                    focus, replay_state, resumable_state, result
                ),
            }
        )

    return {
        "corpus": {
            "queue_root": str(queue_root),
            "runs_root": str(runs_root),
            "briefs_scanned": scanned,
            "briefs_compared": len(comparisons),
            "limit": limit,
            "since": since,
            "skipped": dict(sorted(skipped.items())),
        },
        "replay": {
            "state": replay_state,
            "resumable_state": resumable_state,
            "handoff_hint_applied": False,
        },
        "agreement": _agreement(comparisons),
        "no_signal": _no_signal(comparisons),
        "by_expected_route": _by_expected_route(comparisons),
        "clusters": _clusters(comparisons),
    }


def _scored_nothing(
    focus: str,
    replay_state: dict[str, Any],
    resumable_state: bool | None,
    result: dict[str, Any],
) -> bool:
    """True when the signal tables matched nothing in ``focus``.

    ``classify()`` exposes ``scores`` filtered to ``s > 0``, so an empty map
    normally means no signal fired. One case breaks that reading:
    ``resumable_state=False`` sets ``scores["E"] = -1`` outright, which the
    filter then drops. A brief whose *only* match was an E signal therefore
    reports an empty map despite having signalled strongly — counting it as a
    vocabulary gap would inflate the coverage-gap number with briefs that are
    really deliberate E disqualifications.

    Signal presence is a property of the text, not of the replay knobs, so in
    that one case re-score without the disqualification and read the answer
    off that. ``classify()`` is pure and regex-only, so the extra call is
    cheap and has no side effects.
    """
    if result["scores"]:
        return False
    if resumable_state is not False:
        return True
    undisqualified = classify(
        focus,
        state=replay_state,
        handoff_route=None,
        pr_states=None,
        resumable_state=None,
    )
    return not undisqualified["scores"]


def _agreement(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(comparisons)
    agreed = sum(1 for c in comparisons if c["agrees"])
    return {
        "compared": total,
        "agreed": agreed,
        "disagreed": total - agreed,
        "rate": round(agreed / total, 4) if total else None,
    }


def _no_signal(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Split the corpus by whether the classifier had any evidence at all."""
    items = [c for c in comparisons if c["no_signal"]]
    disagreed = [c for c in items if not c["agrees"]]
    all_disagreements = sum(1 for c in comparisons if not c["agrees"])
    return {
        "count": len(items),
        "disagreed": len(disagreed),
        "share_of_corpus": round(len(items) / len(comparisons), 4)
        if comparisons
        else None,
        "share_of_disagreements": (
            round(len(disagreed) / all_disagreements, 4) if all_disagreements else None
        ),
        "sample_briefs": [c["brief_id"] for c in disagreed[:MAX_SAMPLES]],
    }


def _by_expected_route(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for comparison in comparisons:
        buckets[comparison["expected"]].append(comparison)

    rows: list[dict[str, Any]] = []
    for route in sorted(buckets):
        items = buckets[route]
        agreed = sum(1 for c in items if c["agrees"])
        rows.append(
            {
                "expected": route,
                "route_name": ROUTE_NAMES[route],
                "count": len(items),
                "agreed": agreed,
                "rate": round(agreed / len(items), 4),
            }
        )
    return rows


def _clusters(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group disagreements by ``(expected, predicted)``, most frequent first."""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for comparison in comparisons:
        if not comparison["agrees"]:
            buckets[(comparison["expected"], comparison["predicted"])].append(
                comparison
            )

    clusters: list[dict[str, Any]] = []
    for (expected, predicted), items in buckets.items():
        confident = [c for c in items if c["confidence"] == "high"]
        from_actual = sum(1 for c in items if c["expected_source"] == "actual")
        no_signal = sum(1 for c in items if c["no_signal"])
        clusters.append(
            {
                "expected": expected,
                "predicted": predicted,
                "count": len(items),
                "no_signal_count": no_signal,
                "mis_weighted_count": len(items) - no_signal,
                "high_confidence_count": len(confident),
                "from_actual_route_count": from_actual,
                "actionable": len(confident) >= MIN_ACTIONABLE_CONFIDENT,
                "sample_briefs": [c["brief_id"] for c in items[:MAX_SAMPLES]],
            }
        )

    clusters.sort(key=lambda c: (-c["count"], c["expected"], c["predicted"]))
    return clusters


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_report(report: dict[str, Any]) -> str:
    corpus = report["corpus"]
    replay = report["replay"]
    agreement = report["agreement"]

    lines = ["Classifier Coverage Audit", ""]
    lines.append(
        f"corpus: {corpus['briefs_scanned']} brief(s) scanned, "
        f"{corpus['briefs_compared']} compared"
    )
    lines.append(f"  queue: {corpus['queue_root']}")
    lines.append(f"  runs:  {corpus['runs_root']}")
    if corpus["limit"]:
        lines.append(f"  bounded to newest {corpus['limit']}")
    if corpus["since"]:
        lines.append(f"  since {corpus['since']}")
    for reason, count in corpus["skipped"].items():
        lines.append(f"  skipped {count}: {reason}")

    resumable = replay["resumable_state"]
    resumable_label = "unknown" if resumable is None else str(resumable).lower()
    lines.append("")
    lines.append(
        f"replay: state={json.dumps(replay['state'], sort_keys=True)} "
        f"resumable_state={resumable_label} handoff_hint=withheld"
    )
    lines.append(
        "  (the recommended-route hint is withheld on purpose — scoring the "
        "classifier against a hint it was given is tautological)"
    )
    if resumable is False:
        lines.append(
            "  (resumable_state=false disqualifies Route E outright, so "
            "E-expected briefs score 0% here by construction — read the E rows "
            "as modelling only, not as a regression)"
        )

    lines.append("")
    if agreement["rate"] is None:
        lines.append("agreement: no comparable briefs")
        return "\n".join(lines) + "\n"
    lines.append(
        f"agreement: {agreement['agreed']}/{agreement['compared']} "
        f"({agreement['rate'] * 100:.1f}%)"
    )

    no_signal = report["no_signal"]
    if no_signal["count"]:
        lines.append(
            f"no-signal: {no_signal['count']} brief(s) scored zero classifier "
            f"signals ({no_signal['share_of_corpus'] * 100:.1f}% of corpus); "
            f"{no_signal['disagreed']} of those disagree"
        )
        if no_signal["share_of_disagreements"] is not None:
            lines.append(
                f"  = {no_signal['share_of_disagreements'] * 100:.1f}% of all "
                "disagreements are signal-table coverage gaps, not mis-weighting"
            )

    lines.append("")
    lines.append("Per expected route:")
    for row in report["by_expected_route"]:
        lines.append(
            f"  {row['expected']}  {row['route_name']:<18} "
            f"{row['agreed']:>4}/{row['count']:<4} ({row['rate'] * 100:5.1f}%)"
        )

    clusters = report["clusters"]
    lines.append("")
    if not clusters:
        lines.append("Disagreement clusters: none")
        return "\n".join(lines) + "\n"

    lines.append("Disagreement clusters (expected -> predicted), most frequent first:")
    for cluster in clusters:
        flag = "  <-- actionable" if cluster["actionable"] else ""
        lines.append(
            f"  {cluster['count']:>4}  {cluster['expected']} -> {cluster['predicted']}"
            f"   no-signal: {cluster['no_signal_count']}"
            f"   mis-weighted: {cluster['mis_weighted_count']}"
            f"   high-confidence: {cluster['high_confidence_count']}"
            f"   from-actual: {cluster['from_actual_route_count']}{flag}"
        )
        for brief_id in cluster["sample_briefs"]:
            lines.append(f"          {brief_id}")

    lines.append("")
    lines.append(
        f"`actionable` = at least {MIN_ACTIONABLE_CONFIDENT} disagreement(s) the "
        "classifier made at high confidence (never a no-signal default, which "
        "is always low confidence)."
    )
    lines.append(
        "`no-signal` disagreements need signal-table vocabulary coverage; "
        "`mis-weighted` ones need weight or precedence changes."
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay classify.py over historical handoff briefs and "
        "report route disagreement clusters (read-only)"
    )
    parser.add_argument(
        "--queue-dir",
        default=None,
        help="work-queue root holding queue/ and picked/ "
        "(default: $WORK_QUEUE_DIR or ~/work-queue)",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="run-record root for the actual-route join "
        "(default: worktrail_home()/runs)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"audit only the newest N briefs (default {DEFAULT_LIMIT}; 0 = no limit)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="only briefs created on/after this YYYY-MM-DD date",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="JSON classifier state override (default: the pinned replay state)",
    )
    parser.add_argument(
        "--resumable-state",
        default="unknown",
        choices=["unknown", "true", "false"],
        help="model the front door's resumable-state pre-check "
        "(default unknown: leaves Route E scoring untouched)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    emit_json = "--json" in raw
    args = _build_parser().parse_args([arg for arg in raw if arg != "--json"])

    queue_root = (
        Path(args.queue_dir).expanduser() if args.queue_dir else default_queue_dir()
    )
    runs_root = (
        Path(args.runs_dir).expanduser() if args.runs_dir else worktrail_home() / "runs"
    )

    if not queue_root.is_dir():
        print(f"error: work-queue root not found: {queue_root}", file=sys.stderr)
        return 2

    state: dict[str, Any] | None = None
    if args.state:
        try:
            state = json.loads(args.state)
        except json.JSONDecodeError as exc:
            print(f"error: --state is not valid JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(state, dict):
            print("error: --state must be a JSON object", file=sys.stderr)
            return 2

    resumable = {"unknown": None, "true": True, "false": False}[args.resumable_state]

    report = audit_coverage(
        queue_root=queue_root,
        runs_root=runs_root,
        limit=args.limit,
        since=args.since,
        state=state,
        resumable_state=resumable,
    )

    if emit_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
