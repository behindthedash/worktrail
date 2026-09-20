#!/usr/bin/env python3
"""Risk tier from a judgment about what a change *does*, not which words it uses.

``classify.RISK_SIGNALS`` tiers purely on keyword presence, and both error
directions are live. Measured 2026-09-19 on a 24-item adversarial probe set
(``tests/fixtures/risk_probes.json``, recorded answers in
``tests/fixtures/risk_probe_answers.json``):

============================  ========  =========
metric                        keyword   judgment
============================  ========  =========
exact tier                    8/24      20/24
auto-merge gate decision      10/24     24/24
unsafe auto-merges            7         0
false human gates             7         0
============================  ========  =========

The keyword table rated prod data deletion, an auth fail-open, refund
re-submission and "disable a required check" as *mergeable*, and rated seven
trivial doc/test changes *critical* because they contained ``billing``,
``secrets`` or ``truncate``. The false-gate direction is a confirmed production
incident: brief ``20260910-090241`` (devops PR #366) was hand-merged because a
dependency named ``better-auth`` scored ``high:authz``. Every judgment tier
error was off by one and none crossed the auto-merge boundary.

**This module never decides policy.** It asks one request's worth of questions
and returns the numbers; ``compose_tier()`` -- pure, offline, unit-tested --
maps them onto ``classify.RISK_ORDER``. The mapping stays in code so it is
reviewable and regression-testable without the API.

**Fail safe, always.** ``judge_risk()`` returns ``None`` on a missing
``TYPESAFE_API_KEY``, any HTTP or transport error, a timeout, or an answer that
does not match the expected shape. Its caller
(``classify.classify_risk``) then uses the keyword table, which errs toward
over-gating. CI needs no key and no network: with the key unset the judgment
path is never entered, so the offline behaviour is exactly today's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import typesafe

# The transport, credential, timeout and "what counts as unavailable" rule live
# in `typesafe.py`, shared with `relatedness_judgment.py`. Re-exported so this
# module's own readers and tests keep one import.
API_KEY_ENV = typesafe.API_KEY_ENV

# Noul value at or above which a red line is treated as crossed. From the
# evaluation harness; on the probe set every true red line scored >= 0.9 and
# every false one <= 0.1, so the exact cut is not load-bearing within that gap.
RED_LINE = 0.70

# The four ordered blast-radius levels, index-aligned to classify.RISK_ORDER.
_TIERS = ("low", "medium", "high", "critical")

# Red lines that force `critical` on their own: each is a harm that reverting
# the code does not undo.
_HARD_RED_LINES = ("irreversible_data_loss", "weakens_access_control", "moves_money")
# A weakened guard forces `high` -- serious enough that a human looks, but the
# change itself is still revertible.
_SAFEGUARD_RED_LINE = "disables_a_safeguard"

_NOUL_KEYS = (*_HARD_RED_LINES, _SAFEGUARD_RED_LINE)

QUESTIONS: dict[str, Any] = {
    "blast_radius": {
        "type": "score",
        "instructions": {
            "task": (
                "Rate how much damage this proposed change could do if it "
                "merged with no human review and turned out to be wrong."
            ),
            "judge": (
                "Weigh what the change actually does to real systems and "
                "data, not which words it uses."
            ),
        },
        "criteria": [
            (
                "Contained: affects documentation, comments, tests, local tooling, "
                "formatting, or a small isolated UI detail. A mistake is noticed "
                "easily and reverted with no lasting effect."
            ),
            (
                "Ordinary: normal product code touching a feature's behavior or a "
                "bounded service setting. A mistake degrades a feature for some "
                "users until it is reverted, but nothing is lost."
            ),
            (
                "Serious: changes production infrastructure, data shape, traffic "
                "routing, or removes/weakens a safeguard. A mistake causes an "
                "outage, exposure, or work that cannot simply be reverted."
            ),
            (
                "Severe: destroys or exposes real data, moves real money "
                "incorrectly, removes an access-control check, or performs an "
                "irreversible production cutover. A mistake is not recoverable by "
                "reverting the code."
            ),
        ],
    },
    "irreversible_data_loss": {
        "type": "noul",
        "instructions": (
            "Would running this change permanently remove, overwrite, or "
            "destroy real production data?"
        ),
        "criteria": {
            "true": "Real stored data is deleted, dropped, truncated, or irreversibly rewritten.",
            "false": "No production data is destroyed, or only local/test/cache data is affected.",
        },
    },
    "weakens_access_control": {
        "type": "noul",
        "instructions": (
            "Does this change remove, bypass, loosen, or fail open on an "
            "authentication, authorization, or security check?"
        ),
        "criteria": {
            "true": "Someone who previously could not access or do something now can, "
            "or a check is skipped or allowed to fail open.",
            "false": "Access control is unchanged or strengthened.",
        },
    },
    "moves_money": {
        "type": "noul",
        "instructions": (
            "Does this change affect real money movement -- charges, refunds, "
            "payouts, or the amounts recorded for them?"
        ),
        "criteria": {
            "true": "It can cause a real financial transaction to occur, repeat, "
            "or be recorded at a different amount.",
            "false": "It only displays, documents, or tests financial data without "
            "changing what is charged or paid.",
        },
    },
    "disables_a_safeguard": {
        "type": "noul",
        "instructions": (
            "Does this change turn off, skip, or defang a guard that currently "
            "blocks bad outcomes -- a required check, gate, validation, or "
            "verification step?"
        ),
        "criteria": {
            "true": "A protection that currently blocks or fails is made non-blocking, "
            "skipped, or removed.",
            "false": "No existing guard is weakened.",
        },
    },
}


def is_configured() -> bool:
    """True when an API key is present. No network call, no key value read."""
    return typesafe.is_configured()


def ask(text: str) -> dict[str, Any]:
    """Ask every question about ``text`` in one request. Raises on any failure."""
    return typesafe.post({"proposed_change": text}, QUESTIONS)


def _tier_from_score(score: float) -> str:
    """Round a 0-3 blast-radius score onto ``_TIERS``, clamped to its range."""
    return _TIERS[max(0, min(len(_TIERS) - 1, round(score)))]


def compose_tier(score: float, nouls: dict[str, float]) -> tuple[str, list[str]]:
    """Map one answer set onto ``(tier, labels)``. Pure; no network, no policy state.

    The blast-radius score sets the baseline tier. A crossed red line can only
    RAISE it, never lower it: a hard red line (data loss, access control,
    money) forces ``critical``, and a weakened safeguard forces ``high``. A
    change can be rated "ordinary" on blast radius and still destroy data --
    the red lines exist precisely to catch that, so they are applied as a floor
    rather than folded into the score.

    Labels mirror ``classify_risk``'s ``<tier>:<label>`` shape so downstream
    readers need no new format: the baseline is reported as
    ``<tier>:blast-radius``, and each crossed red line as
    ``<forced-tier>:<red-line>`` with underscores hyphenated.
    """
    tier = _tier_from_score(score)
    labels = [f"{tier}:blast-radius"]
    for key in _NOUL_KEYS:
        if nouls.get(key, 0.0) < RED_LINE:
            continue
        forced = "critical" if key in _HARD_RED_LINES else "high"
        labels.append(f"{forced}:{key.replace('_', '-')}")
        if _TIERS.index(forced) > _TIERS.index(tier):
            tier = forced
    return tier, labels


def parse_answers(response: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Pull ``(blast_radius score, {noul: value})`` out of a response.

    Raises ``ValueError`` on anything that is not the expected shape, so a
    changed or truncated response fails into the keyword fallback rather than
    producing a tier from a missing field read as zero.
    """
    # Every rejection is a ValueError, not a TypeError: to this module a
    # response of the wrong shape and a response missing a field are the same
    # event -- "the answer is unusable" -- and `judge_risk` turns both into the
    # keyword fallback. Splitting them would add a distinction no caller acts on.
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("response has no answers mapping")  # noqa: TRY004
    blast = answers.get("blast_radius")
    if not isinstance(blast, dict) or not isinstance(blast.get("score"), (int, float)):
        raise ValueError("response has no numeric blast_radius score")  # noqa: TRY004
    nouls = {key: typesafe.noul(answers, key) for key in _NOUL_KEYS}
    return float(blast["score"]), nouls


def judge_risk(
    text: str, *, asker: Callable[[str], dict[str, Any]] | None = None
) -> tuple[str, list[str]] | None:
    """``(tier, labels)`` for ``text``, or ``None`` when the judgment is unavailable.

    ``None`` is the "use the keyword table" signal and covers every failure
    mode: no API key, HTTP error, transport error, timeout, unparseable body,
    and an answer set missing a field. ``asker`` is injected by tests; the
    default is :func:`ask`.
    """
    if asker is None:
        if not is_configured():
            return None
        asker = ask
    try:
        score, nouls = parse_answers(asker(text))
    except typesafe.JUDGMENT_ERRORS:
        return None
    return compose_tier(score, nouls)
