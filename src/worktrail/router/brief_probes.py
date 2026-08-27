#!/usr/bin/env python3
"""Evidence-probe extraction from free-form prose.

Pure text extraction: given a block of prose (a handoff brief's captured
description, a deferred-work entry), pull out the path, symbol, and
pull-request tokens that prose actually cites, capped per kind.

Extracted from `check_brief_staleness.py` when the brief/change staleness
guards were removed -- the extraction half was never staleness-specific, and
`check_deferred_work_handoff.has_handoff_coverage` still needs it to ask
whether an existing brief already covers a deferred-work entry.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


# Per-kind cap on the number of probes actually searched. When extraction
# yields more candidates than this, the longest/most distinctive ones are
# kept (see `_cap`) and the drop count is reported rather than silently
# discarding the rest.
PATH_PROBE_CAP = 8
SYMBOL_PROBE_CAP = 8
PR_PROBE_CAP = 8

_BACKTICK_RE = re.compile(r"`([^`]+)`")
_WORD_RE = re.compile(r"\S+")

# A path probe qualifies with a `/` separator *or* a 1-10 char extension --
# the motivating case is a bare `prevent-destructive-commands.py`, named
# without a directory, as briefs habitually do.
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,10}$")

# Symbol probes require backticks (see module docstring / design.md): an
# unquoted snake_case word in prose is far more likely to be a phrase than an
# identifier, and a bad symbol probe is an expensive, noisy `git log -S`.
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

# A GNU long-form CLI flag (`--tier-map`, `--json`), admitted as a symbol
# probe whether backtick-quoted or not -- flags are named in prose as
# routinely as in backticks, the same reasoning that dropped the backtick
# requirement for snake_case symbols (see design.md). A flag typically
# appears as a literal string in an `argparse.add_argument()` call, so it is
# well suited to both the `-S` occurrence-count search and the `--grep`
# commit-message search already run for symbol probes.
_FLAG_RE = re.compile(r"^--[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*$")

# `PR #89`, `PR#89`, `pull #89` (case-insensitive), or `owner/repo#89`.
_PR_RE = re.compile(
    r"(?:[\w.-]+/[\w.-]+#(?P<num1>\d+))"
    r"|(?:\b(?:PR|pull)\s*#(?P<num2>\d+)\b)",
    re.IGNORECASE,
)

_LEADING_PUNCT = "([{\"'"
# `(` is stripped from the tail too so a brief's habitual `compile_run_plan()`
# reduces to the bare identifier. Without it the trailing `(` survives
# `)`-stripping, fails `_SYMBOL_RE`, and the most valuable probes in a brief --
# the function names it actually cites -- are silently discarded.
_TRAILING_PUNCT = ")]}.,;:!?\"'("

# Distinguishes a real path/extension from a task id or version number. `1.1`,
# `2.10`, and `2.1/2.2/2.3/2.4` are pervasive in briefs (task and spec ids) and
# all look path-shaped to a naive `/`-or-extension test.
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")

# Abbreviations that are prose, not paths, but are dot-shaped enough to slip
# past `_EXT_RE` (`e.g` -> a bogus `.g` extension) once `_strip_punct` has
# already dropped the trailing period a sentence hung off them.
_PATH_TOKEN_DENYLIST = frozenset({"e.g", "i.e", "etc", "vs", "a.k.a"})


def _strip_punct(token: str) -> str:
    token = token.strip()
    token = token.lstrip(_LEADING_PUNCT)
    token = token.rstrip(_TRAILING_PUNCT)
    return token


def _is_path_token(token: str) -> bool:
    # `#` rules out both a path and a symbol -- it only shows up here as part
    # of a `owner/repo#N` pull-request reference, never a real path/symbol.
    if not token or "#" in token:
        return False
    # Prose blobs and code fragments are not paths. Briefs routinely backtick
    # a call-site list (`needs_compile()/_print_scope_gap_error()`) or a task
    # chain (`2.1->2.2->2.3->2.4`); neither is searchable as a pathspec, and
    # both crowd out real probes under PATH_PROBE_CAP.
    if any(c in token for c in "()<>"):
        return False
    # `e.g.`/`i.e.`/`etc.`/`vs.`/`a.k.a.` are prose, not paths, however a
    # brief happens to punctuate or capitalize them.
    if token.lower() in _PATH_TOKEN_DENYLIST:
        return False
    # An absolute or home-relative path names something outside the repo being
    # searched -- a brief's `Repo: /home/...` line is the usual source. Passing
    # one to `git log -- <abs>` is useless at best and, observed 2026-08-05, an
    # expensive timeout at worst.
    if token.startswith("/") or token.startswith("~"):
        return False
    if "/" in token:
        # A real path has a letter somewhere; `2.1/2.2/2.3/2.4` does not.
        return bool(_HAS_LETTER_RE.search(token))
    ext = _EXT_RE.search(token)
    # A purely numeric "extension" is a task id or version (`1.1`, `2.10`),
    # not a file suffix.
    return bool(ext and _HAS_LETTER_RE.search(ext.group(0)))


def _is_symbol_token(token: str) -> bool:
    if not token or "#" in token or "/" in token or _EXT_RE.search(token):
        return False
    return bool(_SYMBOL_RE.match(token))


# An unquoted token only becomes a symbol probe if it is *distinctively* an
# identifier: snake_case with letters either side of an underscore. Briefs
# captured through `worktrail-handoff --focus` are plain prose with no
# backticks at all (verified 2026-08-05: the brief that motivated this
# fallback contained zero backticks and four real identifiers), so requiring
# backticks made symbol search dead on arrival for the primary capture path.
# `compile_run_plan` is not a phrase; the underscore is what makes that safe
# to assert without the quoting the original design leaned on.
_SNAKE_CASE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+$")


def _is_unquoted_symbol_token(token: str) -> bool:
    return bool(token) and len(token) >= 6 and bool(_SNAKE_CASE_RE.match(token))


def _is_flag_token(token: str) -> bool:
    return bool(token) and bool(_FLAG_RE.match(token))


def _cap(items: List[str], cap: int) -> Tuple[List[str], int]:
    """Keep the `cap`-many longest/most distinctive items, preserving their
    original relative order; report how many were dropped."""
    if len(items) <= cap:
        return items, 0
    kept = set(sorted(items, key=len, reverse=True)[:cap])
    ordered = [i for i in items if i in kept]
    return ordered, len(items) - cap


def extract_probes(text: str) -> Dict[str, Any]:
    """Extract path, symbol, and pull-request Evidence Probes from `text`.

    Purely textual -- consults no repository, never raises. Returns
    `{"paths": [...], "symbols": [...], "pull_requests": [...], "dropped":
    int}`, each list capped at its per-kind constant and deduplicated in
    first-seen order; `dropped` is the total count of candidates the caps
    discarded across all three kinds.

    Backtick-quoted tokens are the high-confidence source for all three
    kinds, but none of the three requires them. Path probes fall back to
    unquoted path-shaped tokens (a `/` separator or a recognized extension);
    symbol probes fall back to unquoted *snake_case* tokens, and to GNU
    long-form CLI-flag tokens (`--tier-map`, `--json`) whether backtick-quoted
    or not, which is narrow enough to keep ordinary prose out while still
    working on the unbackticked briefs `worktrail-handoff --focus` actually
    produces.

    The negative rules carry as much weight as the positive ones: task ids
    and versions (`1.1`, `2.1/2.2/2.3`), absolute paths, and parenthesised
    call-site lists are all path-shaped to a naive test and all crowd real
    probes out of the caps. See `_is_path_token`.
    """
    text = text or ""

    paths: List[str] = []
    symbols: List[str] = []
    seen_paths: set = set()
    seen_symbols: set = set()

    for raw in _BACKTICK_RE.findall(text):
        token = _strip_punct(raw)
        if not token:
            continue
        if _is_path_token(token):
            if token not in seen_paths:
                seen_paths.add(token)
                paths.append(token)
        elif _is_symbol_token(token) or _is_flag_token(token):
            if token not in seen_symbols:
                seen_symbols.add(token)
                symbols.append(token)

    text_wo_backticks = _BACKTICK_RE.sub(" ", text)
    for raw in _WORD_RE.findall(text_wo_backticks):
        token = _strip_punct(raw)
        if not token:
            continue
        if _is_path_token(token):
            if token not in seen_paths:
                seen_paths.add(token)
                paths.append(token)
        elif (_is_unquoted_symbol_token(token) or _is_flag_token(token)) and token not in seen_symbols:
            seen_symbols.add(token)
            symbols.append(token)

    pull_requests: List[str] = []
    seen_prs: set = set()
    for m in _PR_RE.finditer(text):
        num = m.group("num1") or m.group("num2")
        if num and num not in seen_prs:
            seen_prs.add(num)
            pull_requests.append(num)

    paths, dropped_paths = _cap(paths, PATH_PROBE_CAP)
    symbols, dropped_symbols = _cap(symbols, SYMBOL_PROBE_CAP)
    pull_requests, dropped_prs = _cap(pull_requests, PR_PROBE_CAP)

    return {
        "paths": paths,
        "symbols": symbols,
        "pull_requests": pull_requests,
        "dropped": dropped_paths + dropped_symbols + dropped_prs,
    }
