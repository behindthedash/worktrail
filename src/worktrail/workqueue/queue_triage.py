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
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..orchestrator.integrate import _refresh_pr_labels
from ..router import overlap_check
from ..router.dashboard import _resolve_repo_dir
from ..shared.brief_frontmatter import read_frontmatter, split_frontmatter
from ..shared.homedir import worktrail_home
from . import decisions
from .score_candidates import _overlap_coefficient, _tokenize
from .work_queue import (
    _awaiting_decision_info,
    _set_fm_fields,
    claim,
    done,
    picked_dir,
    queue_dir,
    release,
    resolve,
)

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

# `work-directly` requires evidence citing a specific test, check, or command
# (per the evaluator prompt's step 2b) rather than evidence that only restates
# the brief's description. Matches one of the common reproduction vocabulary
# words/tool names the prompt's own examples use ("reproduces via pytest ...",
# "confirmed via make lint") -- deliberately permissive about *which*
# command/test/check is cited, but every alternative requires an actual named
# tool or verb, never a bare backtick-quoted span on its own (that would also
# match a file path or brief id with no command content at all) and never the
# bare English words "command" or "check", since prose can use those words
# while denying or merely discussing a reproduction reference (e.g. "no
# command needed" or "we should check whether this is still relevant"). `gh`/
# `git` count only with a known read/verify subcommand ("gh repo view", "git
# log") so prose like "git history" or "gh workflow" alone does not qualify;
# `grep`/`rg` count only with a flag ("grep -rn foo") so the bare verb "grep
# for it" does not.
_REPRODUCTION_EVIDENCE_RE = re.compile(
    r"\bpytest\b"
    r"|\btests?/"
    r"|\bmake\s+\w+"
    r"|\bnpm\s+(?:run|test)\b"
    r"|\byarn\s+(?:run|test)\b"
    r"|\bgo\s+test\b"
    r"|\bcargo\s+test\b"
    r"|\bunittest\b"
    r"|\b(?:lint|mypy|ruff|tsc)\b"
    r"|\bgh\s+(?:pr|repo|api|run|issue|release)\b"
    r"|\bgit\s+(?:log|status|show|diff|grep|ls-files|rev-parse|branch|cherry|blame|worktree|fetch)\b"
    r"|\b(?:grep|rg)\s+-"
    r"|\breproduces?\s+via\b"
    r"|\bconfirmed\s+via\b",
    re.IGNORECASE,
)

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
repo), `fold-into-change` is never valid for these briefs. `propose-change` \
is valid only when your evidence names one of these known repos as the \
`target_repo`: {known_repos}. If the brief needs to land somewhere but none \
of these repos fit, or you cannot tell which one does, use `needs-decision` \
with a `question` asking which repo it belongs to, rather than guessing a \
target.

Step 2b — work-directly requires reproduction evidence:
Use `work-directly` only when your evidence cites a specific test, check, or \
command that reproduces or confirms the brief's premise as directly \
actionable right now, naming the command itself (e.g. "reproduces via pytest \
tests/foo -k bar", "confirmed via `make lint`", "confirmed via `gh repo view`", \
"confirmed via `grep -rn foo src/`"). Evidence that only restates the brief's \
description, or says you "read" or "inspected" a file or log without naming a \
command, is not sufficient — apply will downgrade a `work-directly` verdict \
lacking one to `keep`, so prefer `keep` yourself when you don't have it.

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


