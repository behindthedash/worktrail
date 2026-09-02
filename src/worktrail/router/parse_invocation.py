#!/usr/bin/env python3
"""
parse_invocation: the front door's invocation grammar, as code.

The `worktrail-go` skill's Phase 1 ("Classify the Invocation") is the single place
front-door argument parsing happens. It used to be an ordered list of prose
bullets in `skills/worktrail-go/SKILL.md`, which is why it drifted: `fix <request>`
was advertised and matched by no bullet, and `/handoff consume` outlived the
skill rename that made it unresolvable. Prose cannot be unit-tested, so nothing
caught either one. This module is what the skill calls instead.

The grammar is `<front-door> [<repo>] <noun> <verb> [args]`, with three nouns:

    handoff   the work queue        new | list | start <id> | auto | drain [n] [repo]
    spec      spec-driven work      new | implement <id> | continue [<id>] | fix
                                    | explore | route <A-J> [<id>]
    pr        an open pull request  fix

Two bare shortcuts stay outside that shape on purpose, because they are the
two most-typed invocations and carry real muscle memory: a bare `<brief-id>`
(same as `handoff start <id>`) and a bare `<repo>` (that repo's dashboard).
`help` and the empty invocation are the read-only exceptions.

Every pre-noun-verb spelling (`auto`, `drain`, `new`, `implement spec <id>`,
`continue`, `pr`, `brainstorm`, `fix`, `route:<A-J>`, `handoff:<id>`) is still
accepted and rewritten to its canonical form before matching -- see `ALIASES`.
The result's `canonical` field carries the rewritten spelling so a caller can
teach it back.

Precedence:

    1. (empty)               -> dashboard
    2. help [topic]          -> delegate to worktrail-help, never render the dashboard
    3. leading <repo>        -> lifted out as a scope modifier; alone, that repo's dashboard
    4. alias rewrite         -> old spellings become their canonical noun-verb form
    5. <noun> <verb> [args]  -> the grammar; a noun with no recognized verb is a help
                                request for that noun, never free text
    6. bare integer          -> Level-2 picker index only; standalone it is free text
    7. bare/prefix <brief-id>-> resolved against queue/ (same resolution `claim` uses)
    8. free text             -> classified downstream by classify.py

Matching the noun-verb forms ABOVE the old bare intent words matters: `new` is
also the v1 intent keyword for "plan a feature", so `handoff new` must never
reach the intent branch as repo=handoff, intent=new. The alias rewrite runs
first and only ever rewrites a bare leading token, so a spelled-out noun-verb
form is never rewritten a second time.

Free text containing the word "handoff" classifies to Route E at high
confidence in classify.py (a joint-highest-weight signal plus a state boost
whenever the queue is non-empty), so anything that escapes this parser lands
on the wrong route silently. That is why a noun without a verb returns `help`
rather than falling through.

The internal dispatch contract is unchanged: the front door still speaks
`handoff:<id>`, `route:<X>`, and the v1 intent words (`new`, `implement`,
`continue`, `pr`, `brainstorm`) to `worktrail-sdd-workflow`. The `intent` field
therefore carries the executor's vocabulary, not the user's: `spec explore`
yields `intent: brainstorm`, and `spec fix` yields `route: F`, because the
executor has no `fix` intent -- Route F is what `fix` has always meant.

Repo names are injected by the caller (`--repos`) rather than discovered here:
`resolve_repo.py` already owns that lookup, and duplicating it would create
exactly the second implementation this module exists to prevent. Brief-id
resolution is likewise delegated to `work_queue.resolve()`, the same function
`claim` uses, so a bare id can never resolve one way here and another way at
claim time.

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
# These are the executor's vocabulary; the front door translates into them.
V1_INTENTS: tuple[str, ...] = ("new", "implement", "continue", "pr", "brainstorm")

NOUNS: tuple[str, ...] = ("handoff", "spec", "pr")

_ROUTE_LETTER_RE = re.compile(r"^[A-J]$", re.IGNORECASE)
_ROUTE_RE = re.compile(r"^route:([A-J])$", re.IGNORECASE)
_HANDOFF_RE = re.compile(r"^handoff:(.+)$", re.IGNORECASE)
_INT_RE = re.compile(r"^\d+$")

# Modes are the primary dispatch decision. `repo` and `auto` are modifiers that
# coexist with them -- `/go REPO handoff auto` carries mode="auto" AND repo="REPO".
MODES: tuple[str, ...] = (
    "dashboard",
    "help",
    "capture",
    "list",
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

    `parsed` records whether the grammar actually matches the form, or whether
    it merely reads like one and reaches `classify.py` as free text. Only
    `<free text>` itself is the latter now; `fix <request>` used to be, which is
    why the flag exists -- the previous table stated it alongside genuinely
    parsed forms, and that is how the difference went unnoticed.
    """

    syntax: str
    meaning: str
    mode: str
    parsed: bool


