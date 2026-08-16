#!/usr/bin/env python3
"""GO v2 route classifier — deterministic, stdlib-only.

Maps a free-text engineering request (plus optional repo-state signals) onto the
v2 route model A-J, a risk level, autonomy gates, and a confidence grade.

Routes (see references/v2-design.md §2.2):
  A idea-discovery      B epic-planning       C feature-planning
  D implementation      E continue-resume     F defect-repair
  G spec-change         H refactor-debt       I investigation
  J workflow-evolution

Output is structured JSON so the conductor (SKILL.md) and the routing cassette
(test_classify.py + cassettes/routing_cassette.json) can assert it exactly.

Usage:
  classify.py --request "text" [--state '{"active_specs":1,...}']
              [--handoff-route F] --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

ROUTE_NAMES = {
    "A": "idea-discovery",
    "B": "epic-planning",
    "C": "feature-planning",
    "D": "implementation",
    "E": "continue-resume",
    "F": "defect-repair",
    "G": "spec-change",
    "H": "refactor-debt",
    "I": "investigation",
    "J": "workflow-evolution",
}

# Signal tables: (compiled regex, weight, label). Case-insensitive throughout.
# Spec-id citation (e.g. `003-payments`) — named because the D-demotion
# precondition keys on it; a positional ROUTE_SIGNALS["D"][1][0] lookup silently
# broke whenever the D signal list was reordered.
_SPEC_ID_RE = re.compile(r"\b\d{3}-[a-z0-9][a-z0-9-]*\b")

# Cited PR numbers (e.g. `PR #68`), used to look up live PR state so the
# pr-repair signal below doesn't fire for delivery that's already settled.
_PR_NUMBER_RE = re.compile(r"\bpr\s*#?(\d+)\b", re.IGNORECASE)


def _sig(pattern: str, weight: int, label: str) -> Tuple[re.Pattern, int, str]:
    return (re.compile(pattern, re.IGNORECASE), weight, label)


ROUTE_SIGNALS: Dict[str, List[Tuple[re.Pattern, int, str]]] = {
    "J": [
        _sig(r"\bgo\s+(skill|v2|front[- ]?door|conductor)\b", 4, "go-skill"),
        _sig(r"\bfront[- ]?door\b", 3, "front-door"),
        _sig(r"\borchestrator\b", 3, "orchestrator"),
        _sig(r"\bcassette\b", 4, "cassette"),
        _sig(r"\bskill\.md\b|\ba\s+skill\b|\bthe\s+\S+\s+skill\b", 3, "skill-file"),
        _sig(r"\bplugin\b", 3, "plugin"),
        _sig(r"\bagent prompt\b|\bsubagent (contract|prompt)\b", 3, "agent-prompt"),
        _sig(r"\brouting (logic|rules?)\b|\bclassifier\b", 4, "routing-logic"),
        _sig(r"\bclassify\.py\b", 5, "classify-py"),
        _sig(r"\bworkflow itself\b|\bthe (autonomous )?workflow\b", 3, "workflow-self"),
    ],
    "E": [
        _sig(r"\bcontinue\b|\bresume\b|\bpick (it |this )?up\b|\bcarry on\b", 3, "resume-verb"),
        _sig(r"\bleft off\b|\bunfinished\b|\bpartially (complete|done)\b|\bhalf[- ]done\b", 3, "unfinished"),
        _sig(r"\bwhere (was|am) i\b|\bwhat'?s next\b|\bstatus\b", 2, "whereami"),
        _sig(r"\bhandoff\b", 4, "handoff"),
        _sig(r"\bfinish\b.*\b(work|task|spec|feature|branch|pr)\b", 3, "finish-work"),
        _sig(r"\banother agent\b|\bprevious (session|agent|run)\b", 3, "other-agent"),
        _sig(r"\bworktree\b|\bmy branch\b|\bopen pr\b|\bexisting (branch|pr)\b", 2, "existing-work"),
    ],
    "F": [
        _sig(r"\bbug\b|\bdefect\b|\bregression\b", 3, "bug-word"),
        _sig(r"\bbroken\b|\bstopped working\b|\bdoesn'?t work\b|\bnot working\b", 3, "broken"),
        _sig(r"\bcrash(es|ed|ing)?\b|\b5\d\d\b|\bexception\b|\bstack trace\b", 3, "crash"),
        _sig(r"\bfix\b", 2, "fix-verb"),
        _sig(r"\bfails?\b|\bfailing\b|\berrors?\b", 2, "fails"),
        _sig(r"\bwrong(ly)?\b|\bincorrect(ly)?\b|\bshould (be|return|show)\b.*\bbut\b", 3, "violates-expectation"),
    ],
    "I": [
        _sig(r"\binvestigat\w*\b|\bdiagnos\w*\b|\blook into\b|\bdig into\b", 4, "investigate-verb"),
        _sig(r"\broot[- ]cause\b", 3, "root-cause"),
        _sig(r"\bwhy (does|is|did|are|do)\b", 3, "why-question"),
        _sig(r"\bintermittent(ly)?\b|\bsometimes\b|\bflaky\b|\brandomly\b", 3, "nondeterministic"),
        _sig(r"\bnot sure\b|\bno idea\b|\bunknown\b|\bcan'?t (tell|figure)\b", 3, "unknown-cause"),
        _sig(r"\baudit\w*\b", 3, "audit"),
        _sig(r"\bdo(?:es)?\s*not\s+fix\b|\bdon'?t fix\b|\bwithout fixing\b", 3, "no-fix"),
        _sig(r"\brecommend(?:ed|ation)?\b.{0,40}\b(follow[- ]?up|next (?:route|action|step))\b", 3, "recommend-next"),
    ],
    "G": [
        _sig(r"\bchange the (spec|specification|behaviou?r|contract)\b", 4, "change-spec"),
        _sig(r"\binstead of\b|\brather than\b", 2, "instead-of"),
        _sig(r"\bshould now\b|\bfrom now on\b|\bno longer\b", 3, "new-rule"),
        _sig(r"\bdeprecate\b|\bsunset\b|\bretire\b", 3, "deprecate"),
        _sig(r"\bupdate the spec\b|\brevise the spec\b|\bspec(ification)? change\b", 4, "spec-update"),
        _sig(r"\bchange\b.*\bfrom\b.*\bto\b", 3, "from-to"),
    ],
    "H": [
        _sig(r"\brefactor(ing)?\b", 4, "refactor-word"),
        _sig(r"\btech(nical)? debt\b", 4, "tech-debt"),
        _sig(r"\bclean[- ]?up\b|\btidy\b", 3, "cleanup"),
        _sig(r"\bsimplify\b|\bextract\b|\bconsolidate\b|\breorganiz(e|ation)\b|\bmoderniz(e|ation)\b", 2, "restructure"),
        _sig(r"\b(no|without( any)?|preserv\w+) behaviou?r(al)?( change)?\b", 4, "behavior-preserving"),
        _sig(r"\bperformance\b.*\b(improv|optimi|speed)\w*\b|\boptimi[sz]e\b", 2, "performance"),
    ],
    "D": [
        _sig(r"\bimplement\b|\bbuild (it|out|the)\b|\bship\b|\bcode (it|up)\b", 3, "implement-verb"),
        (_SPEC_ID_RE, 3, "spec-id"),
        _sig(r"\bapproved (spec|feature|epic|plan)\b|\bspec is (ready|approved|done)\b", 4, "approved-spec"),
        _sig(r"\bstart (the )?(implementation|coding)\b", 3, "start-impl"),
    ],
    "C": [
        _sig(r"\bnew feature\b|\bfeature\b", 2, "feature-word"),
        _sig(r"\badd\b|\bsupport\b|\benable\b|\ballow (users?|admins?|people)\b", 2, "add-capability"),
        _sig(r"\bplan\b", 2, "plan-verb"),
        _sig(r"\bspec( |-)?(out|up)?\b.*\bfor\b|\bwrite a spec\b", 3, "spec-authoring"),
        _sig(r"\busers? (should|can|need)\b", 2, "user-capability"),
    ],
    "B": [
        _sig(r"\bepic\b", 4, "epic-word"),
        _sig(r"\broadmap\b|\bmulti[- ]phase\b|\bphased\b", 3, "phased"),
        _sig(r"\bplatform\b|\bsuite\b|\bsystem for\b|\bend[- ]to[- ]end (capability|solution)\b", 3, "platform-scale"),
        _sig(r"\bseveral features\b|\bmultiple features\b|\bbreak (this |it )?down into features\b", 4, "multi-feature"),
    ],
    "A": [
        _sig(r"\bidea\b|\bbrainstorm\b", 3, "idea-word"),
        _sig(r"\bwhat if\b|\bcould we\b|\bwould it make sense\b|\bwondering\b", 3, "speculative"),
        _sig(r"\bexplore\b|\bthinking about\b|\bkick around\b", 3, "explore-verb"),
        _sig(r"\bproblem\s*:\s|\bpain point\b|\bstruggl\w+ with\b", 2, "problem-statement"),
        _sig(r"\bnot sure (what|how|if) we (want|need|should)\b", 3, "undefined-outcome"),
    ],
}

# CI/PR-repair signals force Route E (repair existing delivery) with F secondary.
CI_REPAIR = [
    _sig(
        r"\bci\b\s+(?:(?:is|was|keeps?|remains?)\s+)?(?:currently\s+)?"
        r"(?:fail\w*|red\b|broken\b)|"
        r"\b(?:fix|repair)\w*\b.{0,40}\b(?:the\s+)?ci\b",
        6,
        "ci-repair",
    ),
    _sig(r"\bchecks? (are |is )?(failing|red)\b", 4, "checks-failing"),
    _sig(r"\bpr\s*#?\d+\b.*\b(fail|fix|repair|red|broken|conflict)\w*", 4, "pr-repair"),
    _sig(r"\bmerge conflict\b", 4, "merge-conflict"),
    _sig(r"\bpipeline\b.*\b(fail|red|broken)\w*", 4, "pipeline-failing"),
]

# Risk signals (highest matching tier wins). These are free-text keyword
# heuristics over the request/PR prose only -- classify_risk() has no diff and
# cannot tell a real code change from an incidental mention (e.g. citing a
# spec folder named `038-authentication` as an example fires "authz" even
# though no auth code is touched; see PR #86). Do not try to make these
# regexes diff-aware -- there is no diff at classify() time. The correction
# for a false-positive text match lives one layer up, in
# pre_pr_gate.resolve_pr_labels(): when the *actual* diff is confirmed
# docs-only via `is_docs_only()`, the risk these signals produced is capped to
# "low" there before it becomes a PR label. A false-positive match on a
# non-docs-only diff is not caught by that cap and still carries through as
# elevated risk -- an intentional fail-safe/fail-loud default, not a bug: an
# unrelated-but-real code change should get a human's attention at least once
# rather than have its risk silently downgraded from prose analysis alone.
RISK_SIGNALS = [
    ("critical", _sig(r"\bbilling\b|\bpayments?\b|\bstripe\b|\binvoic\w+\b", 0, "billing")),
    ("critical", _sig(r"\bsecrets?\b|\bcredentials?\b|\bapi[- _]?keys?\b|\btokens? rotation\b", 0, "secrets")),
    ("critical", _sig(r"\bdrop\b.{0,40}\b(table|column|database)\b|\bdelete (all|every|production)\b|\bwipe\b|\btruncate\b", 0, "destructive-data")),
    ("critical", _sig(r"\bdisable (auth|authorization|authentication|security)\b|\bbypass (auth|permission)\w*\b", 0, "auth-weakening")),
    ("high", _sig(r"\bauth(entication|orization)?\b|\blogin\b|\bpermissions?\b|\broles?\b|\baccess control\b", 0, "authz")),
    ("high", _sig(r"\bmigrations?\b|\bschema change\b|\balter table\b", 0, "migration")),
    ("high", _sig(r"\bpii\b|\bpersonal data\b|\bpasswords?\b|\bencryption\b", 0, "sensitive-data")),
    ("medium", _sig(r"\bapi\b|\bendpoints?\b|\bcontracts?\b|\bpublic interface\b|\bdatabase\b|\bschema\b", 0, "interface")),
    ("low", _sig(r"\bdocs?\b|\bdocumentation\b|\breadme\b|\bcomments?\b|\btypo\b", 0, "docs-only")),
]

# Protected operations: never auto-merge regardless of policy (v2-design §2.14).
PROTECTED_SIGNALS = [
    _sig(r"\bbilling\b|\bpayments?\b|\bstripe\b", 0, "billing"),
    _sig(r"\bsecrets?\b|\bcredentials?\b|\bapi[- _]?keys?\b", 0, "secrets"),
    _sig(r"\bdrop\b.{0,40}\b(table|column|database)\b|\bdelete (all|every|production)\b|\bwipe\b|\btruncate\b", 0, "destructive-migration"),
    _sig(r"\bdisable (auth|authorization|authentication|security)\b|\bbypass (auth|permission)\w*\b", 0, "auth-weakening"),
    _sig(r"\bmajor (dependency|version) (upgrade|bump)\b", 0, "major-dep-upgrade"),
]

# Pairs where misrouting materially changes the outcome -> ask one question.
MATERIAL_PAIRS = {frozenset(p) for p in (("F", "G"), ("A", "C"), ("B", "C"), ("C", "D"))}
TIE_THRESHOLD = 2

RISK_ORDER = ["low", "medium", "high", "critical"]


def _score_routes(text: str) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
    scores: Dict[str, int] = {r: 0 for r in ROUTE_NAMES}
    hits: Dict[str, List[str]] = {r: [] for r in ROUTE_NAMES}
    for route, signals in ROUTE_SIGNALS.items():
        for rx, weight, label in signals:
            if rx.search(text):
                scores[route] += weight
                hits[route].append(label)
    return scores, hits


def _pr_repair_settled(text: str, pr_states: Optional[Dict[str, str]]) -> bool:
    """True when every PR number cited in `text` is known MERGED/CLOSED --
    the pr-repair signal must not fire for delivery that's already settled.
    No PR-state info (None/{}) means unknown, so this returns False and the
    signal fires as before -- fail-open toward the original behavior."""
    if not pr_states:
        return False
    numbers = _PR_NUMBER_RE.findall(text)
    if not numbers:
        return False
    return all(pr_states.get(n, "").upper() in ("MERGED", "CLOSED") for n in numbers)


def _ci_repair_hits(text: str, pr_states: Optional[Dict[str, str]] = None) -> List[str]:
    hits = []
    for rx, _w, label in CI_REPAIR:
        if not rx.search(text):
            continue
        if label == "pr-repair" and _pr_repair_settled(text, pr_states):
            continue
        hits.append(label)
    return hits


def _pr_state(number: str, repo: Path, runner: Runner = subprocess.run) -> Optional[str]:
    """`gh pr view <number> --json state` -> "OPEN"/"MERGED"/"CLOSED", or None
    if unresolvable. Best-effort/fail-open: any subprocess or parse failure
    returns None (unknown), never a guessed state."""
    try:
        result = runner(["gh", "pr", "view", number, "--json", "state"],
                         cwd=str(repo), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get("state")
    except json.JSONDecodeError:
        return None


def cited_pr_states(text: str, repo: Optional[Path],
                     runner: Runner = subprocess.run) -> Dict[str, str]:
    """Best-effort GitHub state for every `PR #NNN` cited in `text`, keyed by
    PR number string. Returns {} when `repo` is None or every lookup is
    unresolvable -- fail-open, so an unreachable `gh` never narrows scoring
    beyond "no information available"."""
    if not repo:
        return {}
    states: Dict[str, str] = {}
    for number in sorted(set(_PR_NUMBER_RE.findall(text))):
        state = _pr_state(number, repo, runner)
        if state:
            states[number] = state
    return states


def classify_risk(text: str) -> Tuple[str, List[str]]:
    best = "low"
    labels: List[str] = []
    for tier, (rx, _w, label) in RISK_SIGNALS:
        if rx.search(text):
            labels.append(f"{tier}:{label}")
            if RISK_ORDER.index(tier) > RISK_ORDER.index(best):
                best = tier
    return best, labels


def protected_operations(text: str) -> List[str]:
    return [label for rx, _w, label in PROTECTED_SIGNALS if rx.search(text)]


def classify(request: str,
             state: Optional[Dict[str, Any]] = None,
             handoff_route: Optional[str] = None,
             pr_states: Optional[Dict[str, str]] = None,
             resumable_state: Optional[bool] = None) -> Dict[str, Any]:
    """Classify a request. `state` keys (all optional): active_specs (int),
    pending_tasks (bool), open_prs (int), handoff_queue (int), worktrees (int).
    `pr_states` (optional): GitHub state per cited PR number string (from
    `cited_pr_states`), used to suppress the pr-repair signal for PRs already
    MERGED/CLOSED. `resumable_state` (optional, from `check_resumable_state.py`
    for a brief-sourced dispatch): `False` means a mechanical check already
    confirmed no in-flight run record with an existing worktree and no open PR
    reference this brief -- Route E is disqualified outright, regardless of how
    strongly its text signals (e.g. a bug report *about* Route E's own
    false-positive keywords) happen to score. `None` (the default, e.g. a
    free-text dispatch with no claimed brief) leaves E's scoring unchanged.
    This function stays pure/deterministic given its inputs -- live `gh`
    lookups happen only in `main()`/the caller's pre-check, never here."""
    text = (request or "").strip()
    state = state or {}
    reason_parts: List[str] = []

    # 0. Explicit override: "route:X ..." pins the route.
    m = re.match(r"^\s*route\s*:\s*([A-J])\b", text, re.IGNORECASE)
    forced = m.group(1).upper() if m else None

    scores, hits = _score_routes(text)

    handoff_route_norm = (handoff_route.upper()
                           if handoff_route and handoff_route.upper() in ROUTE_NAMES
                           else None)

    # CI/PR repair overrides bug wording: existing delivery is the controlling
    # artifact, so repair routes through E (restore state) with F secondary.
    ci_hits = _ci_repair_hits(text, pr_states)
    if ci_hits:
        scores["E"] = max(scores["E"] + 6, scores["F"] + 4)
        hits["E"].extend(ci_hits)

    # State preconditions.
    if state.get("active_specs", 0) == 0 and not _SPEC_ID_RE.search(text):
        # No pending spec on disk and no spec id cited -> implementation demoted.
        if scores["D"] > 0:
            scores["D"] -= 2
            reason_parts.append("D demoted: no active spec in state and none cited")
    if state.get("handoff_queue", 0) > 0 and "handoff" in text.lower():
        scores["E"] += 2

    # A mechanical pre-check already confirmed no resumable state exists for
    # this specific brief -- disqualify E outright rather than trust text
    # signals, which can't distinguish "resume this worktree" from "no
    # worktree exists" (both contain the word "worktree").
    if resumable_state is False:
        scores["E"] = -1
        reason_parts.append("E disqualified: resumable-state check found no in-flight "
                             "run/worktree or open PR for this brief")

    risk, risk_labels = classify_risk(text)
    protected = protected_operations(text)

    # Pick the route.
    ambiguous_between: List[str] = []
    question = None
    no_signal_default = False
    if forced:
        route = forced
        confidence = "high"
        reason_parts.append(f"explicit override route:{forced}")
    else:
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        (top_route, top_score), (second_route, second_score) = ranked[0], ranked[1]
        if top_score == 0 and resumable_state is not False:
            # No signals at all: resume/dashboard is the safe default ("go").
            # Skipped when a mechanical check already ruled E out -- defaulting
            # to E here would be the same unfounded resume claim, just via the
            # zero-signal path instead of the text-signal path.
            route = "E"
            confidence = "low"
            no_signal_default = True
            reason_parts.append("no signals; defaulting to dashboard/menu (E)")
        else:
            route = top_route
            margin = top_score - second_score
            if (margin < TIE_THRESHOLD
                    and frozenset((top_route, second_route)) in MATERIAL_PAIRS):
                ambiguous_between = sorted([top_route, second_route])
                question = _ambiguity_question(top_route, second_route)
                confidence = "low"
                reason_parts.append(
                    f"material tie {top_route}({top_score}) vs {second_route}({second_score})")
            elif margin >= 4 and top_score >= 3:
                confidence = "high"
            elif margin >= 2:
                confidence = "medium"
            else:
                confidence = "low" if top_score < 3 else "medium"
            reason_parts.append(
                f"{top_route} signals: {hits[top_route]} (score {top_score}, runner-up {second_route}={second_score})")

    # Handoff-recommended-route override. Auto mode has no human present to
    # catch a bad low/medium-confidence guess, so an explicit brief
    # recommendation beats the organic pick outright at low/medium confidence.
    # A high-confidence disagreement is left alone: that's a strong enough
    # organic signal to be worth a fresh look rather than a silent override
    # (repo state may have drifted since the brief was written). This runs
    # after the pick above (not as a score boost) so the override decision is
    # never distorted by its own thumb on the scale.
    #
    # A recommended route absent from organic scores is not, by itself, a
    # safe reason to distrust it: 20260717-091439 (the
    # "handoff-recommended-route-overrides-low-signal-guess" cassette
    # scenario) is exactly that shape (recommended H, organic B=3 from one
    # spurious keyword hit, H absent) and the *recommendation* was correct
    # -- the isolated organic hit was the noise. 20260815-114628 has the
    # identical shape (recommended B, organic J=4/F=2/G=2, B absent) but the
    # *recommendation* was wrong there. Scores alone can't tell these apart
    # by presence/absence; what differs is whether the organic pick has
    # independent corroboration: a lone spike with a zero-score runner-up
    # (second_score == 0, case 20260717) is plausibly a keyword coincidence,
    # while a nonzero runner-up (second_score > 0, case 20260815) means at
    # least two routes independently found signal in the text -- a broader,
    # more trustworthy organic read that an absent, unscored recommendation
    # should not silently beat.
    route_source = "no-signal-default" if no_signal_default else "classifier"
    handoff_route_unsupported = (
        not forced and not no_signal_default
        and scores.get(handoff_route_norm or "", 0) <= 0
        and second_score > 0)
    if (not forced and handoff_route_norm and confidence in ("low", "medium")
            and route != handoff_route_norm
            and not (handoff_route_norm == "E" and resumable_state is False)):
        if handoff_route_unsupported:
            reason_parts.append(
                f"brief recommended-route {handoff_route_norm} ignored: absent from "
                f"organic scores while runner-up {second_route}={second_score} also "
                f"scored (broad organic agreement); organic {route} "
                f"(confidence {confidence}) kept")
        else:
            reason_parts.append(
                f"overrode organic route {route} (confidence {confidence}) "
                f"with brief recommended-route {handoff_route_norm}")
            route = handoff_route_norm
            confidence = "medium"
            ambiguous_between = []
            question = None
            route_source = "handoff-recommended-override"

    # Secondary routes.
    secondary: List[str] = []
    if ci_hits and route == "E":
        secondary.append("F")
    for r, s in scores.items():
        if r != route and s >= 4 and r not in secondary and r not in ambiguous_between:
            secondary.append(r)
    secondary = sorted(set(secondary))

    # Gates (bounded autonomy).
    gates: List[str] = []
    if protected:
        gates.append("require_human_approval")
        gates.append("never_automerge")
    elif risk == "critical":
        gates.append("require_human_approval")
    elif risk == "high":
        gates.append("pause_before_merge")
    if route == "J":
        gates.append("routing_cassette_required")
    if route == "A":
        gates.append("no_implementation_without_approval")

    return {
        "route": route,
        "route_name": ROUTE_NAMES[route],
        "secondary": secondary,
        "risk": risk,
        "risk_signals": risk_labels,
        "protected_operations": protected,
        "gates": gates,
        "confidence": confidence,
        "ambiguous_between": ambiguous_between,
        "question": question,
        "reason": "; ".join(reason_parts),
        "scores": {r: s for r, s in scores.items() if s > 0},
        "route_source": route_source,
    }


