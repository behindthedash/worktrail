#!/usr/bin/env python3
"""
Work-queue lifecycle owner -- the single, atomic implementation of claiming and
releasing handoff briefs, shared by every consumer (the `handoff` skill's Consume
workflow, the `go` front door, and the `sdd-workflow` conductor's handoff-seed
mode) so the move never diverges.

Queue layout (two folders, agent-agnostic, outside ~/.claude):

    $WORK_QUEUE_DIR/        (default: ~/work-queue)
      queue/               briefs waiting to be picked up
      picked/              claimed briefs (in-flight AND completed, distinguished
                           by the frontmatter `status:` field -- there is no
                           separate done/ folder)

Why this script exists
----------------------
Discovery is `ls queue/`. Moving a brief OUT of queue/ is the entire mechanism
that hides it from the next agent. A POSIX rename within one filesystem is atomic,
so `claim` uses it as the arbiter: exactly one racing agent wins the rename; the
loser gets a "already claimed" signal and re-lists. This makes the claim
independent of WHO picks it up.

CLI (all subcommands accept --json):

  queue.py list                         list queue/ newest-first (filename + focus + blocked)
  queue.py resolve  IDENTIFIER          match IDENTIFIER -> match|none|ambiguous
  queue.py claim    IDENTIFIER [--by L] atomic queue/ -> picked/, stamp status,
                                        print picked path; nonzero on none/ambiguous/taken
  queue.py claim-batch PRIMARY [ID...]  claim a primary brief plus related companions
                                        in one batch (per-brief atomic; see below)
  queue.py done     IDENTIFIER          stamp status: done in picked/ (file stays)
                                        Route-C briefs require --planning-only or
                                        --implementation-complete; a --note claiming
                                        re-verification with no shown re-run output
                                        is rejected (see "Closure evidence gate")
  queue.py release  IDENTIFIER          move picked/ -> queue/ (abandoned claim)

Exit codes for `claim`/`claim-batch`: 0 ok, 2 none, 3 ambiguous, 4 already-claimed,
5 io-error, 6 write-verification-failed (keyed off the primary for `claim-batch`).

Write verification
-------------------
Every mutation that leaves a file on disk (`claim`, `done`, `release`, `link`) re-reads
the file it just wrote and validates it (`brief_frontmatter.validate_brief`: a
`---`-fenced block that parses as YAML to a mapping with non-empty `id`/`status`
fields) before reporting success. A validation failure restores the file's
pre-mutation content (and, for `claim`/`release`, undoes the queue/picked move) and
returns `{"status": "write-verification-failed", "error": "<reason>"}` instead of a
false "claimed"/"done"/"released"/"linked". This exists because a write path that
merely returns success without checking its own output can silently leave a broken
brief behind -- `list`, the dashboard, and `/go`'s picker would all have to cope with
it downstream instead of the write itself catching it.

Closure evidence gate (done --note)
------------------------------------
`done()` rejects a `--note` that asserts a re-verification result ("disproven",
"re-verified", "no longer flags", ...) but shows no actual re-run output
(`{"status": "unverified_reverification_claim"}`, no mutation) -- see
`_reverification_claim_missing_evidence`. This closes the gap where a closure
claim like "disproven -- corrected detector no longer flags this file" was
accepted on prose alone; re-executing the cited detector later showed it still
flagged the file (brief
20260817-101013-datalena-release-notes-consolidate-yml-missing-docs-skip-gate).

Batch consumption (claim-batch)
-------------------------------
`claim-batch` folds related briefs into one working session without weakening the
atomicity story: the primary and every companion are each claimed through the same
single-brief rename arbiter, so a racing agent can still win any individual brief.
A companion that loses the race (or doesn't resolve) is reported per-item and never
aborts the batch; only a failed PRIMARY claim aborts (companions stay untouched).
Grouping stays visible in the files themselves: each claimed companion is stamped
`batch-primary: <primary-stem>` and the primary records `batch:` with the companion
stems actually claimed. `done` and `release` remain strictly per-brief -- batch
execution never blurs individual completion state (`release` also strips a stale
`batch-primary` stamp so a re-queued brief doesn't carry a dead link).

Dependency tracking (blocked-by)
---------------------------------
A brief may declare prerequisites via the optional `blocked-by` frontmatter list:

    blocked-by:
      - 20260604-161500-some-prereq

`list` resolves each ID: a brief is **blocked** when any prerequisite is found in queue/
or in picked/ without `status: done`. If a prerequisite ID is not found anywhere it is
treated as satisfied (lenient -- IDs go stale as briefs are completed and archived).
`claim` succeeds (never hard-blocks) but adds a `warnings` list to the response so the
consuming skill can surface the unresolved dependencies to the agent.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..shared.brief_frontmatter import (
    _find_frontmatter_block,
    read_frontmatter,
    validate_brief,
)

_FM_RE = re.compile(r"^---\r?\n(.*?)\n---\r?\n", re.DOTALL)

# Phrases in a closure --note that assert a claim was mechanically re-verified
# ("disproven", "re-verified", "no longer flags", ...) rather than merely
# described ("duplicate of X", "out of scope") -- the latter carry no
# re-verification claim and are unaffected by `_reverification_claim_missing_evidence`.
_REVERIFICATION_CLAIM_RE = re.compile(
    r"\b(disproven|re-?verified|no longer (?:flags?|triggers?|applies)|"
    r"corrected (?:detector|check|script)|already fixed)\b",
    re.IGNORECASE,
)

# What counts as *shown* evidence rather than asserted evidence: a fenced code
# block (the actual re-run transcript) or an explicit command/output label.
_EVIDENCE_MARKER_RE = re.compile(
    r"```|^\s*(?:\$ |command:|output:|detector output:)",
    re.IGNORECASE | re.MULTILINE,
)


def _reverification_claim_missing_evidence(note: str) -> bool:
    """True when `note` asserts a re-verification result (e.g. "disproven",
    "re-verified", "no longer flags") without showing the actual re-run
    output that backs it up.

    A deliberately narrow, code-enforced "show your work" check: it cannot
    judge whether pasted output is honest, only whether something resembling
    a real command transcript accompanies the claim -- mirroring
    `skill_prose_advisory_scan.py`'s mandate-cue heuristic, but as a hard
    `done()` gate rather than an advisory scan. Brief
    20260817-101013-datalena-release-notes-consolidate-yml-missing-docs-skip-gate
    was closed with exactly this claim ("disproven -- corrected detector no
    longer flags this file") and no re-run output; re-executing the cited
    detector afterward showed it still flagged the file.
    """
    if not note:
        return False
    if not _REVERIFICATION_CLAIM_RE.search(note):
        return False
    return not _EVIDENCE_MARKER_RE.search(note)


# --------------------------------------------------------------------------- #
# Location resolution (the one place the path/layout is defined)
# --------------------------------------------------------------------------- #


def base_dir() -> Path:
    """Resolve the work-queue root: $WORK_QUEUE_DIR or ~/work-queue."""
    return Path(os.environ.get("WORK_QUEUE_DIR", "~/work-queue")).expanduser()


def queue_dir() -> Path:
    return base_dir() / "queue"


def picked_dir() -> Path:
    return base_dir() / "picked"


def _agent_label() -> str:
    """Best-effort identity for the claiming agent."""
    return os.environ.get("WORK_QUEUE_AGENT") or f"{socket.gethostname()}:{os.getpid()}"


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Frontmatter helpers (self-contained: no cross-plugin imports)
# --------------------------------------------------------------------------- #


def _read_frontmatter(path: Path) -> Dict[str, Any]:
    return read_frontmatter(path)


def _focus_of(path: Path) -> str:
    """Frontmatter focus, falling back to the first line of the ## Focus body."""
    fm = _read_frontmatter(path)
    if fm.get("focus"):
        return str(fm["focus"])
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"^##\s+Focus\s*$\r?\n(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _has_unterminated_fence(content: str) -> bool:
    """True when `content` opens with a `---` fence but `_FM_RE` still didn't
    match (e.g. the closing fence is missing/malformed) -- as opposed to
    genuinely having no frontmatter at all. Distinguishing these matters: the
    "no frontmatter" case is safe to paper over with a fresh block, but an
    opening fence with no closing one means the file is already corrupt, and
    prepending a fresh (independently well-formed) block on top would demote
    the file's real `id:`/`focus:`/etc. fields to inert body text while
    `validate_brief` -- which only re-parses the new block -- reports success.
    """
    first_line = content.split("\n", 1)[0].rstrip("\r")
    return first_line == "---"


def _yaml_scalar(value: Any) -> str:
    """Render `value` as a single-line YAML scalar, quoting only when needed.

    Interpolating a raw value into `f"{key}: {value}"` is how a brief's whole
    frontmatter block gets silently destroyed: a `focus:` containing `": "`
    makes the block unparsable, `split_frontmatter` leniently degrades it to
    `{}`, and every gating field a scheduler reads (`next-check-after`,
    `blocked-by`, `triage`) then reads as absent -- so a brief explicitly
    deferred to a future date becomes maximally eligible instead. Observed on
    a real queued brief (`20260623-093000-datalena-deferred-dep-upgrades`),
    which `/go auto` picked a fortnight before its `next-check-after`.

    `yaml.safe_dump` decides quoting the same way a parser does, so ordinary
    values (`picked`, a repo path, a URL) stay unquoted and only genuinely
    ambiguous ones gain quotes. A value whose safe rendering would span lines
    falls back to `json.dumps`, which is always a valid single-line
    double-quoted YAML scalar.
    """
    dumped = yaml.safe_dump(
        value, default_flow_style=True, width=10**9, allow_unicode=True
    )
    if dumped.endswith("...\n"):  # document-end marker on a plain scalar
        dumped = dumped[: -len("...\n")]
    scalar = dumped.strip()
    if "\n" in scalar:
        return json.dumps(value if isinstance(value, str) else str(value))
    return scalar


def _set_fm_fields(path: Path, fields: Dict[str, str]) -> None:
    """Set/replace simple scalar fields inside the YAML frontmatter block.

    Operates only on the frontmatter region to preserve body formatting and key
    order. A field already present is replaced in place; a new field is inserted
    just before the closing `---`. Values are rendered via `_yaml_scalar`, and
    the result is validated before the write is allowed to stand.
    """
    content = path.read_text(encoding="utf-8")
    m = _FM_RE.match(content)
    if not m:
        if _has_unterminated_fence(content):
            raise ValueError(
                f"{path}: opening '---' fence present but no closing fence found -- "
                "refusing to prepend a new frontmatter block over already-broken content"
            )
        # no frontmatter -> create a minimal block
        fm_lines = [f"{k}: {_yaml_scalar(v)}" for k, v in fields.items()]
        path.write_text(
            "---\n" + "\n".join(fm_lines) + "\n---\n" + content, encoding="utf-8"
        )
        return

    block = m.group(1)
    lines = block.split("\n")
    remaining = dict(fields)
    for i, line in enumerate(lines):
        key = line.split(":", 1)[0].strip() if ":" in line else None
        if key in remaining:
            lines[i] = f"{key}: {_yaml_scalar(remaining.pop(key))}"
    for k, v in remaining.items():  # append any not already present
        lines.append(f"{k}: {_yaml_scalar(v)}")
    new_block = "\n".join(lines)
    path.write_text(content[: m.start(1)] + new_block + content[m.end(1) :], encoding="utf-8")


def _remove_fm_field(path: Path, key: str) -> None:
    """Remove a scalar frontmatter field (and nothing else) if present."""
    content = path.read_text(encoding="utf-8")
    m = _FM_RE.match(content)
    if not m:
        return
    block = m.group(1)
    key_re = re.compile(r"^" + re.escape(key) + r"\s*:")
    lines = [line for line in block.split("\n") if not key_re.match(line)]
    new_block = "\n".join(lines)
    if new_block != block:
        path.write_text(content[: m.start(1)] + new_block + content[m.end(1) :], encoding="utf-8")


def _set_fm_list_field(path: Path, key: str, values: List[str]) -> None:
    """Set/replace a list field inside the YAML frontmatter block atomically.

    Renders the field as a YAML block sequence. When ``values`` is empty the key
    is omitted entirely. All other frontmatter fields, key order, and the body are
    preserved unchanged.
    """
    content = path.read_text(encoding="utf-8")
    m = _FM_RE.match(content)
    if not m:
        if _has_unterminated_fence(content):
            raise ValueError(
                f"{path}: opening '---' fence present but no closing fence found -- "
                "refusing to prepend a new frontmatter block over already-broken content"
            )
        if not values:
            return
        block_text = key + ":\n" + "".join(f"  - {_yaml_scalar(v)}\n" for v in values)
        path.write_text(f"---\n{block_text}---\n{content}", encoding="utf-8")
        return

    block = m.group(1)
    lines = block.split("\n")
    new_lines: List[str] = []
    skip_continuations = False
    key_found = False
    key_re = re.compile(r"^" + re.escape(key) + r"\s*:")

    for line in lines:
        if skip_continuations:
            if line.startswith("  ") or line.startswith("\t"):
                continue
            skip_continuations = False
        if key_re.match(line):
            key_found = True
            skip_continuations = True
            if values:
                new_lines.append(f"{key}:")
                new_lines.extend(f"  - {_yaml_scalar(v)}" for v in values)
            continue
        new_lines.append(line)

    if not key_found and values:
        new_lines.append(f"{key}:")
        new_lines.extend(f"  - {_yaml_scalar(v)}" for v in values)

    new_block = "\n".join(new_lines)
    path.write_text(content[: m.start(1)] + new_block + content[m.end(1) :], encoding="utf-8")


# --------------------------------------------------------------------------- #
# Listing / resolution
# --------------------------------------------------------------------------- #


def _md_files(d: Path) -> List[Path]:
    if not d.is_dir():
        return []
    return sorted(
        (f for f in d.iterdir() if f.is_file() and f.suffix == ".md"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )


def normalize_dependency_reference(raw: Any) -> str:
    """Return a cleaned dependency reference, rejecting malformed values.

    A dependency reference must be a single non-empty string and must not
    contain commas. Comma-separated values are rejected because callers must
    spell each prerequisite as its own frontmatter item instead of relying on a
    lossy split that can silently change eligibility semantics.
    """
    if not isinstance(raw, str):
        raise ValueError("dependency reference must be a string")
    ref = raw.strip()
    if not ref:
        raise ValueError("dependency reference must not be blank")
    if "," in ref:
        raise ValueError("dependency reference must not be comma-joined")
    return ref


def classify_dependency_reference(raw: Any) -> Dict[str, Any]:
    """Classify one dependency reference against queue/ and picked/.

    Returns a structured result with the original raw value preserved, the
    normalized reference when shape checking succeeds, a state in
    ``done`` / ``active`` / ``stale`` / ``ambiguous`` / ``malformed``, the
    candidate paths that matched, and a ``satisfied`` flag that is true only
    for ``done`` and ``stale``.
    """
    result: Dict[str, Any] = {
        "raw": raw,
        "reference": None,
        "state": "malformed",
        "candidates": [],
        "satisfied": False,
        "error": None,
    }
    try:
        ref = normalize_dependency_reference(raw)
    except ValueError as exc:
        result["error"] = str(exc)
        return result

    result["reference"] = ref
    queue_res = resolve(ref, queue_dir())
    picked_res = resolve(ref, picked_dir())

    candidate_paths: List[str] = []
    if queue_res["status"] == "match":
        candidate_paths.extend(queue_res["candidates"])
    elif queue_res["status"] == "ambiguous":
        candidate_paths.extend(queue_res["candidates"])
    if picked_res["status"] == "match":
        candidate_paths.extend(picked_res["candidates"])
    elif picked_res["status"] == "ambiguous":
        candidate_paths.extend(picked_res["candidates"])

    candidates: List[str] = []
    seen: set[str] = set()
    for candidate in candidate_paths:
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    if queue_res["status"] == "ambiguous" or picked_res["status"] == "ambiguous" or len(candidates) > 1:
        state = "ambiguous"
    elif not candidates:
        state = "stale"
    elif queue_res["status"] == "match":
        state = "active"
    else:
        picked_path = Path(candidates[0])
        picked_fm = _read_frontmatter(picked_path)
        state = "done" if str(picked_fm.get("status") or "").strip() == "done" else "active"

    result["reference"] = ref
    result["candidates"] = candidates
    result["state"] = state
    result["satisfied"] = state in ("done", "stale")
    return result


def _blocked_by_refs(fm: Dict[str, Any]) -> List[Any]:
    deps = fm.get("blocked-by")
    if deps is None:
        return []
    if isinstance(deps, list):
        return deps
    return [deps]


def _is_blocked(path: Path) -> bool:
    """Return True if any `blocked-by` prerequisite is not yet satisfied."""
    fm = _read_frontmatter(path)
    return any(not classify_dependency_reference(dep)["satisfied"] for dep in _blocked_by_refs(fm))


def _is_not_yet_due(path: Path) -> bool:
    """Return True if `next-check-after` names a date that hasn't arrived yet.

    Lenient like `_is_blocked`'s stale-ID handling: a missing or unparsable value
    is treated as due (eligible), never as an error.
    """
    fm = _read_frontmatter(path)
    raw = fm.get("next-check-after")
    if not raw:
        return False
    try:
        due = datetime.date.fromisoformat(str(raw))
    except ValueError:
        return False
    return datetime.date.today() < due


RECENTLY_RELEASED_MINUTES = 20.0

# Release-scoping triage classes for a brief's `triage:` frontmatter.
# "blocker" = must land before the target repo's current release gate ships;
# "deferred" = explicitly scoped to a later release. Absent/None = untriaged
# (ranked between the two by auto-pick; never silently treated as either).
VALID_TRIAGE = ("blocker", "deferred")


def _recently_released_info(path: Path) -> Dict[str, Any]:
    """`{"recently_released": bool, "released_at": str|None, "released_by": str|None}`.

    A brief `release()` just returned to the queue carries a stale `released-at`
    timestamp for a short window -- another live session may have released it
    moments ago after judging it not-yet-actionable (or lost a claim race), and
    an immediate re-pick would repeat the same git-archaeology cost the field
    exists to avoid. `released_by` reuses the brief's `claimed-by` value (still
    present in frontmatter after release -- only `status` and `batch-primary`
    change), i.e. whoever last held the claim before releasing it. Lenient: a
    missing or unparsable `released-at` is never recently-released.
    """
    fm = _read_frontmatter(path)
    raw = fm.get("released-at")
    info: Dict[str, Any] = {"recently_released": False, "released_at": None, "released_by": None}
    if not raw:
        return info
    try:
        released_at = datetime.datetime.fromisoformat(str(raw))
    except ValueError:
        return info
    now = datetime.datetime.now(released_at.tzinfo) if released_at.tzinfo else datetime.datetime.now()
    age_minutes = (now - released_at).total_seconds() / 60.0
    if 0 <= age_minutes < RECENTLY_RELEASED_MINUTES:
        info["recently_released"] = True
        info["released_at"] = str(raw)
        info["released_by"] = fm.get("claimed-by")
    return info


def _frontmatter_unparsable(path: Path) -> bool:
    """True when a brief has a `---` block that does not parse to a mapping.

    Distinct from "has no frontmatter at all": a fenced block that fails YAML
    parsing is a brief whose fields exist on disk but are invisible to every
    reader, because `split_frontmatter` is deliberately lenient and degrades
    it to `{}`. Leniency is right for display, but a scheduler reading `{}`
    concludes "no `next-check-after`, no `blocked-by`, no `triage`" -- i.e.
    *maximally* eligible -- which inverts the file's actual meaning.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _find_frontmatter_block(content) is None:
        return False  # genuinely no frontmatter -- not a corruption signal
    return not read_frontmatter(path)


def _awaiting_decision_info(path: Path) -> Dict[str, Any]:
    """`{"awaiting_decision": id|None, "decision_status": str|None}` for a
    brief's optional `awaiting-decision:` frontmatter link.

    `decision_status` mirrors the decision record's directory (`open` /
    `answered` / `resolved`), or None when the id resolves to no record —
    lenient like `_is_blocked`'s stale-ID handling, so a deleted decision can
    never wedge its brief.
    """
    fm = _read_frontmatter(path)
    dec_id = fm.get("awaiting-decision")
    if not dec_id:
        return {"awaiting_decision": None, "decision_status": None}
    from . import decisions  # function-level: decisions imports this module
    return {
        "awaiting_decision": str(dec_id),
        "decision_status": decisions.decision_status(str(dec_id)),
    }


def list_queue() -> Dict[str, Any]:
    """Queue briefs newest-first:

    {"briefs": [{filename, path, focus, repo, blocked, not_yet_due,
    recently_released, recently_released_by, recently_released_at, related,
    awaiting_decision, decision_status, unparsable}, ...]}.

    A brief awaiting a still-open decision reports `blocked: True` — every
    ready-count and auto-pick consumer already keys on that flag, so the brief
    leaves the eligible pool the moment its decision is filed and re-enters it
    the moment a human answers.
    """

    def _brief_dict(f: Path) -> Dict[str, Any]:
        fm = _read_frontmatter(f)
        related = fm.get("related")
        released = _recently_released_info(f)
        awaiting = _awaiting_decision_info(f)
        return {
            "filename": f.name,
            "path": str(f),
            "focus": _focus_of(f),
            "repo": fm.get("repo"),
            "blocked": _is_blocked(f) or awaiting["decision_status"] == "open",
            "not_yet_due": _is_not_yet_due(f),
            "awaiting_decision": awaiting["awaiting_decision"],
            "decision_status": awaiting["decision_status"],
            "recently_released": released["recently_released"],
            "recently_released_by": released["released_by"],
            "recently_released_at": released["released_at"],
            "related": related if isinstance(related, list) else [],
            "triage": fm.get("triage") if fm.get("triage") in VALID_TRIAGE else None,
            "unparsable": _frontmatter_unparsable(f),
        }

    return {"briefs": [_brief_dict(f) for f in _md_files(queue_dir())]}


def resolve(identifier: str, folder: Path) -> Dict[str, Any]:
    """Match identifier against .md files in `folder`.

    An identifier that is itself an absolute path to a file directly inside
    `folder` resolves immediately -- callers throughout worktrail commonly hold
    a brief's full claimed path rather than its bare id/filename (e.g. the `/go`
    skill's own `$BRIEF_ID`, which is set from this function's own `candidates[0]`;
    `decisions.py ask --brief`; the staleness/resumable-state checkers). Without
    this, passing that same path back in here silently fails to match, since
    none of the id/filename/prefix/suffix forms below ever equal a full path.

    Otherwise, forms tried in order: full filename, stem, leading prefix; then
    stem suffix (e.g. ``handoff-related-field-autodetect`` resolves
    ``20260604-113700-handoff-related-field-autodetect.md``); then ``id``
    frontmatter value.  The suffix pass runs only when the prefix pass finds no
    candidates, so a prefix match always takes precedence over a suffix-only match.
    Returns {"status": "match"|"none"|"ambiguous", "candidates": [path, ...]}.
    """
    direct = Path(identifier)
    if direct.is_absolute():
        try:
            if direct.is_file() and direct.resolve().parent == folder.resolve():
                return {"status": "match", "candidates": [str(direct.resolve())]}
        except OSError:
            pass
    files = _md_files(folder)
    hits: set = set()
    for f in files:
        if f.name == identifier or f.stem == identifier or f.name.startswith(identifier):
            hits.add(f)
    if not hits:
        for f in files:
            if f.stem.endswith(identifier):
                hits.add(f)
    for f in files:
        if f not in hits and _read_frontmatter(f).get("id") == identifier:
            hits.add(f)
    ordered = sorted(hits, key=lambda f: f.stat().st_mtime, reverse=True)
    status = "match" if len(ordered) == 1 else ("none" if not ordered else "ambiguous")
    return {"status": status, "candidates": [str(f) for f in ordered]}


# --------------------------------------------------------------------------- #
# Claim / done / release -- the coordination-critical operations
# --------------------------------------------------------------------------- #



# A queued brief's premise can drift out from under it -- something it asks for gets
# reverted, superseded, or decided against on the base branch while it sits waiting.
# Full ground-truth reconciliation (matching a brief's touched paths against reversal-
# language commits) is spec 019-go-staleness-signals' PR/task/spec reference resolution,
# which this brief predates and does not cover -- there is no existing capability that
# checks a brief's own premise against unrelated base-branch history. This is the
# deliberately cheap interim signal: a brief sitting unclaimed for a while is worth a
# "is this still current?" gut-check before a session invests time in it. 7 days matches
# `_STALE_THRESHOLD_DAYS` already used for spec staleness in dashboard.py.
PREMISE_DRIFT_THRESHOLD_DAYS = 7


def _premise_drift_warning(path: Path) -> Optional[str]:
    """Return a warning string if `created:` is more than PREMISE_DRIFT_THRESHOLD_DAYS
    old, else None. Lenient like the other warning helpers: a missing or unparsable
    `created` value never produces a warning."""
    fm = _read_frontmatter(path)
    raw = fm.get("created")
    if not raw:
        return None
    try:
        created = datetime.datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    now = datetime.datetime.now(created.tzinfo) if created.tzinfo else datetime.datetime.now()
    age_days = (now - created).total_seconds() / 86400.0
    if age_days <= PREMISE_DRIFT_THRESHOLD_DAYS:
        return None
    return (
        f"premise may have drifted: brief is {int(age_days)} days old "
        f"(created {created.date().isoformat()}) -- confirm it's still current "
        "before investing a session"
    )


def _claim_warnings(path: Path) -> List[str]:
    """Return warning strings for any unresolved blocked-by dependencies, plus a
    premise-drift age warning when applicable."""
    fm = _read_frontmatter(path)
    warnings: List[str] = []
    for dep in _blocked_by_refs(fm):
        resolution = classify_dependency_reference(dep)
        if resolution["satisfied"]:
            continue
        raw_value = resolution["raw"]
        raw_display = raw_value if isinstance(raw_value, str) else repr(raw_value)
        state = resolution["state"]
        if state == "malformed":
            warnings.append(
                f"blocked by {raw_display} (malformed dependency reference; raw={resolution['raw']!r})"
            )
            continue
        if state == "ambiguous":
            warnings.append(
                f"blocked by {raw_display} (ambiguous dependency reference; "
                f"candidates: {', '.join(resolution['candidates'])})"
            )
            continue
        warnings.append(f"blocked by {raw_display} ({state} dependency reference)")
    awaiting = _awaiting_decision_info(path)
    if awaiting["decision_status"] == "open":
        warnings.append(
            f"awaiting decision {awaiting['awaiting_decision']} (still open -- "
            "a human has not answered yet; see worktrail-decision show)")
    drift = _premise_drift_warning(path)
    if drift:
        warnings.append(drift)
    return warnings


def _same_owner(existing_path: Path, by: Optional[str]) -> Optional[bool]:
    """Compare an already-claimed brief's stamped `claimed-by` against `by`.

    Returns None (unknown -- never assume ownership) when `by` was not
    supplied by this caller; the two-call intra-session pattern (go's own
    claim, then sdd-workflow's handoff-seed claim) only reads as
    "same owner" when both calls pass the identical explicit `--by` value --
    `_agent_label()`'s PID-based default is a fresh value on every CLI
    invocation, so comparing that default across two calls of one legitimate
    dispatch would misclassify it as "different owner" (see
    docs/specs/research/concurrent-go-dispatch-brief-claim-race.md).
    """
    if by is None:
        return None
    claimed_by = _read_frontmatter(existing_path).get("claimed-by")
    return claimed_by is not None and claimed_by == by


def claim(identifier: str, by: Optional[str] = None) -> Dict[str, Any]:
    """Atomically move the matching queue brief into picked/ and stamp it.

    Returns {"status": "claimed"|"none"|"ambiguous"|"already-claimed"|"error",
             "path": str|None, "candidates": [...], "error": str|None,
             "warnings": [...], "same_owner": bool|None}.
    The rename is the atomic arbiter: a racing loser sees the source gone and
    gets "already-claimed". On success, "warnings" lists any unresolved blocked-by
    dependencies (never hard-blocks -- autonomous workflows must not be broken).

    `same_owner` distinguishes, on an "already-claimed" result, whether the
    caller's own earlier claim (within the same /go dispatch, identified by a
    stable `--by` value threaded through both the front door's claim and
    sdd-workflow's handoff-seed claim) already holds this brief (True), a
    different owner holds it (False), or ownership cannot be determined
    because no `--by` was supplied (None). A fresh "claimed" result is
    trivially `same_owner: True` -- the caller just became the owner.
    """
    res = resolve(identifier, queue_dir())
    if res["status"] != "match":
        # Not in queue/. If the brief is already sitting in picked/, that's a
        # normal "already-claimed" (exit 4, handled gracefully by the consuming
        # skills), not "none" (exit 2, which surfaces as a red error box).
        if res["status"] == "none":
            picked_res = resolve(identifier, picked_dir())
            if picked_res["status"] == "match":
                picked_path = Path(picked_res["candidates"][0])
                return {
                    "status": "already-claimed",
                    "path": str(picked_path),
                    "candidates": [],
                    "error": None,
                    "warnings": [],
                    "same_owner": _same_owner(picked_path, by),
                }
        return {
            "status": res["status"],
            "path": None,
            "candidates": res["candidates"],
            "error": None,
            "warnings": [],
            "same_owner": None,
        }

    src = Path(res["candidates"][0])
    dst = picked_dir() / src.name
    try:
        picked_dir().mkdir(parents=True, exist_ok=True)
        if dst.exists():  # same-name brief already in picked/
            return {
                "status": "already-claimed",
                "path": str(dst),
                "candidates": [],
                "error": None,
                "warnings": [],
                "same_owner": _same_owner(dst, by),
            }
        os.rename(src, dst)  # atomic within one filesystem
    except FileNotFoundError:  # another agent won the race
        return {
            "status": "already-claimed",
            "path": None,
            "candidates": [],
            "error": None,
            "warnings": [],
            "same_owner": False,
        }
    except OSError as exc:
        return {
            "status": "error",
            "path": None,
            "candidates": [],
            "error": str(exc),
            "warnings": [],
        }

    try:
        original = dst.read_text(encoding="utf-8")
    except OSError as exc:  # extremely unlikely right after a successful rename
        return {
            "status": "claimed",
            "path": str(dst),
            "candidates": [],
            "error": f"read before stamp failed: {exc}",
            "warnings": [],
            "same_owner": True,
        }

    try:
        _set_fm_fields(
            dst, {"status": "picked", "claimed-at": _now_iso(), "claimed-by": by or _agent_label()}
        )
    except OSError as exc:  # claim already holds; stamping is best-effort
        return {
            "status": "claimed",
            "path": str(dst),
            "candidates": [],
            "error": f"stamp failed: {exc}",
            "warnings": [],
            "same_owner": True,
        }
    except ValueError as exc:
        # Unlike a transient OSError, this means the brief's pre-existing
        # frontmatter is unrecoverably broken (e.g. an unterminated fence) --
        # reporting "claimed" here would be exactly the false-success bug
        # this validation exists to prevent. Roll back like a post-write
        # validate_brief failure: nothing was written (the raise happens
        # before any write), so restoring is just undoing the move.
        try:
            os.rename(dst, src)
        except OSError:
            pass
        return {
            "status": "write-verification-failed",
            "path": None,
            "candidates": [],
            "error": str(exc),
            "warnings": [],
        }

    ok, verr = validate_brief(dst)
    if not ok:
        # The claim's whole purpose is moving a brief out of queue/ so no
        # other agent picks it up -- reporting "claimed" on a file that just
        # failed validation would hide a broken brief in picked/ instead of
        # in queue/ where a human or a future claim would at least see it.
        # Best-effort rollback: restore prior content, undo the move.
        try:
            dst.write_text(original, encoding="utf-8")
            os.rename(dst, src)
        except OSError:
            pass
        return {
            "status": "write-verification-failed",
            "path": None,
            "candidates": [],
            "error": verr,
            "warnings": [],
        }

    warnings = _claim_warnings(dst)
    return {
        "status": "claimed",
        "path": str(dst),
        "candidates": [],
        "error": None,
        "warnings": warnings,
        "same_owner": True,
    }


def claim_batch(primary: str, companions: List[str], by: Optional[str] = None) -> Dict[str, Any]:
    """Claim a primary brief plus related companion briefs as one batch.

    The primary is claimed first via claim(); any non-claimed outcome aborts the
    batch (companions untouched) and is returned as-is with an empty companions
    list. Each companion is then claimed individually through the same atomic
    rename; per-companion failures (already-claimed / none / ambiguous) are
    reported in the companions list and never abort the batch. Claimed
    companions are stamped `batch-primary: <primary-stem>`; the primary gets a
    `batch:` list of the companion stems actually claimed.
    """
    label = by or _agent_label()
    primary_res = claim(primary, by=label)
    if primary_res["status"] != "claimed":
        return {**primary_res, "companions": []}

    primary_path = Path(primary_res["path"])
    claimed_stems: List[str] = []
    companion_results: List[Dict[str, Any]] = []
    for ident in companions:
        res = claim(ident, by=label)
        entry: Dict[str, Any] = {
            "identifier": ident,
            "status": res["status"],
            "path": res.get("path"),
            "warnings": res.get("warnings", []),
        }
        if res["status"] == "claimed":
            comp_path = Path(res["path"])
            try:
                _set_fm_fields(comp_path, {"batch-primary": primary_path.stem})
            except (OSError, ValueError) as exc:  # claim already holds; stamping is best-effort
                entry["error"] = f"stamp failed: {exc}"
            claimed_stems.append(comp_path.stem)
        companion_results.append(entry)

    if claimed_stems:
        try:
            _set_fm_list_field(primary_path, "batch", claimed_stems)
        except (OSError, ValueError):
            pass  # grouping stamp is best-effort; the claims themselves hold

    return {
        "status": "claimed",
        "path": str(primary_path),
        "candidates": [],
        "error": None,
        "warnings": primary_res.get("warnings", []),
        "same_owner": True,
        "companions": companion_results,
    }


def done(
    identifier: str,
    *,
    planning_only: bool = False,
    implementation_complete: bool = False,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Stamp a picked brief as completed.

    Route-C briefs have an explicit C-to-D decision point after spec-to-tasks.
    Refuse an unqualified completion so a planning pass cannot silently hide
    implementation work from the queue.

    ``note``, when truthy, is appended to the brief body as a ``## Closure
    Note`` section before the ``status: done`` / ``completed-at`` frontmatter
    stamp is applied, so a future reader can see why the brief was closed.

    A ``note`` that asserts a re-verification result ("disproven",
    "re-verified", "no longer flags", ...) without showing the actual re-run
    output is rejected outright (``status: unverified_reverification_claim``,
    no mutation) -- see `_reverification_claim_missing_evidence`.
    """
    if planning_only and implementation_complete:
        return {
            "status": "error",
            "path": None,
            "candidates": [],
            "error": "completion modes are mutually exclusive",
        }
    res = resolve(identifier, picked_dir())
    if res["status"] != "match":
        return {
            "status": res["status"],
            "path": None,
            "candidates": res["candidates"],
            "error": None,
        }
    path = Path(res["candidates"][0])
    fm = _read_frontmatter(path)
    route = str(fm.get("recommended-route") or "").strip().upper()
    if route == "C" and not (planning_only or implementation_complete):
        return {
            "status": "awaiting_implementation_decision",
            "path": str(path),
            "candidates": [],
            "error": (
                "Route-C brief requires an explicit decision: continue to Route D "
                "or finish with --planning-only"
            ),
        }
    if note and _reverification_claim_missing_evidence(note):
        return {
            "status": "unverified_reverification_claim",
            "path": str(path),
            "candidates": [],
            "error": (
                "Closure note asserts a re-verification result (e.g. "
                "'disproven'/'re-verified'/'no longer flags') without showing the "
                "actual command output that backs it up. Paste the literal re-run "
                "command and its output (e.g. inside a ``` fenced block, or a "
                "'Command:'/'Output:' pair) and retry."
            ),
        }
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "path": str(path), "candidates": [], "error": str(exc)}
    if note:
        try:
            path.write_text(original + f"\n## Closure Note\n\n{note}\n", encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "path": str(path), "candidates": [], "error": str(exc)}
    try:
        _set_fm_fields(path, {"status": "done", "completed-at": _now_iso()})
    except (OSError, ValueError) as exc:
        try:
            path.write_text(original, encoding="utf-8")
        except OSError:
            pass
        return {"status": "error", "path": str(path), "candidates": [], "error": str(exc)}

    ok, verr = validate_brief(path)
    if not ok:
        try:
            path.write_text(original, encoding="utf-8")
        except OSError:
            pass
        return {"status": "write-verification-failed", "path": str(path), "candidates": [], "error": verr}

    return {"status": "done", "path": str(path), "candidates": [], "error": None}