class Alias(NamedTuple):
    """An older spelling that is still accepted, and the form it now means."""

    old: str
    new: str


# The canonical list of advertised forms, in precedence order so the table
# teaches the grammar rather than just listing it. `worktrail-help`'s reference
# block is generated from this and pinned by
# tests/router/test_parse_invocation.py::test_help_forms_block_matches_the_parser_registry.
FORMS: tuple[Form, ...] = (
    Form("<front-door>", "dashboard and interactive picker", "dashboard", True),
    Form("<front-door> help [topic]", "show this reference", "help", True),
    Form("<front-door> <repo>", "show active work for a repository", "dashboard", True),
    Form(
        "<front-door> handoff new <focus>",
        "capture a new handoff brief",
        "capture",
        True,
    ),
    Form("<front-door> handoff list", "list queued handoff briefs", "list", True),
    Form(
        "<front-door> handoff start <id>",
        "claim or resume a queued handoff",
        "brief",
        True,
    ),
    Form("<front-door> handoff auto", "auto-pick one ranked queue brief", "auto", True),
    Form(
        "<front-door> handoff drain [max-items] [repo]",
        "drain multiple items in fresh contexts",
        "drain",
        True,
    ),
    Form("<front-door> spec new <request>", "plan a new feature", "intent", True),
    Form("<front-door> spec implement <id>", "execute a specification", "intent", True),
    Form("<front-door> spec continue [<id>]", "resume in-flight work", "intent", True),
    Form("<front-door> spec fix <request>", "defect repair (Route F)", "route", True),
    Form(
        "<front-door> spec explore <idea>", "idea discovery (Route A)", "intent", True
    ),
    Form("<front-door> spec route <A-J> [<id>]", "force a route", "route", True),
    Form("<front-door> pr fix", "PR / CI repair", "intent", True),
    Form(
        "<front-door> <brief-id>",
        "claim or resume a queued handoff (same as handoff start)",
        "brief",
        True,
    ),
    Form(
        "<front-door> <free text>",
        "classify and route any other request",
        "free_text",
        False,
    ),
)

# Older spellings, still accepted. Each is rewritten to its canonical form
# before matching; nothing that worked before this grammar stops working.
ALIASES: tuple[Alias, ...] = (
    Alias("auto", "handoff auto"),
    Alias("drain [max-items] [repo]", "handoff drain [max-items] [repo]"),
    Alias("handoff:<id>", "handoff start <id>"),
    Alias("new <request>", "spec new <request>"),
    Alias("implement [spec] <id>", "spec implement <id>"),
    Alias("continue [<id>]", "spec continue [<id>]"),
    Alias("fix <request>", "spec fix <request>"),
    Alias("brainstorm <idea>", "spec explore <idea>"),
    Alias("spec brainstorm <idea>", "spec explore <idea>"),
    Alias("route:<A-J> [<id>]", "spec route <A-J> [<id>]"),
    Alias("pr", "pr fix"),
)

# Bare leading words rewritten to a noun-verb pair. `pr` is absent because it
# is also a noun; `_canonicalize` handles it explicitly.
_BARE_WORD_ALIASES: dict[str, tuple[str, str]] = {
    "auto": ("handoff", "auto"),
    "drain": ("handoff", "drain"),
    "new": ("spec", "new"),
    "implement": ("spec", "implement"),
    "continue": ("spec", "continue"),
    "fix": ("spec", "fix"),
    "brainstorm": ("spec", "explore"),
}

