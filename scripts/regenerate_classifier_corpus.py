#!/usr/bin/env python3
"""Regenerate ``tests/fixtures/classifier_corpus.json`` from the live work queue.

The fixture is a redacted sample of the operator's personal work queue, used by
``tests/router/test_classifier_coverage_ratchet.py`` as a CI-visible input to
``classify.py``. Until this script existed, the fixture's ``_meta`` referred to a
"regeneration script" that was never committed, so nobody could reproduce the
fixture, audit its redaction, or change how it is labelled. That is why the
label-quality defect below went unnoticed for a month.

**Only outcome-labelled briefs are sampled.** A brief's route label comes from
one of two places (``classifier_coverage.audit_coverage``'s precedence):

  1. ``actual`` — a run record whose ``handoffs_consumed`` names the brief, read
     from its ``selected_route``. What was really executed.
  2. ``recommended`` — the brief's own ``recommended-route`` frontmatter.

Only (1) is an outcome. (2) is a machine suggestion produced by ``classify.py``
itself or by a capture-time guess, and its staleness is already documented in
``docs/specs/research/recommended-route-frontmatter-staleness-design.md``.
Scoring ``classify.py`` against (2) measures agreement with a guess, so a
ratchet built on it pins noise rather than detecting a routing regression.
Measured 2026-09-19 over all 236 items of the previous fixture: the shipped
regex classifier agreed on 70 (29.7%), a single 10-way LLM Choice on 62 (26.3%),
and a decomposed judgment composed by code on 56 (23.7%); the three methods
agreed with each other on only 23 items, and on those 23 the label matched 12.

Every emitted item carries ``label_source: "actual"``, so the distinction
survives the next regeneration instead of having to be rediscovered.

**Stratification.** The previous fixture took up to 24 items per route, which
produced a near-uniform 22-25 per route. Outcome labels are far rarer (121 of
2246 briefs on 2026-09-19), so the cap is kept but most routes fall well under
it and route B has none at all. Uneven counts are accepted rather than padded
with frontmatter guesses -- padding is exactly the defect being removed.

**Redaction** is applied to every focus text before it is written, and
``tests/router/test_classifier_corpus_redaction.py`` re-checks the committed
fixture so a regeneration cannot quietly leak. See ``REDACTIONS``.

Usage:
    python scripts/regenerate_classifier_corpus.py            # write the fixture
    python scripts/regenerate_classifier_corpus.py --dry-run  # print the summary only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "classifier_corpus.json"

# Max items kept per route. Inherited from the previous fixture's stratification
# so the two are comparable; with outcome labels only, most routes are under it.
MAX_PER_ROUTE = 24

# Placeholder every redacted identifier collapses to. One token, not a per-kind
# family, because the classifier reads word signals and a varied placeholder set
# would itself become a signal.
PLACEHOLDER = "the-repo"

# Ordered redactions, applied to each focus text in sequence. Each is
# (name, compiled pattern, replacement); `name` is what the redaction test
# reports when the committed fixture still matches one.
REDACTIONS: list[tuple[str, re.Pattern[str], str]] = [
    # URLs first: they contain host and path segments the later rules would
    # only partly scrub.
    ("url", re.compile(r"https?://\S+"), "<url>"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    # Absolute home paths, before the bare-repo-name rule eats their segments.
    (
        "home-path",
        re.compile(r"(?:~|/home/[\w.-]+)(?:/[\w.\-]+)*"),
        f"<{PLACEHOLDER}-path>",
    ),
    (
        "pr-ref",
        re.compile(r"(?:PR|issue|pull request)\s*#\s*\d+", re.IGNORECASE),
        "<pr>",
    ),
    # Also the bare `#123` that a run of PR references degenerates into
    # ("PRs #1686/#1687/#1688"), which a `/` in the lookbehind would miss.
    ("bare-issue-ref", re.compile(r"(?<!\w)#\d+\b"), "<pr>"),
    # Work-queue brief ids and run ids (`20260820-024527`, `go-20260811-132806`)
    # are timestamps, but they identify a specific private brief or run.
    ("brief-or-run-id", re.compile(r"\d{8}-\d{6}"), "<brief-id>"),
    # 7-40 hex chars standing alone: a commit sha. Bounded by non-word chars so
    # it cannot eat the hex-looking tail of an identifier.
    ("commit-sha", re.compile(r"(?<![\w-])[0-9a-f]{7,40}(?![\w-])"), "<sha>"),
]


# Names that are not a directory under ``~/projects`` but identify the same
# repos or the products inside them. Every one is documented in ``~/REPOS.md``
# (its "Naming Notes" section for the renames, its project table for the rest);
# there is no machine-readable source for them, so they are listed here and
# ``tests/router/test_classifier_corpus_redaction.py`` re-checks the committed
# fixture against the full set. Add to this list when a repo or product gains
# a name or an alias.
EXTRA_NAMES: tuple[str, ...] = (
    "ggb",  # gracefully-giving-back
    "hearsay",  # former name of career-teleprompt
    "aperi",  # former name of datalena
    "limato",  # former name of datalena
    "limato-nexus",
    "lena",  # the product inside datalena
    "jokgle",  # the app inside briankudera
    "behindthedash",  # the GitHub org
)


def repo_name_redaction(repo_names: list[str]) -> tuple[str, re.Pattern[str], str]:
    r"""A redaction collapsing every known repo/product name to ``PLACEHOLDER``.

    Built from the caller's repo list (plus ``EXTRA_NAMES``) rather than being
    hardcoded wholesale, so onboarding a new repo does not silently start
    leaking its directory name into the fixture. Longest first, so
    ``gracefully-giving-back`` is matched before ``giving``.

    The boundaries are word-character only, NOT ``[\w-]``: a hyphen-joined
    compound is exactly where these names hide (``worktrail-preflight``,
    ``007-lena-knowledge-context-layer``), and excluding hyphen adjacency let
    every one of those through.
    """
    ordered = sorted(
        {n for n in [*repo_names, *EXTRA_NAMES] if len(n) >= 3}, key=len, reverse=True
    )
    pattern = re.compile(
        r"(?<!\w)(" + "|".join(re.escape(n) for n in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )
    return ("repo-name", pattern, PLACEHOLDER)


def redact(text: str, redactions: list[tuple[str, re.Pattern[str], str]]) -> str:
    for _name, pattern, replacement in redactions:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def find_leaks(
    text: str, redactions: list[tuple[str, re.Pattern[str], str]]
) -> list[str]:
    """Names of redactions that still match ``text``. Empty means clean."""
    return [name for name, pattern, _ in redactions if pattern.search(text)]


def known_repo_names(projects_root: Path) -> list[str]:
    """Directory names under ``~/projects`` plus their common short forms."""
    if not projects_root.is_dir():
        return []
    names = {
        p.name
        for p in projects_root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    }
    # `<repo>-worktrees` siblings would otherwise survive as a distinct token.
    names |= {n.removesuffix("-worktrees") for n in names}
    return sorted(names)


def collect(queue_root: Path, runs_root: Path, projects_root: Path) -> dict:
    """Outcome-labelled, redacted, route-stratified items plus a summary."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from worktrail.router import classifier_coverage as cc

    briefs = cc.load_briefs(queue_root)
    actual = cc.load_actual_routes(runs_root)
    redactions = [*REDACTIONS, repo_name_redaction(known_repo_names(projects_root))]

    by_route: dict[str, list[str]] = defaultdict(list)
    for brief in briefs:
        route = actual.get(brief["brief_id"])
        focus = (brief["focus"] or "").strip()
        if not route or not focus:
            continue
        by_route[route].append(redact(focus, redactions))

    items = []
    counts = {}
    for route in sorted(by_route):
        pool = by_route[route]
        kept = _evenly_spaced(pool, MAX_PER_ROUTE)
        counts[route] = len(kept)
        items.extend(
            {"focus": f, "expected_route": route, "label_source": "actual"}
            for f in kept
        )
    return {
        "items": items,
        "counts": counts,
        "considered": len(briefs),
        "outcome_labelled": sum(len(v) for v in by_route.values()),
        "redactions": redactions,
    }


