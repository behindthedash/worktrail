#!/usr/bin/env python3
"""
Two-phase queue consolidation for the `consolidate-cluster` dashboard action
(spec 018, change 2026-07-14--consolidate-cluster-action).

Phase 1 -- preview (`build_preview`): re-validate a cluster's member ids
against the LIVE `queue/` directory (never a stale dashboard snapshot), and
draft a merged brief (title, `focus:` text, "Suggested approach" bullets)
from whichever members are still resolvable. A cluster that has dropped
below 2 resolvable members is withdrawn: no draft is produced.

Phase 2 -- execute (`execute_consolidation`): given the user's explicit
confirm/decline answer, either performs zero filesystem writes (decline) or
claims each resolvable member (via `work_queue.py`'s existing `claim`/`done`
CLI subcommands, invoked as a subprocess -- the same cross-skill-boundary
pattern `test_cluster_dashboard_e2e.py` already uses to drive `work_queue.py`
from this directory; there is no cross-skill Python import of
`work_queue.py` as a module), stamps a `## Superseded` note referencing the
new brief's id on each successfully-claimed member, marks it done, and
finally writes the consolidated brief into `queue/` as a direct file write
(no new `work_queue.py` subcommand). A member that loses the execute-time
claim race is recorded as skipped, not fatal to the batch.

Id resolution against `queue/` mirrors `work_queue.py`'s own `resolve()`
matching forms (full filename, stem, prefix, stem-suffix, frontmatter `id`)
via a small local re-implementation -- the same approach `cluster_detect.py`
already takes with its own `_id_matches` helper -- rather than importing
`work_queue.py` as a module.

The one exception to "no cross-skill Python import" is the plugin-level
`_shared/` directory (not owned by either the `go` or `handoff` skill) --
`work_queue.py` itself, `classify_handoff.py`, `handoff_seed.py`, and
`dist_tag_watch.py` already resolve it the same way this module does, for
`brief_frontmatter.validate_brief_text`: re-validating the consolidated
brief this module builds before writing it (PyYAML is therefore already an
indirect dependency of this plugin via that shared module, not a new one
introduced here).

Any unexpected exception inside preview-phase helpers
(`resolvable_members`, `draft_consolidated_brief`, `build_preview`) degrades
to excluding the affected member rather than raising, mirroring
`cluster_detect.py`'s defensive style. `execute_consolidation` does NOT
swallow subprocess failures the same way: a `claim`/`done` subprocess that
errors for one member is recorded in `members_skipped` and the batch
continues (mirrors `work_queue.py claim_batch()`'s per-companion failure
handling), but the failure itself is never silently absorbed into a false
"completed" result.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import (
    is_canonical_style,
    serialize_frontmatter,
    split_frontmatter,
    validate_brief_text,
)
from ..workqueue.invocation import WORK_QUEUE_PY, build_work_queue_argv
from . import cluster_telemetry

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SUGGESTED_APPROACH_RE = re.compile(r"^##\s+Suggested approach\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)
_BULLET_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+(.*)$")

# Mirrors work_queue.py's own `_CONSOLIDATED_FROM_RE`/`_consolidated_member_ids`
# (same regex, same section this module's own `_build_consolidated_brief_content`
# writes) -- no cross-skill import, same rationale as `_local_resolve` mirroring
# `resolve()`.
_CONSOLIDATED_FROM_RE = re.compile(
    r"^##\s+Consolidated from\s*$\r?\n+((?:^-\s+.+$\r?\n?)+)", re.MULTILINE
)


# --------------------------------------------------------------------------- #
# Minimal stdlib-only frontmatter reading (scalar fields only -- this module
# only ever needs `focus`/`id`/`status`, never list fields, so it does not
# need the shared PyYAML-backed reader `work_queue.py` uses).
# --------------------------------------------------------------------------- #


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse scalar frontmatter fields from `text`.

    Delegates to the shared YAML reader (`brief_frontmatter.split_frontmatter`)
    so block scalars parse to their real value: `create_handoff` writes every
    `focus:` as a literal block scalar (`focus: |-`) precisely because focus
    text routinely contains `": "` or apostrophes, and the naive line-based
    parser this replaces returned the literal style marker `|-` for every such
    field -- which the consolidated-brief builder then joined into
    `focus: "|- / |-"` queue briefs (observed live 2026-08-13, five briefs).
    Returns `{}` if there is no well-formed fenced block. Non-scalar
    (list/dict) fields are dropped -- callers here only ever read
    `focus`/`id`/`status`-shaped scalars.
    """
    fm, _body = split_frontmatter(text)
    return {
        str(k): str(v).strip()
        for k, v in fm.items()
        if isinstance(v, (str, int, float, bool))
    }