# 3.2's `propose-change` apply action spawns a second, separate agent (after
# `openspec new change` has scaffolded the change directory) to author the
# actual proposal/design/specs/tasks content -- the triage evaluator prompt
# above only ever *decides* a brief deserves its own change, it never drafts
# one. Kept as a module-level constant for the same reason
# `EVALUATOR_PROMPT_TEMPLATE` is: `tests/workqueue/test_queue_triage.py` can
# assert on it directly.
PROPOSE_CHANGE_PROMPT_TEMPLATE = """\
You are authoring a new OpenSpec change for the repo `{repo}`, proposed as \
`{proposed_change_name}` by queue-triage from work-queue brief `{brief_id}`.

The change directory already exists at \
`openspec/changes/{proposed_change_name}/` (scaffolded via `openspec new \
change {proposed_change_name}`). Write its `proposal.md`, `design.md` (only \
if the change is complex enough to warrant one), the delta spec(s) under \
`specs/`, and `tasks.md` -- follow this repo's own OpenSpec conventions \
(inspect a couple of existing changes under `openspec/changes/` for the \
expected shape if unsure).

Brief evidence (why this change is being proposed):
{evidence}

When you are done, the change must pass `openspec validate \
{proposed_change_name} --strict` -- run it yourself and fix any errors \
before finishing. Do not `git commit` or `git push`; that is handled by the \
caller once you're done.
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


def has_unresolved_decision(path: Path) -> bool:
    """True if `path` carries an `awaiting-decision:` link still `open` or `answered`.

    Mirrors `work_queue._awaiting_decision_info()`'s own reading (already used by
    `claim()`'s warnings and `list_queue()`'s `blocked` flag): `resolved` (a human
    answered and the answer was consumed) and no link at all both read as "not
    unresolved". Per design D8 ("needs-decision ... reuses ... the existing
    'awaiting decision' skip"), a brief still genuinely waiting on a human must not
    be re-evaluated by a later `evaluate` run before that answer lands.
    """
    return _awaiting_decision_info(path)["decision_status"] in ("open", "answered")


def inventory(within_days: int) -> tuple[dict[str, list[Path]], list[Path]]:
    """Compose `group_queue_by_repo()` + `is_recently_triaged()` into an evaluation set.

    Briefs whose most recent `## Triage` section falls within `within_days` fail the
    dedup check and are excluded from the returned groups (so 2.x never re-evaluates
    them) but collected into `skipped` for report visibility. A brief with an
    unresolved pending decision (`has_unresolved_decision()`) is likewise excluded
    from the groups, but -- unlike a dedup skip -- is not added to `skipped`: it isn't
    a triage outcome to report on, it's simply not ready to be re-judged yet, and will
    resurface on its own once a human answers. A group left empty by filtering is
    dropped entirely rather than kept as an empty bucket.
    """
    skipped: list[Path] = []
    groups: dict[str, list[Path]] = {}
    for key, paths in group_queue_by_repo().items():
        kept: list[Path] = []
        for path in paths:
            if is_recently_triaged(path, within_days):
                skipped.append(path)
            elif has_unresolved_decision(path):
                continue
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


def _effective_repo(v: Verdict) -> str | None:
    """The repo `v` actually targets: the group's own `repo` when it is not
    `NO_REPO_KEY`, else `v.target_repo` -- 2.5's rule letting a repo-less
    (`NO_REPO_KEY`) group's `propose-change` verdict name its own target repo,
    since its group has none. `None` when neither is set.
    """
    if v.repo and v.repo != NO_REPO_KEY:
        return v.repo
    return v.target_repo or None


def _propose_change_wip_cap_status(
    v: Verdict, repos_root: str | Path | None = None
) -> tuple[str | None, int, int]:
    """`(repo, cap, count)` for `v`, a fresh apply-time re-check of the WIP cap.

    Recomputed here rather than trusting `v.held_by_wip_cap` (2.4's evaluate-time
    preview): `evaluate` and `apply` can run far enough apart that the repo's
    active-change count or its `max_active_changes` policy value has since
    changed, and 3.6's enforcement point is apply time, not evaluate time.
    `repo` (`_effective_repo(v)`) is `None` for a verdict with neither a group
    repo nor a `target_repo`, in which case `cap`/`count` are both `0` and the
    caller never treats it as over cap. `_effective_repo(v)` can be a bare
    directory basename (2.5's `NO_REPO_KEY` propose-change flow shows the
    evaluator only basenames) -- resolve it against `repos_root` via
    `_resolve_repo_dir()` before reading its cap/count, falling back to the
    raw value only when it doesn't resolve, so the reported `repo` still
    reflects what was checked.
    """
    repo = _effective_repo(v)
    if not repo:
        return None, 0, 0
    repo_path = _resolve_repo_dir(repo, repos_root)
    checked = str(repo_path) if repo_path is not None else repo
    return repo, _repo_wip_cap(checked), _count_active_changes(checked)


def _propose_change_over_cap(v: Verdict, repos_root: str | Path | None = None) -> bool:
    """True if `v` is a `propose-change` verdict whose repo is at or over a
    non-zero `max_active_changes` cap, re-checked fresh (see
    `_propose_change_wip_cap_status()`). A cap of `0` (unset/disabled) never
    holds anything."""
    if v.verdict != "propose-change":
        return False
    _repo, cap, count = _propose_change_wip_cap_status(v, repos_root)
    return cap > 0 and count >= cap


def _propose_change_wip_cap_note(
    v: Verdict, repos_root: str | Path | None = None
) -> str:
    """The `## Triage <date>` note body for a `propose-change` downgraded by
    the WIP cap: names the cap, the current active-change count, and the
    repo's top fold candidates (2.1's `rank_change_candidates()`, re-ranked
    against this brief) as an alternative for the operator to consider instead
    of a new change."""
    repo, cap, count = _propose_change_wip_cap_status(v, repos_root)
    path = _resolve_brief_path(v.brief_id)
    candidates = rank_change_candidates(path, repo) if path and repo else []
    return (
        f"propose-change held by the WIP cap: repo '{repo}' has {count} active "
        f"change(s), at or over its max_active_changes cap of {cap}. Consider "
        f"folding into one of the repo's active changes instead: "
        f"{_format_candidates(candidates)}"
    )


def _apply_propose_change_wip_cap_downgrade(
    v: Verdict, run_date: str, repos_root: str | Path | None = None
) -> dict:
    """Downgrade an over-cap `propose-change` verdict to a no-op `keep`.

    Mirrors `_apply_needs_update()`'s in-place `## Triage <run_date>` note
    append -- the brief is left exactly where it already sits (`queue/` or
    `picked/`), never claimed or closed, so a held `propose-change` behaves
    like any other `keep`: it simply remains available for a later triage run
    once the repo's active-change count drops back under the cap.
    """
    note = _propose_change_wip_cap_note(v, repos_root)
    base = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "action": "append-triage-note",
        "confirm": True,
        "note": note,
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
            content.rstrip("\n") + f"\n\n## Triage {run_date}\n\n{note}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return {**base, "status": "error", "path": str(path), "error": str(exc)}
    return {
        **base,
        "status": "downgraded-to-keep",
        "path": str(path),
        "error": None,
    }


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


def _known_repos(repos_root: str | Path | None) -> list[str]:
    """Sorted basenames of every directory under `repos_root`.

    The set of repos a repo-less (`{no_repo_key}`) group's `propose-change` \
    verdict may name as `target_repo` -- mirrors `_resolve_repo_dir()`'s own \
    basename-under-`repos_root` resolution, so a name the evaluator is offered \
    here is guaranteed to resolve later at apply time. `repos_root` falsy or \
    not a directory returns `[]` -- there is nothing to offer.
    """
    if not repos_root:
        return []
    root = Path(repos_root)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def evaluate_group(
    repo: str,
    briefs: list[Path],
    *,
    agent: str = "claude",
    cwd: str | Path,
    repos_root: str | Path | None = None,
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
    "candidates_by_brief", "known_repos_by_brief"}]` -- rather than the raw
    string directly, so a caller fanning out across multiple groups can
    `.extend()` every call's result into one flat list, without a shape
    special-case for the archived short-circuit. `raw_text` is untouched
    worker output (or, in the archived case, the synthesized equivalent);
    parsing/validation is `parse_verdicts`'s job, not this function's.
    `candidates_by_brief` maps each brief id to the list of candidate change
    ids 2.1's `rank_change_candidates()` presented to the evaluator for it
    (`[]` for the archived short-circuit and for `{no_repo_key}` groups,
    which have no target repo to rank against) -- a caller passes this
    straight through to `parse_verdicts()` so a `fold-into-change` naming
    anything else is rejected. `known_repos_by_brief` maps each brief id to
    `_known_repos(repos_root)` -- the repos a `{no_repo_key}` group's
    `propose-change` may legally target -- when `repo` is `{no_repo_key}`,
    else `{}` (a repo-bearing group's briefs carry no such restriction).
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
                "known_repos_by_brief": {},
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
    known_repos = _known_repos(repos_root) if repo == NO_REPO_KEY else []
    known_repos_str = (
        ", ".join(known_repos)
        if known_repos
        else ("(none found)" if repo == NO_REPO_KEY else "(not applicable)")
    )
    prompt = EVALUATOR_PROMPT_TEMPLATE.format(
        repo=repo,
        briefs=brief_lines,
        no_repo_key=NO_REPO_KEY,
        memory_index=_memory_index_path(cwd),
        known_repos=known_repos_str,
    )
    result = spawnlib.spawn_agent(prompt, cwd, tier=DEFAULT_TIER, prefer=agent)
    candidates_by_brief = {
        path.stem: [c["id"] for c in candidates_by_path[path]] for path in briefs
    }
    known_repos_by_brief = (
        {bid: known_repos for bid in brief_ids} if repo == NO_REPO_KEY else {}
    )
    return [
        {
            "repo": repo,
            "brief_ids": brief_ids,
            "raw_text": result.text,
            "candidates_by_brief": candidates_by_brief,
            "known_repos_by_brief": known_repos_by_brief,
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
    known_repos: list[str] | None = None,
) -> bool:
    """True if `verdict_type`'s required target field(s) are present and valid in `obj`.

    Each verdict type that names a target validates that target here; every other
    verdict type (`keep`, `stale-close`, `needs-update`, `work-directly`) has no
    target field to check and is always valid. `presented_candidates` is the set of
    change ids actually offered to the evaluator for this brief (per 2.1's
    `rank_change_candidates()`) -- a `fold-into-change` naming anything else,
    including a plausible-looking id, is invalid, since accepting it would let the
    evaluator fold into a change it was never shown. `known_repos`, when not
    `None`, is the set of repos offered to a `{no_repo_key}` group's brief (per
    `evaluate_group()`'s `known_repos_by_brief`) -- a `propose-change` for such a
    brief is only valid when its `target_repo` is one of those; `None` (a
    repo-bearing brief, which carries no such restriction) leaves the existing
    well-formedness check as the sole gate.
    """
    if verdict_type == "duplicate-of":
        return duplicate_of is not None
    if verdict_type == "fold-into-change":
        target_change = obj.get("target_change")
        return isinstance(target_change, str) and target_change in presented_candidates
    if verdict_type == "propose-change":
        target_repo = obj.get("target_repo")
        proposed_change_name = obj.get("proposed_change_name")
        well_formed = (
            isinstance(target_repo, str)
            and target_repo.strip() != ""
            and isinstance(proposed_change_name, str)
            and bool(_KEBAB_CASE_RE.fullmatch(proposed_change_name))
        )
        if not well_formed:
            return False
        if known_repos is not None:
            return target_repo.strip() in known_repos
        return True
    if verdict_type == "needs-decision":
        question = obj.get("question")
        return isinstance(question, str) and question.strip() != ""
    return True


def parse_verdicts(
    raw_text: str,
    expected_brief_ids: list[str],
    candidates_by_brief: dict[str, list[str]] | None = None,
    known_repos_by_brief: dict[str, list[str]] | None = None,
) -> list[Verdict]:
    """Parse `evaluate_group()`'s raw evaluator text into one `Verdict` per expected brief.

    Implements the spec's "Evidence-required verdict per brief" requirement: a verdict
    must have `verdict` in `VALID_VERDICT_TYPES`, non-empty `evidence`, and (per
    `_has_valid_target()`) a well-formed target for verdict types that require one --
    `duplicate-of` needs `duplicate_of`, `fold-into-change` needs `target_change`
    naming one of `candidates_by_brief[brief_id]`'s presented candidates,
    `propose-change` needs `target_repo` and a kebab-case `proposed_change_name`
    (and, for a brief in `known_repos_by_brief`, a `target_repo` in that brief's
    list -- 2.5's rule letting a repo-less group's `propose-change` name a known
    repo), and `needs-decision` needs `question` -- to be accepted as-is.
    `work-directly` and `keep` have no target field. Anything missing, unparsable,
    or failing that check falls back to `keep` with the first JSON snippet the
    evaluator emitted under this brief_id retained as evidence -- `raw_text`
    itself is never used here when a group call batches multiple briefs, since it
    would otherwise bleed every other brief's own verdict/evidence into this
    one's fallback. Only when the evaluator emitted nothing identifiable for this
    brief_id at all does `raw_text` remain the fallback, since there is no
    narrower snippet to prefer -- every id in `expected_brief_ids` always appears
    exactly once in the result, in that order, never silently dropped.

    `candidates_by_brief` defaults to no candidates presented for any brief, so a
    caller that hasn't wired 2.1's ranking through yet still gets a safe,
    always-downgraded `fold-into-change` rather than an unchecked target.
    `known_repos_by_brief` defaults to no restriction for any brief (a
    repo-bearing brief is never in this map to begin with, per
    `evaluate_group()`), so a caller that hasn't wired this through yet gets the
    prior, unrestricted `propose-change` well-formedness check unchanged.

    `repo`/`held_by_wip_cap` (2.4's per-repo reporting fields) are left at their
    defaults here -- `apply_wip_cap_preview()` stamps them afterward, once per
    group, rather than this per-brief parser threading `repo` through itself.
    """
    candidates_by_brief = candidates_by_brief or {}
    known_repos_by_brief = known_repos_by_brief or {}
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
        known_repos = known_repos_by_brief.get(bid)
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
                verdict_type, obj, duplicate_of, presented_candidates, known_repos
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

    Passes `triaged=True` (plus `duplicate_of` for `duplicate-of`) so `done()`
    treats this as a triage closure: the Route-C planning-vs-implementation
    decision gate does not apply, and a consolidated batch closed as a
    duplicate is not asked for per-sub-item shipping evidence (its sub-items
    live on in the surviving brief). If `done()` still doesn't return "done"
    (ownership mismatch, an unbacked re-verification claim, ...), the brief
    must not stay stranded in
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
    done_res = done(
        v.brief_id, note=v.evidence, triaged=True, duplicate_of=v.duplicate_of
    )
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


def _apply_work_directly(v: Verdict, run_date: str) -> dict:
    """`work-directly`: stamp `seeded-from`/`recommended-route` in place, or downgrade to `keep`.

    Requires `v.evidence` to cite a specific test, check, or command
    (`_REPRODUCTION_EVIDENCE_RE`, per the evaluator prompt's "work-directly
    requires reproduction evidence" rule) -- evidence that only restates the
    brief's description without one is not proof this is directly actionable
    right now, so this downgrades to a no-op `keep` rather than stamping it.
    When the citation is present, the brief's frontmatter is stamped
    `seeded-from: triage:<run_date>:direct` and `recommended-route: F` and the
    brief is left in `queue/` -- unlike `stale-close`, `work-directly` never
    claims or closes it.
    """
    base = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "confirm": True,
        "note": v.evidence,
    }
    if not _REPRODUCTION_EVIDENCE_RE.search(v.evidence or ""):
        return {
            **base,
            "action": "noop",
            "status": "downgraded-to-keep",
            "path": None,
            "error": None,
            "note": (
                "evidence does not cite a test, check, or command -- downgraded to keep"
            ),
        }

    path = _resolve_brief_path(v.brief_id)
    if path is None:
        return {
            **base,
            "action": "stamp-frontmatter",
            "status": "error",
            "path": None,
            "error": "brief not found in queue/ or picked/",
        }
    try:
        _set_fm_fields(
            path,
            {
                "seeded-from": f"triage:{run_date}:direct",
                "recommended-route": "F",
            },
        )
    except (OSError, ValueError) as exc:
        return {
            **base,
            "action": "stamp-frontmatter",
            "status": "error",
            "path": str(path),
            "error": str(exc),
        }
    return {
        **base,
        "action": "stamp-frontmatter",
        "status": "executed",
        "path": str(path),
        "error": None,
    }


# Per design D8: filed via `decisions.ask()`, which builds the versioned
# envelope with `decisions.pending_decision_envelope()` under the hood --
# `_apply_needs_decision()` supplies the mandatory `why`/`context`/`options`
# fields `ask()` itself requires (an evaluator verdict carries a question and
# evidence, not a full structured decision record) generically, since every
# `needs-decision` verdict shares the same underlying situation: an automated
# run could not resolve the brief on its own.
_NEEDS_DECISION_WHY = (
    "Queue-triage evaluation could not resolve this brief without a human "
    "product decision; see the cited evidence."
)
_NEEDS_DECISION_OPTIONS = [
    "Proceed with the brief as currently scoped",
    "Revise or close the brief per the evaluator's findings",
]


def _apply_needs_decision(v: Verdict) -> dict:
    """`needs-decision`: file a pending decision, leaving the brief queued.

    Builds a deterministic `decisions.decision_identity()` (source
    `"queue-triage"`, subject=`v.brief_id`, question=`v.question`) so a later
    run re-evaluating the same still-open question converges on the existing
    record instead of filing a duplicate, then files it via `decisions.ask()`
    -- which builds the versioned envelope with `pending_decision_envelope()`
    and stamps the brief `awaiting-decision: <id>` in place. `release_brief`
    is always False: unlike `_apply_close()`, this action never claims the
    brief in the first place, so there is nothing to release -- it simply
    stays in `queue/`, now excluded from later `evaluate` runs by
    `has_unresolved_decision()` until a human answers.

    `ask()`'s own result fields are checked, not just `error`: a failed
    brief stamp (`brief_stamped` false) is reported as `status="error"`
    rather than `"executed"`, matching `_apply_work_directly()`'s fail-closed
    behaviour -- otherwise the skip clause above silently would not hold for
    that brief while the log claims it does. A re-file against an already
    resolved decision (`decision_record_status == "already-resolved"`)
    creates nothing and re-stamps the brief with the resolved id, so it is
    reported as `status="already-resolved"`, not `"executed"`.
    """
    base = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "action": "file-decision",
        "confirm": True,
        "note": v.evidence,
    }
    question = (v.question or "").strip()
    if not question:
        return {
            **base,
            "status": "error",
            "path": None,
            "error": "verdict has no question to file a decision for",
        }

    repo = v.repo if v.repo and v.repo != NO_REPO_KEY else None
    decision_id = decisions.decision_identity(
        source="queue-triage",
        repo=repo or NO_REPO_KEY,
        subject=v.brief_id,
        question=question,
    )
    try:
        result = decisions.ask(
            question,
            background=v.evidence,
            why=_NEEDS_DECISION_WHY,
            context=v.evidence,
            options=list(_NEEDS_DECISION_OPTIONS),
            repo=repo,
            brief=v.brief_id,
            release_brief=False,
            decision_id=decision_id,
            source="queue-triage",
            subject=v.brief_id,
        )
    except ValueError as exc:
        return {**base, "status": "error", "path": None, "error": str(exc)}

    if result.get("error"):
        return {
            **base,
            "status": "error",
            "path": result.get("path"),
            "error": result["error"],
        }
    if not result.get("brief_stamped"):
        return {
            **base,
            "status": "error",
            "path": result.get("path"),
            "error": (
                f"decision record {result.get('id')!r} was created/found at "
                f"{result.get('path')}, but the brief could not be stamped "
                f"with awaiting-decision: (not found under queue/ or "
                f"picked/, or unwritable) -- the skip clause in later "
                f"evaluate runs will not hold for this brief"
            ),
            "decision_id": result.get("id"),
            "decision_record_status": result.get("status"),
        }
    if result.get("status") == "already-resolved":
        return {
            **base,
            "status": "already-resolved",
            "path": result.get("path"),
            "error": None,
            "decision_id": result.get("id"),
            "decision_record_status": result.get("status"),
        }
    return {
        **base,
        "status": "executed",
        "path": result.get("path"),
        "error": None,
        "decision_id": result.get("id"),
        "decision_record_status": result.get("status"),
    }


def _planned_fold_propose_target(v: Verdict) -> str | None:
    """The change id a fold/propose apply would target -- `target_change`
    for `fold-into-change`, `proposed_change_name` for `propose-change`."""
    return (
        v.target_change if v.verdict == "fold-into-change" else v.proposed_change_name
    )


def _planned_fold_propose_branch(v: Verdict) -> str:
    """The branch name a fold/propose apply (3.1/3.2) would create.

    Deterministic from the verdict's own fields alone -- no worktree or git
    call is made to produce this, so it is safe to compute for a preview
    without needing to run the fold/propose apply action itself.
    """
    if v.verdict == "fold-into-change":
        return f"queue-triage/fold-{v.brief_id}-into-{v.target_change}"
    return f"queue-triage/propose-{v.proposed_change_name}"


def _planned_fold_propose_pr_title(v: Verdict) -> str:
    """The pull request title a fold/propose apply (3.1/3.2) would open with."""
    if v.verdict == "fold-into-change":
        return f"Fold {v.brief_id} into {v.target_change}"
    return f"Propose change: {v.proposed_change_name}"


def _fold_propose_worktree_dir(repo: Path, branch: str) -> Path:
    """Sibling-directory worktree path for `branch`, mirroring
    `orchestrator.worktree.default_worktree_base()`'s `<repo>-worktrees/`
    convention -- kept as a plain slug-of-branch subdirectory here since a
    fold/propose apply has no per-task/spec naming to reuse.
    """
    return repo.parent / f"{repo.name}-worktrees" / branch.replace("/", "-")


def _repo_base_branch(repo: Path) -> str:
    """`repo`'s base branch: policy's `base_branch` first, else `origin/HEAD`'s
    target, else `main`. Mirrors `router.sweep_stale_worktrees.default_base_branch()`
    (not imported: this module already reads policy directly for
    `_repo_wip_cap()`, and a fold/propose apply's branch point should honor
    an explicit `base_branch` override the same way other repo-scoped git
    operations in this codebase do).
    """
    from ..router import policy as policy_mod

    configured = policy_mod.load_policy(repo).get("base_branch")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "symbolic-ref",
                "--short",
                "refs/remotes/origin/HEAD",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "main"
    if result.returncode == 0:
        branch = result.stdout.strip()
        if branch.startswith("origin/"):
            return branch[len("origin/") :]
    return "main"


_TASK_GROUP_HEADING_RE = re.compile(r"^##\s+(\d+)\.", re.MULTILINE)


def _next_task_group_number(tasks_text: str) -> int:
    """One past the highest `## N.` group heading in `tasks_text`, or `1` if none."""
    numbers = [int(n) for n in _TASK_GROUP_HEADING_RE.findall(tasks_text)]
    return max(numbers) + 1 if numbers else 1


_GITHUB_SLUG_RE = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")
# `worktrail-compile` on an OpenSpec change may run one model inference pass
# (no static per-task file scope in tasks.md); bound it well above the
# evaluator's own spawn so a slow pass fails loud instead of hanging the drain.
_COMPILE_TIMEOUT_S = 900


def _github_slug(url: str) -> str | None:
    m = _GITHUB_SLUG_RE.search(url.strip())
    return m.group(1) if m else None


def _push_target(repo_path: Path) -> tuple[str, str | None]:
    """Remote to push a triage branch to, plus its GitHub `owner/repo` slug for
    `gh pr create -R`.

    Honours `git config remote.pushDefault` -- the standard git knob for "I
    push to my fork, not upstream" -- and falls back to `origin` with no slug
    (so `gh` infers the base repo the way it always did). Live 2026-09-02: the
    first unattended propose-change against `aspens`, whose `origin` is the
    upstream `aspenkit/aspens`, pushed there and was denied; the fork remote
    was never consulted.
    """
    cfg = subprocess.run(
        ["git", "-C", str(repo_path), "config", "--get", "remote.pushDefault"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    remote = (cfg.stdout or "").strip() if cfg.returncode == 0 else ""
    if not remote:
        return "origin", None
    url = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", remote],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    slug = _github_slug(url.stdout or "") if url.returncode == 0 else None
    return remote, slug


def _worktree_pr_close(
    v: Verdict,
    *,
    repo_path: Path,
    branch: str,
    base_branch: str,
    worktree_dir: Path,
    prepare: Callable[[Path], str | None],
    validate_target: str,
    commit_message: str,
    pr_body: str,
) -> dict:
    """Shared worktree + validate + commit/push/PR + claim/close sequence.

    Per the spec's "Fold and propose are applied as a pull request,
    fail-closed" requirement: creates a fresh worktree on `branch` off
    `base_branch`, calls `prepare(worktree_dir)` to let the caller author the
    change in place (returning an error string on failure, `None` on
    success), runs `openspec validate <validate_target> --strict`, runs
    `worktrail-compile` on the change so its `.compile-ok` marker matches the
    edited `tasks.md` (CI's Scope check, `check_compile_markers.py`, refuses a
    change PR without one -- live 2026-09-02, worktrail #897/#898 both failed
    it), commits, pushes to `_push_target()`'s remote, and opens the pull
    request via `gh pr create` against that remote's repo. Only once
    `gh pr create` reports a URL does this claim and close the brief (via
    `claim()`/`done(..., triaged_to=pr_url)`, with rollback on `done()`
    failure) -- any failure before that point returns `status="error"` with
    the brief left completely untouched and the `branch` name it would have
    used, so a caller can diagnose or retry without the queue and the target
    repo disagreeing about what happened. The local worktree (and, on
    failure, the local branch) are always cleaned up in a `finally`,
    regardless of outcome -- a pushed branch survives (git has no local undo
    for a push), but nothing local should outlive this call.
    """
    result = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "action": "open-pull-request",
        "confirm": True,
        "note": v.evidence,
    }

    add = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree_dir),
            base_branch,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if add.returncode != 0:
        return {
            **result,
            "status": "error",
            "path": None,
            "error": f"git worktree add failed: {(add.stderr or add.stdout).strip()}",
            "branch": branch,
        }

    pr_url = None
    try:
        prepare_error = prepare(worktree_dir)
        if prepare_error:
            return {
                **result,
                "status": "error",
                "path": None,
                "error": prepare_error,
                "branch": branch,
            }

        validate = subprocess.run(
            ["openspec", "validate", validate_target, "--strict"],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(worktree_dir),
            timeout=120,
        )
        if validate.returncode != 0:
            return {
                **result,
                "status": "error",
                "path": None,
                "error": (
                    "openspec validate failed: "
                    f"{(validate.stderr or validate.stdout).strip()}"
                ),
                "branch": branch,
            }

        compiled = subprocess.run(
            [
                "worktrail-compile",
                str(worktree_dir / "openspec" / "changes" / validate_target),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(worktree_dir),
            timeout=_COMPILE_TIMEOUT_S,
        )
        if compiled.returncode != 0:
            return {
                **result,
                "status": "error",
                "path": None,
                "error": (
                    "worktrail-compile failed: "
                    f"{(compiled.stderr or compiled.stdout).strip()}"
                ),
                "branch": branch,
            }

        for git_args in (["add", "-A"], ["commit", "-m", commit_message]):
            r = subprocess.run(
                ["git", *git_args],
                check=False,
                capture_output=True,
                text=True,
                cwd=str(worktree_dir),
                timeout=60,
            )
            if r.returncode != 0:
                return {
                    **result,
                    "status": "error",
                    "path": None,
                    "error": (
                        f"git {' '.join(git_args)} failed: "
                        f"{(r.stderr or r.stdout).strip()}"
                    ),
                    "branch": branch,
                }

        push_remote, base_slug = _push_target(repo_path)
        push = subprocess.run(
            ["git", "push", "-u", push_remote, branch],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(worktree_dir),
            timeout=120,
        )
        if push.returncode != 0:
            return {
                **result,
                "status": "error",
                "path": None,
                "error": f"git push failed: {(push.stderr or push.stdout).strip()}",
                "branch": branch,
            }

        labels = _refresh_pr_labels(worktree_dir, ["go:risk-low"], base_branch) or [
            "go:risk-low"
        ]
        pr_cmd = ["gh", "pr", "create"]
        if base_slug:
            pr_cmd += ["-R", base_slug]
        pr_cmd += ["--base", base_branch, "--head", branch]
        for label in labels:
            pr_cmd += ["--label", label]
        pr_cmd += [
            "--title",
            _planned_fold_propose_pr_title(v),
            "--body",
            pr_body,
        ]
        pr = subprocess.run(
            pr_cmd,
            check=False,
            capture_output=True,
            text=True,
            cwd=str(worktree_dir),
            timeout=60,
        )
        pr_output = (pr.stdout or pr.stderr).strip()
        pr_url = pr_output.splitlines()[-1] if pr_output else ""
        if pr.returncode != 0 or not pr_url.startswith("http"):
            pr_url = ""
            return {
                **result,
                "status": "error",
                "path": None,
                "error": f"gh pr create failed: {pr_output or '(no output)'}",
                "branch": branch,
            }
    finally:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "worktree",
                "remove",
                "--force",
                str(worktree_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if not pr_url:
            subprocess.run(
                ["git", "-C", str(repo_path), "branch", "-D", branch],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )

    # A PR now exists -- from here on, closing the brief is safe to attempt.
    # Any failure past this point is reported against the now-real branch/PR,
    # never against a not-yet-opened one.
    claim_res = claim(v.brief_id, by="queue-triage")
    if claim_res["status"] != "claimed":
        detail = claim_res.get("error")
        return {
            **result,
            "status": "error",
            "path": claim_res.get("path"),
            "error": f"claim: {claim_res['status']}"
            + (f" ({detail})" if detail else ""),
            "branch": branch,
            "pr_url": pr_url,
        }
    done_res = done(v.brief_id, note=v.evidence, triaged_to=pr_url)
    if done_res["status"] != "done":
        detail = done_res.get("error")
        release_res = release(v.brief_id)
        return {
            **result,
            "status": "error",
            "path": done_res.get("path"),
            "error": f"done: {done_res['status']}" + (f" ({detail})" if detail else ""),
            "rolled_back": release_res["status"] == "released",
            "branch": branch,
            "pr_url": pr_url,
        }
    return {
        **result,
        "status": "executed",
        "path": done_res.get("path"),
        "error": None,
        "branch": branch,
        "pr_url": pr_url,
    }


def _apply_fold_into_change(
    v: Verdict, *, repos_root: str | Path | None = None
) -> dict:
    """`fold-into-change`: worktree + proposal/tasks edit + validate + PR + close.

    Appends a `## Folded from <brief-id>` section to the target change's
    `proposal.md` and a new unchecked `## N. Folded from <brief-id>` task
    group to its `tasks.md` (`_next_task_group_number()`), then hands off to
    `_worktree_pr_close()` for the shared validate/commit/push/PR/close
    sequence.
    """
    result = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "action": "open-pull-request",
        "confirm": True,
        "note": v.evidence,
    }
    repo = v.repo if v.repo and v.repo != NO_REPO_KEY else None
    if not repo or not v.target_change:
        return {
            **result,
            "status": "error",
            "path": None,
            "error": "verdict is missing repo or target_change to fold into",
        }

    repo_path = _resolve_repo_dir(repo, repos_root)
    if repo_path is None:
        return {
            **result,
            "status": "error",
            "path": None,
            "error": f"could not resolve repo '{repo}' to a checkout on disk",
        }
    branch = _planned_fold_propose_branch(v)
    base_branch = _repo_base_branch(repo_path)
    worktree_dir = _fold_propose_worktree_dir(repo_path, branch)

    def prepare(worktree_dir: Path) -> str | None:
        change_dir = worktree_dir / "openspec" / "changes" / v.target_change
        proposal_path = change_dir / "proposal.md"
        tasks_path = change_dir / "tasks.md"
        if not proposal_path.is_file() or not tasks_path.is_file():
            return (
                f"target change '{v.target_change}' has no proposal.md/"
                f"tasks.md under {change_dir}"
            )

        try:
            proposal_text = proposal_path.read_text(encoding="utf-8")
            proposal_path.write_text(
                proposal_text.rstrip("\n")
                + f"\n\n## Folded from {v.brief_id}\n\n{v.evidence}\n",
                encoding="utf-8",
            )
            tasks_text = tasks_path.read_text(encoding="utf-8")
            group_number = _next_task_group_number(tasks_text)
            tasks_path.write_text(
                tasks_text.rstrip("\n")
                + f"\n\n## {group_number}. Folded from {v.brief_id}\n\n"
                + f"- [ ] {group_number}.1 {v.evidence}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return f"failed to edit proposal.md/tasks.md: {exc}"
        return None

    return _worktree_pr_close(
        v,
        repo_path=repo_path,
        branch=branch,
        base_branch=base_branch,
        worktree_dir=worktree_dir,
        prepare=prepare,
        validate_target=v.target_change,
        commit_message=f"Fold {v.brief_id} into {v.target_change}",
        pr_body=f"{v.evidence}\n\nFolded via queue-triage from brief `{v.brief_id}`.",
    )


def _apply_propose_change(
    v: Verdict, *, agent: str = "claude", repos_root: str | Path | None = None
) -> dict:
    """`propose-change`: worktree + `openspec new change` + agent-authored change + validate + PR + close.

    Runs `openspec new change <proposed_change_name>` to scaffold the change
    directory, then one `spawnlib.spawn_agent()` call (per
    `PROPOSE_CHANGE_PROMPT_TEMPLATE`) to author `proposal.md`/`design.md`/
    `specs/`/`tasks.md` in place, before handing off to
    `_worktree_pr_close()` for the shared validate/commit/push/PR/close
    sequence (mirroring `_apply_fold_into_change()`, per the spec's "Fold
    and propose are applied as a pull request, fail-closed" requirement,
    which covers both verdict types).

    `repo` is `_effective_repo(v)` -- the group's own `repo` when it is not
    `NO_REPO_KEY`, else `v.target_repo` (2.5). When the group repo *was*
    `NO_REPO_KEY`, the queued brief itself carries no `repo:` yet, so once
    `_resolve_repo_dir()` confirms `repo` resolves to a real checkout (and
    before any worktree op), this stamps `repo: <repo>` onto the brief's
    frontmatter -- the stamp is reported back in the action-log entry, and
    survives even if the worktree/PR sequence that follows fails, since it
    happens first.
    """
    result = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "action": "open-pull-request",
        "confirm": True,
        "note": v.evidence,
    }
    repo = _effective_repo(v)
    if not repo or not v.proposed_change_name:
        return {
            **result,
            "status": "error",
            "path": None,
            "error": "verdict is missing repo or proposed_change_name to propose",
        }

    repo_path = _resolve_repo_dir(repo, repos_root)
    if repo_path is None:
        return {
            **result,
            "status": "error",
            "path": None,
            "error": f"could not resolve repo '{repo}' to a checkout on disk",
        }

    stamped: dict[str, str] | None = None
    if v.repo == NO_REPO_KEY:
        brief_path = _resolve_brief_path(v.brief_id)
        if brief_path is None:
            return {
                **result,
                "status": "error",
                "path": None,
                "error": "brief not found in queue/ or picked/ to stamp repo",
            }
        try:
            _set_fm_fields(brief_path, {"repo": repo})
        except (OSError, ValueError) as exc:
            return {
                **result,
                "status": "error",
                "path": str(brief_path),
                "error": str(exc),
            }
        stamped = {"repo": repo}

    branch = _planned_fold_propose_branch(v)
    base_branch = _repo_base_branch(repo_path)
    worktree_dir = _fold_propose_worktree_dir(repo_path, branch)

    def prepare(worktree_dir: Path) -> str | None:
        new_change = subprocess.run(
            ["openspec", "new", "change", v.proposed_change_name],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(worktree_dir),
            timeout=60,
        )
        if new_change.returncode != 0:
            return (
                "openspec new change failed: "
                f"{(new_change.stderr or new_change.stdout).strip()}"
            )

        from ..orchestrator import spawnlib

        prompt = PROPOSE_CHANGE_PROMPT_TEMPLATE.format(
            repo=repo,
            proposed_change_name=v.proposed_change_name,
            brief_id=v.brief_id,
            evidence=v.evidence,
        )
        spawnlib.spawn_agent(prompt, worktree_dir, tier=DEFAULT_TIER, prefer=agent)

        change_dir = worktree_dir / "openspec" / "changes" / v.proposed_change_name
        proposal_path = change_dir / "proposal.md"
        tasks_path = change_dir / "tasks.md"
        if not proposal_path.is_file() or not tasks_path.is_file():
            return (
                "evaluator agent did not produce proposal.md/tasks.md "
                f"under {change_dir}"
            )
        return None

    entry = _worktree_pr_close(
        v,
        repo_path=repo_path,
        branch=branch,
        base_branch=base_branch,
        worktree_dir=worktree_dir,
        prepare=prepare,
        validate_target=v.proposed_change_name,
        commit_message=f"Propose change: {v.proposed_change_name}",
        pr_body=f"{v.evidence}\n\nProposed via queue-triage from brief `{v.brief_id}`.",
    )
    if stamped is not None:
        entry = {**entry, "stamped": stamped}
    return entry


def _preview_verdict(
    v: Verdict, run_date: str, repos_root: str | Path | None = None
) -> dict:
    """The no-`--confirm` preview of one non-`keep` verdict's apply action.

    Never claims, closes, stamps, or files anything -- every field here is
    derived purely from `v` (plus `run_date` for the two frontmatter-
    stamping verdict types, mirroring `_apply_work_directly`'s own stamp) so
    a caller can preview a run's effects before committing to it, per spec's
    "Apply step never closes a brief without an approved verdict"
    requirement. `fold-into-change`/`propose-change` preview their planned
    branch, target change, and PR title -- those fields are fully determined
    by the verdict alone, without running either apply action. A repo-less
    (`NO_REPO_KEY`) group's `propose-change` additionally previews the
    `repo:` stamp `_apply_propose_change()` would make, as
    `planned_stamp: {"repo": ...}`. `work-directly`
    previews its planned
    frontmatter stamp (or the same downgrade-to-keep `_apply_work_directly`
    would make); `needs-decision` previews the `awaiting-decision` stamp and
    the full `pending_decision_envelope()` `decisions.ask()` would file.
    """
    base = {
        "brief_id": v.brief_id,
        "verdict": v.verdict,
        "duplicate_of": v.duplicate_of,
        "confirm": False,
        "path": None,
        "error": None,
    }

    if v.verdict in ("fold-into-change", "propose-change"):
        if v.verdict == "propose-change" and _propose_change_over_cap(v, repos_root):
            return {
                **base,
                "action": "append-triage-note",
                "status": "planned-downgrade-to-keep",
                "note": _propose_change_wip_cap_note(v, repos_root),
            }
        entry = {
            **base,
            "action": "open-pull-request",
            "status": "planned",
            "note": v.evidence,
            "planned_branch": _planned_fold_propose_branch(v),
            "planned_target_change": _planned_fold_propose_target(v),
            "planned_pr_title": _planned_fold_propose_pr_title(v),
        }
        if v.verdict == "propose-change" and v.repo == NO_REPO_KEY:
            repo = _effective_repo(v)
            if repo:
                entry["planned_stamp"] = {"repo": repo}
        return entry

    if v.verdict == "work-directly":
        if not _REPRODUCTION_EVIDENCE_RE.search(v.evidence or ""):
            return {
                **base,
                "action": "noop",
                "status": "planned-downgrade-to-keep",
                "note": (
                    "evidence does not cite a test, check, or command -- "
                    "will be downgraded to keep"
                ),
            }
        return {
            **base,
            "action": "stamp-frontmatter",
            "status": "planned",
            "note": v.evidence,
            "planned_stamp": {
                "seeded-from": f"triage:{run_date}:direct",
                "recommended-route": "F",
            },
        }

    if v.verdict == "needs-decision":
        question = (v.question or "").strip()
        if not question:
            return {
                **base,
                "action": "file-decision",
                "status": "error",
                "note": v.evidence,
                "error": "verdict has no question to file a decision for",
            }
        repo = v.repo if v.repo and v.repo != NO_REPO_KEY else None
        decision_id = decisions.decision_identity(
            source="queue-triage",
            repo=repo or NO_REPO_KEY,
            subject=v.brief_id,
            question=question,
        )
        envelope = decisions.pending_decision_envelope(
            decision_id=decision_id,
            question=question,
            options=list(_NEEDS_DECISION_OPTIONS),
            source="queue-triage",
            repo=repo,
            subject=v.brief_id,
            brief=v.brief_id,
        )
        return {
            **base,
            "action": "file-decision",
            "status": "planned",
            "note": v.evidence,
            "planned_stamp": {"awaiting-decision": decision_id},
            "planned_envelope": envelope,
        }

    action = (
        "claim+done"
        if v.verdict in ("stale-close", "duplicate-of")
        else "append-triage-note"
    )
    return {**base, "action": action, "status": "planned", "note": v.evidence}


def apply_verdicts(
    verdicts: list[Verdict],
    *,
    confirm: bool,
    agent: str = "claude",
    repos_root: str | Path | None = None,
) -> list[dict]:
    """Execute (or, when `confirm` is false, only preview) each non-`keep` verdict.

    Callers are expected to have already run this batch through
    `resolve_duplicate_targets()` -- this function acts on whatever `verdict`
    each `Verdict` carries, it does not re-check dangling `duplicate-of`
    targets itself. Per spec: `stale-close` and `duplicate-of` close the
    brief via `claim()` + `done(..., note=evidence)`; `needs-update` appends
    an in-place `## Triage <run-date>` body section instead of closing it;
    `work-directly` stamps `seeded-from`/`recommended-route` frontmatter in
    place, or downgrades to a no-op `keep` when the evidence lacks a
    reproduction reference (`_apply_work_directly`); `needs-decision` files a
    pending decision via `decisions.ask()` and leaves the brief queued
    (`_apply_needs_decision`); `fold-into-change` creates a worktree, edits
    the target change's `proposal.md`/`tasks.md`, validates, and opens a pull
    request before closing the brief (`_apply_fold_into_change`);
    `propose-change` creates a worktree, runs `openspec new change`, spawns
    an agent to author the new change's `proposal.md`/`design.md`/`specs/`/
    `tasks.md`, validates, and opens a pull request before closing the brief
    (`_apply_propose_change`, using `agent` as the evaluator harness/model
    hint per `spawnlib.spawn_agent()`'s `prefer` parameter). `keep` is always
    a no-op.

    When `confirm` is false, nothing is executed and nothing is written to
    the queue or any repo -- every other non-`keep` verdict is instead
    logged via `_preview_verdict()`: `fold-into-change`/`propose-change`
    preview their planned branch, target change, and PR title;
    `work-directly`/`needs-decision` preview their planned frontmatter
    stamp (and, for `needs-decision`, the pending-decision envelope) --
    so a caller can preview a run's effects before committing to it.

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

        if not confirm:
            log.append(
                {
                    **_preview_verdict(v, run_date, repos_root=repos_root),
                    "confirm": confirm,
                }
            )
            continue

        if v.verdict in ("stale-close", "duplicate-of"):
            action = "claim+done"
        elif v.verdict == "work-directly":
            action = "stamp-frontmatter"
        elif v.verdict == "needs-decision":
            action = "file-decision"
        elif v.verdict in ("fold-into-change", "propose-change"):
            action = "open-pull-request"
        else:
            action = "append-triage-note"

        if action == "claim+done":
            log.append(_apply_close(v))
        elif action == "stamp-frontmatter":
            log.append(_apply_work_directly(v, run_date))
        elif action == "file-decision":
            log.append(_apply_needs_decision(v))
        elif action == "open-pull-request":
            if v.verdict == "fold-into-change":
                log.append(_apply_fold_into_change(v, repos_root=repos_root))
            elif _propose_change_over_cap(v, repos_root):
                log.append(
                    _apply_propose_change_wip_cap_downgrade(
                        v, run_date, repos_root=repos_root
                    )
                )
            else:
                log.append(_apply_propose_change(v, agent=agent, repos_root=repos_root))
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
    repos_root = args.repos_root or str(Path.home() / "projects")

    verdicts: list[Verdict] = []
    for repo, briefs in groups.items():
        cwd = repo if repo != NO_REPO_KEY else _worktrail_repo_root()
        for result in evaluate_group(
            repo, briefs, agent=args.agent, cwd=cwd, repos_root=repos_root
        ):
            group_verdicts = parse_verdicts(
                result["raw_text"],
                result["brief_ids"],
                candidates_by_brief=result["candidates_by_brief"],
                known_repos_by_brief=result["known_repos_by_brief"],
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
    `--agent` is only consulted for `propose-change`, whose apply action spawns an
    agent to author the new change (`_apply_propose_change`).
    """
    raw = Path(args.verdict_file).read_text(encoding="utf-8")
    verdicts = [Verdict(**entry) for entry in json.loads(raw)]

    repos_root = args.repos_root or str(Path.home() / "projects")

    resolved = resolve_duplicate_targets(verdicts)
    action_log = apply_verdicts(
        resolved, confirm=args.confirm, agent=args.agent, repos_root=repos_root
    )

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
            if entry.get("planned_branch"):
                print(f"    branch: {entry['planned_branch']}")
            if entry.get("planned_target_change"):
                print(f"    target change: {entry['planned_target_change']}")
            if entry.get("planned_pr_title"):
                print(f"    PR title: {entry['planned_pr_title']}")
            if entry.get("planned_stamp"):
                print(f"    stamp: {json.dumps(entry['planned_stamp'])}")
            if entry.get("planned_envelope"):
                print(f"    envelope: {json.dumps(entry['planned_envelope'])}")
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
    evaluate_parser.add_argument(
        "--repos-root",
        default=None,
        dest="repos_root",
        help="directory of sibling repo checkouts to list as known repos for a "
        "repo-less group's propose-change (default ~/projects)",
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
        "--agent",
        default="claude",
        choices=sorted(SUPPORTED_AGENTS),
        help="agent to spawn to author a propose-change verdict's new change (default claude)",
    )
    apply_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the action log as JSON on exit",
    )
    apply_parser.add_argument(
        "--repos-root",
        default=None,
        dest="repos_root",
        help="directory of sibling repo checkouts to resolve a bare `repo` "
        "name against (default ~/projects)",
    )

    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return cmd_evaluate(args)
    if args.command == "apply":
        return cmd_apply(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