def release(identifier: str, next_check_after: Optional[str] = None) -> Dict[str, Any]:
    """Return an abandoned picked brief to the queue (status: queued).

    ``next_check_after``, when given, is written as the `next-check-after` frontmatter
    date so the brief is suppressed from auto-pick/picker eligibility until that date
    (see `_is_not_yet_due`). Omitting it leaves any existing field untouched. Always
    stamps `released-at` -- consumed by `_recently_released_info` to suppress an
    immediate re-pick of a brief another session just released moments ago.
    """
    res = resolve(identifier, picked_dir())
    if res["status"] != "match":
        return {
            "status": res["status"],
            "path": None,
            "candidates": res["candidates"],
            "error": None,
        }
    src = Path(res["candidates"][0])
    dst = queue_dir() / src.name
    try:
        original = src.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "path": None, "candidates": [], "error": str(exc)}
    try:
        queue_dir().mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        _set_fm_fields(dst, {"status": "queued", "released-at": _now_iso()})
        _remove_fm_field(dst, "batch-primary")  # a re-queued brief must not carry a stale link
        if next_check_after:
            _set_fm_fields(dst, {"next-check-after": next_check_after})
    except (OSError, ValueError) as exc:
        return {"status": "error", "path": None, "candidates": [], "error": str(exc)}

    ok, verr = validate_brief(dst)
    if not ok:
        try:
            dst.write_text(original, encoding="utf-8")
            os.rename(dst, src)
        except OSError:
            pass
        return {"status": "write-verification-failed", "path": None, "candidates": [], "error": verr}

    return {"status": "released", "path": str(dst), "candidates": [], "error": None}