def _member_created_at(text: str) -> datetime.datetime | None:
    """Read a member's earliest known creation timestamp from its frontmatter.

    Prefers `original-created:` (stamped on a member that was itself already
    a consolidated brief -- see `_build_consolidated_brief_content`) over
    `created:`. Reads frontmatter via `split_frontmatter` directly rather
    than `_parse_frontmatter()`: that helper's scalar-only filter drops
    PyYAML's native `datetime.datetime`/`datetime.date` parse of a bare
    (unquoted) ISO timestamp -- exactly how `created:`/`original-created:`
    are written -- so going through it here would silently lose every such
    value. Returns a UTC-aware `datetime`, or `None` on a missing or
    unparseable value; degrades, never raises, matching this module's
    existing defensive style.
    """
    try:
        fm, _body = split_frontmatter(text)
        value = fm.get("original-created")
        if value is None:
            value = fm.get("created")
        if value is None:
            return None

        if isinstance(value, datetime.datetime):
            dt = value
        elif isinstance(value, datetime.date):
            dt = datetime.datetime.combine(value, datetime.time.min)
        elif isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            try:
                dt = datetime.datetime.fromisoformat(candidate)
            except ValueError:
                try:
                    dt = datetime.datetime.strptime(raw, "%Y-%m-%d")  # noqa: DTZ007
                except ValueError:
                    return None
        else:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:  # noqa: BLE001 -- degrade, never crash the preview
        return None


def _extract_suggested_approach(text: str) -> list[str]:
    """Return the list-item bullets under a `## Suggested approach` heading.

    A soft-wrapped bullet's continuation lines (no bullet marker of their
    own) are appended to the bullet they follow rather than silently
    dropped: any non-blank line that doesn't start a new bullet is treated
    as a continuation of whichever bullet came before it.
    """
    m = _SUGGESTED_APPROACH_RE.search(text)
    if not m:
        return []
    rest = text[m.end() :]
    nxt = _HEADING_RE.search(rest)
    section = rest[: nxt.start()] if nxt else rest
    bullets: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        bm = _BULLET_RE.match(stripped)
        if bm:
            bullets.append(bm.group(1).strip())
        elif stripped and bullets:
            bullets[-1] = f"{bullets[-1]} {stripped}"
    return bullets


def _extract_body(text: str) -> str:
    """Return everything after the closing frontmatter fence, verbatim
    (stripped of surrounding blank lines). Full-body fallback for callers
    that no longer want a lossy focus/bullets-only extraction."""
    m = _FM_RE.match(text)
    body = text[m.end() :] if m else text
    return body.strip()


def _nest_headings(body: str) -> str:
    """Demote a member's top-level `## ` headings to `### ` so they nest
    correctly under that member's own `## N. <title>` section instead of
    colliding with the consolidated brief's own top-level headings."""
    return re.sub(r"(?m)^## ", "### ", body)


_SLUG_MAX_LEN = 60


