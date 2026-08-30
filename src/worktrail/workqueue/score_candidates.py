#!/usr/bin/env python3
"""
Candidate scorer for the handoff skill's Create-time auto-detect step, and for
the go front door's Consume-time batch detection.

Capture mode (default): scans queue/ and picked/ (excluding status: done) for
briefs related to a newly-written brief, scores each using same-repo boost +
token overlap on focus + shared paths/identifiers in body, excludes blocked-by
pairs, caps to the top 3 above the minimum threshold, and emits JSON:

    {"auto_link": [...], "confirm": [...]}

where each entry is {"path": ..., "id": ..., "focus": ...}.

Briefs that are same-repo AND above the high-confidence threshold go to
auto_link; all others above the minimum threshold go to confirm.

Batch mode (--mode batch): scans queue/ ONLY (a brief someone else already
picked can't be batched) for briefs that could be folded into the same working
session as the brief about to be claimed. Same-repo is REQUIRED, `related`-
linked briefs are included unconditionally, a shared `target-spec` earns a
boost, and the score floor is stricter than capture mode (batching commits the
session to actually doing the work; a loose textual echo isn't enough). Emits:

    {"batch": [{"path": ..., "id": ..., "focus": ..., "reason": ...}, ...]}

with reason one of "related-link" | "same-target-spec" | "identifier-overlap" |
"score", capped at BATCH_TOP_N companions. "identifier-overlap" marks a pair
that only cleared BATCH_MIN because of a shared identifier-shaped token (a
filename, snake_case, or kebab-case name) -- see IDENTIFIER_BOOST below.

No import of work_queue.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import split_frontmatter

# Scoring thresholds — conservative defaults per spec
MIN_OVERLAP = 0.15  # minimum total score to include a candidate
HIGH_CONFIDENCE = 0.45  # total-score threshold for same-repo auto-link
SAME_REPO_BOOST = 0.20  # added to score when candidate shares the new brief's repo
TOP_N = 3  # max combined candidates across auto_link + confirm

# Batch-mode thresholds (Consume-time; stricter than capture mode)
BATCH_MIN = 0.45  # score floor for a companion with no structural signal
TARGET_SPEC_BOOST = 0.25  # added when both briefs name the same target-spec
BATCH_TOP_N = 3  # max companions per batch (keeps one run/PR reviewable)

# A shared identifier-shaped token (filename, snake_case, kebab-case name) is a
# far more precise signal than a shared plain word, but the flat token-overlap
# coefficient below dilutes it into the surrounding prose on long focus text —
# three datalena CI-guard briefs each sharing one concrete filename/job-name
# identifier still scored 0.32/0.42/0.36 against BATCH_MIN (brief
# 20260821-172334). Flat boost (not scaled by overlap magnitude, matching
# SAME_REPO_BOOST/TARGET_SPEC_BOOST's own style) applied once per pair when at
# least one identifier-shaped token is shared.
IDENTIFIER_BOOST = 0.15

# Filenames (foo.py), snake_case, and kebab-case compound tokens — segments of
# letters/digits joined by '_', '.', or '-'. Plain words never match (no
# joining character), so this is additive to, not a replacement for, _tokenize.
# Each segment must be >= 2 chars, both to require a real leading segment and
# to exclude "e.g"/"i.e."-style abbreviations that would otherwise match as a
# spurious two-segment "identifier" and inflate unrelated-brief scores.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+(?:[_.\-][A-Za-z0-9_]{2,})+")


def _read_brief(path: Path):
    """Return (frontmatter_dict, body_text) or (None, None) on error."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    return split_frontmatter(content)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set:
    """Return lowercase alphanumeric tokens of length >= 3 from text."""
    if not text:
        return set()
    return {t.lower() for t in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", text)}


def _overlap_coefficient(a: set, b: set) -> float:
    """Overlap coefficient: |A ∩ B| / min(|A|, |B|)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _identifier_tokens(text: str) -> set:
    """Return lowercase identifier-shaped tokens: filenames, snake_case, kebab-case."""
    if not text:
        return set()
    return {t.lower() for t in _IDENTIFIER_RE.findall(text)}


def _normalize_repo(val: Any) -> str | None:
    """Return None for absent/null repos so they never match each other."""
    if val is None or val in ("null", "~", ""):
        return None
    return str(val)


def _id_matches(identifier: str, stem: str) -> bool:
    """True if identifier resolves to this brief stem (mirrors resolve() forms)."""
    if not identifier:
        return False
    # Exact filename or stem
    if identifier in (stem + ".md", stem):
        return True
    # Prefix match (e.g. "20260604-161500" matches "20260604-161500-ggb-...")
    if stem.startswith(identifier):
        return True
    # Suffix/contains match (slug without date-time prefix)
    return bool(stem.endswith(identifier))


def _is_blocked_by_pair(
    new_fm: dict[str, Any],
    new_stem: str,
    cand_fm: dict[str, Any],
    cand_stem: str,
) -> bool:
    """True if either brief has a blocked-by entry pointing at the other."""

    def _deps(fm: dict[str, Any]) -> list[str]:
        raw = fm.get("blocked-by") or []
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, list):
            return [str(x) for x in raw if x]
        return []

    for dep in _deps(new_fm):
        if _id_matches(dep, cand_stem):
            return True
    for dep in _deps(cand_fm):
        if _id_matches(dep, new_stem):
            return True
    return False


def _md_files(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return [f for f in d.iterdir() if f.is_file() and f.suffix == ".md"]


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------


def _score_against_queue(
    new_focus_tokens: set,
    new_body_tokens: set,
    new_repo: str | None,
    base_dir: Path,
    *,
    exclude_path: Path | None = None,
    new_fm: dict[str, Any] | None = None,
    new_stem: str | None = None,
) -> list[dict[str, Any]]:
    """Score every non-done queue/picked brief against the given focus/body/repo.

    Shared core for `score_candidates()` (post-write: scores an existing brief
    file, so it excludes itself and applies blocked-by filtering) and
    `precheck_duplicate()` (pre-write: no file and no id yet to exclude or
    filter blocked-by against, so `exclude_path`/`new_fm`/`new_stem` are all
    optional). Returns entries sorted by `total_score` descending, uncapped —
    callers apply their own TOP_N/threshold slicing.
    """
    scored: list[dict[str, Any]] = []

    for subdir in ("queue", "picked"):
        for f in _md_files(base_dir / subdir):
            if exclude_path is not None and f.resolve() == exclude_path.resolve():
                continue  # skip self

            cand_fm, cand_body = _read_brief(f)
            if cand_fm is None:
                continue  # malformed — skip leniently (AC-017)

            if cand_fm.get("status") == "done":
                continue  # exclude done briefs (AC-012)

            if (
                new_fm is not None
                and new_stem is not None
                and _is_blocked_by_pair(new_fm, new_stem, cand_fm, f.stem)
            ):
                continue  # exclude blocked-by pairs (AC-016)

            cand_focus = str(cand_fm.get("focus") or "")
            cand_focus_tokens = _tokenize(cand_focus)
            cand_body_tokens = _tokenize(cand_body or "")

            focus_score = _overlap_coefficient(new_focus_tokens, cand_focus_tokens)
            body_score = _overlap_coefficient(new_body_tokens, cand_body_tokens)
            base_score = focus_score * 0.7 + body_score * 0.3

            cand_repo = _normalize_repo(cand_fm.get("repo"))
            same_repo = cand_repo is not None and cand_repo == new_repo

            total_score = base_score + (SAME_REPO_BOOST if same_repo else 0.0)

            if total_score < MIN_OVERLAP:
                continue  # below minimum threshold (AC-013, AC-017)

            scored.append(
                {
                    "path": str(f),
                    "id": f.stem,
                    "focus": cand_focus,
                    "same_repo": same_repo,
                    "base_score": base_score,
                    "total_score": total_score,
                }
            )

    scored.sort(key=lambda c: c["total_score"], reverse=True)
    return scored


def score_candidates(new_brief_path: Path, base_dir: Path) -> dict[str, Any]:
    """Score non-done queue/picked briefs against new_brief_path.

    Returns {"auto_link": [...], "confirm": [...]} where each entry is
    {"path": str, "id": str, "focus": str}.
    """
    new_fm, new_body = _read_brief(new_brief_path)
    if new_fm is None:
        return {"auto_link": [], "confirm": []}

    new_stem = new_brief_path.stem
    new_focus = str(new_fm.get("focus") or "")
    new_repo = _normalize_repo(new_fm.get("repo"))

    # Sort by total_score descending, cap to TOP_N (AC-013)
    scored = _score_against_queue(
        _tokenize(new_focus),
        _tokenize(new_body or ""),
        new_repo,
        base_dir,
        exclude_path=new_brief_path,
        new_fm=new_fm,
        new_stem=new_stem,
    )[:TOP_N]

    auto_link = []
    confirm = []
    for c in scored:
        entry = {"path": c["path"], "id": c["id"], "focus": c["focus"]}
        if c["same_repo"] and c["total_score"] >= HIGH_CONFIDENCE:
            auto_link.append(entry)  # AC-014: same-repo high-confidence auto-link
        else:
            confirm.append(entry)

    return {"auto_link": auto_link, "confirm": confirm}


def precheck_duplicate(
    focus: str, body: str, repo: str | None, base_dir: Path
) -> dict[str, Any] | None:
    """Best same-repo, high-confidence match for not-yet-written brief content.

    Pre-write counterpart to `score_candidates()`'s post-write auto-link tier
    (same repo AND `total_score >= HIGH_CONFIDENCE`) -- same threshold, same
    scoring, just run before a brief file exists. No self-exclusion or
    blocked-by filtering applies: there is no id/stem yet for a brief that
    hasn't been written. Returns the single best match as
    `{"path", "id", "focus", "total_score"}`, or `None` when nothing clears
    the bar (including when `repo` is `None` -- same-repo can never be
    established against a null repo, matching `score_candidates()`'s own
    behavior for a null-repo new brief).
    """
    scored = _score_against_queue(
        _tokenize(focus), _tokenize(body), _normalize_repo(repo), base_dir
    )
    for c in scored:
        if c["same_repo"] and c["total_score"] >= HIGH_CONFIDENCE:
            return {
                "path": c["path"],
                "id": c["id"],
                "focus": c["focus"],
                "total_score": c["total_score"],
            }
    return None


def _coerce_id_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x) for x in val if x]
    if isinstance(val, str) and val:
        return [val]
    return []


def batch_candidates(brief_path: Path, base_dir: Path) -> dict[str, Any]:
    """Find queue/ briefs foldable into the same session as brief_path.

    Same-repo is required (no repo on the brief -> no candidates). Inclusion:
    `related`-linked in either direction (unconditional), same non-null
    `target-spec` (after boost), or total score >= BATCH_MIN. blocked-by pairs
    are excluded in either direction — a dependency is sequencing, not a batch.
    Returns {"batch": [{"path", "id", "focus", "reason"}, ...]} capped at
    BATCH_TOP_N, related-link entries first, then by score.
    """
    fm, body = _read_brief(brief_path)
    if fm is None:
        return {"batch": []}

    repo = _normalize_repo(fm.get("repo"))
    if repo is None:
        return {"batch": []}  # can't establish "same repo" — don't guess

    stem = brief_path.stem
    target_spec = _normalize_repo(fm.get("target-spec"))
    related_ids = _coerce_id_list(fm.get("related"))
    focus_text = str(fm.get("focus") or "")
    focus_tokens = _tokenize(focus_text)
    body_tokens = _tokenize(body or "")
    ident_tokens = _identifier_tokens(focus_text) | _identifier_tokens(body or "")

    scored: list[dict[str, Any]] = []
    for f in _md_files(base_dir / "queue"):
        if f.resolve() == brief_path.resolve():
            continue
        cand_fm, cand_body = _read_brief(f)
        if cand_fm is None:
            continue
        if _is_blocked_by_pair(fm, stem, cand_fm, f.stem):
            continue
        if _normalize_repo(cand_fm.get("repo")) != repo:
            continue

        related_link = any(_id_matches(rid, f.stem) for rid in related_ids) or any(
            _id_matches(rid, stem) for rid in _coerce_id_list(cand_fm.get("related"))
        )
        cand_spec = _normalize_repo(cand_fm.get("target-spec"))
        spec_match = target_spec is not None and cand_spec == target_spec

        cand_focus = str(cand_fm.get("focus") or "")
        base_score = (
            _overlap_coefficient(focus_tokens, _tokenize(cand_focus)) * 0.7
            + _overlap_coefficient(body_tokens, _tokenize(cand_body or "")) * 0.3
        )
        cand_ident_tokens = _identifier_tokens(cand_focus) | _identifier_tokens(
            cand_body or ""
        )
        shares_identifier = bool(ident_tokens & cand_ident_tokens)

        subtotal = (
            base_score + SAME_REPO_BOOST + (TARGET_SPEC_BOOST if spec_match else 0.0)
        )
        identifier_boost = IDENTIFIER_BOOST if shares_identifier else 0.0
        total = subtotal + identifier_boost

        if not (related_link or spec_match or total >= BATCH_MIN):
            continue
        if related_link:
            reason = "related-link"
        elif spec_match:
            reason = "same-target-spec"
        elif identifier_boost and subtotal < BATCH_MIN:
            reason = "identifier-overlap"
        else:
            reason = "score"
        scored.append(
            {
                "path": str(f),
                "id": f.stem,
                "focus": cand_focus,
                "reason": reason,
                "_related": related_link,
                "_score": total,
            }
        )

    scored.sort(key=lambda c: (not c["_related"], -c["_score"]))
    return {
        "batch": [
            {k: v for k, v in c.items() if not k.startswith("_")}
            for c in scored[:BATCH_TOP_N]
        ]
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Score related-brief candidates for a handoff brief"
    )
    p.add_argument("brief_path", help="Path to the brief to score against")
    p.add_argument(
        "--queue-dir",
        required=True,
        help="Base queue directory (contains queue/ and picked/ subdirs)",
    )
    p.add_argument(
        "--mode",
        choices=("capture", "batch"),
        default="capture",
        help="capture: create-time auto-link candidates (default); "
        "batch: consume-time same-session companions from queue/ only",
    )
    args = p.parse_args(argv)
    if args.mode == "batch":
        result = batch_candidates(Path(args.brief_path), Path(args.queue_dir))
    else:
        result = score_candidates(Path(args.brief_path), Path(args.queue_dir))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