def set_triage(identifier: str, value: str) -> Dict[str, Any]:
    """Classify a brief for release scoping: `triage: blocker|deferred`.

    Resolves against queue/ first, then picked/ (a claimed brief can still be
    re-scoped mid-flight). `clear` removes the field, returning the brief to
    untriaged. Frontmatter-only edit via _set_fm_fields, so body and key order
    survive — never hand-edit the file for this.
    """
    if value not in VALID_TRIAGE + ("clear",):
        return {
            "status": "error",
            "path": None,
            "candidates": [],
            "error": f"triage value must be one of {', '.join(VALID_TRIAGE)} or clear",
        }
    res = resolve(identifier, queue_dir())
    if res["status"] != "match":
        res = resolve(identifier, picked_dir())
    if res["status"] != "match":
        return {
            "status": res["status"],
            "path": None,
            "candidates": res["candidates"],
            "error": None,
        }
    path = Path(res["candidates"][0])
    try:
        if value == "clear":
            _remove_fm_field(path, "triage")
        else:
            _set_fm_fields(path, {"triage": value})
    except (OSError, ValueError) as exc:
        return {"status": "error", "path": str(path), "candidates": [], "error": str(exc)}
    return {"status": "triaged", "path": str(path), "candidates": [], "error": None}


def link(id_a: str, id_b: str) -> Dict[str, Any]:
    """Link two briefs as related (symmetric, idempotent, never touches blocked-by).

    Resolves both identifiers across queue/ and picked/. Appends each brief's
    stem to the other's ``related`` frontmatter list via _set_fm_list_field.
    Re-linking an already-linked pair is a no-op success. Self-links are no-ops.
    Returns {"status": "linked"|"none"|"ambiguous"|"error",
             "paths": [...], "candidates": [...], "error": str|None}.
    """

    def _resolve_anywhere(ident: str) -> Dict[str, Any]:
        q_res = resolve(ident, queue_dir())
        if q_res["status"] == "match":
            return q_res
        p_res = resolve(ident, picked_dir())
        if p_res["status"] == "match":
            return p_res
        if q_res["status"] == "ambiguous" or p_res["status"] == "ambiguous":
            return {
                "status": "ambiguous",
                "candidates": q_res["candidates"] + p_res["candidates"],
            }
        return {"status": "none", "candidates": []}

    res_a = _resolve_anywhere(id_a)
    if res_a["status"] != "match":
        return {
            "status": res_a["status"],
            "paths": [],
            "candidates": res_a["candidates"],
            "error": None,
        }
    res_b = _resolve_anywhere(id_b)
    if res_b["status"] != "match":
        return {
            "status": res_b["status"],
            "paths": [],
            "candidates": res_b["candidates"],
            "error": None,
        }

    path_a = Path(res_a["candidates"][0])
    path_b = Path(res_b["candidates"][0])

    if path_a == path_b:  # self-link: no-op success, write nothing
        return {
            "status": "linked",
            "paths": [str(path_a), str(path_b)],
            "candidates": [],
            "error": None,
        }

    stem_a = path_a.stem
    stem_b = path_b.stem

    def _coerce_ids(val: Any) -> List[str]:
        if isinstance(val, list):
            return [str(x) for x in val if x is not None]
        if isinstance(val, str) and val:
            return [val]
        return []

    try:
        original_a = path_a.read_text(encoding="utf-8")
        original_b = path_b.read_text(encoding="utf-8")
        fm_a = _read_frontmatter(path_a)
        fm_b = _read_frontmatter(path_b)
        related_a = _coerce_ids(fm_a.get("related"))
        related_b = _coerce_ids(fm_b.get("related"))
        if stem_b not in related_a:
            related_a.append(stem_b)
            _set_fm_list_field(path_a, "related", related_a)
        if stem_a not in related_b:
            related_b.append(stem_a)
            _set_fm_list_field(path_b, "related", related_b)
    except (OSError, ValueError) as exc:
        return {"status": "error", "paths": [], "candidates": [], "error": str(exc)}

    for path in (path_a, path_b):
        ok, verr = validate_brief(path)
        if not ok:
            # link() is meant to be symmetric -- a half-linked pair (a -> b
            # written, b -> a lost) is worse than a no-op, so a failure on
            # either side rolls back both.
            try:
                path_a.write_text(original_a, encoding="utf-8")
                path_b.write_text(original_b, encoding="utf-8")
            except OSError:
                pass
            return {"status": "write-verification-failed", "paths": [], "candidates": [], "error": verr}

    return {
        "status": "linked",
        "paths": [str(path_a), str(path_b)],
        "candidates": [],
        "error": None,
    }


