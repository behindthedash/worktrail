#!/usr/bin/env python3
"""Queue triage: repo-scoped dedup/staleness evaluation of the work queue.

Recommended cadence: monthly, or pre-drain weekly -- not nightly. A full
evaluation pass spawns one agent per repo group and costs on the order of
1M tokens over a non-trivial queue, while queue churn between runs is slow
enough that a tighter cadence would mostly re-spend tokens re-judging
briefs just reviewed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..router import overlap_check
from ..shared.brief_frontmatter import read_frontmatter, split_frontmatter
from ..shared.homedir import worktrail_home
from .score_candidates import _overlap_coefficient, _tokenize
from .work_queue import claim, done, picked_dir, queue_dir, release, resolve

logger = logging.getLogger(__name__)

_FOCUS_BODY_RE = re.compile(r"^##\s+Focus\s*$\r?\n(.+)$", re.MULTILINE)

NO_REPO_KEY = "__none__"

# Tier the evaluator worker spawns under (design D3): routing, not this
# caller, owns the harness/model choice for a given tier.
DEFAULT_TIER = "t2-build"

VALID_VERDICT_TYPES = {
    "keep",
    "stale-close",
    "needs-update",
    "duplicate-of",
    "fold-into-change",
    "propose-change",
    "work-directly",
    "needs-decision",
}

# `propose-change`'s `proposed_change_name` must be a valid OpenSpec change id:
# lowercase alphanumerics separated by single hyphens, no leading/trailing/
# doubled hyphen.
_KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_TRIAGE_HEADING_RE = re.compile(r"^##\s+Triage\s+(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)

# Codifies the 2026-07-31 pilot's lessons (see design.md's "Evaluator prompt
# template" decision): repo-fetch-first, a bounded per-brief tool-call budget,
# a memory check before raising a false alarm, and fail-open-to-`keep` on any
# undecidable case. One spawn per repo group, so `{repo}`/`{briefs}` describe
# a whole group, not a single brief. Kept as a module-level constant (not a
# file) so `tests/workqueue/test_queue_triage.py` can assert on it directly,
# matching how `drain.py` keeps `PROMPT` as an importable constant.
#
# `{briefs}` (built by `evaluate_group()`) carries each brief's 2.1-ranked
# fold/propose candidates inline, so the fold-into-change/propose-change/
# work-directly/needs-decision rules below can be stated once per group
# rather than duplicated per brief.
EVALUATOR_PROMPT_TEMPLATE = """\
You are triaging work-queue briefs for the repo group `{repo}` for staleness, \
duplication, and whether they belong folded into or proposed as an OpenSpec \
change. Evaluate ONLY the briefs listed below; do not scan the queue for \
others.

Briefs in this group (each with its ranked candidate target changes, if any):
{briefs}

Step 1 — repo check (do this first, before judging any brief):
Run `gh repo view --json isArchived,name -- {repo}` (skip this step if `{repo}` \
is `{no_repo_key}` — these briefs are cross-cutting and have no target repo). \
If the repo is confirmed archived or renamed away, every brief in this group is \
`stale-close` on that fact alone — no further per-brief evidence is required. \
If the check fails or is inconclusive (network error, ambiguous name, etc.), \
proceed to step 2 for every brief as normal.

Step 2 — per-brief evaluation:
For each brief above, spend at most 3-4 tool calls (e.g. `git log`, `gh pr list \
--search`, `grep`) confirming or refuting the brief's premise. Cite the specific \
PR, commit, or file you found as evidence — a verdict without cited evidence is \
invalid.

