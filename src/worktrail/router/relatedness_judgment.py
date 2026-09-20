#!/usr/bin/env python3
"""Decide brief relatedness by reading the pair, not by counting shared words.

`cluster_detect` forms a `focus-overlap` edge at `OVERLAP_THRESHOLD` over
bag-of-words overlap. Lexical overlap and shared work are different things, and
the gap is measured in both directions (2026-09-19):

- **Paraphrases are invisible.** Three pairs describing identical work scored
  **0.00-0.12** lexical overlap -- far below the 0.45 threshold, so the rule
  cannot see them at all -- while the judgment rated them **0.76-0.86** on
  `same_work`.
- **High overlap is not relatedness.** Of the 14 highest-overlap real corpus
  pairs, **7 were unrelated** by inspection: two different epics'
  decomposition-gap briefs, a reject-UI brief against a vitest-coverage brief,
  and so on.

Two Nouls per pair -- `same_work` and `should_cluster` -- are the whole
interface. `should_edge()` is pure and offline, so the rule that turns those
two numbers into an edge stays reviewable and regression-testable without the
service.

**The lexical stage is demoted to a prefilter, never removed.** It still
chooses which pairs are worth a request (`PREFILTER_FLOOR`, far below the edge
threshold) and still ranks them, so a bounded budget is spent on the most
plausible candidates first. And it is still the whole decision whenever the
judgment is unavailable.

**Bounded and fail-safe.** At most `MAX_JUDGED_PAIRS` pairs are judged per run
-- pair count grows quadratically with queue size, and an unbounded fan-out
over a 68-brief queue is not something a dashboard render may do. Every pair
beyond the cap, and every pair at all when there is no credential or the
service errors, falls back to the existing threshold behaviour unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import typesafe

# Noul value at or above which the judgment is taken as a yes. Same cut the
# risk backend uses; on the measured pairs the true and false populations sat
# at 0.76-0.86 and below 0.2, so the exact value is not load-bearing in that gap.
JUDGMENT_THRESHOLD = 0.70

# Lexical overlap a pair must clear to be *offered* to the judgment. Far below
# `OVERLAP_THRESHOLD` on purpose: the paraphrase pairs this exists to catch
# scored 0.00-0.12, so a prefilter anywhere near the edge threshold would
# reproduce exactly the blindness being fixed. It is not zero because a pair
# with no shared token at all is not a candidate a reader would even consider,
# and the budget is better spent elsewhere.
PREFILTER_FLOOR = 0.05

# Parallelism for a batch. Each pair is one blocking HTTP round trip at ~0.55s,
# so judging a full budget serially would add ~22s to whatever asked for it --
# on the dashboard's render path, unusable. Eight is enough to bring a full
# budget under ~3s while staying far below anything a rate limiter would mind.
MAX_CONCURRENCY = 8

# Most pairs judged in one run. Pairs are ranked by lexical overlap descending,
# so the budget goes to the strongest candidates first and the rest keep the
# lexical decision. The queue was 68 briefs when this was written -- 2278 pairs
# before repo scoping -- so the cap, not the floor, is what bounds cost.
MAX_JUDGED_PAIRS = 40

QUESTIONS: dict[str, Any] = {
    "same_work": {
        "type": "noul",
        "instructions": (
            "Do these two queued work items describe the same underlying piece "
            "of work, such that doing one would substantially do the other?"
        ),
        "criteria": {
            "true": (
                "They target the same defect, capability, or artifact; "
                "completing one would make the other redundant or trivial."
            ),
            "false": (
                "They are separate pieces of work, even if they touch the same "
                "area, repository, or vocabulary."
            ),
        },
    },
    "should_cluster": {
        "type": "noul",
        "instructions": (
            "Should these two items be picked up together in one change, "
            "rather than by two independent agents?"
        ),
        "criteria": {
            "true": (
                "Doing them separately would cause rework, conflicting edits, "
                "or a half-done outcome."
            ),
            "false": (
                "They can be done independently at different times without interfering."
            ),
        },
    },
}


def is_configured() -> bool:
    """True when the judgment backend has a credential to use."""
    return typesafe.is_configured()


def ask(focus_a: str, focus_b: str) -> dict[str, Any]:
    """Both questions about one pair, in one request. Raises on any failure."""
    return typesafe.post({"item_a": focus_a, "item_b": focus_b}, QUESTIONS)


def should_edge(same_work: float, should_cluster: float) -> bool:
    """Whether this pair's two Nouls make an edge. Pure; no network, no policy state.

    Either signal on its own is enough. They are not redundant: `same_work`
    true means one item subsumes the other, and `should_cluster` true means
    doing them apart causes rework even though they are distinct pieces. A pair
    that is either of those belongs in one cluster, so the rule is a union
    rather than a conjunction -- requiring both would drop exactly the
    "different work, must land together" case the second question exists for.
    """
    return same_work >= JUDGMENT_THRESHOLD or should_cluster >= JUDGMENT_THRESHOLD


def judge_pair(
    focus_a: str,
    focus_b: str,
    *,
    asker: Callable[[str, str], dict[str, Any]] | None = None,
) -> tuple[bool, float, float] | None:
    """`(edge, same_work, should_cluster)` for one pair, or `None` if unavailable.

    `None` is the "keep the lexical decision" signal and covers every failure
    mode: no credential, HTTP error, transport error, timeout, unparseable
    body, and an answer missing either Noul. `asker` is injected by tests.
    """
    if asker is None:
        if not is_configured():
            return None
        asker = ask
    try:
        answers = asker(focus_a, focus_b)["answers"]
        same_work = typesafe.noul(answers, "same_work")
        should_cluster = typesafe.noul(answers, "should_cluster")
    except typesafe.JUDGMENT_ERRORS:
        return None
    return should_edge(same_work, should_cluster), same_work, should_cluster


def judge_pairs(
    pairs: Sequence[tuple[str, str]],
    *,
    asker: Callable[[str, str], dict[str, Any]] | None = None,
) -> list[tuple[bool, float, float] | None]:
    """`judge_pair` over a whole batch, concurrently, in the caller's order.

    Returns one entry per input pair, positionally aligned, so the caller's
    own ordering (and therefore its determinism) is unaffected by which round
    trip finishes first. An entry is `None` exactly when that pair was
    unavailable.

    Concurrency lives here rather than in the caller so the module that owns
    the client owns how hard it is driven, and so `cluster_detect` -- whose
    contract is read-only and never-crash -- stays free of thread management.
    """
    if not pairs:
        return []
    if not is_configured() and asker is None:
        return [None] * len(pairs)
    workers = min(MAX_CONCURRENCY, len(pairs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(lambda pair: judge_pair(pair[0], pair[1], asker=asker), pairs)
        )
