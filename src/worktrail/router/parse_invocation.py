#!/usr/bin/env python3
"""
parse_invocation: the front door's invocation grammar, as code.

The `worktrail-go` skill's Phase 1 ("Classify the Invocation") is the single place
front-door argument parsing happens, but until now it existed only as an ordered
list of prose bullets in `skills/worktrail-go/SKILL.md`. Every other command a
SKILL.md issues is a console script from this package -- the grammar was the one
exception, which is why it drifted: `fix <request>` is advertised in
`skills/worktrail-help/SKILL.md` and matched by no bullet at all, and the
`/handoff consume` spelling outlived the skill rename that made it unresolvable.
Prose cannot be unit-tested, so nothing caught either one.

This module encodes that same ordered grammar so it can be. It deliberately
reproduces the CURRENT precedence rather than improving it -- a regularized
noun-then-verb grammar is a separate, later change, and it is only safe to make
once the existing forms are pinned by tests.

Precedence, matching `SKILL.md`'s bullet order exactly:

    1. (empty)         -> dashboard
    2. help            -> delegate to worktrail-help, never render the dashboard
    3. drain           -> `drain [max-items] [repo]`
    4. auto            -> modifier; combinable with a leading repo (`/go REPO auto`)
    5. route:<A-J>     -> explicit route override
    6. v1 intent       -> new | implement | continue | pr | brainstorm
    7. handoff:<id>    -> explicit brief id
    8. bare integer    -> Level-2 picker index only; standalone it is free text
    9. bare/prefix id  -> resolved against queue/ (same resolution `claim` uses)
   10. free text       -> classified downstream by classify.py

A leading token that names a known repo is lifted out as `repo` before the rest
is classified, which is how `/go REPO auto` and `/go REPO implement spec X` work
today. Repo names are injected by the caller (`--repos`) rather than discovered
here: `resolve_repo.py` already owns that lookup, and duplicating it would create
exactly the second implementation this module exists to prevent.

Brief-id resolution is likewise delegated -- to `work_queue.resolve()`, the same
function `claim` uses -- so a bare id can never resolve one way here and another
way at claim time.

The parser never shells out and never writes; it reads `queue/` only when a
folder is supplied.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

# v1 intent keywords, mapped to routes by the executor (see
# skills/worktrail-sdd-workflow/SKILL.md's positional table):
# new -> C+D, implement -> D, continue -> E, pr -> E(pr), brainstorm -> A.
V1_INTENTS: tuple[str, ...] = ("new", "implement", "continue", "pr", "brainstorm")

_ROUTE_RE = re.compile(r"^route:([A-J])$", re.IGNORECASE)
_HANDOFF_RE = re.compile(r"^handoff:(.+)$", re.IGNORECASE)
_INT_RE = re.compile(r"^\d+$")

# Modes are the primary dispatch decision. `repo` and `auto` are modifiers that
# coexist with them -- `/go REPO auto` carries mode="auto" AND repo="REPO".
MODES: tuple[str, ...] = (
    "dashboard",
    "help",
    "drain",
    "auto",
    "route",
    "intent",
    "brief",
    "picker_index",
    "free_text",
)


class Form(NamedTuple):
    """One advertised front-door form.

    `parsed` records whether a precedence bullet actually matches the form, or
    whether it merely reads like one and reaches `classify.py` as free text.
    `fix <request>` is the latter: long advertised in worktrail-help, matched by
    no bullet. Recording that honestly is the point -- the previous table stated
    it alongside genuinely parsed forms, which is how the difference went
    unnoticed.
    """

    syntax: str
    meaning: str
    mode: str
    parsed: bool


# The canonical list of advertised forms, in precedence order so the table
# teaches the grammar rather than just listing it. `worktrail-help`'s reference
# block is generated from this and pinned by
# tests/router/test_help_forms_match_the_parser.
FORMS: tuple[Form, ...] = (
    Form("<front-door>", "dashboard and interactive picker", "dashboard", True),
    Form("<front-door> help", "show this reference", "help", True),
    Form(
        "<front-door> drain [max-items] [repo]",
        "drain multiple items in fresh contexts",
        "drain",
        True,
    ),
    Form("<front-door> auto", "auto-pick one ranked queue brief", "auto", True),
    Form(
        "<front-door> <repo> auto",
        "auto-pick one brief for that repository",
        "auto",
        True,
    ),
    Form("<front-door> route:<A-J>", "force a route", "route", True),
    Form(
        "<front-door> <repo> route:<A-J> <id>",
        "force a route for a repo/spec",
        "route",
        True,
    ),
    Form("<front-door> new <request>", "plan a new feature", "intent", True),
    Form("<front-door> implement spec <id>", "execute a specification", "intent", True),
    Form(
        "<front-door> <repo> implement spec <id>",
        "execute a specification in that repo",
        "intent",
        True,
    ),
    Form("<front-door> continue", "resume in-flight work", "intent", True),
    Form("<front-door> pr", "PR / CI repair", "intent", True),
    Form("<front-door> brainstorm", "idea discovery", "intent", True),
    Form(
        "<front-door> handoff:<id>", "claim or resume a queued handoff", "brief", True
    ),
    Form("<front-door> <brief-id>", "claim or resume a queued handoff", "brief", True),
    Form("<front-door> <repo>", "show active work for a repository", "dashboard", True),
    Form(
        "<front-door> fix <request>",
        "classify and route a defect/request",
        "free_text",
        False,
    ),
    Form(
        "<front-door> <free text>",
        "classify and route any other request",
        "free_text",
        False,
    ),
)

_FOOTNOTE = "* not a parsed form -- reaches the route classifier as free text"


def render_forms() -> str:
    """Render FORMS as the aligned reference block worktrail-help publishes."""
    width = max(len(f.syntax) for f in FORMS) + 2
    lines = []
    for f in FORMS:
        marker = "  " if f.parsed else "* "
        lines.append(f"{f.syntax.ljust(width)}{marker}{f.meaning}".rstrip())
    lines.append("")
    lines.append(_FOOTNOTE)
    return "\n".join(lines)


def _result(raw: str, mode: str, reason: str, **fields: Any) -> dict[str, Any]:
    """Build a complete result dict so every key is always present.

    Callers act on this in shell (`jq`-style field reads); a key that is present
    only for some modes would make every consumer write existence checks.
    """
    out: dict[str, Any] = {
        "raw": raw,
        "mode": mode,
        "repo": None,
        "auto": False,
        "help_topic": None,
        "drain_max_items": None,
        "drain_repo": None,
        "route": None,
        "intent": None,
        "spec": None,
        "brief_id": None,
        "brief_path": None,
        "brief_status": None,
        "brief_candidates": [],
        "picker_index": None,
        "free_text": None,
        "reason": reason,
    }
    out.update(fields)
    return out


def _tokens(raw: str) -> list[str]:
    """Split a raw argument string the way a shell would, tolerating bad quoting.

    A user typing an unbalanced quote should still get a parse rather than a
    traceback -- fall back to whitespace splitting.
    """
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def parse(
    raw: str,
    *,
    known_repos: Sequence[str] = (),
    queue_folder: Path | None = None,
    picker_active: bool = False,
) -> dict[str, Any]:
    """Classify a raw front-door argument string.

    Args:
        raw: Everything the user typed after the front-door command.
        known_repos: Repo names/keywords a leading token may match, from
            `resolve_repo.py`. Empty means no repo lifting is attempted, and a
            repo-looking token falls through to brief-id resolution then free
            text -- the same outcome as today when the repo is unrecognized.
        queue_folder: `queue/` directory. When supplied, a bare or prefix brief
            id is resolved through `work_queue.resolve()`. When omitted, the
            candidate is reported without resolution and the mode stays
            `free_text`, since only resolution can tell the two apart.
        picker_active: True when a Level-2 category picker is open. A bare
            integer is a picker index only then; standalone it is free text
            (SKILL.md's "Bare integer" bullet).

    Returns:
        A dict with every key in the result contract; see module docstring.
    """
    raw = (raw or "").strip()
    if not raw:
        return _result(raw, "dashboard", "no arguments -- orientation dashboard")

    tokens = _tokens(raw)
    if not tokens:
        return _result(raw, "dashboard", "no arguments -- orientation dashboard")

    # A leading known repo name is a scope modifier, not the invocation itself.
    repo: str | None = None
    if known_repos and tokens[0].lower() in {r.lower() for r in known_repos}:
        repo = tokens[0]
        tokens = tokens[1:]
        if not tokens:
            return _result(
                raw, "dashboard", "repo only -- that repo's dashboard", repo=repo
            )

    head = tokens[0].lower()
    rest = tokens[1:]

    # 2. help -- delegate and stop; never fetch or render the dashboard.
    if head == "help":
        topic = " ".join(rest) or None
        return _result(raw, "help", "help requested", repo=repo, help_topic=topic)

    # 3. drain [max-items] [repo]
    if head == "drain":
        max_items: int | None = None
        drain_repo: str | None = None
        remaining = list(rest)
        if remaining and _INT_RE.match(remaining[0]):
            max_items = int(remaining[0])
            remaining = remaining[1:]
        if remaining:
            drain_repo = remaining[0]
        return _result(
            raw,
            "drain",
            "drain requested",
            repo=repo,
            drain_max_items=max_items,
            drain_repo=drain_repo,
        )

    # 4. auto -- a modifier, combinable with a leading repo arg (spec 017).
    if head == "auto":
        return _result(raw, "auto", "auto-pick one ranked brief", repo=repo, auto=True)

    # 5. route:<A-J>, optionally followed by a spec id.
    route_match = _ROUTE_RE.match(tokens[0])
    if route_match:
        return _result(
            raw,
            "route",
            "explicit route override",
            repo=repo,
            route=route_match.group(1).upper(),
            spec=rest[0] if rest else None,
        )

    # 6. v1 intent keywords -- skip classification, map straight to routes.
    if head in V1_INTENTS:
        spec: str | None = None
        remaining = list(rest)
        # `implement spec <id>` carries a literal filler token; `implement <id>`
        # is equally valid and both are documented.
        if remaining and remaining[0].lower() == "spec":
            remaining = remaining[1:]
        if remaining:
            spec = remaining[0]
        return _result(
            raw,
            "intent",
            f"v1 intent keyword: {head}",
            repo=repo,
            intent=head,
            spec=spec,
            free_text=" ".join(rest) or None,
        )

    # 7. handoff:<id> -- explicit brief id.
    handoff_match = _HANDOFF_RE.match(tokens[0])
    if handoff_match:
        candidate = handoff_match.group(1)
        return _resolve_brief(
            raw,
            candidate,
            repo=repo,
            queue_folder=queue_folder,
            reason="explicit handoff:<id>",
            require_match=False,
        )

    # 8. Bare integer -- a picker index only while a picker is open.
    if _INT_RE.match(tokens[0]) and len(tokens) == 1:
        if picker_active:
            return _result(
                raw,
                "picker_index",
                "bare integer with a picker open",
                repo=repo,
                picker_index=int(tokens[0]),
            )
        return _result(
            raw,
            "free_text",
            "bare integer with no picker open -- free text",
            repo=repo,
            free_text=raw,
        )

    # 9. Bare or prefix brief id -- resolved against queue/ before anything else
    #    is done with it. Only a single token can be one.
    if len(tokens) == 1:
        return _resolve_brief(
            raw,
            tokens[0],
            repo=repo,
            queue_folder=queue_folder,
            reason="bare or prefix brief id",
            require_match=True,
        )

    # 10. Free text -- classified downstream by classify.py (Phase 5).
    return _result(
        raw, "free_text", "unstructured request", repo=repo, free_text=" ".join(tokens)
    )


def _resolve_brief(
    raw: str,
    candidate: str,
    *,
    repo: str | None,
    queue_folder: Path | None,
    reason: str,
    require_match: bool,
) -> dict[str, Any]:
    """Resolve a brief-id candidate through `work_queue.resolve()`.

    `require_match` distinguishes the two callers. A bare token is only a brief
    id if it actually resolves -- `none`/`ambiguous` means it was never one, and
    it falls through to free text (SKILL.md's bullet 9). An explicit
    `handoff:<id>` was declared to be a brief id, so a failed resolution is
    reported as such instead of being silently reinterpreted as a request.
    """
    if queue_folder is None:
        # Without the queue we cannot tell a brief id from a request. Report the
        # candidate and let the caller resolve rather than guessing either way.
        mode = "brief" if not require_match else "free_text"
        return _result(
            raw,
            mode,
            f"{reason} (unresolved -- no queue folder supplied)",
            repo=repo,
            brief_id=candidate,
            free_text=raw if mode == "free_text" else None,
        )

    from worktrail.workqueue.work_queue import resolve as _queue_resolve

    res = _queue_resolve(candidate, queue_folder)
    status = res.get("status")
    candidates = res.get("candidates", [])

    if status == "match":
        return _result(
            raw,
            "brief",
            reason,
            repo=repo,
            brief_id=candidate,
            brief_path=candidates[0],
            brief_status=status,
            brief_candidates=candidates,
        )

    if require_match:
        return _result(
            raw,
            "free_text",
            f"{reason} did not resolve ({status}) -- free text",
            repo=repo,
            brief_status=status,
            brief_candidates=candidates,
            free_text=raw,
        )

    return _result(
        raw,
        "brief",
        f"{reason} ({status})",
        repo=repo,
        brief_id=candidate,
        brief_status=status,
        brief_candidates=candidates,
    )


def main(argv: list | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (for testing). Defaults to sys.argv[1:].

    Returns:
        Exit code: 0 always -- an unparseable argument is free text, not an error.
    """
    parser = argparse.ArgumentParser(
        description="Parse a worktrail-go front-door invocation into structured fields",
    )
    parser.add_argument(
        "args",
        nargs="?",
        default="",
        help="Raw argument string as typed after the front-door command",
    )
    parser.add_argument(
        "--repos",
        default="",
        help="Comma-separated known repo names, from resolve_repo.py",
    )
    parser.add_argument(
        "--queue-dir",
        default=None,
        help="Work-queue root; defaults to $WORK_QUEUE_DIR or ~/work-queue",
    )
    parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="Skip brief-id resolution (grammar only, no queue read)",
    )
    parser.add_argument(
        "--picker-active",
        action="store_true",
        help="A Level-2 category picker is open, so a bare integer is an index",
    )
    parser.add_argument(
        "--forms",
        action="store_true",
        help="Print the advertised form reference instead of parsing",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON (default)")
    args = parser.parse_args(argv)

    if args.forms:
        print(render_forms())
        return 0

    queue_folder: Path | None = None
    if not args.no_resolve:
        if args.queue_dir:
            queue_folder = Path(args.queue_dir).expanduser() / "queue"
        else:
            from worktrail.workqueue.work_queue import queue_dir as _queue_dir

            queue_folder = _queue_dir()

    known = [r.strip() for r in args.repos.split(",") if r.strip()]
    result = parse(
        args.args,
        known_repos=known,
        queue_folder=queue_folder,
        picker_active=args.picker_active,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
