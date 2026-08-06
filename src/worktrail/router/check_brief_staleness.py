#!/usr/bin/env python3
"""
`/go`'s pre-dispatch brief-staleness guard (the third guard in the
`check_repo_freshness.py` / `check_spec_collision.py` family).

Incident: brief `20260731-204048` described work that `behindthedash/devops`
PR #89 had already delivered -- merged 2026-08-02, while the brief sat in the
queue. Nobody noticed until it was claimed and a worktree, run record, and
dispatch already existed for it, five days after the PR landed. Nothing
looked at the brief's own `created:` timestamp against the repo's commit
history before dispatch; by the time anyone did, the work had been redone
from scratch.

This module answers one question: "did the work this brief describes already
land, since it was captured?" It does so in the same shape as its two
siblings -- pure extraction, then a bounded, best-effort history search, with
every step best-effort and **never raising to its caller**. Any condition
under which the question cannot be answered (non-git path, unreadable or
malformed input, a missing/unparseable `created:` timestamp, a subprocess
timeout, a git failure, or an empty probe set) degrades to `checked: false`
plus a non-null `warning` -- never an exception, never a block. `checked:
false` and `checked: true, matches: []` are deliberately different answers:
the first means the question could not be asked, the second means it was
asked and the brief is clean. Callers MUST treat `checked: false` as "no
signal", never as "no evidence of prior delivery" -- collapsing the two would
let a git failure silently read as "nothing landed" (the exact failure mode
`check_repo_freshness.py`'s docstring warns about for a stale checkout).

`extract_probes()` is pure text extraction -- it consults no repository.
`check()` extracts probes from a brief's focus text and searches the
resolved base branch's history for changes made at or after the brief's
`created:` timestamp. Evidence surfaced here is for a human to judge, never
auto-applied: this module never closes, stamps, or otherwise mutates a
brief -- see `openspec/changes/stale-brief-precheck/design.md`.
"""
from __future__ import annotations

import datetime
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Per-kind cap on the number of probes actually searched. When extraction
# yields more candidates than this, the longest/most distinctive ones are
# kept (see `_cap`) and the drop count is reported rather than silently
# discarding the rest.
PATH_PROBE_CAP = 8
SYMBOL_PROBE_CAP = 8
PR_PROBE_CAP = 8

# Wall-clock budget for a single `git` subprocess invocation. The check must
# stay cheap enough that nobody has to weigh whether to run it -- a hanging
# `git log -S` must not hang the dispatch it's guarding.
SUBPROCESS_TIMEOUT_SECONDS = 5

_LOG_FORMAT = "%h\x1f%ad\x1f%s"


# --- extract_probes(): pure text extraction ------------------------------------

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

# `PR #89`, `PR#89`, `pull #89` (case-insensitive), or `owner/repo#89`.
_PR_RE = re.compile(
    r"(?:[\w.-]+/[\w.-]+#(?P<num1>\d+))"
    r"|(?:\b(?:PR|pull)\s*#(?P<num2>\d+)\b)",
    re.IGNORECASE,
)

_LEADING_PUNCT = "([{\"'"
_TRAILING_PUNCT = ")]}.,;:!?\"'"


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
    return "/" in token or bool(_EXT_RE.search(token))


def _is_symbol_token(token: str) -> bool:
    if not token or "#" in token or "/" in token or _EXT_RE.search(token):
        return False
    return bool(_SYMBOL_RE.match(token))


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
    kinds. Path probes additionally fall back to unquoted path-shaped tokens
    (a `/` separator or a recognized extension); symbol probes do not, since
    an unquoted identifier-shaped word in prose is usually just a word.
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
        elif _is_symbol_token(token):
            if token not in seen_symbols:
                seen_symbols.add(token)
                symbols.append(token)

    text_wo_backticks = _BACKTICK_RE.sub(" ", text)
    for raw in _WORD_RE.findall(text_wo_backticks):
        token = _strip_punct(raw)
        if token and _is_path_token(token) and token not in seen_paths:
            seen_paths.add(token)
            paths.append(token)

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


# --- git plumbing ---------------------------------------------------------------