def _ambiguity_question(a: str, b: str) -> str:
    pair = frozenset((a, b))
    if pair == frozenset(("F", "G")):
        return ("Is the current behavior violating the documented/expected behavior "
                "(bug fix), or are we intentionally changing what the accepted "
                "behavior should be (spec change)?")
    if pair == frozenset(("A", "C")):
        return ("Is this still an open idea to explore, or a defined feature ready "
                "to be specified?")
    if pair == frozenset(("B", "C")):
        return ("Should this be planned as one feature, or decomposed into an epic "
                "with multiple independently valuable features?")
    if pair == frozenset(("C", "D")):
        return ("Do you want a specification first, or is this approved to go "
                "straight to implementation?")
    return f"Route {a} or route {b}?"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--request", default="", help="the user's request text")
    p.add_argument("--state", default=None,
                   help="JSON object of repo-state signals (active_specs, pending_tasks, ...)")
    p.add_argument("--handoff-route", default=None,
                   help="recommended-route letter from a handoff brief")
    p.add_argument("--resumable-state", default=None, choices=["true", "false"],
                   help="result of check_resumable_state.py for a brief-sourced "
                        "dispatch: 'false' disqualifies Route E outright regardless "
                        "of text signals. Omit for a free-text dispatch with no "
                        "claimed brief (leaves E's scoring unchanged).")
    p.add_argument("--repo", default=None,
                   help="repo path for a best-effort gh lookup of cited PR numbers' "
                        "state, so the pr-repair signal doesn't fire for a PR that's "
                        "already merged/closed (omit to skip; fail-open on any error)")
    p.add_argument("--json", action="store_true", help="emit JSON (default)")
    args = p.parse_args(argv)

    state = json.loads(args.state) if args.state else None
    pr_states = cited_pr_states(args.request, Path(args.repo) if args.repo else None)
    resumable_state = {"true": True, "false": False}.get(args.resumable_state)
    result = classify(args.request, state=state, handoff_route=args.handoff_route,
                       pr_states=pr_states, resumable_state=resumable_state)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