# --------------------------------------------------------------------------- #
# Optional git backup (opt-in, best-effort, single-machine)
# --------------------------------------------------------------------------- #


def _git_backup(reason: str) -> None:
    """Commit + push the work-queue to its git remote after a mutation.

    Opt-in via the WORK_QUEUE_GIT_SYNC env var (truthy: 1/true/yes/on) and a no-op
    unless the queue root is itself a git repo. Intended for a single-machine setup
    where the remote is just an offsite backup (push-only; never pull here -- a pull
    would race the atomic rename that arbitrates claims).

    Every git call is best-effort: a backup must never be able to break a queue
    mutation, so all errors (offline, no remote, nothing staged) are swallowed.
    """
    if os.environ.get("WORK_QUEUE_GIT_SYNC", "").strip().lower() not in ("1", "true", "yes", "on"):
        return
    root = base_dir()
    if not (root / ".git").is_dir():
        return

    import subprocess  # local import: only needed when sync is enabled

    def _git(*args: str) -> None:
        try:
            subprocess.run(
                ["git", *args],
                cwd=str(root),
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception:  # noqa: BLE001 -- backup is best-effort, never disrupt the queue op
            pass

    _git("add", "-A")
    _git("commit", "-m", f"chore(work-queue): {reason}")
    _git("push")


# Status that means a mutation actually changed disk, so a backup is warranted.
_BACKUP_ON = {
    "claim": "claimed",
    "claim-batch": "claimed",
    "done": "done",
    "release": "released",
    "link": "linked",
    "triage": "triaged",
}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

_CLAIM_EXIT = {
    "claimed": 0,
    "none": 2,
    "ambiguous": 3,
    "already-claimed": 4,
    "error": 5,
    "write-verification-failed": 6,
}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="work-queue lifecycle owner")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit JSON")
    subs = p.add_subparsers(dest="cmd")
    subs.required = True

    subs.add_parser("list", parents=[common], help="list queue/ newest-first")
    rp = subs.add_parser("resolve", parents=[common], help="resolve an identifier against queue/")
    rp.add_argument("identifier")
    cp = subs.add_parser(
        "claim", parents=[common], help="atomically claim a brief (queue/ -> picked/)"
    )
    cp.add_argument("identifier")
    cp.add_argument("--by", default=None, help="claiming-agent label")
    bp = subs.add_parser(
        "claim-batch",
        parents=[common],
        help="claim a primary brief plus related companions in one batch",
    )
    bp.add_argument("primary")
    bp.add_argument("companions", nargs="*")
    bp.add_argument("--by", default=None, help="claiming-agent label")
    dp = subs.add_parser("done", parents=[common], help="mark a picked brief done")
    dp.add_argument("identifier")
    mode = dp.add_mutually_exclusive_group()
    mode.add_argument(
        "--planning-only",
        action="store_true",
        help="explicitly stop a Route-C brief after planning/specification",
    )
    mode.add_argument(
        "--implementation-complete",
        action="store_true",
        help="mark a Route-C brief done after inline Route-D implementation",
    )
    dp.add_argument(
        "--note",
        default=None,
        help="append a ## Closure Note section to the brief body before closing it",
    )
    sp = subs.add_parser("release", parents=[common], help="return a picked brief to the queue")
    sp.add_argument("identifier")
    sp.add_argument(
        "--next-check-after",
        default=None,
        metavar="ISO-DATE",
        help="suppress auto-pick/picker eligibility until this date (still blocked upstream)",
    )
    lp = subs.add_parser(
        "link", parents=[common], help="link two briefs as related (symmetric, idempotent)"
    )
    lp.add_argument("id_a")
    lp.add_argument("id_b")

    tp = subs.add_parser(
        "triage", parents=[common], help="classify a brief for release scoping"
    )
    tp.add_argument("identifier")
    tp.add_argument("value", choices=VALID_TRIAGE + ("clear",))

    args = p.parse_args(argv)

    if args.cmd == "list":
        result = list_queue()
    elif args.cmd == "resolve":
        result = resolve(args.identifier, queue_dir())
    elif args.cmd == "claim":
        result = claim(args.identifier, by=args.by)
    elif args.cmd == "claim-batch":
        result = claim_batch(args.primary, args.companions, by=args.by)
    elif args.cmd == "done":
        result = done(
            args.identifier,
            planning_only=args.planning_only,
            implementation_complete=args.implementation_complete,
            note=args.note,
        )
    elif args.cmd == "link":
        result = link(args.id_a, args.id_b)
    elif args.cmd == "triage":
        result = set_triage(args.identifier, args.value)
    else:
        result = release(args.identifier, next_check_after=args.next_check_after)

    if args.json:
        print(json.dumps(result))
    else:
        _print_human(args.cmd, result)

    if result.get("status") == _BACKUP_ON.get(args.cmd):
        target = result.get("path") or (result.get("paths") or [None])[0]
        _git_backup(f"{args.cmd} {Path(target).stem}".strip() if target else args.cmd)

    if args.cmd in ("claim", "claim-batch"):
        return _CLAIM_EXIT.get(result["status"], 5)
    if result.get("status") in (
        "none",
        "ambiguous",
        "error",
        "write-verification-failed",
        "awaiting_implementation_decision",
        "unverified_reverification_claim",
    ):
        return 1
    return 0