Step 2a — fold vs. propose vs. decide:
Each brief above lists its ranked candidate target changes (from the repo's \
active OpenSpec changes), if any were found. `fold-into-change` may only name \
`target_change` as one of *that brief's own* listed candidate ids — never a \
change that wasn't presented to you, even a plausible-looking one. If none of \
the listed candidates are a good fit but the brief still clearly belongs in \
this repo, use `propose-change` with a `target_repo` and a kebab-case \
`proposed_change_name` instead. If `{repo}` is `{no_repo_key}` (no target \
repo), `fold-into-change` and `propose-change` are never valid for these \
briefs — if the brief needs to land somewhere but you cannot tell where, use \
`needs-decision` with a `question` asking which repo it belongs to, rather \
than guessing a target.

Step 2b — work-directly requires reproduction evidence:
Use `work-directly` only when your evidence cites a specific test, check, or \
command that reproduces or confirms the brief's premise as directly \
actionable right now (e.g. "reproduces via pytest tests/foo -k bar", "confirmed \
via `make lint`"). Evidence that only restates the brief's description without \
such a citation is not sufficient — apply will downgrade a `work-directly` \
verdict lacking one to `keep`, so prefer `keep` yourself when you don't have it.

Step 3 — memory check before raising an alarm:
Before flagging anything you observe as a live operational concern, check \
{memory_index} for whether it already documents the same state as expected or \
known. If it does, that is not new evidence of staleness or a problem — treat \
it as confirming the brief's premise rather than refuting it.

Step 4 — fail open:
If evidence is inconclusive after steps 1-3, do not guess: verdict `keep` and \
record what you checked and why it was inconclusive as the evidence.

For each brief, output one JSON object with exactly these fields (omit fields \
that don't apply to your chosen verdict, or set them null):
{{"brief_id": "...", "verdict": "keep|stale-close|needs-update|duplicate-of|\
fold-into-change|propose-change|work-directly|needs-decision", "duplicate_of": \
"<brief-id or null>", "target_change": "<one of the brief's listed candidate \
ids, for fold-into-change only>", "target_repo": "<repo, for propose-change \
only>", "proposed_change_name": "<kebab-case id, for propose-change only>", \
"question": "<for needs-decision only>", "evidence": "<cited PR/commit/file/\
test, or why inconclusive for a fail-open keep>", "confidence": \
"high|medium|low"}}
"""


def group_queue_by_repo() -> dict[str, list[Path]]:
    """Group every brief in `queue_dir()` by its frontmatter `repo:` value.

    A brief with no `repo` field, or a null/empty one, collapses into the
    single `"__none__"` group so callers always have exactly one bucket for
    repo-less briefs instead of needing to special-case `None`.
    """
    groups: dict[str, list[Path]] = {}
    d = queue_dir()
    if not d.is_dir():
        return groups
    for path in sorted(f for f in d.iterdir() if f.is_file() and f.suffix == ".md"):
        fm = read_frontmatter(path)
        repo = fm.get("repo")
        key = repo.strip() if isinstance(repo, str) and repo.strip() else NO_REPO_KEY
        groups.setdefault(key, []).append(path)
    return groups


def is_recently_triaged(path: Path, within_days: int) -> bool:
    """True if `path`'s most recent ``## Triage <ISO date>`` section is within `within_days`.

    Lenient like the rest of this module's date handling (`work_queue._is_not_yet_due`,
    `_recently_released_info`): an unreadable file, a body with no `## Triage` section, or
    every such section carrying an unparsable date all fall through to False rather than
    raising, since a dedup check that can't confirm recency must not block a brief from
    being evaluated.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    _, body = split_frontmatter(content)

    dates: list[datetime.date] = []
    for raw in _TRIAGE_HEADING_RE.findall(body):
        try:
            dates.append(datetime.date.fromisoformat(raw))
        except ValueError:
            continue
    if not dates:
        return False

    most_recent = max(dates)
    age_days = (datetime.date.today() - most_recent).days  # noqa: DTZ011
    return age_days <= within_days


def inventory(within_days: int) -> tuple[dict[str, list[Path]], list[Path]]:
    """Compose `group_queue_by_repo()` + `is_recently_triaged()` into an evaluation set.

    Briefs whose most recent `## Triage` section falls within `within_days` fail the
    dedup check and are excluded from the returned groups (so 2.x never re-evaluates
    them) but collected into `skipped` for report visibility. A group left empty by
    filtering is dropped entirely rather than kept as an empty bucket.
    """
    skipped: list[Path] = []
    groups: dict[str, list[Path]] = {}
    for key, paths in group_queue_by_repo().items():
        kept: list[Path] = []
        for path in paths:
            if is_recently_triaged(path, within_days):
                skipped.append(path)
            else:
                kept.append(path)
        if kept:
            groups[key] = kept
    return groups, skipped


def _brief_focus(path: Path) -> str:
    """Frontmatter `focus:`, falling back to the first line of a `## Focus` body section.

    Mirrors `work_queue._focus_of()` (not imported directly -- that helper is
    private to `work_queue.py`) so a brief authored before `focus:` frontmatter
    existed still surfaces something for the evaluator prompt.
    """
    fm = read_frontmatter(path)
    if fm.get("focus"):
        return str(fm["focus"])
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _FOCUS_BODY_RE.search(content)
    return m.group(1).strip() if m else ""


def _brief_created(path: Path) -> str:
    created = read_frontmatter(path).get("created")
    return str(created) if created else "unknown"


def rank_change_candidates(
    brief: Path, repo: str | None, top_k: int = 5
) -> list[dict[str, Any]]:
    """Rank `repo`'s active OpenSpec changes as fold/dedup targets for `brief`.

    Enumerates `repo`'s active changes via `overlap_check.scan()` (filtered to
    `stage == "active"`, i.e. `changes/*/proposal.md` entries -- `scan()` also
    returns completed `specs/*/spec.md` entries, which are not fold targets)
    and scores each against `brief`'s focus tokens using the
    `duplicate-brief-detection` spec's focus-overlap coefficient
    (`score_candidates._overlap_coefficient`, `|A ∩ B| / min(|A|, |B|)`), over
    the union of the change's `proposal.md` feature-summary tokens and its
    `tasks.md` task-line tokens (both checked and unchecked, via
    `overlap_check._parse_openspec_tasks()`) -- so a change whose remaining
    work matches the brief ranks even when its proposal summary is sparse.

    Returns the top `top_k` by score descending (ties keep `scan()`'s
    alphabetical order), each as `{"id", "feature_summary",
    "open_task_count", "score"}`. `repo` falsy (`repo: null`) and a repo with
    no active changes both return `[]` -- there is nothing to rank against.
    """
    if not repo:
        return []

    specs_root = Path(repo) / "openspec"
    changes = [c for c in overlap_check.scan(specs_root) if c.get("stage") == "active"]
    if not changes:
        return []

    brief_tokens = _tokenize(_brief_focus(Path(brief)))

    scored: list[tuple[float, dict[str, Any]]] = []
    for change in changes:
        summary = change.get("feature_summary") or ""
        tasks_file = specs_root / "changes" / change["spec_id"] / "tasks.md"
        open_task_count = 0
        task_tokens: set[str] = set()
        if tasks_file.is_file():
            try:
                tasks_text = tasks_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                tasks_text = ""
            for entry in overlap_check._parse_openspec_tasks(tasks_text):
                task_tokens |= _tokenize(entry["task_text"])
                if not entry["checked"]:
                    open_task_count += 1

        score = _overlap_coefficient(brief_tokens, _tokenize(summary) | task_tokens)
        scored.append(
            (
                score,
                {
                    "id": change["spec_id"],
                    "feature_summary": summary,
                    "open_task_count": open_task_count,
                    "score": score,
                },
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in scored[:top_k]]


def _count_active_changes(repo: str) -> int:
    """Count of `repo`'s active OpenSpec changes, per `overlap_check.scan()`.

    Shares `rank_change_candidates()`'s active-change filter (`stage ==
    "active"`) -- a nonexistent `openspec/` dir (e.g. a repo path that isn't
    checked out locally, or a test fixture) scans to `[]`, i.e. 0, rather than
    raising.
    """
    specs_root = Path(repo) / "openspec"
    return sum(1 for c in overlap_check.scan(specs_root) if c.get("stage") == "active")


def _repo_wip_cap(repo: str) -> int:
    """`repo`'s `max_active_changes` policy value, 0 (disabled) if unset or non-int.

    Reads via `router.policy.load_policy()` directly rather than requiring 3.6's
    formal `max_active_changes` key/validation to have landed first: an
    undeclared key in a repo's `.worktrail/policy.yaml` still round-trips onto
    `load_policy()`'s returned dict (as an "unknown key", per its own
    docstring), so a plain `.get(..., 0)` here works whether or not 3.6 has
    shipped yet. Per design D7, 0 means the cap is off -- no repo is ever held.
    """
    from ..router import policy as policy_mod

    value = policy_mod.load_policy(Path(repo)).get("max_active_changes", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def apply_wip_cap_preview(repo: str, verdicts: list[Verdict]) -> list[Verdict]:
    """Stamp `repo`/`held_by_wip_cap` onto `verdicts`, a report-time preview of 3.6's cap.

    Per design D7 ("WIP cap ... only throttles `propose-change`"): every verdict
    gets `repo` set for reporting; a `propose-change` verdict additionally gets
    `held_by_wip_cap=True` when `repo`'s active-change count is at or over its
    (non-zero) `max_active_changes` cap. This is visibility only -- unlike 3.6's
    actual apply-time enforcement, it never mutates `verdict` itself, so `apply`
    run against this run's verdict file is unaffected until 3.6 lands.
    `NO_REPO_KEY`/falsy `repo` is never held: `propose-change` is never a valid
    verdict for a repo-less brief in the first place (per the evaluator prompt).
    """
    if not repo or repo == NO_REPO_KEY:
        return [
            Verdict(**{**asdict(v), "repo": repo, "held_by_wip_cap": False})
            for v in verdicts
        ]

    cap = _repo_wip_cap(repo)
    over_cap = cap > 0 and _count_active_changes(repo) >= cap
    return [
        Verdict(
            **{
                **asdict(v),
                "repo": repo,
                "held_by_wip_cap": over_cap and v.verdict == "propose-change",
            }
        )
        for v in verdicts
    ]


def _format_candidates(candidates: list[dict[str, Any]]) -> str:
    """Render `rank_change_candidates()`'s output for one brief's prompt line.

    `(none)` when the repo has no active changes, is `null`, or is the
    `{no_repo_key}` group -- so the evaluator prompt makes the absence of a
    fold target explicit rather than silently omitting the line.
    """
    if not candidates:
        return "(none)"
    return "; ".join(
        f"{c['id']} (score {c['score']:.2f}, {c['open_task_count']} open tasks): "
        f"{c['feature_summary']}"
        for c in candidates
    )


def _memory_index_path(cwd: str | Path) -> Path:
    """Path to Claude Code's per-project memory index for `cwd`.

    Matches the on-disk `~/.claude/projects/<slug>/memory/MEMORY.md` convention,
    where `<slug>` is `cwd` with every `/` replaced by `-`. The evaluator prompt
    (see `EVALUATOR_PROMPT_TEMPLATE`'s memory-check step) greps this path before
    raising anything as a live concern, so the operator's already-known state
    isn't re-reported as new evidence of staleness.
    """
    slug = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory" / "MEMORY.md"


def _check_repo_archived(repo: str, cwd: str | Path) -> bool | None:
    """Run `gh repo view --json isArchived,name -- <repo>`, confirming archival status.

    Returns `True` only on a clean, well-formed confirmation that the repo is
    archived; `False` on a clean confirmation that it is not; `None` on any check
    failure (missing `gh`, non-zero exit, timeout, unparsable JSON) -- the spec's
    "on any check failure, proceed to 2.2 unchanged" rule treats `None` the same
    as "not archived", it just can't be trusted as a `stale-close` reason on its own.
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "isArchived,name", "--", repo],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("isArchived") is True


def evaluate_group(
    repo: str,
    briefs: list[Path],
    *,
    agent: str = "claude",
    cwd: str | Path,
) -> list[dict]:
    """Spawn one evaluator agent over `repo`'s brief group, per the pilot's grouping.

    Builds `EVALUATOR_PROMPT_TEMPLATE` for this group (one `{id, focus, created}`
    line per brief, `path.stem` as `id` -- matching `work_queue.resolve()`'s
    primary identifier) and spawns one cold headless worker via
    `spawnlib.spawn_agent()` in `cwd`, under `DEFAULT_TIER` with `agent` passed
    through as a soft `prefer` hint (design D3: routing, not this caller, owns
    the tier's harness/model choice). `cwd` is the group's target repo checkout
    when `repo` is not `NO_REPO_KEY` (so the evaluator's `git`/`gh` calls run
    against real repo state), else the worktrail repo itself.

    Before spawning, and only when `repo` is not `NO_REPO_KEY`, runs
    `_check_repo_archived()`. A confirmed `True` short-circuits: every brief in
    the group is synthesized as `stale-close` (as evaluator-shaped JSON text, so
    `parse_verdicts()` consumes it identically to real agent output) without
    spawning an evaluator at all. A `False` or `None` (check failure or
    inconclusive) falls through to the normal spawn unchanged.

    Returns a single-element list -- `[{"repo", "brief_ids", "raw_text",
    "candidates_by_brief"}]` -- rather than the raw string directly, so a
    caller fanning out across multiple groups can `.extend()` every call's
    result into one flat list, without a shape special-case for the archived
    short-circuit. `raw_text` is untouched worker output (or, in the archived
    case, the synthesized equivalent); parsing/validation is `parse_verdicts`'s
    job, not this function's. `candidates_by_brief` maps each brief id to the
    list of candidate change ids 2.1's `rank_change_candidates()` presented to
    the evaluator for it (`[]` for the archived short-circuit and for
    `{no_repo_key}` groups, which have no target repo to rank against) -- a
    caller passes this straight through to `parse_verdicts()` so a
    `fold-into-change` naming anything else is rejected.
    """
    brief_ids = [path.stem for path in briefs]

    if repo != NO_REPO_KEY and _check_repo_archived(repo, cwd) is True:
        raw_text = "\n".join(
            json.dumps(
                {
                    "brief_id": bid,
                    "verdict": "stale-close",
                    "duplicate_of": None,
                    "evidence": f"gh repo view confirmed repo '{repo}' is archived",
                    "confidence": "high",
                }
            )
            for bid in brief_ids
        )
        return [
            {
                "repo": repo,
                "brief_ids": brief_ids,
                "raw_text": raw_text,
                "candidates_by_brief": {bid: [] for bid in brief_ids},
            }
        ]

    from ..orchestrator import spawnlib

    rank_repo = repo if repo != NO_REPO_KEY else None
    candidates_by_path = {
        path: rank_change_candidates(path, rank_repo) for path in briefs
    }

    brief_lines = "\n".join(
        f"- {path.stem}: {_brief_focus(path) or '(no focus recorded)'} "
        f"(created {_brief_created(path)})\n"
        f"  Candidate targets: {_format_candidates(candidates_by_path[path])}"
        for path in briefs
    )
    prompt = EVALUATOR_PROMPT_TEMPLATE.format(
        repo=repo,
        briefs=brief_lines,
        no_repo_key=NO_REPO_KEY,
        memory_index=_memory_index_path(cwd),
    )
    result = spawnlib.spawn_agent(prompt, cwd, tier=DEFAULT_TIER, prefer=agent)
    candidates_by_brief = {
        path.stem: [c["id"] for c in candidates_by_path[path]] for path in briefs
    }
    return [
        {
            "repo": repo,
            "brief_ids": brief_ids,
            "raw_text": result.text,
            "candidates_by_brief": candidates_by_brief,
        }
    ]


@dataclass
class Verdict:
    """One brief's triage outcome, per spec's "Evidence-required verdict per brief".

    `duplicate_of`, `target_change`, `target_repo`, `proposed_change_name`, and
    `question` are the target fields for the verdict types that need one
    (`duplicate-of`, `fold-into-change`, `propose-change`, `needs-decision`
    respectively) -- each stays `None` for every other verdict type.

    `repo` is the group's `repo:` value this verdict was evaluated under (set by
    `cmd_evaluate()` when accumulating `parse_verdicts()`'s output across groups;
    `None` for verdicts built directly, e.g. in tests). `held_by_wip_cap` is a
    report-time-only preview (2.4; the real downgrade-to-`keep` enforcement is
    3.6's job at apply time): true when this is a `propose-change` verdict whose
    `repo` is at or over its `max_active_changes` policy cap (see
    `apply_wip_cap_preview()`), so `write_report()` can surface "held by cap"
    counts ahead of 3.6 landing.
    """

    brief_id: str
    verdict: str
    duplicate_of: str | None
    evidence: str
    confidence: str | None = None
    target_change: str | None = None
    target_repo: str | None = None
    proposed_change_name: str | None = None
    question: str | None = None
    repo: str | None = None
    held_by_wip_cap: bool = False


def _extract_json_objects(text: str) -> list[str]:
    """Return every balanced `{...}` substring of `text`, in order of appearance.

    Evaluator output is free-form text (reasoning, markdown fences, etc.) with one
    JSON object embedded per brief, per `EVALUATOR_PROMPT_TEMPLATE`'s instructed
    output shape -- not a single top-level JSON document. A brace-depth scan (with
    quote-awareness so a literal `{`/`}` inside a string doesn't unbalance the
    count) finds each candidate without assuming anything about what surrounds it.
    """
    objects: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_string = False
        escape = False
        j = i
        while j < n:
            c = text[j]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
            elif c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        if depth == 0:
            objects.append(text[start:j])
            i = j
        else:
            i += 1
    return objects


def _has_valid_target(
    verdict_type: str,
    obj: dict,
    duplicate_of: str | None,
    presented_candidates: list[str],
) -> bool:
    """True if `verdict_type`'s required target field(s) are present and valid in `obj`.

    Each verdict type that names a target validates that target here; every other
    verdict type (`keep`, `stale-close`, `needs-update`, `work-directly`) has no
    target field to check and is always valid. `presented_candidates` is the set of
    change ids actually offered to the evaluator for this brief (per 2.1's
    `rank_change_candidates()`) -- a `fold-into-change` naming anything else,
    including a plausible-looking id, is invalid, since accepting it would let the
    evaluator fold into a change it was never shown.
    """
    if verdict_type == "duplicate-of":
        return duplicate_of is not None
    if verdict_type == "fold-into-change":
        target_change = obj.get("target_change")
        return isinstance(target_change, str) and target_change in presented_candidates
    if verdict_type == "propose-change":
        target_repo = obj.get("target_repo")
        proposed_change_name = obj.get("proposed_change_name")
        return (
            isinstance(target_repo, str)
            and target_repo.strip() != ""
            and isinstance(proposed_change_name, str)
            and bool(_KEBAB_CASE_RE.fullmatch(proposed_change_name))
        )
    if verdict_type == "needs-decision":
        question = obj.get("question")
        return isinstance(question, str) and question.strip() != ""
    return True


def parse_verdicts(
    raw_text: str,
    expected_brief_ids: list[str],
    candidates_by_brief: dict[str, list[str]] | None = None,
) -> list[Verdict]:
    """Parse `evaluate_group()`'s raw evaluator text into one `Verdict` per expected brief.

    Implements the spec's "Evidence-required verdict per brief" requirement: a verdict
    must have `verdict` in `VALID_VERDICT_TYPES`, non-empty `evidence`, and (per
    `_has_valid_target()`) a well-formed target for verdict types that require one --
    `duplicate-of` needs `duplicate_of`, `fold-into-change` needs `target_change`
    naming one of `candidates_by_brief[brief_id]`'s presented candidates,
    `propose-change` needs `target_repo` and a kebab-case `proposed_change_name`,
    and `needs-decision` needs `question` -- to be accepted as-is. `work-directly`
    and `keep` have no target field. Anything missing, unparsable, or failing that
    check falls back to `keep` with the first JSON snippet the evaluator emitted
    under this brief_id retained as evidence -- `raw_text` itself is never used
    here when a group call batches multiple briefs, since it would otherwise bleed
    every other brief's own verdict/evidence into this one's fallback. Only when
    the evaluator emitted nothing identifiable for this brief_id at all does
    `raw_text` remain the fallback, since there is no narrower snippet to prefer --
    every id in `expected_brief_ids` always appears exactly once in the result, in
    that order, never silently dropped.

    `candidates_by_brief` defaults to no candidates presented for any brief, so a
    caller that hasn't wired 2.1's ranking through yet still gets a safe,
    always-downgraded `fold-into-change` rather than an unchecked target.

    `repo`/`held_by_wip_cap` (2.4's per-repo reporting fields) are left at their
    defaults here -- `apply_wip_cap_preview()` stamps them afterward, once per
    group, rather than this per-brief parser threading `repo` through itself.
    """
    candidates_by_brief = candidates_by_brief or {}
    candidates_by_id: dict[str, list[tuple[str, dict]]] = {
        bid: [] for bid in expected_brief_ids
    }
    for snippet in _extract_json_objects(raw_text):
        try:
            obj = json.loads(snippet)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        bid = obj.get("brief_id")
        if isinstance(bid, str) and bid in candidates_by_id:
            candidates_by_id[bid].append((snippet, obj))

    verdicts: list[Verdict] = []
    for bid in expected_brief_ids:
        chosen: Verdict | None = None
        presented_candidates = candidates_by_brief.get(bid, [])
        # This brief's own best-effort evidence when nothing valid is found: the
        # first JSON snippet the evaluator emitted under this brief_id, so a
        # downgrade never falls back past it to `raw_text` -- in a multi-brief
        # batch call, `raw_text` covers every brief's own verdict/evidence, and
        # using it here would bleed other briefs' content into this one's record.
        # Only when the evaluator never emitted anything identifiable for this
        # brief_id at all does `raw_text` remain the sole available evidence.
        fallback_evidence: str | None = None
        for snippet, obj in candidates_by_id[bid]:
            if fallback_evidence is None:
                fallback_evidence = snippet
            verdict_type = obj.get("verdict")
            evidence = obj.get("evidence")
            dup_raw = obj.get("duplicate_of")
            duplicate_of = (
                dup_raw if isinstance(dup_raw, str) and dup_raw.strip() else None
            )
            confidence = obj.get("confidence")

            has_evidence = isinstance(evidence, str) and evidence.strip() != ""
            has_verdict = verdict_type in VALID_VERDICT_TYPES
            has_valid_target = has_verdict and _has_valid_target(
                verdict_type, obj, duplicate_of, presented_candidates
            )

            if has_verdict and has_evidence and has_valid_target:
                target_change = obj.get("target_change")
                target_repo = obj.get("target_repo")
                proposed_change_name = obj.get("proposed_change_name")
                question = obj.get("question")
                chosen = Verdict(
                    brief_id=bid,
                    verdict=verdict_type,
                    duplicate_of=duplicate_of,
                    evidence=evidence,
                    confidence=confidence if isinstance(confidence, str) else None,
                    target_change=target_change
                    if isinstance(target_change, str)
                    else None,
                    target_repo=target_repo if isinstance(target_repo, str) else None,
                    proposed_change_name=proposed_change_name
                    if isinstance(proposed_change_name, str)
                    else None,
                    question=question if isinstance(question, str) else None,
                )
                break

        if chosen is not None:
            verdicts.append(chosen)
        else:
            verdicts.append(
                Verdict(
                    brief_id=bid,
                    verdict="keep",
                    duplicate_of=None,
                    evidence=fallback_evidence
                    if fallback_evidence is not None
                    else raw_text,
                    confidence=None,
                )
            )
    return verdicts


def resolve_duplicate_targets(verdicts: list[Verdict]) -> list[Verdict]:
    """Downgrade dangling `duplicate-of` verdicts to a no-op `keep`.

    Implements the spec's "Duplicate-of verdicts resolve safely" requirement: a
    `duplicate-of` verdict is only safe to act on when its target brief is either
    absent from this batch (not re-evaluated here, so its queue state is
    unaffected by this run) or itself verdicted `keep`. Any other target verdict
    (`stale-close`, `needs-update`, or another `duplicate-of`) means the target is
    about to be closed or rewritten by this same run, so the pointer would apply
    against a moving target -- the referencing verdict is downgraded to `keep`
    (an always-no-op per 4.2's `apply_verdicts()`) with a logged warning, evidence
    and confidence otherwise untouched, rather than acted on.
    """
    by_id = {v.brief_id: v for v in verdicts}
    resolved: list[Verdict] = []
    for v in verdicts:
        target = by_id.get(v.duplicate_of) if v.verdict == "duplicate-of" else None
        if target is not None and target.verdict != "keep":
            logger.warning(
                "dangling duplicate-of: '%s' points to '%s', verdicted '%s' "
                "(not keep/absent) in this batch -- downgrading to a no-op keep",
                v.brief_id,
                v.duplicate_of,
                target.verdict,
            )
            resolved.append(
                Verdict(
                    brief_id=v.brief_id,
                    verdict="keep",
                    duplicate_of=None,
                    evidence=v.evidence,
                    confidence=v.confidence,
                )
            )
        else:
            resolved.append(v)
    return resolved


def _resolve_brief_path(identifier: str) -> Path | None:
    """Locate `identifier`'s brief file, trying `queue_dir()` before `picked_dir()`.

    Only `_apply_needs_update()` needs this: `needs-update` never claims the
    brief, so by the time `apply` runs it may still be sitting in queue/, or
    (if some other session claimed it in the meantime) in picked/ instead.
    `stale-close`/`duplicate-of` don't need this -- `claim()` already resolves
    against queue/ itself.
    """
    for folder in (queue_dir(), picked_dir()):
        res = resolve(identifier, folder)
        if res["status"] == "match":
            return Path(res["candidates"][0])
    return None


def _apply_close(v: Verdict) -> dict:
    """`stale-close`/`duplicate-of`: `claim()` then `done(..., note=evidence)`.

    If `done()` doesn't return "done" (e.g. a Route-C brief that needs an
    explicit `planning_only`/`implementation_complete` decision `done()`
    refuses to make on our behalf), the brief must not stay stranded in
    `picked/` under a `queue-triage` claim nobody will ever release -- so
    this releases it back to `queue/` before returning the error entry,
    keeping a failed apply a true no-op.
    """
    base = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "action": "claim+done",
        "confirm": True,
        "note": v.evidence,
    }
    claim_res = claim(v.brief_id, by="queue-triage")
    if claim_res["status"] != "claimed":
        detail = claim_res.get("error")
        return {
            **base,
            "status": "error",
            "path": claim_res.get("path"),
            "error": f"claim: {claim_res['status']}"
            + (f" ({detail})" if detail else ""),
        }
    done_res = done(v.brief_id, note=v.evidence)
    if done_res["status"] != "done":
        detail = done_res.get("error")
        release_res = release(v.brief_id)
        return {
            **base,
            "status": "error",
            "path": done_res.get("path"),
            "error": f"done: {done_res['status']}" + (f" ({detail})" if detail else ""),
            "rolled_back": release_res["status"] == "released",
        }
    return {**base, "status": "executed", "path": done_res.get("path"), "error": None}


def _apply_needs_update(v: Verdict, run_date: str) -> dict:
    """`needs-update`: append an in-place `## Triage <run_date>` body section.

    Uses the same section shape `is_recently_triaged()` scans for, so a
    subsequent triage run's dedup check sees this run's note without any
    extra bookkeeping. The brief itself is left in place -- unlike
    `_apply_close()`, `needs-update` never claims or closes it.
    """
    base = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "action": "append-triage-note",
        "confirm": True,
        "note": v.evidence,
    }
    path = _resolve_brief_path(v.brief_id)
    if path is None:
        return {
            **base,
            "status": "error",
            "path": None,
            "error": "brief not found in queue/ or picked/",
        }
    try:
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.rstrip("\n") + f"\n\n## Triage {run_date}\n\n{v.evidence}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return {**base, "status": "error", "path": str(path), "error": str(exc)}
    return {**base, "status": "executed", "path": str(path), "error": None}


# Intake-triage verdict types 2.2 makes parseable but whose own apply actions
# ship in later tasks (3.1 fold-into-change, 3.2 propose-change, 3.3
# work-directly, 3.4 needs-decision). Until those land, `apply_verdicts()`
# must not silently fall through to `needs-update`'s append-triage-note
# action for them -- that would misapply the wrong side effect to a brief
# this evaluator run correctly classified differently.
_APPLY_NOT_YET_IMPLEMENTED = frozenset(
    {"fold-into-change", "propose-change", "work-directly", "needs-decision"}
)


def apply_verdicts(verdicts: list[Verdict], *, confirm: bool) -> list[dict]:
    """Execute (or, when `confirm` is false, only log) each non-`keep` verdict.

    Callers are expected to have already run this batch through
    `resolve_duplicate_targets()` -- this function acts on whatever `verdict`
    each `Verdict` carries, it does not re-check dangling `duplicate-of`
    targets itself. Per spec: `stale-close` and `duplicate-of` close the
    brief via `claim()` + `done(..., note=evidence)`; `needs-update` appends
    an in-place `## Triage <run-date>` body section instead of closing it.
    `keep` is always a no-op. `_APPLY_NOT_YET_IMPLEMENTED` verdict types
    (fold-into-change/propose-change/work-directly/needs-decision) have no
    apply action yet -- they are logged `status="not-yet-implemented"` with
    no filesystem mutation, `confirm` notwithstanding, rather than being
    misapplied via the `needs-update` fallback. When `confirm` is false,
    nothing is executed -- every other non-`keep` verdict is logged as
    `"planned"` with no filesystem mutation, so a caller can preview a run
    before committing to it.

    Returns one action-log dict per verdict, in the same order as `verdicts`,
    never dropping any -- `apply`'s `--json`/human output (4.3) renders this
    list directly.
    """
    run_date = datetime.date.today().isoformat()  # noqa: DTZ011
    log: list[dict] = []
    for v in verdicts:
        if v.verdict == "keep":
            log.append(
                {
                    "brief_id": v.brief_id,
                    "verdict": v.verdict,
                    "duplicate_of": v.duplicate_of,
                    "action": "noop",
                    "confirm": confirm,
                    "status": "noop",
                    "path": None,
                    "note": None,
                    "error": None,
                }
            )
            continue

        if v.verdict in _APPLY_NOT_YET_IMPLEMENTED:
            log.append(
                {
                    "brief_id": v.brief_id,
                    "verdict": v.verdict,
                    "duplicate_of": v.duplicate_of,
                    "action": "noop",
                    "confirm": confirm,
                    "status": "not-yet-implemented",
                    "path": None,
                    "note": (
                        f"apply action for verdict '{v.verdict}' is not yet "
                        "implemented; brief left untouched"
                    ),
                    "error": None,
                }
            )
            continue

        action = (
            "claim+done"
            if v.verdict in ("stale-close", "duplicate-of")
            else "append-triage-note"
        )

        if not confirm:
            log.append(
                {
                    "brief_id": v.brief_id,
                    "verdict": v.verdict,
                    "duplicate_of": v.duplicate_of,
                    "action": action,
                    "confirm": confirm,
                    "status": "planned",
                    "path": None,
                    "note": v.evidence,
                    "error": None,
                }
            )
            continue

        if action == "claim+done":
            log.append(_apply_close(v))
        else:
            log.append(_apply_needs_update(v, run_date))
    return log


def write_verdict_file(verdicts: list[Verdict], out_dir: str | Path) -> Path:
    """Serialize `verdicts` to `<out_dir>/verdict.json`, the sole input the `apply` step may act on.

    Per spec's "Apply step never closes a brief without an approved verdict" requirement,
    every verdict here -- including `parse_verdicts()`'s fail-open `keep` fallbacks -- is
    written as-is and in order, so this file is a complete, machine-applyable record of the
    run with nothing silently dropped. Lives outside any target repo (default
    `worktrail_home()/triage/<run-id>/`, per design.md); `out_dir` itself is the
    caller's concern (3.2's CLI), not this function's.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "verdict.json"
    path.write_text(
        json.dumps([asdict(v) for v in verdicts], indent=2) + "\n", encoding="utf-8"
    )
    return path


def compute_run_summary(verdicts: list[Verdict]) -> dict[str, Any]:
    """Verdict-type counts plus 2.4's two derived counts, computed once for reuse.

    `write_report()` and `cmd_evaluate()`'s `--json`/human summary line both call
    this on the same `verdicts` list, so the Markdown report and the JSON-facing
    output can never drift apart for a given run -- matching the spec's existing
    "report counts match the JSON file's contents exactly" scenario, extended to
    these two new counts.

    `pull_requests_opened` counts `fold-into-change`/`propose-change` verdicts --
    the two verdict types whose apply actions (3.1, 3.2) open a pull request --
    so it is a preview of PRs this run's verdicts *will* open once applied, not
    a record of PRs already opened (this function only ever sees pre-apply
    verdicts). A `propose-change` verdict `apply_wip_cap_preview()` flagged
    `held_by_wip_cap` is excluded from this count: 3.6 downgrades it to `keep`
    at apply time, so it will not open a PR, and counting it here would
    contradict the `held_by_wip_cap` figure reported for the same brief.
    `held_by_wip_cap` is `{repo: count}` of `propose-change` verdicts whose
    `held_by_wip_cap` field `apply_wip_cap_preview()` set true, per repo --
    repos with a zero count are omitted rather than zero-filled, since the set
    of repos in a run is dynamic (unlike the fixed `VALID_VERDICT_TYPES` set).
    """
    counts: dict[str, int] = {}
    held_by_wip_cap: dict[str, int] = {}
    pull_requests_opened = 0
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
        if v.held_by_wip_cap and v.repo:
            held_by_wip_cap[v.repo] = held_by_wip_cap.get(v.repo, 0) + 1
        if v.verdict in ("fold-into-change", "propose-change") and not (
            v.verdict == "propose-change" and v.held_by_wip_cap
        ):
            pull_requests_opened += 1
    return {
        "verdict_counts": counts,
        "pull_requests_opened": pull_requests_opened,
        "held_by_wip_cap": held_by_wip_cap,
    }


def write_report(
    verdicts: list[Verdict], skipped: list[Path], out_dir: str | Path
) -> Path:
    """Render a human-readable Markdown summary of the run to `<out_dir>/report.md`.

    Per spec's "Verdict file and human-readable report" requirement: briefs evaluated,
    briefs skipped via dedup, verdict counts by type, and the full per-brief verdict list
    with evidence. Counts are computed directly from `verdicts` (via `compute_run_summary()`)
    so they can never drift from `write_verdict_file()`'s JSON contents for the same run, per
    the spec scenario that the report's verdict counts must match the JSON file's contents
    exactly -- 2.4 extends this to the four new verdict types (zero-filled the same as every
    other type) plus `pull_requests_opened` and per-repo `held_by_wip_cap` counts.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.md"

    summary = compute_run_summary(verdicts)
    counts = summary["verdict_counts"]

    lines: list[str] = [
        "# Queue triage report",
        "",
        f"Briefs evaluated: {len(verdicts)}",
        f"Briefs skipped (recently triaged): {len(skipped)}",
        "",
        "## Verdict counts",
        "",
    ]
    for verdict_type in sorted(VALID_VERDICT_TYPES):
        lines.append(f"- {verdict_type}: {counts.get(verdict_type, 0)}")

    lines += ["", "## Skipped via dedup", ""]
    if skipped:
        lines += [f"- {Path(p).stem}" for p in skipped]
    else:
        lines.append("(none)")

    lines += [
        "",
        "## Pull requests opened",
        "",
        f"- pull_requests_opened: {summary['pull_requests_opened']}",
        "",
        "## Held by WIP cap (per repo)",
        "",
    ]
    if summary["held_by_wip_cap"]:
        for repo in sorted(summary["held_by_wip_cap"]):
            lines.append(f"- {repo}: {summary['held_by_wip_cap'][repo]}")
    else:
        lines.append("(none)")

    lines += [
        "",
        "## Per-brief verdicts",
        "",
        "| Brief | Verdict | Duplicate of | Confidence | Evidence |",
        "|---|---|---|---|---|",
    ]
    for v in verdicts:
        evidence = v.evidence.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {v.brief_id} | {v.verdict} | {v.duplicate_of or ''} | "
            f"{v.confidence or ''} | {evidence} |"
        )
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _worktrail_repo_root() -> Path:
    """This checkout's repo root, for `evaluate_group()`'s repo-less-group `cwd`.

    File-relative resolution, mirroring `drain.default_work_queue_py()`: AGENTS.md
    guarantees the editable install always points at the canonical worktrail
    checkout (never a task worktree), so walking up from `__file__` reliably
    lands on the real repo root without a separate lookup.
    """
    return Path(__file__).resolve().parents[3]


def _default_out_dir() -> Path:
    """`worktrail_home()/triage/<run-id>/`, per design.md -- run output lives
    beside `worktrail_home()/runs`."""
    run_id = f"triage-{time.strftime('%Y%m%d-%H%M%S')}"
    return worktrail_home() / "triage" / run_id


def cmd_evaluate(args: argparse.Namespace) -> int:
    """`evaluate` subcommand: wires 1.3 `inventory()` -> 2.x per group -> 3.1 write.

    `--queue-dir` is applied as a temporary `WORK_QUEUE_DIR` override (restored in
    a `finally`, matching `create_handoff.create()`'s pattern) since `inventory()`
    reaches `queue_dir()` with no override parameter of its own.
    """
    previous_queue_dir = os.environ.get("WORK_QUEUE_DIR")
    if args.queue_dir:
        os.environ["WORK_QUEUE_DIR"] = str(args.queue_dir)
    try:
        groups, skipped = inventory(args.skip_if_triaged_within_days)
    finally:
        if args.queue_dir:
            if previous_queue_dir is None:
                os.environ.pop("WORK_QUEUE_DIR", None)
            else:
                os.environ["WORK_QUEUE_DIR"] = previous_queue_dir

    out_dir = Path(args.out_dir) if args.out_dir else _default_out_dir()

    verdicts: list[Verdict] = []
    for repo, briefs in groups.items():
        cwd = repo if repo != NO_REPO_KEY else _worktrail_repo_root()
        for result in evaluate_group(repo, briefs, agent=args.agent, cwd=cwd):
            group_verdicts = parse_verdicts(
                result["raw_text"],
                result["brief_ids"],
                candidates_by_brief=result["candidates_by_brief"],
            )
            verdicts.extend(apply_wip_cap_preview(repo, group_verdicts))

    verdict_path = write_verdict_file(verdicts, out_dir)
    report_path = write_report(verdicts, skipped, out_dir)

    summary = compute_run_summary(verdicts)
    counts = summary["verdict_counts"]

    if args.as_json:
        print(
            json.dumps(
                {
                    "groups_evaluated": len(groups),
                    "briefs_skipped": len(skipped),
                    "verdict_counts": counts,
                    "pull_requests_opened": summary["pull_requests_opened"],
                    "held_by_wip_cap": summary["held_by_wip_cap"],
                    "verdict_file": str(verdict_path),
                    "report_file": str(report_path),
                },
                indent=2,
            )
        )
    else:
        counts_str = ", ".join(
            f"{vtype}={counts.get(vtype, 0)}" for vtype in sorted(VALID_VERDICT_TYPES)
        )
        print(f"report: {report_path}")
        print(
            f"groups evaluated: {len(groups)}, briefs skipped: {len(skipped)}, "
            f"verdicts: {counts_str}"
        )
        print(
            f"pull requests opened: {summary['pull_requests_opened']}, "
            f"held by WIP cap: {summary['held_by_wip_cap']}"
        )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """`apply` subcommand: loads a verdict file, runs 4.1 -> 4.2, prints the action log.

    The verdict file is `evaluate`'s `write_verdict_file()` output (a JSON list of
    `Verdict`-shaped objects) -- this is the only input `apply` may act on, per spec's
    "Apply step never closes a brief without an approved verdict" requirement. Every
    verdict is run through `resolve_duplicate_targets()` before `apply_verdicts()` so a
    dangling `duplicate-of` in the file can never be acted on, regardless of `--confirm`.
    """
    raw = Path(args.verdict_file).read_text(encoding="utf-8")
    verdicts = [Verdict(**entry) for entry in json.loads(raw)]

    resolved = resolve_duplicate_targets(verdicts)
    action_log = apply_verdicts(resolved, confirm=args.confirm)

    if args.as_json:
        print(json.dumps(action_log, indent=2))
    else:
        for entry in action_log:
            dup = f" -> {entry['duplicate_of']}" if entry.get("duplicate_of") else ""
            error = f" ({entry['error']})" if entry.get("error") else ""
            print(
                f"{entry['brief_id']}{dup}: {entry['verdict']} [{entry['action']}] "
                f"{entry['status']}{error}"
            )
        executed = sum(1 for e in action_log if e["status"] == "executed")
        planned = sum(1 for e in action_log if e["status"] == "planned")
        errors = sum(1 for e in action_log if e["status"] == "error")
        noop = sum(1 for e in action_log if e["status"] == "noop")
        not_yet_implemented = sum(
            1 for e in action_log if e["status"] == "not-yet-implemented"
        )
        print(
            f"executed: {executed}, planned: {planned}, errors: {errors}, "
            f"noop: {noop}, not-yet-implemented: {not_yet_implemented}"
        )
    return 1 if any(e["status"] == "error" for e in action_log) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repo-scoped dedup/staleness triage of the work queue. "
        "Recommended cadence: monthly, or pre-drain weekly -- not "
        "nightly (~1M tokens per full run over a non-trivial queue)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    from ..orchestrator.spawnlib import SUPPORTED_AGENTS

    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="evaluate every repo group and write a verdict file + report",
    )
    evaluate_parser.add_argument(
        "--skip-if-triaged-within-days",
        type=int,
        default=25,
        dest="skip_if_triaged_within_days",
        help="dedup window: briefs with a recent '## Triage' section are skipped (default 25)",
    )
    evaluate_parser.add_argument(
        "--agent",
        default="claude",
        choices=sorted(SUPPORTED_AGENTS),
        help="evaluator agent to spawn per repo group (default claude)",
    )
    evaluate_parser.add_argument(
        "--out-dir",
        default=None,
        help="where to write verdict.json + report.md (default ~/.worktrail/triage/<run-id>/)",
    )
    evaluate_parser.add_argument(
        "--queue-dir",
        default=None,
        help="WORK_QUEUE_DIR override for this run",
    )
    evaluate_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the run summary as JSON on exit",
    )

    apply_parser = subparsers.add_parser(
        "apply",
        help="apply a verdict file's verdicts (or preview them without --confirm)",
    )
    apply_parser.add_argument(
        "--verdict-file",
        required=True,
        dest="verdict_file",
        help="path to an evaluate-produced verdict.json",
    )
    apply_parser.add_argument(
        "--confirm",
        action="store_true",
        help="execute the actions; without this, only log what would happen",
    )
    apply_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the action log as JSON on exit",
    )

    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return cmd_evaluate(args)
    if args.command == "apply":
        return cmd_apply(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