def _run_git(repo: Path, args: List[str], timeout: int) -> Optional["subprocess.CompletedProcess[str]"]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def resolve_base_ref(repo: Path, base: Optional[str] = None) -> str:
    """Resolve the ref to search history against: `origin/<base>` if it
    exists, else `<base>`, else `HEAD`.

    Preferring the remote-tracking ref means a stale local checkout does not
    blind the check to work that landed upstream but hasn't been pulled --
    the same failure mode `check_repo_freshness.py` warns about. Never
    raises; if nothing verifies, `HEAD` is returned unconditionally as the
    last resort (best-effort -- a subsequent `git log` failure against it is
    handled by the caller).
    """
    repo = Path(repo)
    candidates = [f"origin/{base}", base] if base else []
    candidates.append("HEAD")
    for ref in candidates:
        out = _run_git(repo, ["rev-parse", "--verify", "--quiet", ref], SUBPROCESS_TIMEOUT_SECONDS)
        if out is not None and out.returncode == 0:
            return ref
    return "HEAD"


def _normalize_since(since: Any) -> Optional[str]:
    if since is None:
        return None
    if isinstance(since, (datetime.date, datetime.datetime)):
        return since.isoformat()
    if not isinstance(since, str):
        return None
    value = since.strip()
    if not value:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.datetime.fromisoformat(candidate)
        return value
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            datetime.datetime.strptime(value, fmt)
            return value
        except ValueError:
            continue
    return None


def _parse_log(output: str, probe: str, kind: str) -> List[Dict[str, str]]:
    matches: List[Dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f", 2)
        if len(parts) != 3:
            continue
        sha, date, subject = parts
        matches.append({"sha": sha, "date": date, "subject": subject, "probe": probe, "kind": kind})
    return matches


def _search_probe(repo: Path, base_ref: str, since: str, extra: List[str]) -> Optional[str]:
    out = _run_git(
        repo,
        ["log", base_ref, f"--since={since}", "--date=short", f"--format={_LOG_FORMAT}", *extra],
        SUBPROCESS_TIMEOUT_SECONDS,
    )
    if out is None or out.returncode != 0:
        return None
    return out.stdout


# --- check(): extraction + bounded history search --------------------------------

def check(repo: Path, text: str, since: Any, base: Optional[str] = None) -> Dict[str, object]:
    """Did the work `text` (a brief's focus prose) describes already land on
    `repo`'s base branch, at or after `since`?

    Returns `{"checked": bool, "probes": {...}, "matches": [...],
    "pull_requests": [...], "warning": str|None}`. `probes` is
    `extract_probes()`'s output. `matches` is a list of
    `{"sha", "date", "subject", "probe", "kind"}` per matching commit,
    restricted to commits at or after `since` on the resolved base branch.
    `pull_requests` is reserved for the `gh`-backed merged-PR lookup (a
    later, independently-degradable step); it is always `[]` here.

    Never raises. `checked` is `false` when the question could not be
    answered at all: `repo` is not a git repository, `since` is missing or
    unparseable, or extraction yielded no probes to search (nothing landing
    "before" an empty probe set can be distinguished from nothing existing
    to search for). `checked: true` with an empty `matches` means probes
    were searched and none matched -- a definite, searched-and-clean
    negative, not "unknown".
    """
    repo = Path(repo)
    result: Dict[str, object] = {
        "checked": False,
        "probes": {"paths": [], "symbols": [], "pull_requests": [], "dropped": 0},
        "matches": [],
        "pull_requests": [],
        "warning": None,
    }

    if not (repo / ".git").exists():
        result["warning"] = f"{repo} is not a git repository"
        return result

    since_str = _normalize_since(since)
    if since_str is None:
        result["warning"] = f"missing or unparseable created timestamp: {since!r}"
        return result

    probes = extract_probes(text)
    result["probes"] = probes

    if not probes["paths"] and not probes["symbols"] and not probes["pull_requests"]:
        result["warning"] = "no path, symbol, or pull-request probes extracted from brief text"
        return result

    base_ref = resolve_base_ref(repo, base)

    matches: List[Dict[str, str]] = []
    warnings: List[str] = []

    for probe in probes["paths"]:
        pathspec = probe if "/" in probe else f"**/{probe}"
        out = _search_probe(repo, base_ref, since_str, ["--", pathspec])
        if out is None:
            warnings.append(f"git log timed out or failed for path probe {probe!r}")
            continue
        matches.extend(_parse_log(out, probe, "path"))

    for probe in probes["symbols"]:
        out = _search_probe(repo, base_ref, since_str, [f"-S{probe}", "--"])
        if out is None:
            warnings.append(f"git log -S timed out or failed for symbol probe {probe!r}")
            continue
        matches.extend(_parse_log(out, probe, "symbol"))

    result["checked"] = True
    result["matches"] = matches
    if warnings:
        result["warning"] = "; ".join(warnings)
    return result
