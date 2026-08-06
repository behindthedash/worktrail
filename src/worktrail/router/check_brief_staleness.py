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
import json
import re
import shutil
import subprocess
import time
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

# `gh` calls hit the network, unlike the local `git` calls above -- a wider
# budget avoids treating an ordinary slow API round-trip as a timeout.
GH_SUBPROCESS_TIMEOUT_SECONDS = 8

# Bound on the number of `pull_requests` entries returned, applied both as a
# per-`gh pr list` `--limit` and as a final cap on the combined, deduplicated
# result. `gh pr list --search` matches title/body text against a path probe
# and returns up to 30 hits per call uncapped -- at `PATH_PROBE_CAP` probes
# that is unbounded evidence handed to a human close/proceed decision. The
# most-recently-merged entries are kept; the rest are counted, not silently
# dropped (see `probes["dropped"]` for the analogous probe-side cap).
PR_RESULT_CAP = 20

# Aggregate wall-clock budget for the whole `gh` phase (PR-number resolution
# plus path-based search), separate from `GH_SUBPROCESS_TIMEOUT_SECONDS`
# above which only bounds a single call. Without this, worst case is 1
# `auth status` + `PR_PROBE_CAP` `gh pr view` + `PATH_PROBE_CAP` `gh pr list`
# round-trips, each up to `GH_SUBPROCESS_TIMEOUT_SECONDS` -- unbounded in
# aggregate even though every individual call is bounded. Once the budget is
# exhausted, remaining probes are skipped (never run) and counted in a
# warning; probes already resolved are kept, never discarded.
GH_PHASE_BUDGET_SECONDS = 20

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
# `(` is stripped from the tail too so a brief's habitual `compile_run_plan()`
# reduces to the bare identifier. Without it the trailing `(` survives
# `)`-stripping, fails `_SYMBOL_RE`, and the most valuable probes in a brief --
# the function names it actually cites -- are silently discarded.
_TRAILING_PUNCT = ")]}.,;:!?\"'("

# Distinguishes a real path/extension from a task id or version number. `1.1`,
# `2.10`, and `2.1/2.2/2.3/2.4` are pervasive in briefs (task and spec ids) and
# all look path-shaped to a naive `/`-or-extension test.
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")


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
    symbol probes fall back to unquoted *snake_case* tokens only, which is
    narrow enough to keep ordinary prose out while still working on the
    unbackticked briefs `worktrail-handoff --focus` actually produces.

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
        elif _is_symbol_token(token):
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
        elif _is_unquoted_symbol_token(token) and token not in seen_symbols:
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
    candidates: List[str] = []
    if base:
        candidates.extend([f"origin/{base}", base])
    else:
        # No explicit base: prefer the remote's own default branch over the
        # local HEAD. On a feature branch -- which is exactly where /go runs
        # this check -- HEAD is missing the upstream commits the check exists
        # to find, so falling straight through to HEAD would report a clean
        # brief for work that had already landed on the base branch.
        out = _run_git(
            repo, ["symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD"],
            SUBPROCESS_TIMEOUT_SECONDS,
        )
        if out is not None and out.returncode == 0 and out.stdout.strip():
            candidates.append(out.stdout.strip())
        candidates.extend(["origin/main", "origin/master"])
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


def _to_utc_datetime(value: Any) -> Optional[datetime.datetime]:
    """Parse an ISO-ish timestamp (as produced by `_normalize_since` or a
    `gh --json mergedAt` field) into an aware UTC `datetime`, or `None` if it
    can't be parsed."""
    if not value or not isinstance(value, str):
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt: Optional[datetime.datetime] = None
    try:
        dt = datetime.datetime.fromisoformat(candidate)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


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


# --- gh lookup: independently-degradable final step ------------------------------
#
# Resolves extracted `PR #N` probes and searches merged PRs matching path
# probes, via `gh`. This step is deliberately isolated from the git search
# above: `gh` missing, unauthenticated, erroring, or timing out at any point
# degrades to an empty `pull_requests` list plus a warning, and never
# discards -- or even touches -- the `matches` the git search already
# collected. Results are restricted to PRs merged at or after `since` and
# capped at `PR_RESULT_CAP`, mirroring the git search's `--since` filter and
# the probe-side caps above.