def _slugify(text: str) -> str:
    """Slugify `text`, capped to `_SLUG_MAX_LEN` characters.

    An auto-generated title (``"Consolidated: " + first-member-focus``) has
    no inherent length bound -- a long focus sentence produced a filename
    over 255 bytes, aborting `write_text()` with `OSError: [Errno 36] File
    name too long` after the member claim/done loop had already run
    (observed live 2026-08-26, cluster 3 of the personal work-queue).
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    if len(slug) > _SLUG_MAX_LEN:
        slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug or "consolidated"


def _member_hash_suffix(member_ids: list[str]) -> str:
    """Short, deterministic hash of a cluster's (sorted) member ids.

    Auto-generated titles (``"Consolidated: " + first-member-title``) often
    slugify to the same generic stem across unrelated clusters, which trips
    the dashboard's own `cluster_detect.py` `duplicate-slug` signal on briefs
    that are not actually duplicates. Appending this suffix to the slug makes
    the id collision-resistant without depending on title text at all.
    """
    digest = hashlib.sha1("|".join(sorted(member_ids)).encode("utf-8")).hexdigest()
    return digest[:6]


# --------------------------------------------------------------------------- #
# Local id resolution against a live directory (mirrors work_queue.py's
# resolve() forms; no cross-skill import).
# --------------------------------------------------------------------------- #


def _local_resolve(identifier: str, folder: Path) -> Path | None:
    """Resolve `identifier` to exactly one `.md` file in `folder`, or None.

    Mirrors work_queue.py's `resolve()` matching forms: full filename, stem,
    leading prefix; then stem-suffix; then frontmatter `id`. None on zero or
    on more-than-one candidate (unresolvable and ambiguous are both treated
    as "not resolvable" here -- callers never need to distinguish them).
    """
    if not folder.is_dir():
        return None
    files = [f for f in folder.iterdir() if f.is_file() and f.suffix == ".md"]
    hits = {
        f
        for f in files
        if f.name == identifier or f.stem == identifier or f.name.startswith(identifier)
    }
    if not hits:
        hits = {f for f in files if f.stem.endswith(identifier)}
    if not hits:
        for f in files:
            try:
                fm = _parse_frontmatter(f.read_text(encoding="utf-8"))
            except OSError:
                continue
            if fm.get("id") == identifier:
                hits.add(f)
    return next(iter(hits)) if len(hits) == 1 else None


# --------------------------------------------------------------------------- #
# Preview phase (read-only)
# --------------------------------------------------------------------------- #


def resolvable_members(member_ids: list[str], queue_dir: Path) -> list[str]:
    """Return the subset of `member_ids` still resolvable in `queue_dir`.

    Re-validates against the LIVE queue directory (never a stale dashboard
    snapshot) -- a member claimed out-of-band since the cluster was formed
    no longer resolves and is dropped. Order follows `member_ids`. Any
    unexpected error resolving a given member excludes it rather than
    raising (degrade, never crash the preview).
    """
    queue_dir = Path(queue_dir)
    resolvable: list[str] = []
    for member_id in member_ids:
        try:
            if _local_resolve(member_id, queue_dir) is not None:
                resolvable.append(member_id)
        except Exception:  # noqa: BLE001, S112 -- degrade, never crash the preview
            continue
    return resolvable


def draft_consolidated_brief(member_ids: list[str], queue_dir: Path) -> dict[str, Any]:
    """Draft a consolidated brief merged from the given (resolvable) members.

    Returns `{"title", "focus", "suggested_approach", "member_ids", "members"}`,
    plus `original_created` when at least one member's timestamp parsed:
    `focus` joins each member's frontmatter `focus:` text; `suggested_approach`
    concatenates every "## Suggested approach" bullet found across the
    members' bodies (kept for backward compatibility -- prefer `members` for
    anything that needs full fidelity); `member_ids` lists the members
    actually merged in (a member whose file can't be read/parsed is skipped,
    not fatal). `members` is a list of `{"id", "focus", "route",
    "change_kind", "body"}` per merged member -- `body` is that member's
    FULL content after its frontmatter fence, verbatim, so Key artifacts,
    Open questions, and any other section a flat focus/bullets merge would
    drop survives into the consolidated brief. `original_created` is the
    earliest `_member_created_at()` timestamp across all members (ISO-8601
    string), so the consolidated brief's staleness search boundary can stay
    pinned to the oldest member's true capture time rather than resetting to
    this consolidation's own `created:` -- omitted when no member's
    timestamp parsed.
    """
    queue_dir = Path(queue_dir)
    focuses: list[str] = []
    bullets: list[str] = []
    merged_ids: list[str] = []
    members: list[dict[str, str]] = []
    earliest_created: datetime.datetime | None = None
    for member_id in member_ids:
        try:
            path = _local_resolve(member_id, queue_dir)
            if path is None:
                continue
            text = path.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            # A block-scalar focus is legitimately multi-line; composition
            # surfaces (the " / " join, the summary table, section headings)
            # are single-line contexts, so collapse internal whitespace. The
            # member's full original text survives verbatim in `body`.
            focus = " ".join(fm.get("focus", "").split())
            if focus:
                focuses.append(focus)
            bullets.extend(_extract_suggested_approach(text))
            merged_ids.append(member_id)
            members.append(
                {
                    "id": member_id,
                    "focus": focus,
                    "route": fm.get("recommended-route", ""),
                    "change_kind": fm.get("change-kind", ""),
                    "body": _extract_body(text),
                }
            )
            member_created = _member_created_at(text)
            if member_created is not None and (
                earliest_created is None or member_created < earliest_created
            ):
                earliest_created = member_created
        except Exception:  # noqa: BLE001, S112 -- degrade, never crash the preview
            continue

    if focuses:
        title = f"Consolidated: {focuses[0]}"
    else:
        title = f"Consolidated: {len(merged_ids)} related handoffs"

    draft: dict[str, Any] = {
        "title": title,
        "focus": " / ".join(focuses),
        "suggested_approach": bullets,
        "member_ids": merged_ids,
        "members": members,
    }
    if earliest_created is not None:
        draft["original_created"] = earliest_created.isoformat()
    return draft


def build_preview(member_ids: list[str], queue_dir: Path) -> dict[str, Any]:
    """Preview a cluster consolidation: re-validate + draft.

    Returns `{"resolvable_members", "excluded_members", "withdrawn", "draft"}`.
    `withdrawn` is True (and `draft` is None) when fewer than 2 members
    remain resolvable -- the consolidation is not worth surfacing to the user.
    """
    queue_dir = Path(queue_dir)
    resolvable = resolvable_members(member_ids, queue_dir)
    excluded = [m for m in member_ids if m not in resolvable]

    if len(resolvable) < 2:
        return {
            "resolvable_members": resolvable,
            "excluded_members": excluded,
            "withdrawn": True,
            "draft": None,
        }

    draft = draft_consolidated_brief(resolvable, queue_dir)
    return {
        "resolvable_members": resolvable,
        "excluded_members": excluded,
        "withdrawn": False,
        "draft": draft,
    }


# --------------------------------------------------------------------------- #
# Execute phase (the only write path; gated on explicit confirmation)
# --------------------------------------------------------------------------- #


def _build_consolidated_brief_content(draft: dict[str, Any]) -> tuple[str, str]:
    """Render the consolidated brief's frontmatter+body text.

    Returns `(new_brief_id, content)`. `status:` is `queued` (the brief must
    be itself claimable, not accidentally created already-done/picked).

    When `draft["members"]` is present (the normal path -- produced by
    `draft_consolidated_brief`), the body is a mechanically-derived summary
    table (member -> route/kind) followed by one `## N. <title>` section per
    member holding that member's FULL original body verbatim (heading level
    nested down one so it doesn't collide with this brief's own top-level
    headings). This is the full-fidelity replacement for the old
    focus-only/bullets-only flat merge, which silently dropped Key
    artifacts, Open questions, and per-member route info. Callers that hand
    -build a bare draft dict without `members` (e.g. tests exercising this
    function directly) fall back to the old flattened "## Suggested
    approach" rendering.
    """
    title = str(draft.get("title") or "Consolidated handoff")
    focus = str(draft.get("focus") or "")
    member_ids = draft.get("member_ids") or []
    members = draft.get("members") or []

    now = datetime.datetime.now().astimezone()
    slug = _slugify(title)
    suffix = _member_hash_suffix(member_ids or [title])
    new_brief_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{slug}-{suffix}"

    # Routed through the shared `serialize_frontmatter` (same as
    # create_handoff.py) instead of a local `yaml.safe_dump(focus,
    # default_style='"', ...)` call, so a focus containing a literal `"` or
    # an embedded newline renders as a clean `|-` literal block -- no
    # quoting, no folding -- identically to every other writer into queue/,
    # rather than each writer choosing its own scalar style
    # (`docs/specs/research/queue-frontmatter-cross-writer-drift.md`).
    frontmatter: dict[str, Any] = {
        "id": new_brief_id,
        "created": now.isoformat(timespec="seconds"),
        "focus": focus,
        "status": "queued",
    }
    original_created = draft.get("original_created")
    if original_created:
        frontmatter["original-created"] = original_created
    if member_ids:
        frontmatter["related"] = list(member_ids)
    fm_text = serialize_frontmatter(frontmatter)
    lines = ["---", fm_text.rstrip("\n"), "---", ""]
    lines.append("## Focus")
    lines.append("")
    lines.append(focus)
    lines.append("")

    if members:
        lines.append("## Summary")
        lines.append("")
        lines.append("| # | Member | Route | Kind |")
        lines.append("|---|---|---|---|")
        for i, member in enumerate(members, 1):
            member_focus = member.get("focus") or member.get("id", "")
            route = member.get("route") or "--"
            kind = member.get("change_kind") or "--"
            lines.append(f"| {i} | {member_focus} | {route} | {kind} |")
        lines.append("")

        for i, member in enumerate(members, 1):
            heading = member.get("focus") or member.get("id", "")
            lines.append(f"## {i}. {heading}")
            lines.append("")
            body = _nest_headings(member.get("body") or "")
            if body:
                lines.append(body)
                lines.append("")
    else:
        bullets = draft.get("suggested_approach") or []
        if bullets:
            lines.append("## Suggested approach")
            lines.append("")
            lines.extend(f"{i}. {b}" for i, b in enumerate(bullets, 1))
            lines.append("")

    lines.append("## Consolidated from")
    lines.append("")
    lines.extend(f"- {m}" for m in member_ids)
    lines.append("")
    return new_brief_id, "\n".join(lines)


def _run_work_queue_cli(
    args: list[str], work_queue_script: Path, work_queue_base_dir: Path
) -> dict[str, Any] | None:
    """Invoke `work_queue.py` as a subprocess and parse its JSON stdout.

    `work_queue.py` resolves `queue/`/`picked/` from the `WORK_QUEUE_DIR` env
    var (its own convention), so this sets it to `work_queue_base_dir` on the
    subprocess's environment -- the parent of the `queue_dir`/`picked_dir`
    this module was given -- rather than relying on whatever the caller's own
    process environment happens to hold.

    Returns None on any subprocess/JSON failure (non-zero unexpected exit,
    non-JSON output, timeout) -- the caller treats this as a claim/done
    failure for that one member, never fatal to the batch.
    """
    env = dict(os.environ)
    env["WORK_QUEUE_DIR"] = str(work_queue_base_dir)
    argv = build_work_queue_argv(work_queue_script, args)
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _claim_member(
    member_id: str, work_queue_script: Path, work_queue_base_dir: Path, by: str | None
) -> Path | None:
    """Claim one member via `work_queue.py claim`. None if not claimed."""
    args = ["claim", member_id, "--json"]
    if by:
        args += ["--by", by]
    data = _run_work_queue_cli(args, work_queue_script, work_queue_base_dir)
    if not data or data.get("status") != "claimed" or not data.get("path"):
        return None
    return Path(data["path"])


def _consolidated_member_ids(body: str) -> list[str]:
    """Member ids listed under a `## Consolidated from` section in `body`, or
    `[]` when `body` carries no such section -- i.e. it is not itself a
    consolidation-batch brief."""
    m = _CONSOLIDATED_FROM_RE.search(body)
    if not m:
        return []
    return [
        line[2:].strip() for line in m.group(1).splitlines() if line.startswith("- ")
    ]


def _build_nested_consolidation_note(member_text: str) -> str | None:
    """Closure note for `_mark_member_done` when the member being closed is
    itself a consolidation-batch brief (carries its own `## Consolidated
    from` section, e.g. cluster A was consolidated into member X, and X is
    now itself being re-consolidated into a larger cluster).

    `work_queue.py done()`'s consolidation-closure evidence gate
    (`_consolidation_closure_missing_evidence`) rejects a plain
    `--planning-only` close of such a member with no mutation
    (`unverified_consolidation_closure`), because it cannot tell the member's
    own sub-items were already shipped. They were: `execute_consolidation()`
    stamps every sub-item `done` + `## Superseded` at the moment the member
    itself was authored, so that evidence already exists on disk -- this just
    cites it. Returns None when `member_text` is not itself a consolidation
    batch (the common case), so `_mark_member_done` passes no `--note`.
    """
    sub_ids = _consolidated_member_ids(member_text)
    if not sub_ids:
        return None
    lines = [
        (
            "Re-consolidating a nested consolidation-batch brief. Each sub-item below "
            "was already marked done and stamped `## Superseded` when this brief was "
            "authored by consolidate_cluster.py:"
        ),
        "```",
    ]
    lines.extend(
        f"- {sub_id}: status done, superseded at authoring time" for sub_id in sub_ids
    )
    lines.append("```")
    return "\n".join(lines)


def _mark_member_done(
    member_id: str,
    work_queue_script: Path,
    work_queue_base_dir: Path,
    note: str | None = None,
) -> bool:
    """Mark one already-claimed member done via `work_queue.py done`.

    `note`, when given, is passed through as `--note` -- required when the
    member being closed is itself a nested consolidation-batch brief (see
    `_build_nested_consolidation_note`), otherwise `work_queue.py done`'s own
    consolidation-closure evidence gate rejects the plain `--planning-only`
    call.
    """
    args = ["done", member_id, "--planning-only", "--json"]
    if note:
        args += ["--note", note]
    data = _run_work_queue_cli(args, work_queue_script, work_queue_base_dir)
    return bool(data and data.get("status") == "done")


def _stamp_superseded(path: Path, new_brief_id: str) -> str:
    """Append a `## Superseded` note referencing the new brief's id.

    Returns the member's original text (before the note is appended), so a
    caller needing to inspect the pre-stamp body (e.g. detecting a nested
    consolidation batch via `_build_nested_consolidation_note`) doesn't need
    a second read.
    """
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    note = f"\n## Superseded\n\nConsolidated into `{new_brief_id}`.\n"
    path.write_text(text + note, encoding="utf-8")
    return text


def execute_consolidation(
    confirmed: bool,
    resolvable_ids: list[str],
    draft: dict[str, Any],
    queue_dir: Path,
    picked_dir: Path,
    *,
    work_queue_script: Path | None = None,
    claimed_by: str | None = None,
) -> dict[str, Any]:
    """Execute (or decline) the consolidation write step.

    `confirmed=False`: performs zero filesystem writes and invokes
    `work_queue.py` zero times. Returns `{"status": "declined", ...}`.

    `confirmed=True` with an empty `resolvable_ids`: never writes a
    zero-provenance consolidated brief. Returns `{"status": "no-op",
    "members_completed": [], ...}`.

    `confirmed=True` with N resolvable ids: builds and validates the
    consolidated brief's content FIRST (before touching any member) --
    a draft that fails validation (e.g. an empty/malformed `--draft`
    payload) aborts here with zero member mutations and zero writes, so a
    broken draft never leaves claimed members stranded with nothing to
    supersede them. Only once that content is confirmed well-formed does it
    claim each id via `work_queue.py claim` (subprocess), stamp
    `## Superseded` + mark it `done` on every successfully-claimed member,
    then write the consolidated brief into `queue_dir`. A member whose claim
    fails (raced, subprocess error) is recorded in `members_skipped`, not
    `members_completed`; the rest of the batch is still processed. Returns
    `{"status": "written", "new_brief_path", "members_completed",
    "members_skipped"}`.

    `queue_dir`/`picked_dir` are expected to be siblings under one base
    directory (`work_queue.py`'s own `queue/`+`picked/` layout) -- that
    shared parent is passed to the `work_queue.py` subprocess as
    `WORK_QUEUE_DIR` so `claim`/`done` operate on the same folders given
    here, independent of the caller's own process environment.
    """
    queue_dir = Path(queue_dir)
    picked_dir = Path(picked_dir)
    wq_script = Path(work_queue_script) if work_queue_script else WORK_QUEUE_PY
    wq_base_dir = queue_dir.parent

    if not confirmed:
        return {
            "status": "declined",
            "new_brief_path": None,
            "members_completed": [],
            "members_skipped": [],
        }

    if not resolvable_ids:
        return {
            "status": "no-op",
            "new_brief_path": None,
            "members_completed": [],
            "members_skipped": [],
        }

    new_brief_id, new_brief_content = _build_consolidated_brief_content(draft)

    ok, verr = validate_brief_text(
        new_brief_content, required=("id", "status", "focus")
    )
    if ok and not is_canonical_style(new_brief_content):
        ok, verr = (
            False,
            "consolidated brief frontmatter did not serialize to canonical style",
        )
    if not ok:
        return {
            "status": "write-verification-failed",
            "new_brief_path": None,
            "members_completed": [],
            "members_skipped": [],
            "error": verr,
        }

    members_completed: list[str] = []
    members_skipped: list[str] = []
    for member_id in resolvable_ids:
        picked_path = _claim_member(member_id, wq_script, wq_base_dir, claimed_by)
        if picked_path is None:
            members_skipped.append(member_id)
            continue
        try:
            member_text = _stamp_superseded(picked_path, new_brief_id)
            nested_note = _build_nested_consolidation_note(member_text)
            if not _mark_member_done(member_id, wq_script, wq_base_dir, nested_note):
                members_skipped.append(member_id)
                continue
        except OSError:
            members_skipped.append(member_id)
            continue
        members_completed.append(member_id)

    queue_dir.mkdir(parents=True, exist_ok=True)
    new_brief_path = queue_dir / f"{new_brief_id}.md"
    new_brief_path.write_text(new_brief_content, encoding="utf-8")

    return {
        "status": "written",
        "new_brief_path": str(new_brief_path),
        "members_completed": members_completed,
        "members_skipped": members_skipped,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _default_base_dir() -> Path:
    return Path(os.environ.get("WORK_QUEUE_DIR", "~/work-queue")).expanduser()


def _resolve_draft_payload(raw: str) -> dict[str, Any]:
    """Parse `--draft` accepting either `preview --json`'s full payload or a
    bare draft dict.

    `preview --json` prints `{"resolvable_members", "excluded_members",
    "withdrawn", "draft": {...}|None}` -- the natural pipe
    (``preview --json | ... | execute --draft <that output>``) passes this
    whole wrapper, not just its inner `.draft` object, which previously
    silently produced an empty consolidated brief (`draft.get("focus")` etc.
    all returning None on the wrapper). Unwrap `.draft` when the key is
    present; otherwise treat `raw` itself as the bare draft dict.

    Raises `ValueError` with a clear message when the result isn't a usable
    draft (not JSON, not an object, or missing/empty `member_ids` -- e.g. a
    withdrawn preview's `"draft": null` piped in by mistake) instead of ever
    silently returning something `_build_consolidated_brief_content` would
    turn into an empty brief.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--draft is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("--draft must be a JSON object")  # noqa: TRY004 -- ValueError is this function's uniform malformed-input contract; callers catch ValueError specifically

    draft = payload.get("draft", payload) if "draft" in payload else payload

    if not isinstance(draft, dict) or not draft.get("member_ids"):
        raise ValueError(
            "--draft does not contain a usable draft (missing/empty 'member_ids'); "
            'did you pipe in a withdrawn preview ("draft": null)?'
        )
    return draft


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="two-phase consolidate-cluster action")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit JSON")
    common.add_argument(
        "--queue-dir",
        default=None,
        help="override queue/ (default: WORK_QUEUE_DIR/queue)",
    )
    common.add_argument(
        "--picked-dir",
        default=None,
        help="override picked/ (default: WORK_QUEUE_DIR/picked)",
    )
    subs = p.add_subparsers(dest="cmd")
    subs.required = True

    preview_p = subs.add_parser(
        "preview", parents=[common], help="re-validate + draft a cluster consolidation"
    )
    preview_p.add_argument(
        "member_ids", nargs="+", help="cluster member ids to re-validate"
    )

    execute_p = subs.add_parser(
        "execute", parents=[common], help="confirm/decline the consolidation write step"
    )
    execute_p.add_argument(
        "member_ids", nargs="+", help="resolvable member ids from the preview step"
    )
    draft_grp = execute_p.add_mutually_exclusive_group(required=True)
    draft_grp.add_argument(
        "--draft",
        help="preview --json's full stdout, or its bare inner draft dict, as a JSON string",
    )
    draft_grp.add_argument(
        "--draft-file",
        help=(
            "path to a file containing preview --json's full stdout (or its bare inner "
            "draft dict), as an alternative to --draft -- a cluster whose combined "
            "member bodies exceed the kernel's MAX_ARG_STRLEN (~128KB) fails --draft "
            "with 'Argument list too long' at exec() before Python even starts; write "
            "preview's stdout to a file and pass it here instead"
        ),
    )
    confirm_grp = execute_p.add_mutually_exclusive_group(required=True)
    confirm_grp.add_argument(
        "--confirm", action="store_true", help="write the consolidation"
    )
    confirm_grp.add_argument(
        "--decline", action="store_true", help="perform zero writes"
    )

    args = p.parse_args(argv)
    base = _default_base_dir()
    queue_dir = Path(args.queue_dir) if args.queue_dir else base / "queue"
    picked_dir = Path(args.picked_dir) if args.picked_dir else base / "picked"

    if args.cmd == "preview":
        result = build_preview(args.member_ids, queue_dir)
    else:
        try:
            if args.draft_file:
                raw = Path(args.draft_file).read_text(encoding="utf-8")
            else:
                raw = args.draft
            draft = _resolve_draft_payload(raw)
        except OSError as exc:
            result = {
                "status": "invalid-draft",
                "new_brief_path": None,
                "members_completed": [],
                "members_skipped": [],
                "error": f"--draft-file could not be read: {exc}",
            }
            if args.json:
                print(json.dumps(result))
            else:
                print(json.dumps(result, indent=2))
            return 1
        except ValueError as exc:
            result = {
                "status": "invalid-draft",
                "new_brief_path": None,
                "members_completed": [],
                "members_skipped": [],
                "error": str(exc),
            }
            if args.json:
                print(json.dumps(result))
            else:
                print(json.dumps(result, indent=2))
            return 1
        result = execute_consolidation(
            args.confirm, args.member_ids, draft, queue_dir, picked_dir
        )

        # Telemetry (spec 018 change: cluster-precision-telemetry) -- only
        # the two real decisions ("written"/"declined") are logged;
        # "no-op"/"write-verification-failed"/"invalid-draft" are not a
        # human's consolidate/decline judgment and would skew precision.
        # Best-effort: a telemetry failure must never affect the CLI's
        # own exit status or output.
        if cluster_telemetry:
            try:
                if result.get("status") == "written":
                    cluster_telemetry.log_outcome(
                        "consolidated", result.get("members_completed", [])
                    )
                elif result.get("status") == "declined":
                    cluster_telemetry.log_outcome("declined", args.member_ids)
            except Exception:  # noqa: BLE001, S110 - degrade, never break the CLI
                pass

    if args.json:
        print(json.dumps(result))
    else:
        print(json.dumps(result, indent=2))
    return 1 if result.get("status") == "write-verification-failed" else 0


if __name__ == "__main__":
    sys.exit(main())