def _evenly_spaced(pool: list[str], cap: int) -> list[str]:
    """Up to ``cap`` evenly spaced picks across ``pool``, order preserved."""
    if len(pool) <= cap:
        return list(pool)
    step = len(pool) / cap
    return [pool[int(index * step)] for index in range(cap)]


def build_fixture(collected: dict, generated: str) -> dict:
    return {
        "_meta": {
            "description": (
                "Bounded, redacted sample of the operator's personal work-queue "
                "corpus (~/work-queue), used by "
                "tests/router/test_classifier_coverage_ratchet.py as a CI-visible "
                "input to classify.py's route classifier. Each item is focus text "
                "(the same field the classifier reads live) plus the route the "
                "brief was ACTUALLY run under, read from the selected_route of a "
                "run record that consumed it. Briefs labelled only by their own "
                "recommended-route frontmatter are excluded: that is a machine "
                "suggestion, not an outcome, and scoring classify.py against it "
                "measures agreement with a guess. label_source records this per "
                "item so the distinction survives the next regeneration."
            ),
            "sampling": (
                f"Stratified: up to {MAX_PER_ROUTE} items per route (A-J), evenly "
                "spaced across each route's available pool. Outcome labels are "
                "rare, so most routes fall well under the cap and a route with no "
                "outcome-labelled brief is absent entirely. Uneven counts are "
                "accepted rather than padded with frontmatter guesses."
            ),
            "regenerated_by": "scripts/regenerate_classifier_corpus.py",
            "generated": generated,
            "item_count": len(collected["items"]),
            "per_route": collected["counts"],
            "briefs_considered": collected["considered"],
            "outcome_labelled_available": collected["outcome_labelled"],
        },
        "items": collected["items"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-root", default=str(Path.home() / "work-queue"))
    parser.add_argument("--runs-root", default=str(Path.home() / ".worktrail" / "runs"))
    parser.add_argument("--projects-root", default=str(Path.home() / "projects"))
    parser.add_argument("--generated", default="", help="date stamp for _meta")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    collected = collect(
        Path(args.queue_root).expanduser(),
        Path(args.runs_root).expanduser(),
        Path(args.projects_root).expanduser(),
    )
    leaked = {
        name
        for item in collected["items"]
        for name in find_leaks(item["focus"], collected["redactions"])
    }
    if leaked:
        print(f"redaction failed -- still matching: {sorted(leaked)}", file=sys.stderr)
        return 1

    print(
        f"{collected['considered']} briefs considered, "
        f"{collected['outcome_labelled']} outcome-labelled, "
        f"{len(collected['items'])} kept after stratification"
    )
    print(f"per route: {collected['counts']}")
    if args.dry_run:
        return 0

    generated = args.generated or __import__("datetime").date.today().isoformat()
    FIXTURE_PATH.write_text(
        json.dumps(build_fixture(collected, generated), indent=1, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