def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _run_gh(repo: Path, args: List[str], timeout: int) -> Optional["subprocess.CompletedProcess[str]"]:
    try:
        return subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=timeout, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _gh_authenticated(repo: Path, timeout: int) -> bool:
    out = _run_gh(repo, ["auth", "status"], timeout)
    return out is not None and out.returncode == 0


def _pr_from_json(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    number = data.get("number")
    if number is None:
        return None
    return {
        "number": number,
        "title": data.get("title", ""),
        "url": data.get("url", ""),
        "merged_at": data.get("mergedAt", ""),
    }


def _resolve_pr_number_probes(
    repo: Path, numbers: List[str], timeout: int, deadline: float
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve extracted `PR #N` / `owner/repo#N` probes via `gh pr view`,
    keeping only PRs that are actually merged. Bare numbers resolve against
    `repo` itself -- `extract_probes()` discards any `owner/repo` qualifier
    -- so a probe naming a different repository silently resolves that
    repo's PR instead; callers are warned when this path yields anything."""
    found: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for i, num in enumerate(numbers):
        if time.monotonic() >= deadline:
            warnings.append(
                f"gh phase budget exceeded; skipped {len(numbers) - i} PR-number probe(s)"
            )
            break
        out = _run_gh(repo, ["pr", "view", num, "--json", "number,title,state,url,mergedAt"], timeout)
        if out is None:
            warnings.append(f"gh pr view timed out or is unavailable for PR #{num}")
            continue
        if out.returncode != 0:
            warnings.append(f"gh pr view failed for PR #{num}: {out.stderr.strip()[:200]}")
            continue
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            warnings.append(f"gh pr view returned unparseable JSON for PR #{num}")
            continue
        if not isinstance(data, dict):
            warnings.append(f"gh pr view returned unexpected JSON shape for PR #{num}")
            continue
        if data.get("state") == "MERGED":
            pr = _pr_from_json(data)
            if pr:
                found.append(pr)
    if found:
        warnings.append(
            f"{len(found)} pull request(s) resolved from bare PR-number references against "
            "the current repository only; if the brief named a different owner/repo, this may "
            "be the wrong repository's PR"
        )
    return found, warnings


def _search_merged_prs_by_path(
    repo: Path, paths: List[str], timeout: int, deadline: float
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Search merged PRs whose title/body mention each path probe, via
    `gh pr list --search`."""
    found: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for i, path in enumerate(paths):
        if time.monotonic() >= deadline:
            warnings.append(
                f"gh phase budget exceeded; skipped {len(paths) - i} path probe(s)"
            )
            break
        out = _run_gh(
            repo,
            ["pr", "list", "--state", "merged", "--search", path,
             "--json", "number,title,url,mergedAt", "--limit", str(PR_RESULT_CAP)],
            timeout,
        )
        if out is None:
            warnings.append(f"gh pr list timed out or is unavailable for path probe {path!r}")
            continue
        if out.returncode != 0:
            warnings.append(f"gh pr list failed for path probe {path!r}: {out.stderr.strip()[:200]}")
            continue
        try:
            items = json.loads(out.stdout)
        except json.JSONDecodeError:
            warnings.append(f"gh pr list returned unparseable JSON for path probe {path!r}")
            continue
        if not isinstance(items, list):
            warnings.append(f"gh pr list returned unexpected JSON shape for path probe {path!r}")
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            pr = _pr_from_json(item)
            if pr:
                found.append(pr)
    return found, warnings


def _lookup_pull_requests(
    repo: Path, probes: Dict[str, Any], since: str, timeout: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """The `gh` lookup itself: resolve PR-number probes and search merged PRs
    matching path probes. Never raises; any failure yields `([], warning)`.

    Results merged before `since` are excluded -- the same restriction the
    git search applies via `--since` -- so a PR that merged long before the
    brief was even written can't be surfaced as evidence the brief's work
    already landed; a count of excluded entries is warned, never silently
    dropped. A resolved PR whose merge date can't be parsed is kept rather
    than excluded, since a human should see it rather than have it vanish
    indistinguishably from "nothing found". The combined, deduplicated
    result is then capped at `PR_RESULT_CAP`, keeping the most-recently-
    merged entries (undated entries sort last). The whole phase is bounded
    by `GH_PHASE_BUDGET_SECONDS`; probes not yet run when the budget is
    exhausted are skipped and counted in a warning, not silently omitted.
    """
    numbers = probes.get("pull_requests") or []
    paths = probes.get("paths") or []
    if not numbers and not paths:
        return [], None

    if not _gh_available():
        return [], "gh not found on PATH; skipping pull-request lookup"
    if not _gh_authenticated(repo, timeout):
        return [], "gh is not authenticated; skipping pull-request lookup"

    deadline = time.monotonic() + GH_PHASE_BUDGET_SECONDS
    found, warnings = _resolve_pr_number_probes(repo, numbers, timeout, deadline)
    path_found, path_warnings = _search_merged_prs_by_path(repo, paths, timeout, deadline)
    warnings.extend(path_warnings)

    seen_numbers = {pr["number"] for pr in found}
    for pr in path_found:
        if pr["number"] not in seen_numbers:
            seen_numbers.add(pr["number"])
            found.append(pr)

    since_dt = _to_utc_datetime(since)
    if since_dt is not None:
        kept: List[Dict[str, Any]] = []
        undated: List[Dict[str, Any]] = []
        excluded_before_since = 0
        for pr in found:
            merged_dt = _to_utc_datetime(pr.get("merged_at"))
            if merged_dt is None:
                undated.append(pr)
            elif merged_dt >= since_dt:
                kept.append(pr)
            else:
                excluded_before_since += 1
        found = kept + undated
        if excluded_before_since:
            warnings.append(
                f"{excluded_before_since} resolved pull request(s) merged before {since} "
                "were excluded"
            )
        if undated:
            warnings.append(
                f"{len(undated)} resolved pull request(s) had no parseable merge date and "
                "could not be checked against since -- included anyway"
            )

    found.sort(key=lambda pr: _to_utc_datetime(pr.get("merged_at")) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    if len(found) > PR_RESULT_CAP:
        dropped = len(found) - PR_RESULT_CAP
        found = found[:PR_RESULT_CAP]
        warnings.append(f"{dropped} additional merged pull request(s) exceeded PR_RESULT_CAP={PR_RESULT_CAP} and were dropped")

    return found, ("; ".join(warnings) if warnings else None)


# --- check(): extraction + bounded history search --------------------------------

def check(repo: Path, text: str, since: Any, base: Optional[str] = None) -> Dict[str, object]:
    """Did the work `text` (a brief's focus prose) describes already land on
    `repo`'s base branch, at or after `since`?

    Returns `{"checked": bool, "probes": {...}, "matches": [...],
    "pull_requests": [...], "warning": str|None}`. `probes` is
    `extract_probes()`'s output. `matches` is a list of
    `{"sha", "date", "subject", "probe", "kind"}` per matching commit,
    restricted to commits at or after `since` on the resolved base branch.
    `pull_requests` is the `gh`-backed lookup: extracted PR-number probes
    resolved via `gh pr view` (kept only if merged) plus merged PRs found by
    searching for path probes via `gh pr list --search`, deduplicated by PR
    number, restricted to PRs merged at or after `since` (the same "since it
    was captured" restriction the git search applies), and capped at
    `PR_RESULT_CAP` (most-recently-merged kept). That lookup is independently
    degradable -- `gh` missing, unauthenticated, erroring, or timing out
    yields `pull_requests: []` plus a warning appended alongside any git-side
    warning, without discarding `matches`.

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
        # `:(glob)` magic is required, not decoration: with git's *default*
        # pathspec matching, `**` must consume at least one path component, so
        # a plain `**/widget.py` matches `src/widget.py` but never a
        # repo-root `widget.py` -- silently missing exactly the bare-filename
        # case this probe kind exists for. Under `:(glob)`, `**/` matches zero
        # or more components and both locations are found.
        pathspec = probe if "/" in probe else f":(glob)**/{probe}"
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

    # Commit-message search, complementing `-S` above. `-S` only sees commits
    # that changed a symbol's occurrence count, so a commit that moved, wrapped,
    # or merely *described* the work can be invisible to it while naming the
    # symbol plainly in its subject -- e.g. "fix(conductor): deterministic
    # same-file ordering repair in apply_to_tasks()". Deduplicated against the
    # `-S` hits so a commit found both ways is reported once.
    seen_message_hits = {(m["sha"], m["probe"]) for m in matches}
    for probe in probes["symbols"]:
        out = _search_probe(repo, base_ref, since_str, [f"--grep={probe}", "--"])
        if out is None:
            warnings.append(f"git log --grep timed out or failed for symbol probe {probe!r}")
            continue
        for hit in _parse_log(out, probe, "message"):
            if (hit["sha"], hit["probe"]) not in seen_message_hits:
                seen_message_hits.add((hit["sha"], hit["probe"]))
                matches.append(hit)

    result["checked"] = True
    result["matches"] = matches
    if warnings:
        result["warning"] = "; ".join(warnings)

    pull_requests, gh_warning = _lookup_pull_requests(repo, probes, since_str, GH_SUBPROCESS_TIMEOUT_SECONDS)
    result["pull_requests"] = pull_requests
    if gh_warning:
        result["warning"] = f"{result['warning']}; {gh_warning}" if result["warning"] else gh_warning

    return result


# --- CLI ------------------------------------------------------------------------

def _read_brief(path: Path) -> Tuple[Optional[str], Any, Optional[str]]:
    """Pull `(focus_text, created, error)` off a brief file.

    Reuses `handoff_seed.build_seed` for the focus text so the CLI reads a
    brief exactly the way the rest of the dispatch path does (focus
    frontmatter + `## Focus` + `## Suggested approach`), rather than
    hand-rolling a second, subtly different parse. `created:` is not part of
    the seed contract, so it comes from `read_frontmatter` directly.
    """
    try:
        from .handoff_seed import build_seed
        from ..shared.brief_frontmatter import read_frontmatter
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        return None, None, f"could not import brief readers: {exc!r}"

    try:
        seed = build_seed(path)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        return None, None, f"could not parse brief {path}: {exc!r}"
    if seed.get("error"):
        return None, None, f"could not read brief {path}: {seed['error']}"

    try:
        fm = read_frontmatter(path)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        return seed.get("feature_idea") or seed.get("focus") or "", None, \
            f"could not read frontmatter of {path}: {exc!r}"

    text = seed.get("feature_idea") or seed.get("focus") or ""
    return text, fm.get("created"), None


def _format_human(res: Dict[str, object]) -> str:
    if not res["checked"]:
        return f"unknown: {res.get('warning') or 'staleness could not be determined'}"

    raw_matches = res["matches"]
    raw_prs = res["pull_requests"]
    matches: List[Dict[str, Any]] = list(raw_matches) if isinstance(raw_matches, list) else []
    prs: List[Dict[str, Any]] = list(raw_prs) if isinstance(raw_prs, list) else []
    if not matches and not prs:
        line = "no evidence: probes searched, nothing landed since the brief was captured"
        if res.get("warning"):
            line += f"\n  warning: {res['warning']}"
        return line

    lines = [f"EVIDENCE: {len(matches)} commit(s), {len(prs)} merged pull request(s)"]
    for m in matches:
        lines.append(f"  {m['sha']}  {m['date']}  {m['subject']}   [{m['kind']} probe: {m['probe']}]")
    for pr in prs:
        lines.append(f"  PR #{pr['number']}  {pr.get('merged_at') or '?'}  {pr.get('title') or ''}")
    lines.append("  -> surface these to the operator; never close the brief on this signal alone")
    if res.get("warning"):
        lines.append(f"  warning: {res['warning']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", default=None, help="brief focus prose to extract probes from")
    src.add_argument(
        "--brief", default=None,
        help="path to a brief .md file; focus text and `created:` are read from it",
    )
    p.add_argument(
        "--since", default=None,
        help="override the search-window start (defaults to the brief's `created:`)",
    )
    p.add_argument("--base", default=None, help="base branch to search (default: auto-resolved)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    text = args.text
    since: Any = args.since
    read_error: Optional[str] = None

    if args.brief:
        text, created, read_error = _read_brief(Path(args.brief))
        if since is None:
            since = created

    if read_error and text is None:
        res: Dict[str, object] = {
            "checked": False,
            "probes": {"paths": [], "symbols": [], "pull_requests": [], "dropped": 0},
            "matches": [],
            "pull_requests": [],
            "warning": read_error,
        }
    else:
        res = check(Path(args.repo), text or "", since, base=args.base)
        if read_error:
            res["warning"] = f"{res['warning']}; {read_error}" if res.get("warning") else read_error

    if args.json:
        print(json.dumps(res))
    else:
        print(_format_human(res))

    # Always 0: this is a signal source for a human decision, not a gate. A
    # non-zero exit here would turn "could not determine" into a dispatch
    # failure, which is precisely the fail-open contract this module exists
    # to honor.
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
