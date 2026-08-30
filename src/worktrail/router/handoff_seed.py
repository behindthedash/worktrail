#!/usr/bin/env python3
"""
`sdd-workflow` conductor -- handoff brief seed-mapper.

Given the path to a single handoff brief (already selected and claimed via the
handoff skill's `work_queue.py`), map its content onto the `go` `new`-pipeline
brainstorm seed. This module is path-agnostic and read-only: it never lists,
resolves, moves, or stamps the queue -- that lifecycle is owned by the handoff
skill's `work_queue.py`, the single shared owner, so the claim/move can never
diverge between consumers.

CLI:

  python3 handoff_seed.py seed PATH [--json]

seed output shape:
  {"feature_idea": str, "constraints": str, "repo": str|null,
   "base_branch": str|null, "focus": str, "suggested_skills": [str, ...],
   "recommended_route": str|null, "change_kind": str|null,
   "target_spec": str|null, "implementation_intent": str, "error": str|null}

Field mapping (this docstring is the canonical statement of it):
  feature_idea  <- focus frontmatter + ## Focus + ## Suggested approach
  constraints   <- ## Discovery context + ## Key artifacts + ## Open questions / blockers
  repo          <- repo frontmatter (null preserved as null)
  base_branch   <- base-branch frontmatter
  focus         <- focus frontmatter (display)
  suggested_skills <- suggested-skills frontmatter (informational; NOT auto-invoked)
  recommended_route <- recommended-route frontmatter, normalized to one of A-J
                       (anything else -> null); feeds classify.py --handoff-route
  implementation_intent <- implementation-intent frontmatter, normalized to
                            requested|planning-only|unknown; missing -> unknown
  change_kind <- change-kind frontmatter, normalized to new|delta|bugfix
  target_spec <- target-spec frontmatter
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import split_frontmatter

# --------------------------------------------------------------------------- #
# Core brief parsing
# --------------------------------------------------------------------------- #


def parse_brief(path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter and ## sections from a brief file.

    Returns {"frontmatter": dict, "sections": dict, "error": str|None}.
    On read failure the "error" key is set; frontmatter and sections are empty.
    Uses yaml.safe_load exclusively (never yaml.load).
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return {"frontmatter": {}, "sections": {}, "error": str(exc)}

    frontmatter, body = split_frontmatter(content)
    return {
        "frontmatter": frontmatter,
        "sections": _parse_sections(body),
        "error": None,
    }


def _parse_sections(body: str) -> dict[str, str]:
    """Split body on `## ` headings; return {heading: content} dict."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)

    if current is not None:
        sections[current] = "\n".join(buf).strip()

    return sections


# --------------------------------------------------------------------------- #
# Seed building
# --------------------------------------------------------------------------- #


def build_seed(path: Path) -> dict[str, Any]:
    """Parse a brief and build the go `new`-pipeline brainstorm seed.

    Mapping (see the module docstring for the full field table):
      feature_idea  <- focus frontmatter + ## Focus + ## Suggested approach
      constraints   <- ## Discovery context + ## Key artifacts + ## Open questions / blockers

    Returns the seed dict; on read error "error" is set and other fields are empty.
    Missing sections / frontmatter fields are treated as empty strings (no crash).
    """
    brief = parse_brief(Path(path))

    if brief["error"]:
        return {
            "feature_idea": "",
            "constraints": "",
            "repo": None,
            "base_branch": None,
            "focus": "",
            "suggested_skills": [],
            "recommended_route": None,
            "change_kind": None,
            "target_spec": None,
            "implementation_intent": "unknown",
            "error": brief["error"],
        }

    fm = brief["frontmatter"]
    sec = brief["sections"]

    focus_fm: str = fm.get("focus") or ""
    focus_body: str = sec.get("Focus", "")
    suggested_approach: str = sec.get("Suggested approach", "")

    discovery_context: str = sec.get("Discovery context", "")
    key_artifacts: str = sec.get("Key artifacts", "")
    open_questions: str = sec.get("Open questions / blockers", "")

    feature_idea = "\n\n".join(
        p for p in [focus_fm, focus_body, suggested_approach] if p
    )
    constraints = "\n\n".join(
        p for p in [discovery_context, key_artifacts, open_questions] if p
    )

    # focus for display: prefer frontmatter, fall back to first line of ## Focus
    focus = focus_fm or (focus_body.splitlines()[0].strip() if focus_body else "")

    raw_skills = fm.get("suggested-skills") or []
    suggested_skills: list[str] = (
        [raw_skills] if isinstance(raw_skills, str) else list(raw_skills)
    )

    # Optional GO v2 routing hint: a single letter A-J; anything else -> null.
    raw_route = str(fm.get("recommended-route") or "").strip().upper()
    recommended_route = raw_route if raw_route in set("ABCDEFGHIJ") else None

    raw_change_kind = str(fm.get("change-kind") or "").strip().lower()
    change_kind = (
        raw_change_kind if raw_change_kind in {"new", "delta", "bugfix"} else None
    )
    target_spec = str(fm.get("target-spec") or "").strip() or None
    raw_intent = str(fm.get("implementation-intent") or "").strip().lower()
    implementation_intent = (
        raw_intent
        if raw_intent in {"requested", "planning-only", "unknown"}
        else "unknown"
    )

    return {
        "feature_idea": feature_idea,
        "constraints": constraints,
        "repo": fm.get("repo"),
        "base_branch": fm.get("base-branch"),
        "focus": focus,
        "suggested_skills": suggested_skills,
        "recommended_route": recommended_route,
        "change_kind": change_kind,
        "target_spec": target_spec,
        "implementation_intent": implementation_intent,
        "error": None,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    # Normalize argv: strip --json from any position so it works both before
    # and after the subcommand (argparse subparsers reset top-level flag defaults).
    raw = list(sys.argv[1:] if argv is None else argv)
    emit_json = "--json" in raw
    argv = [a for a in raw if a != "--json"]

    p = argparse.ArgumentParser(
        description="sdd-workflow conductor handoff brief seed-mapper"
    )

    subs = p.add_subparsers(dest="mode")
    subs.required = True

    sp = subs.add_parser("seed", help="parse a brief and build the brainstorm seed")
    sp.add_argument(
        "path", help="path to the brief .md file (e.g. the claimed picked/ path)"
    )

    args = p.parse_args(argv)

    result = build_seed(Path(args.path))

    if emit_json:
        print(json.dumps(result))
        # Same exit-code contract as the text mode: a failed seed is non-zero
        # so scripted callers don't have to parse `error` to notice.
        return 0 if result.get("error") is None else 1

    err = result.get("error")
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    print(f"focus: {result['focus']}")
    print(f"repo:  {result['repo']}")
    fi = result["feature_idea"]
    print(f"feature_idea: {fi[:80]}{'...' if len(fi) > 80 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