def _print_human(cmd: str, result: Dict[str, Any]) -> None:
    if cmd == "list":
        ready = [
            b for b in result["briefs"] if not b.get("blocked") and not b.get("not_yet_due")
        ]
        blocked = [b for b in result["briefs"] if b.get("blocked")]
        not_yet_due = [
            b for b in result["briefs"] if b.get("not_yet_due") and not b.get("blocked")
        ]
        if not ready and not blocked and not not_yet_due:
            print("Queue is empty.")
        for i, b in enumerate(ready, 1):
            print(f"{i}. {b['filename']}  focus: {b['focus']}")
        if blocked:
            print("\n[blocked — waiting on prerequisites]")
            for b in blocked:
                print(f"  {b['filename']}  focus: {b['focus']}")
        if not_yet_due:
            print("\n[watching — not due for recheck yet]")
            for b in not_yet_due:
                print(f"  {b['filename']}  focus: {b['focus']}")
        return
    status = result["status"]
    if status in ("match", "claimed", "done", "released", "triaged"):
        print(f"{status}: {result.get('path') or result['candidates'][0]}")
        for comp in result.get("companions") or []:
            print(f"  companion {comp['identifier']}: {comp['status']}" +
                  (f" -> {comp['path']}" if comp.get("path") else ""))
    elif status == "linked":
        paths = result.get("paths", [])
        if len(paths) >= 2:
            print(f"linked: {paths[0]} <-> {paths[1]}")
        else:
            print("linked")
    elif status == "ambiguous":
        print("ambiguous -- candidates:")
        for c in result["candidates"]:
            print(f"  {c}")
    elif status == "already-claimed":
        print("already claimed by another session -- re-list and pick another.")
    elif status == "none":
        print("none: no matching brief found")
    elif status == "write-verification-failed":
        print(f"write verification failed (rolled back): {result.get('error')}", file=sys.stderr)
    else:
        print(f"error: {result.get('error')}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