_FOOTNOTE = "* not a parsed form -- reaches the route classifier as free text"
_REPO_NOTE = (
    "Every <noun> <verb> form takes an optional leading <repo> to scope it, "
    "e.g. <front-door> <repo> spec implement <id>."
)
_ALIAS_HEADING = "Compatibility spellings, still accepted and read as the form shown:"


def render_forms() -> str:
    """Render FORMS and ALIASES as the reference block worktrail-help publishes."""
    width = max(len(f.syntax) for f in FORMS) + 2
    lines = []
    for f in FORMS:
        marker = "  " if f.parsed else "* "
        lines.append(f"{f.syntax.ljust(width)}{marker}{f.meaning}".rstrip())
    lines.append("")
    lines.append(_FOOTNOTE)
    lines.append(_REPO_NOTE)
    lines.append("")
    lines.append(_ALIAS_HEADING)
    alias_width = max(len(a.old) for a in ALIASES) + 2
    for a in ALIASES:
        lines.append(f"<front-door> {a.old.ljust(alias_width)}= <front-door> {a.new}")
    return "\n".join(lines)


def _result(raw: str, mode: str, reason: str, **fields: Any) -> dict[str, Any]:
    """Build a complete result dict so every key is always present.

    Callers act on this in shell (`jq`-style field reads); a key that is present
    only for some modes would make every consumer write existence checks.
    """
    out: dict[str, Any] = {
        "raw": raw,
        "canonical": None,
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


def _canonicalize(tokens: list[str]) -> list[str]:
    """Rewrite an older leading spelling into its canonical noun-verb tokens.

    Only a bare leading token is ever rewritten, so a spelled-out noun-verb form
    passes through untouched and can never be rewritten twice.
    """
    head = tokens[0]
    lower = head.lower()
    rest = tokens[1:]

    if lower in _BARE_WORD_ALIASES:
        return _drop_spec_filler([*_BARE_WORD_ALIASES[lower], *rest])
    if lower == "spec":
        if rest and rest[0].lower() == "brainstorm":
            return ["spec", "explore", *rest[1:]]
        return _drop_spec_filler(tokens)
    if lower == "pr":
        # `pr` is both the old bare intent and the noun; `pr fix` is already canonical.
        if rest and rest[0].lower() == "fix":
            return tokens
        return ["pr", "fix", *rest]
    handoff_match = _HANDOFF_RE.match(head)
    if handoff_match:
        return ["handoff", "start", handoff_match.group(1), *rest]
    route_match = _ROUTE_RE.match(head)
    if route_match:
        return ["spec", "route", route_match.group(1).upper(), *rest]
    return tokens


def _drop_spec_filler(tokens: list[str]) -> list[str]:
    """`implement spec <id>` carried a literal filler token; keep accepting it."""
    if (
        len(tokens) > 2
        and tokens[1].lower() in ("implement", "continue")
        and tokens[2].lower() == "spec"
    ):
        return tokens[:2] + tokens[3:]
    return tokens


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

    # 2. help -- delegate and stop; never fetch or render the dashboard.
    if tokens[0].lower() == "help":
        topic = " ".join(tokens[1:]) or None
        return _result(raw, "help", "help requested", help_topic=topic)

    # 3. A leading known repo name is a scope modifier, not the invocation itself.
    repo: str | None = None
    if known_repos and tokens[0].lower() in {r.lower() for r in known_repos}:
        repo = tokens[0]
        tokens = tokens[1:]
        if not tokens:
            return _result(
                raw, "dashboard", "repo only -- that repo's dashboard", repo=repo
            )

    # 4. Alias rewrite, then 5. the noun-verb grammar.
    tokens = _canonicalize(tokens)
    canonical = " ".join(([repo] if repo else []) + tokens)
    head = tokens[0].lower()
    if head in NOUNS:
        return _parse_noun_verb(
            raw,
            tokens,
            repo=repo,
            canonical=canonical,
            queue_folder=queue_folder,
        )

    # 6. Bare integer -- a picker index only while a picker is open.
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

    # 7. Bare or prefix brief id -- resolved against queue/ before anything else
    #    is done with it. Only a single token can be one.
    if len(tokens) == 1:
        return _resolve_brief(
            raw,
            tokens[0],
            repo=repo,
            canonical=f"{repo + ' ' if repo else ''}handoff start {tokens[0]}",
            queue_folder=queue_folder,
            reason="bare or prefix brief id",
            require_match=True,
        )

    # 8. Free text -- classified downstream by classify.py (Phase 5).
    return _result(
        raw, "free_text", "unstructured request", repo=repo, free_text=" ".join(tokens)
    )


def _parse_noun_verb(
    raw: str,
    tokens: list[str],
    *,
    repo: str | None,
    canonical: str,
    queue_folder: Path | None,
) -> dict[str, Any]:
    """Match `<noun> <verb> [args]` once the tokens are canonical."""
    noun = tokens[0].lower()
    verb = tokens[1].lower() if len(tokens) > 1 else None
    args = tokens[2:]
    text = " ".join(args) or None
    common = {"repo": repo, "canonical": canonical}

    def unknown() -> dict[str, Any]:
        # A noun on its own, or with an unrecognized verb, is a request to be
        # told the verbs -- never free text, which classify.py would route
        # somewhere confident and wrong (module docstring).
        what = f"{noun} {verb}" if verb else noun
        return _result(
            raw,
            "help",
            f"`{what}` is not a form -- showing the {noun} forms",
            repo=repo,
            canonical=canonical,
            help_topic=noun,
        )

    if noun == "handoff":
        if verb == "new":
            return _result(
                raw, "capture", "capture a new handoff brief", free_text=text, **common
            )
        if verb == "list":
            return _result(raw, "list", "list queued handoff briefs", **common)
        if verb == "start" and args:
            return _resolve_brief(
                raw,
                args[0],
                repo=repo,
                canonical=canonical,
                queue_folder=queue_folder,
                reason="explicit handoff start <id>",
                require_match=False,
            )
        if verb == "auto":
            return _result(
                raw, "auto", "auto-pick one ranked brief", auto=True, **common
            )
        if verb == "drain":
            max_items: int | None = None
            drain_repo: str | None = None
            remaining = list(args)
            if remaining and _INT_RE.match(remaining[0]):
                max_items = int(remaining[0])
                remaining = remaining[1:]
            if remaining:
                drain_repo = remaining[0]
            return _result(
                raw,
                "drain",
                "drain requested",
                drain_max_items=max_items,
                drain_repo=drain_repo,
                **common,
            )
        return unknown()

    if noun == "spec":
        if verb in ("new", "explore"):
            intent = "brainstorm" if verb == "explore" else "new"
            return _result(
                raw,
                "intent",
                f"spec {verb} -> v1 intent {intent}",
                intent=intent,
                free_text=text,
                **common,
            )
        if verb in ("implement", "continue"):
            return _result(
                raw,
                "intent",
                f"spec {verb} -> v1 intent {verb}",
                intent=verb,
                spec=args[0] if args else None,
                free_text=text,
                **common,
            )
        if verb == "fix":
            # The executor has no `fix` intent; Route F is what fix has always meant.
            return _result(
                raw, "route", "spec fix -> Route F", route="F", free_text=text, **common
            )
        if verb == "route" and args and _ROUTE_LETTER_RE.match(args[0]):
            return _result(
                raw,
                "route",
                "explicit route override",
                route=args[0].upper(),
                spec=args[1] if len(args) > 1 else None,
                **common,
            )
        return unknown()

    # noun == "pr"
    if verb == "fix":
        return _result(
            raw,
            "intent",
            "pr fix -> v1 intent pr",
            intent="pr",
            free_text=text,
            **common,
        )
    return unknown()


def _resolve_brief(
    raw: str,
    candidate: str,
    *,
    repo: str | None,
    canonical: str,
    queue_folder: Path | None,
    reason: str,
    require_match: bool,
) -> dict[str, Any]:
    """Resolve a brief-id candidate through `work_queue.resolve()`.

    `require_match` distinguishes the two callers. A bare token is only a brief
    id if it actually resolves -- `none`/`ambiguous` means it was never one, and
    it falls through to free text. An explicit `handoff start <id>` was declared
    to be a brief id, so a failed resolution is reported as such instead of
    being silently reinterpreted as a request.
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
            canonical=canonical if mode == "brief" else None,
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
            canonical=canonical,
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
        canonical=canonical,
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
