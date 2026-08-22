#!/usr/bin/env python3
"""
`/go`'s pre-orchestrator-dispatch stale-bookkeeping guard for OpenSpec
changes -- a sibling of `check_repo_freshness.py` / `check_spec_collision.py`
/ `check_brief_staleness.py` in this same pre-dispatch-guard family, run just
before `#precheck-gate` launches the orchestrator (see
`skills/worktrail-go/references/subagent-prompts.md#stale-spec-check`).

Incident shape (3 confirmed occurrences): PR #547 (2026-08-19), PR #548
(2026-08-20), and `stale-brief-precheck-recheck-search-boundary` (PR #610,
2026-08-21) each had an OpenSpec change whose `tasks.md` checkboxes said
pending work remained, but the described work had already landed on base --
every one was discovered only after a live orchestrator dispatch burned a
full run and found zero-diff on the flagged tasks. `dashboard.is_stale_spec()`
cannot catch this shape even where it is reachable: it flags a spec stale
only when *zero* tasks are checked, so a change with even one flipped
checkbox already -- exactly PR #610's shape -- reads as "in progress", not
stale. It is also checkbox-only by design, with no git-history signal at
all, so a change whose checkboxes are simply behind reality never gets
flagged regardless of age. And it is unreachable in practice for an OpenSpec
change to begin with: it only ever looks under `docs/specs/$SPEC_ID/`
(devkit format), while `#stale-spec-check`'s own `modify`-pipeline call site
(`pipeline-details.md#modify-pipeline` step 5) already passes it a
`$CHANGE_DIR` under `openspec/changes/$CHANGE_ID/` -- a path
`is_stale_spec()`'s `find_spec_file()` never finds, so today it silently
returns `False` on every real OpenSpec dispatch.

This module asks a narrower, different question than `is_stale_spec()`: not
"does this look abandoned by its checkbox ratio and age" but "did the work
described by this change's own still-pending tasks already land on base,
going by git history" -- the same question `check_brief_staleness.py` asks
of a queued brief, asked here of an OpenSpec change's pending tasks instead.

It reuses `check_brief_staleness.check()` unchanged for the actual
git-history probe search (extraction, `--since` search, race-grace
widening, `gh` pull-request lookup) -- this module's only job is building
the two inputs that function needs for an OpenSpec change instead of a
queue brief: the probe text (every still-pending task's title, plus
`proposal.md` if present, mirroring the brief's `focus + ## Suggested
approach` mix `check_brief_staleness.py` already scans) and the search
boundary (`since`), taken from the change's *own* first commit on base
rather than a queue brief's `created:` frontmatter -- an OpenSpec change
carries no such field of its own.

One gap `check_brief_staleness.check()` has no equivalent of: a probe drawn
from a task's own title is, by construction, text that already exists in
`tasks.md` -- the commit that authored that task line always matches its own
probe once `since` reaches back that far (a `-S` symbol search sees any
commit that changes a string's occurrence count, and authoring the line is
exactly that). `_change_own_commit_shas()` excludes every commit that ever
touched the change's own `openspec/changes/<id>/` directory from the
reported matches, so only commits *outside* the change directory -- genuine
candidate delivering commits -- surface as evidence.

Advisory only, exactly like its three siblings: never mutates `tasks.md`,
never archives, never blocks a dispatch on its own. Evidence surfaced here
is for the dispatching agent's own `#stale-spec-check` confirmation step to
weigh, not to act on unattended -- see that section's `AskUserQuestion` gate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import check_brief_staleness as _staleness
from ..taskformats.openspec.schema import STATUS_COMPLETED, parse_tasks_md


def _change_first_commit_date(repo: Path, base_ref: str, change_id: str) -> Optional[str]:
    """Return the ISO date of the earliest commit on `base_ref` that added
    `openspec/changes/<change_id>/`, or `None` if that path has no history
    there (the change hasn't merged to base yet, or this repo doesn't use
    that layout). This is the change's own creation-on-base anchor, standing
    in for a queue brief's `created:` frontmatter, which an OpenSpec change
    has no equivalent of.
    """
    change_path = f"openspec/changes/{change_id}"
    out = _staleness._run_git(
        repo,
        [
            "log", base_ref, "--diff-filter=A", "--format=%ad", "--date=short",
            "--reverse", "--", change_path,
        ],
        _staleness.SUBPROCESS_TIMEOUT_SECONDS,
    )
    if out is None or out.returncode != 0:
        return None
    lines = out.stdout.strip().splitlines()
    return lines[0] if lines else None


def _change_own_commit_shas(repo: Path, base_ref: str, change_id: str) -> set:
    """Every commit on `base_ref` that touched `openspec/changes/<change_id>/`
    itself (propose, refine, or any later edit to `tasks.md`/`proposal.md`).

    A pending task's title (and `proposal.md`'s prose) is exactly the probe
    text searched for -- so the commit that *wrote* that text into
    `tasks.md`/`proposal.md` always matches its own probes (a `-S` symbol
    search sees any commit that changes a string's occurrence count, and
    authoring the task line is exactly that). Those are the change
    describing its own pending work, not evidence the work landed elsewhere;
    `check_change()` excludes every sha this returns from the reported
    matches so only genuinely external delivering commits surface. Returns
    an empty set (never raises) if the lookup fails -- callers then exclude
    nothing, which only risks a false positive an operator can dismiss, not
    a false negative that hides real evidence.
    """
    out = _staleness._run_git(
        repo,
        ["log", base_ref, "--format=%h", "--", f"openspec/changes/{change_id}"],
        _staleness.SUBPROCESS_TIMEOUT_SECONDS,
    )
    if out is None or out.returncode != 0:
        return set()
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def check_change(
    repo: Path, change_id: str, *, base: Optional[str] = None,
) -> Dict[str, Any]:
    """Did the still-pending tasks in OpenSpec change `change_id` already
    land on base, going by git history?

    Returns `{"checked": bool, "change_dir": str|None, "since": str|None,
    "pending_task_ids": [...], "warning": str|None}` merged with
    `check_brief_staleness.check()`'s own `probes`/`matches`/
    `pull_requests`/`research_notes` fields once a search actually runs.

    `checked=False` means the question could not be asked at all: no
    `tasks.md`, no pending tasks (nothing left to check is a definite
    negative, not "unknown" -- see `pending_task_ids: []` in that case), or
    the change has no history on `base` to anchor `since` to (not yet
    merged). Never treat `checked=False` as "clean" -- same contract as
    `check_brief_staleness.check()`.
    """
    repo = Path(repo)
    result: Dict[str, Any] = {
        "checked": False,
        "change_dir": None,
        "since": None,
        "pending_task_ids": [],
        "probes": {"paths": [], "symbols": [], "pull_requests": [], "dropped": 0},
        "matches": [],
        "pull_requests": [],
        "research_notes": [],
        "warning": None,
    }

    change_dir = repo / "openspec" / "changes" / change_id
    tasks_md = change_dir / "tasks.md"
    if not tasks_md.is_file():
        result["warning"] = f"tasks.md not found: {tasks_md}"
        return result
    result["change_dir"] = str(change_dir)

    try:
        parsed = parse_tasks_md(tasks_md.read_text())
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"could not parse {tasks_md}: {exc!r}"
        return result

    pending = [t for t in parsed.tasks if t.status != STATUS_COMPLETED]
    result["pending_task_ids"] = [t.id for t in pending]
    if not pending:
        result["warning"] = "no pending tasks -- nothing to check"
        return result

    base_ref = _staleness.resolve_base_ref(repo, base)
    since = _change_first_commit_date(repo, base_ref, change_id)
    if since is None:
        result["warning"] = f"{change_dir} has no history on {base_ref}; cannot anchor search"
        return result
    result["since"] = since

    proposal_text = ""
    proposal_md = change_dir / "proposal.md"
    if proposal_md.is_file():
        try:
            proposal_text = proposal_md.read_text(errors="ignore")
        except OSError:
            proposal_text = ""

    probe_text = "\n".join(t.title for t in pending)
    if proposal_text:
        probe_text = f"{probe_text}\n{proposal_text}"

    sub = _staleness.check(repo, probe_text, since, base=base)
    own_shas = _change_own_commit_shas(repo, base_ref, change_id)
    if own_shas:
        sub["matches"] = [m for m in sub["matches"] if m["sha"] not in own_shas]
    result.update(sub)
    return result


# --- CLI ------------------------------------------------------------------------

def _format_human(res: Dict[str, object]) -> str:
    if not res["checked"]:
        return f"unknown: {res.get('warning') or 'staleness could not be determined'}"

    matches = res.get("matches") or []
    prs = res.get("pull_requests") or []
    notes = res.get("research_notes") or []
    pending = res.get("pending_task_ids") or []
    if not matches and not prs and not notes:
        line = (
            f"no evidence: {len(pending)} pending task(s) probed, "
            "nothing landed on base since the change's own first commit there"
        )
        if res.get("warning"):
            line += f"\n  warning: {res['warning']}"
        return line

    lines = [
        f"EVIDENCE: {len(matches)} commit(s), {len(prs)} merged pull request(s), "
        f"{len(notes)} research note(s) against {len(pending)} pending task(s)"
    ]
    for m in matches:
        lines.append(f"  {m['sha']}  {m['date']}  {m['subject']}   [{m['kind']} probe: {m['probe']}]")
    for pr in prs:
        lines.append(f"  PR #{pr['number']}  {pr.get('merged_at') or '?'}  {pr.get('title') or ''}")
    for n in notes:
        lines.append(f"  {n['path']}  {n.get('date') or '?'}   [{n['kind']} probe: {n['probe']}]")
    lines.append("  -> surface these to the operator; never flip checkboxes on this signal alone")
    if res.get("warning"):
        lines.append(f"  warning: {res['warning']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--change-id", required=True)
    p.add_argument("--base", default=None, help="base branch to search (default: auto-resolved)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    res = check_change(Path(args.repo), args.change_id, base=args.base)

    if args.json:
        print(json.dumps(res))
    else:
        print(_format_human(res))

    # Always 0: this is a signal source for a human/agent decision, not a
    # gate -- the same fail-open contract check_brief_staleness.py's main()
    # documents. A non-zero exit here would turn "could not determine" into
    # a dispatch failure, which is precisely what this module exists to avoid.
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
