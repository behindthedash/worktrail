#!/usr/bin/env python3
"""WARN-only advisory scan for SKILL.md prose narrating a corrective action as
mandatory with nothing registered proving it is code-enforced, outside the
closed go:risk-*/go:no-automerge vocabulary
tests/router/test_skill_prose_enforcement_coverage.py already hard-gates.

Motivated by work-queue brief `20260810-111717`, itself a followup to
`docs/specs/research/skill-prose-enforcement-coverage-design.md` (PR #287).
That design note prototyped this exact generic pairing -- an emphatic mandate
cue (`mandatory`, `MUST`, `immediately run`, `never skip`) plus a
backtick-quoted named script/callable -- as the *primary, hard-gating*
detection mechanism and rejected it on evidence: near-zero recall at safe
precision, and one confirmed false pairing in
`skills/worktrail-go/SKILL.md`'s Phase-8 paragraph (pairs "mandatory," about
`pre_pr_gate.py`, with an unrelated later mention of
`worktrail-reconcile-pr-labels`).

At WARN/advisory severity that same imprecision is an acceptable cost: a
spurious candidate is triage noise for a human during Route J review, not a
blocked PR. Candidates whose paragraph already mentions the closed
go:risk-*/go:no-automerge vocabulary (`LABEL_FAMILY_MARKERS`) are excluded --
that family is already hard-gated elsewhere, so re-flagging it here
(including the false pairing above, which mentions that exact vocabulary)
would just be duplicate noise.

This does not catch a brand-new corrective-action family the *first* time it
appears without a mandate cue + backtick action in the same paragraph, and it
does not verify a flagged paragraph's claim is actually false -- it is a
recall aid for a human reviewer, not a proof. Promoting a genuinely
recurring family out of "advisory candidate" and into a hard gate means
adding its markers to `LABEL_FAMILY_MARKERS`
(src/worktrail/router/label_family_markers.py) with a registered
`FILE_CONSUMERS` proof, per the design note above.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .label_family_markers import LABEL_FAMILY_MARKERS

SKILLS_ROOT_DEFAULT = Path(__file__).resolve().parents[3] / "skills"

# Emphatic mandate cues, taken verbatim from the brief's own vocabulary.
# Case-sensitive: an ordinary lowercase "must" in prose is not the same
# signal as this codebase's emphatic "MUST" convention for binding
# instructions.
MANDATE_CUES = ("mandatory", "MUST", "immediately run", "never skip")

_BACKTICK_RE = re.compile(r"`([^`]+)`")
_CALLABLE_CORE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _looks_like_named_action(token: str) -> bool:
    """A backtick-quoted token that plausibly names a script or callable --
    not an arbitrary code span such as a variable, a route letter, or a
    filename this paragraph merely references in passing."""
    if re.search(r"\.(py|sh)\b", token):
        return True
    if token.startswith("worktrail-"):
        return True
    core = token.removesuffix("()")
    return bool(_CALLABLE_CORE_RE.match(core) and "_" in core)


def _split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def scan(skills_root: Path = SKILLS_ROOT_DEFAULT) -> dict[str, Any]:
    """Scan every `skills/**/*.md` paragraph for an emphatic mandate cue
    paired with a named corrective action, excluding paragraphs already
    covered by the closed go:risk-*/go:no-automerge vocabulary.

    Returns `{"candidates": [...], "files_scanned": int}`. Each candidate is
    `{"file": <path relative to skills_root>, "cue": <matched mandate cue>,
    "action": <matched backtick token>, "excerpt": <paragraph, truncated>}`.

    Pure text extraction -- never raises, never touches git or the network.
    """
    skills_root = Path(skills_root)
    candidates: list[dict[str, Any]] = []
    files_scanned = 0

    for path in sorted(skills_root.rglob("*.md")):
        files_scanned += 1
        text = path.read_text()
        rel = str(path.relative_to(skills_root))
        for paragraph in _split_paragraphs(text):
            if any(marker in paragraph for marker in LABEL_FAMILY_MARKERS):
                continue  # already hard-gated elsewhere; not a new candidate
            cue = next((c for c in MANDATE_CUES if c in paragraph), None)
            if cue is None:
                continue
            action = next(
                (
                    t
                    for t in _BACKTICK_RE.findall(paragraph)
                    if _looks_like_named_action(t)
                ),
                None,
            )
            if action is None:
                continue
            excerpt = " ".join(paragraph.strip().split())
            if len(excerpt) > 240:
                excerpt = excerpt[:237] + "..."
            candidates.append(
                {"file": rel, "cue": cue, "action": action, "excerpt": excerpt}
            )

    return {"candidates": candidates, "files_scanned": files_scanned}


def _format_human(res: dict[str, Any]) -> str:
    candidates = res["candidates"]
    if not candidates:
        return f"no candidates ({res['files_scanned']} file(s) scanned) -- nothing to triage"
    lines = [
        f"{len(candidates)} candidate(s) for human triage "
        f"({res['files_scanned']} file(s) scanned) -- advisory only, not a CI failure:"
    ]
    for c in candidates:
        lines.append(f"  {c['file']}  cue={c['cue']!r}  action=`{c['action']}`")
        lines.append(f"    {c['excerpt']}")
    lines.append(
        "  -> for a genuinely new recurring corrective-action family, register it in "
        "LABEL_FAMILY_MARKERS (src/worktrail/router/label_family_markers.py) with a "
        "FILE_CONSUMERS proof -- see "
        "docs/specs/research/skill-prose-enforcement-coverage-design.md"
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--skills-root",
        default=None,
        help="skills/ directory to scan (default: this repo's own skills/)",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    root = Path(args.skills_root) if args.skills_root else SKILLS_ROOT_DEFAULT
    res = scan(root)

    if args.json:
        print(json.dumps(res))
    else:
        print(_format_human(res))

    # Always 0: advisory-only by design (brief 20260810-111717) -- this
    # surfaces candidates for human triage during Route J review and must
    # never fail a build the way the closed-vocabulary hard gate
    # (test_skill_prose_enforcement_coverage.py) does.
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
