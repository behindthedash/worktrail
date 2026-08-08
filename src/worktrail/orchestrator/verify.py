#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- post-PR verify stage + gated cleanup.

`finish_real()` (integrate.py) opens one PR per group and stops. That left two
gaps in the real-repo path: (1) PR-vs-base conflicts and CI status were never
checked, and (2) every task worktree + branch leaked (nothing was ever torn
down). This module closes both.

For each group PR, in dependency order (base group before its dependents; a base
group that fails verification quarantines its dependents -- mirroring the
`depends_on quarantined` logic in finish_real):

  1. MERGEABILITY -- `gh pr view --json mergeable,mergeStateStatus`. If the base
     advanced and the PR is CONFLICTING, spawn a `resolve` worker in the group's
     worktree to merge origin/<base>, push, and re-check (bounded by strikes).
  2. CI -- BLOCK on `gh pr view --json statusCheckRollup` until checks finish
     (policy: no detach; log progress so it is visibly waiting). Red -> capture
     the failing-run log, spawn a `ci-fix` worker in the group worktree, push,
     re-poll. Bounded by the same 3-strikes budget; still red -> quarantine,
     KEEP the worktree, report the failing check + log tail.
  3. CLEANUP GATE -- only groups that reach green + mergeable are AUTO-MERGED
     (locked policy 2026-05-30: personal/sandbox repos, no human gate) and then
     have their task worktrees + branches removed. Quarantined / escalated groups
     keep everything for inspection.

Every external effect goes through two injected callables so the control flow is
unit-testable with a mocked gh/git and a mocked worker:

    run(cmd)            -> CompletedProcess-like (.returncode/.stdout/.stderr)
    spawn(prompt, wt)   -> the worker's final message (report-back text)

INFERENCE (not verifiable in this sandbox): the live GitHub behaviors this relies
on -- `mergeable`/`mergeStateStatus`/`statusCheckRollup` JSON shapes, and GitHub
auto-retargeting a child PR to its base when the parent PR merges -- are taken
from GitHub's documented behavior. Parsing is kept defensive and mergeability is
re-checked per group so a retargeted child PR is reconciled by the resolve loop.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import dispatch
from . import spawnlib
from . import worktree
from ..taskformats import resolve as taskformats


class _LazyAutomergePreflight:
    """Attribute-transparent stand-in for `worktrail.router.automerge_preflight`
    (moved from the original external implementation in a later extraction sub-phase within this same
    package). Resolves the real module on first attribute access rather than
    at `verify.py` import time, so `verify.py` stays importable (and its own
    non-automerge test suite runnable) in a partial install where
    `worktrail.router` isn't present yet -- while still supporting the
    existing `verify.automerge_preflight.<attr> = ...` monkeypatch convention
    the test suite already uses (an assigned attribute shadows the proxy
    lookup normally, no `__setattr__` override needed).

    Callers must treat "module unavailable" the same as a transient preflight
    query error -- see `auto_merge()` -- never as a confirmed-safe-to-merge
    state.
    """

    def __getattr__(self, name):
        try:
            from ..router import automerge_preflight as _mod
        except ImportError as exc:
            raise ImportError(
                "worktrail.router.automerge_preflight is not installed "
                "(part of the router extraction sub-phase)"
            ) from exc
        return getattr(_mod, name)


automerge_preflight = _LazyAutomergePreflight()

# Check classification (GraphQL statusCheckRollup). CheckRun carries status +
# conclusion; legacy StatusContext carries state.
_PENDING_CHECK_STATUS = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}
_FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED",
                     "STARTUP_FAILURE", "STALE", "ERROR"}
_PENDING_CONTEXT_STATE = {"PENDING", "EXPECTED"}
_FAIL_CONTEXT_STATE = {"FAILURE", "ERROR"}

DEFAULT_MODEL = "sonnet"
# ci-fix workers cap at 900s: if the root cause isn't found in 15 min, a 3rd
# attempt won't help. resolve workers use the caller-injected timeout (default 1800s).
CI_FIX_TIMEOUT = 900

# Substrings that signal a branch-protection merge block (not a transient error).
# On these, retry with --auto so GitHub queues the merge for when requirements are met.
_BRANCH_PROTECTION_SIGNALS = (
    "policy prohibit",
    "required review",
    "branch protection",
    "waiting for required approvals",
    "required approvals",
)

# Substrings that signal the --auto call was rejected because the chosen merge method
# is not allowed by enablePullRequestAutoMerge (e.g., repo allows squash but not merge
# commits, yet _detect_merge_method returned "merge" from repo settings). On these,
# retry --auto with each remaining method before quarantining.
_AUTO_MERGE_METHOD_SIGNALS = (
    "enablepullrequestautomerge",
    "merge commits are not allowed",
    "merge method",
)

# The declared human-merge-gate label, stamped at PR creation time by
# pre_pr_gate based on policy risk tier (policy.automerge_eligible()).
# auto_merge() must never arm `gh pr merge` itself while this label is
# present, on ANY of its internal paths (brief
# 20260723-174500-verify-automerge-fallback-bypasses-policy).
_NO_AUTOMERGE_LABEL = "go:no-automerge"

# Transient gh/network failures should not quarantine a healthy group on the first
# blip: pr_status retries a failed `gh pr view` this many times before giving up.
GH_RETRIES = 3

# Same env var `_run_integration_smoke` (integrate.py) honors, so one knob tunes
# both the pre-PR and post-merge smoke timeouts for the common case of reusing
# the same command for both.
POST_MERGE_SMOKE_TIMEOUT = int(os.environ.get("ORCH_SMOKE_TIMEOUT", "1800"))

# Structural backstop for dispatch.py's prompt-level "Hard rules" (resolve/ci-fix/
# assembly-resolve workers must not touch shared CI config or orchestrator/spec
# state as a side effect of their fix). A prompt instruction alone can be
# rationalized around under pressure; this is the deterministic check against the
# worker's actual pushed diff, covering both existing soft rules with one mechanism.
FORBIDDEN_WORKER_PATH_PREFIXES = (
    ".github/workflows/",
    "docs/specs/",
    "openspec/",
    ".specify/",
)


def forbidden_prefixes_for(spec_path: "str | None" = None) -> tuple:
    """Deny-list prefixes for the format this run is driving.

    A guard naming the wrong spec root is not a weaker guard, it is no guard:
    an OpenSpec run checked against `docs/specs/` would let a worker rewrite
    `openspec/**` and report clean. Falls back to the module constant when no
    spec path is known, so callers predating the seam keep today's behavior.
    """
    if not spec_path:
        return FORBIDDEN_WORKER_PATH_PREFIXES
    return (".github/workflows/", taskformats.spec_root_prefix_for(spec_path))


# --------------------------------------------------------------------------- #
# Default (live) effect runners -- replaced by fakes in tests
# --------------------------------------------------------------------------- #
def _real_run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _make_live_spawn(model: str = DEFAULT_MODEL,
                     timeout: int = 1800,
                     agent: str = "claude") -> Callable[[str, Path], str]:
    """A spawn that drives a headless agent worker in the worktree, with
    bounded retry on a transient (non-zero/empty) spawn failure (spawnlib).

    --setting-sources project,local excludes the operator's USER-level
    ~/.claude/settings.json (and its Stop hook) from these group-level
    resolve/ci-fix/assembly-resolve workers, the same defect class fixed for
    task-level workers in live.py's _LEAN_WORKER_FLAGS (investigation
    20260711-130900, PR #252). No --tools restriction: build_group_prompt's
    resolve/ci-fix instructions (git fetch/merge/push, run tests/build) all
    run through Bash, which the task-level lean set already includes, so a
    narrower --tools list would only add risk without saving anything here.
    """
    def spawn(prompt: str, worktree_path: Path) -> str:
        extra_args = ["--setting-sources", "project,local"] if agent == "claude" else []
        return spawnlib.spawn_agent(
            prompt, worktree_path, agent=agent, model=model, timeout=timeout,
            extra_args=extra_args,
            log=lambda m: print(m, file=sys.stderr)).text
    return spawn


# --------------------------------------------------------------------------- #
# Check classification (pure)
# --------------------------------------------------------------------------- #
# Checks that must never gate a merge: explicitly non-required checks, or any
# check whose name is tagged informational (e.g. "E2E (informational)").
_INFORMATIONAL_NAME_MARKERS = ("informational", "non-blocking", "optional")


def _is_informational(check: Dict[str, Any], name: str) -> bool:
    if check.get("isRequired") is False:
        return True
    low = name.lower()
    return any(m in low for m in _INFORMATIONAL_NAME_MARKERS)


def classify_checks(
    rollup: Optional[List[Dict[str, Any]]],
    required: Optional[Iterable[str]] = None,
) -> Tuple[bool, List[str]]:
    """Return (any_pending, [failing check names]) for a statusCheckRollup list.

    A null/empty rollup with no `required` names means no required checks ->
    (False, []) -> treated as green (nothing to wait on). When `required` is
    given (the branch ruleset's required status check contexts), any of those
    names still absent from `rollup` counts as pending too -- a required check
    that has not yet been scheduled/reported must never be read as "nothing to
    wait on" just because the rollup is currently empty or partial.
    """
    pending = False
    failing: List[str] = []
    seen: set = set()
    for c in rollup or []:
        name = c.get("name") or c.get("context") or "(check)"
        seen.add(name)
        if _is_informational(c, name):                  # never gates a merge
            continue
        if "conclusion" in c or "status" in c:          # CheckRun
            status = (c.get("status") or "").upper()
            conclusion = (c.get("conclusion") or "").upper()
            if status in _PENDING_CHECK_STATUS:
                pending = True
                continue
            if conclusion in _FAIL_CONCLUSIONS:
                failing.append(name)
        else:                                            # StatusContext (legacy)
            state = (c.get("state") or "").upper()
            if state in _PENDING_CONTEXT_STATE:
                pending = True
            elif state in _FAIL_CONTEXT_STATE:
                failing.append(name)
    if required and any(name not in seen for name in required):
        pending = True
    return pending, failing


# --------------------------------------------------------------------------- #
# Verifier
# --------------------------------------------------------------------------- #
class Verifier:
    def __init__(self, repo: Path, remote: str, base: str, spec_id: str, *,
                 run: Optional[Callable[[List[str]], Any]] = None,
                 spawn: Optional[Callable[[str, Path], str]] = None,
                 ci_fix_spawn: Optional[Callable[[str, Path], str]] = None,
                 log: Callable[[str], None] = print,
                 sleep: Callable[[float], None] = time.sleep,
                 worktree_base: Optional[Path] = None,
                 max_strikes: int = dispatch.MAX_REVIEW_RETRIES,
                 poll_interval: float = 10.0,
                 poll_interval_max: float = 60.0,
                 max_polls: int = 360,
                 git_lock: Optional[threading.Lock] = None,
                 merge_method: Optional[str] = None,
                 spec_rel: Optional[str] = None,
                 declared_files: Optional[Dict[str, List[str]]] = None,
                 post_merge_smoke_cmd: Optional[str] = None,
                 post_merge_smoke: Optional[
                     Callable[[str, Path], Tuple[bool, str]]] = None,
                 merge_lock: Optional[threading.Lock] = None,
                 cumulative_regression: Optional[Dict[str, str]] = None) -> None:
        self.repo = Path(repo).resolve()
        self.remote = remote
        self.base = base
        self.spec_id = spec_id
        # Repo-relative path of the spec/change this run drives. Optional so
        # existing callers are unaffected; when absent the deny-list falls back
        # to the devkit prefix, which is today's behavior.
        self.spec_rel = spec_rel
        # {group name: files its tasks declare}. Lets the deny-list tell a
        # deliverable apart from an out-of-scope edit; absent -> no carve-out,
        # which is the pre-existing behavior.
        self.declared_files = declared_files or {}
        self.run = run or _real_run
        self.spawn = spawn or _make_live_spawn()
        # ci-fix workers use a shorter timeout and default to sonnet. When a test
        # injects `spawn`, reuse it so tests remain role-agnostic.
        self._ci_fix_spawn = (ci_fix_spawn or spawn
                              or _make_live_spawn(DEFAULT_MODEL, CI_FIX_TIMEOUT))
        self.log = log
        self.sleep = sleep
        self.worktree_base = (Path(worktree_base) if worktree_base
                              else worktree.default_worktree_base(self.repo))
        self.max_strikes = max_strikes
        self.poll_interval = poll_interval
        self.poll_interval_max = poll_interval_max
        self.max_polls = max_polls
        self.gh_repo = self._derive_gh_repo()
        # Memoized required-status-check contexts for `self.base` (see
        # `_required_check_names`). Fetched at most once per Verifier -- every
        # group in a run shares the same base branch, so there is nothing to
        # gain from re-querying per group or per poll.
        self._required_check_names_fetched = False
        self._required_check_names_cache: Optional[List[str]] = None
        # WorktreeManager routed through the SAME injected runner, so live runs
        # use worktree.remove()/prune() and tests stay hermetic.
        self.wm = worktree.WorktreeManager(
            repo_root=self.repo, spec_id=spec_id,
            worktree_base=self.worktree_base, runner=self._wm_runner)
        # Serialises the shared-.git mutations (worktree add/remove, branch -D,
        # prune) when independent group PRs are verified concurrently (#15); the
        # long CI waits + gh calls run in parallel outside it.
        # In pipeline mode a shared process-wide lock is injected so fan-out and
        # verify both serialize on the same object (AC-012 / TASK-005).
        self._git_lock = git_lock if git_lock is not None else threading.Lock()
        # Cached merge method detection (lazy init on first auto_merge call).
        # Pre-seeded from `merge_method` (go-policy.yaml's `merge_method_by_base`,
        # resolved by the caller for `self.base` -- see policy.py) when the repo
        # needs a branch-aware method _detect_merge_method()'s repo-wide query
        # cannot express (e.g. squash for dev-target PRs, merge for stg/prd
        # promotions on a repo that allows both).
        self._merge_method: Optional[str] = merge_method
        # Structural backstop for dispatch.py's second Hard rule ("do NOT run
        # `gh pr merge`, enable auto-merge, or take any merge action yourself"):
        # group name -> violation detail, populated by `_detect_self_merge`.
        # `run_all`'s wave loop treats a populated entry as terminal (like
        # `quarantined`) rather than folding it into the ordinary strike-failure
        # reason -- a landed merge can't be undone by another strike or retry.
        self._self_merge_violations: Dict[str, str] = {}
        # Group name -> structured evidence that a post-spawn auto-merge explains
        # an otherwise suspicious mid-turn MERGED flip.
        self._automerge_evidence: Dict[str, Dict[str, str]] = {}
        # Group name -> structured record of a required-checks preflight READ
        # failure (transient `gh api` hiccup) falling back to `gh pr merge --auto`
        # instead of quarantining. Populated by `auto_merge`; distinct keys per
        # group written from independent verify-wave threads, same safety as
        # `_automerge_evidence` above.
        self._preflight_fallbacks: Dict[str, Dict[str, Any]] = {}
        # Cumulative post-merge gate (go-policy.yaml's post_merge_smoke_cmd, falling
        # back to integrate_smoke_cmd -- see policy.resolve_post_merge_smoke_cmd()).
        # None = gate disabled, identical to pre-existing behavior. See auto_merge's
        # caller (verify_one) and _merge_with_cumulative_gate below for the mechanism:
        # independent FEATURE groups within a wave verify (mergeability + CI wait)
        # CONCURRENTLY as before, but the actual merge step is serialized on
        # `_merge_lock` so each confirmed merge is re-validated against the ACTUAL
        # updated base HEAD before the next group's merge is attempted.
        self.post_merge_smoke_cmd = post_merge_smoke_cmd
        self._post_merge_smoke = post_merge_smoke or self._default_post_merge_smoke
        # `merge_lock`/`cumulative_regression` are injectable (like `git_lock`)
        # because the pipeline scheduler (live.py `_pipeline_scheduler`)
        # constructs a FRESH Verifier per group -- a plain instance attribute
        # would give each group its own isolated lock/flag and silently defeat
        # cross-group serialization in that mode. The caller shares one
        # `threading.Lock()` and one `dict` across every per-group Verifier it
        # builds; a plain Verifier() with neither passed (e.g. the serial
        # run_all path, or a test) gets private ones, identical in effect to
        # not having this seam at all.
        self._merge_lock = merge_lock if merge_lock is not None else threading.Lock()
        # group name -> failure detail, for every group whose CONFIRMED merge
        # failed the cumulative post-merge smoke check. Normally holds at most
        # one entry -- once non-empty, no further group in this run is allowed
        # to merge (the merge that caused it already landed and cannot be
        # undone here, so the safest response is to stop compounding the run's
        # changes onto a known-broken base and surface it loudly) -- but stays
        # a dict rather than a single slot because two groups racing past the
        # check in the same instant (before either observes the other's entry)
        # can both legitimately regress independently; both must be visible.
        self._cumulative_regression: Dict[str, str] = (
            cumulative_regression if cumulative_regression is not None else {})
        # Lazily created, reused across every post-merge smoke check in this run
        # (one worktree, reset to the fetched base HEAD before each check) rather
        # than a throwaway-per-check worktree -- there is at most one smoke run
        # in flight at a time (serialized on `_merge_lock`), so reuse is safe and
        # avoids repeated `git worktree add`/`remove` churn on the shared registry.
        self._post_merge_worktree_path: Optional[Path] = None

    # -- low-level helpers ------------------------------------------------- #
    def _git(self, *args: str):
        return self.run(["git", "-C", str(self.repo), *args])

    def _git_in(self, path: Path, *args: str):
        """`git -C <path>`, for commands scoped to a worktree other than
        `self.repo` (the shared post-merge smoke worktree)."""
        return self.run(["git", "-C", str(path), *args])

    def _wm_runner(self, cmd: List[str]):
        return self.run(cmd)

    def _gh(self, *args: str):
        cmd = ["gh", *args]
        if self.gh_repo:
            cmd += ["--repo", self.gh_repo]
        return self.run(cmd)

    def _preflight_runner(self, cmd: List[str], **_: Any):
        """Adapt the injected verifier runner to automerge_preflight's API.

        `gh api` has no `--repo`/`-R` flag; automerge_preflight's endpoints
        already embed `owner/repo` literally (e.g. `repos/{owner_repo}/...`),
        so no rewrite is needed for `gh api` calls.
        """
        if cmd[:3] == ["git", "remote", "get-url"]:
            cmd = ["git", "-C", str(self.repo), *cmd[1:]]
        return self.run(cmd)

    def _derive_gh_repo(self) -> Optional[str]:
        p = self._git("remote", "get-url", self.remote)
        url = (getattr(p, "stdout", "") or "").strip()
        if "github.com" in url:
            return url.rstrip("/").removesuffix(".git").split("github.com", 1)[-1].lstrip(":/")
        return None

    def _required_check_names(self) -> Optional[List[str]]:
        """Required status check context names for `self.base`, memoized.

        `_block_on_checks` cross-checks the live `statusCheckRollup` against
        this list so a required check that has not yet reported (a fresh PR,
        or a workflow GitHub hasn't scheduled yet) is never read as "no
        required checks" -- see `classify_checks`. None means the query
        itself failed after retries, or `gh_repo` is unresolved; callers fall
        back to rollup-only classification rather than blocking forever on an
        unrelated `gh api` outage (same query-error posture as
        `automerge_preflight.required_checks_gate`).
        """
        if self._required_check_names_fetched:
            return self._required_check_names_cache
        self._required_check_names_fetched = True
        if self.gh_repo is None:
            return None
        names: Optional[List[str]] = None
        for attempt in range(3):
            try:
                names = automerge_preflight.required_status_check_contexts(
                    self.gh_repo, self.base, runner=self._preflight_runner)
            except ImportError:
                names = None
                break
            if names is not None:
                break
            if attempt < 2:
                self.sleep(2.0 * (attempt + 1))
        self._required_check_names_cache = names
        return names

    def _detect_merge_method(self) -> str:
        """Query repo settings and return the best available merge method.

        Preference order: merge > squash > rebase (matches GitHub UI defaults).
        Falls back to "squash" if the query fails or all methods are disabled
        (defensive: squash is a safer default than failing outright).
        """
        if self.gh_repo is None:
            # No GitHub remote -> can't query; assume merge (original behavior)
            return "merge"
        p = self.run(["gh", "repo", "view", self.gh_repo, "--json",
                      "squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed"])
        if getattr(p, "returncode", 1) != 0:
            self.log(f"    could not query repo merge settings: "
                     f"{(getattr(p, 'stderr', '') or '')[:200]}")
            return "squash"  # safe fallback
        try:
            data = json.loads(getattr(p, "stdout", "") or "{}")
        except json.JSONDecodeError:
            return "squash"
        if data.get("mergeCommitAllowed"):
            return "merge"
        if data.get("squashMergeAllowed"):
            return "squash"
        if data.get("rebaseMergeAllowed"):
            return "rebase"
        # All disabled -> squash as defensive default
        return "squash"

    # -- PR status --------------------------------------------------------- #
    def pr_status(self, gb: str) -> Optional[Dict[str, Any]]:
        """`gh pr view <branch> --json ...` -> dict, or None if unavailable.

        Retries a failed `gh pr view` (GH_RETRIES) before returning None: a
        transient gh/network blip must not be read as 'CI unavailable' and
        quarantine an otherwise-healthy group.
        """
        last_err = ""
        for attempt in range(1, GH_RETRIES + 1):
            p = self._gh("pr", "view", gb, "--json",
                         "number,state,mergeable,mergeStateStatus,"
                         "statusCheckRollup,headRefOid,autoMergeRequest,mergedBy")
            if getattr(p, "returncode", 1) == 0:
                try:
                    return json.loads(getattr(p, "stdout", "") or "{}")
                except json.JSONDecodeError:
                    return None
            last_err = (getattr(p, "stderr", "") or "").strip()
            if attempt < GH_RETRIES:
                self.sleep(min(10.0, 2.0 * attempt))
        self.log(f"    pr view failed for {gb} after {GH_RETRIES} attempts: {last_err[:200]}")
        return None

    def failed_logs(self, gb: str, head_sha: str = "") -> Tuple[str, str]:
        """Best-effort (failing-check names, failing-run log tail) for ci-fix."""
        st = self.pr_status(gb) or {}
        _, failing = classify_checks(st.get("statusCheckRollup"))
        names = ", ".join(failing) or "(unknown)"
        p = self._gh("run", "list", "--branch", gb, "--limit", "10",
                     "--json", "databaseId,conclusion,headSha")
        log = ""
        try:
            runs = json.loads(getattr(p, "stdout", "") or "[]")
        except json.JSONDecodeError:
            runs = []
        target = next((r for r in runs
                       if (r.get("conclusion") or "").upper() in _FAIL_CONCLUSIONS
                       and (not head_sha or r.get("headSha") == head_sha)), None)
        if target is None:
            target = next((r for r in runs
                           if (r.get("conclusion") or "").upper() in _FAIL_CONCLUSIONS),
                          None)
        if target and target.get("databaseId") is not None:
            lp = self._gh("run", "view", str(target["databaseId"]), "--log-failed")
            log = getattr(lp, "stdout", "") or ""
        return names, log

    # -- worker dispatch --------------------------------------------------- #
    def _group_worktree(self, group: Dict[str, Any], gb: str) -> Optional[Path]:
        """Lazily create a worktree checked out on the group branch."""
        path = self.worktree_base / f"{self.spec_id}-verify-{group['name']}"
        if path.exists():
            return path
        with self._git_lock:  # `git worktree add` mutates the shared .git registry
            if path.exists():
                return path
            self.worktree_base.mkdir(parents=True, exist_ok=True)
            p = self._git("worktree", "add", str(path), gb)
        if getattr(p, "returncode", 1) != 0:
            self.log(f"    could not create verify worktree for {group['name']}: "
                     f"{(getattr(p, 'stderr', '') or '').strip()[:200]}")
            return None
        return path

    def _post_merge_worktree(self) -> Optional[Path]:
        """Lazily create a detached worktree tracking `base`, reused for every
        cumulative post-merge smoke check this Verifier instance runs -- reset
        to the freshly fetched `remote/base` HEAD before each check rather than
        recreated. Safe to reuse because at most one smoke check runs at a time
        (the caller holds `_merge_lock` for the duration).

        The pipeline scheduler builds a FRESH Verifier per group (see
        `_merge_lock`/`cumulative_regression`'s injection note above), so a
        worktree already registered at this path by an earlier group's
        Verifier -- or left over from a prior interrupted run against this
        same spec -- is a normal, expected condition here, not a defect.
        `git worktree add` refuses to create over an existing path; check
        `path.exists()` first and reuse it, mirroring `_group_worktree`'s
        same idiom above, instead of failing the whole post-merge gate on
        every group after the first that reuses this path."""
        if self._post_merge_worktree_path is not None:
            return self._post_merge_worktree_path
        path = self.worktree_base / f"{self.spec_id}-postmerge"
        if path.exists():
            self._post_merge_worktree_path = path
            return path
        with self._git_lock:  # `git worktree add` mutates the shared .git registry
            if self._post_merge_worktree_path is not None:
                return self._post_merge_worktree_path
            if path.exists():
                self._post_merge_worktree_path = path
                return path
            self.worktree_base.mkdir(parents=True, exist_ok=True)
            fetch = self._git("fetch", "-q", self.remote, self.base)
            if getattr(fetch, "returncode", 1) != 0:
                self.log(f"    post-merge smoke: could not fetch {self.remote}/{self.base}: "
                         f"{(getattr(fetch, 'stderr', '') or '').strip()[:200]}")
                return None
            p = self._git("worktree", "add", "-f", "--detach", str(path),
                          f"{self.remote}/{self.base}")
            if getattr(p, "returncode", 1) != 0:
                self.log(f"    post-merge smoke: could not create worktree: "
                         f"{(getattr(p, 'stderr', '') or '').strip()[:200]}")
                return None
            self._post_merge_worktree_path = path
        return path

    def _default_post_merge_smoke(self, name: str, wt: Path) -> Tuple[bool, str]:
        """Run `post_merge_smoke_cmd` in `wt` (already reset to the fetched base
        HEAD by the caller). Real subprocess -- mirrors integrate.py's
        `_run_integration_smoke`; fail-closed on timeout/spawn error (never treat
        an unverified post-merge state as clean)."""
        cmd = self.post_merge_smoke_cmd
        if not cmd:
            return False, "post_merge_smoke_cmd not configured"
        self.log(f"  POST-MERGE SMOKE [{name:9}] against updated base: {cmd}")
        try:
            r = subprocess.run(
                cmd, shell=True, cwd=str(wt), capture_output=True, text=True,
                timeout=POST_MERGE_SMOKE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False, f"timed out after {POST_MERGE_SMOKE_TIMEOUT}s"
        except OSError as e:
            return False, f"could not run post-merge smoke command: {e}"
        if r.returncode == 0:
            return True, "ok"
        tail = ((r.stderr or "") + (r.stdout or "")).strip()[-300:]
        return False, f"exit {r.returncode}: {tail}"

    def _run_post_merge_smoke(self, name: str) -> Tuple[bool, str]:
        """Fetch + hard-reset the shared post-merge worktree to the ACTUAL
        updated base HEAD and run the configured smoke command there.

        Called only when `post_merge_smoke_cmd` is configured and a group's PR
        just CONFIRMED merged (see `_merge_with_cumulative_gate`); the caller
        holds `_merge_lock`, so this is never concurrent with another group's
        merge+smoke attempt.
        """
        wt = self._post_merge_worktree()
        if wt is None:
            return False, "could not prepare post-merge smoke worktree"
        with self._git_lock:
            fetch = self._git("fetch", "-q", self.remote, self.base)
            if getattr(fetch, "returncode", 1) != 0:
                return False, (f"could not fetch {self.remote}/{self.base}: "
                               f"{(getattr(fetch, 'stderr', '') or '').strip()[:200]}")
            reset = self._git_in(wt, "reset", "--hard", f"{self.remote}/{self.base}")
            if getattr(reset, "returncode", 1) != 0:
                return False, (f"could not reset post-merge worktree to "
                               f"{self.remote}/{self.base}: "
                               f"{(getattr(reset, 'stderr', '') or '').strip()[:200]}")
        return self._post_merge_smoke(name, wt)

    def _spawn_group_worker(self, role: str, group: Dict[str, Any], gb: str,
                            extra: Dict[str, Any]) -> bool:
        """Build the brief, spawn the worker, return True if it reported success."""
        wt = self._group_worktree(group, gb)
        if wt is None:
            return False
        ctx = {"spec_id": self.spec_id, "group_branch": gb,
               "base_branch": self.base, "remote": self.remote,
               "worktree_path": str(wt), **extra}
        prompt = dispatch.build_group_prompt(role, group, ctx)
        spawn_fn = self._ci_fix_spawn if role == dispatch.ROLE_CI_FIX else self.spawn
        # Captured BEFORE the worker runs: `gb` is shared between this repo and the
        # worktree (`git worktree add`), so a commit the worker makes in `wt` is
        # visible on this ref immediately, before it even pushes.
        pre_sha = (getattr(self._git("rev-parse", gb), "stdout", "") or "").strip()
        pre_status = self.pr_status(gb)
        try:
            raw = spawn_fn(prompt, wt)
        except subprocess.TimeoutExpired:
            self.log(f"    {role} worker timed out — treating as strike failure")
            return False
        # Checked regardless of what the worker reports: a worker that merged the
        # PR itself is a violation even if it also reports status=success/failed.
        if self._detect_self_merge(role, group, gb, pre_status):
            return False
        try:
            rep = dispatch.parse_report_back(raw)
        except ValueError as e:
            self.log(f"    {role} worker report-back parse failed: {e}")
            return False
        if rep.get("status") != "success":
            return False
        forbidden = self._forbidden_paths_touched(pre_sha, gb, group)
        if forbidden:
            self.log(f"    {role} worker touched forbidden path(s) despite "
                     f"status=success — treating as strike failure: {forbidden}")
            return False
        return True

    def _detect_self_merge(self, role: str, group: Dict[str, Any], gb: str,
                           pre_status: Optional[Dict[str, Any]]) -> bool:
        """Structural backstop for dispatch.py's Hard rule: 'do NOT run `gh pr
        merge`, enable auto-merge, or take any merge action yourself.'

        `_spawn_group_worker` runs only from `ensure_mergeable`'s resolve loop
        and `wait_and_fix_ci`'s ci-fix loop -- both of which run BEFORE
        `auto_merge()` is ever called for this group in this `verify_one` run.
        So a PR that flips to MERGED between this worker's pre- and post-spawn
        `pr_status()` calls was categorically not this orchestrator's own
        `auto_merge()` (that hasn't run yet). It is either the worker itself
        running a merge, or unrelated external automation/a human.

        Distinguished from a pre-armed external auto-merge (e.g. this repo's
        own "CI: Auto-merge on open" workflow, or an earlier `--auto` queued by
        THIS run for a different reason) by checking `autoMergeRequest` on the
        PRE-spawn status: if auto-merge was already armed before this worker's
        turn even started, GitHub could merge on its own at any moment
        independent of the worker, so a MERGED flip is not attributable to it.
        Only a flip with NO prior `autoMergeRequest` is treated as confirmed
        worker self-merge evidence.
        """
        if pre_status is None:
            return False  # can't diff without a baseline; fail open
        if (pre_status.get("state") or "").upper() == "MERGED":
            return False  # already merged before this worker's turn -- not its doing
        if pre_status.get("autoMergeRequest"):
            return False  # external auto-merge was already armed pre-spawn
        post_status = self.pr_status(gb)
        if post_status is None or (post_status.get("state") or "").upper() != "MERGED":
            return False
        post_auto = ((post_status.get("autoMergeRequest") or {})
                     .get("enabledBy") or {})
        merged_by = post_status.get("mergedBy") or {}
        if (post_auto.get("login")
                and post_auto.get("login") == merged_by.get("login")):
            self._automerge_evidence[group["name"]] = {
                "enabledBy": post_auto.get("login"),
                "mergedBy": merged_by.get("login"),
            }
            self.log(
                "    auto-merge explained mid-turn merge "
                f"[{group['name']}]: enabledBy={post_auto.get('login')} "
                f"mergedBy={merged_by.get('login')}"
            )
            return False  # post-spawn auto-merge signal explains the merge
        detail = (f"{role} worker merged {gb} directly (state flipped to MERGED "
                 "mid-turn with no prior autoMergeRequest) -- violates the "
                 "'never merge yourself' Hard rule")
        self.log(f"    !! SELF-MERGE VIOLATION [{group['name']}]: {detail}")
        self._self_merge_violations[group["name"]] = detail
        return True

    def _forbidden_paths_touched(
        self, pre_sha: str, gb: str, group: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Deny-list check on what the worker actually changed (not what it says
        it changed): files under FORBIDDEN_WORKER_PATH_PREFIXES touched between
        the worker's pre-run HEAD and its post-run HEAD on `gb`.

        Two tiers, because they protect different things:

        * **The spec root is absolute.** It is the run's own bookkeeping -- task
          status reaches the artifact once, at integrate, on the base checkout
          (design §4.3). A worker writing there reintroduces the cross-branch
          conflict class P0 removed, so no declaration exempts it.
        * **Everything else is out-of-scope protection**, and a path the group's
          own tasks declare is by definition in scope. Blanket-denying
          `.github/workflows/**` makes any CI-focused spec unimplementable:
          datalena's spec 080 exists to modify `qa-pipeline.yml`, and its ci-fix
          worker was struck out for touching the file the spec is about.

        Residual risk, stated rather than designed away: a ci-fix worker whose
        group declares a workflow file can still weaken the check it is trying to
        turn green. That edit lands in the PR diff under human review, which is a
        better trade than a guard that blocks the deliverable outright.

        `pre_sha` empty (rev-parse failed) -> can't diff; fail open, since this
        is a defense-in-depth backstop and a `gh pr view`/CI check downstream
        still gates the merge.

        Also logs (never gates on) the same touched-vs-declared mismatch
        `conductor.plan_audit` computes standalone, so the compile-accuracy
        signal is captured automatically for every real run instead of only
        when someone remembers to run the audit by hand.
        """
        if not pre_sha:
            return []
        p = self._git("diff", "--name-only", f"{pre_sha}..{gb}")
        if getattr(p, "returncode", 1) != 0:
            return []
        touched = (getattr(p, "stdout", "") or "").splitlines()
        spec_root = forbidden_prefixes_for(self.spec_rel)[1]
        others = tuple(x for x in forbidden_prefixes_for(self.spec_rel) if x != spec_root)
        declared = set((self.declared_files or {}).get((group or {}).get("name"), ()))
        if declared:
            undeclared = sorted(set(touched) - declared)
            if undeclared:
                self.log(
                    f"    [{(group or {}).get('name')}] plan-audit: "
                    f"touched-not-declared {undeclared}"
                )
        return [
            f
            for f in touched
            if f.startswith(spec_root) or (f.startswith(others) and f not in declared)
        ]

    # -- per-stage logic --------------------------------------------------- #
    def ensure_mergeable(self, group: Dict[str, Any], gb: str) -> Tuple[bool, str]:
        """Drive the resolve loop until the PR is mergeable or strikes run out."""
        for strike in range(self.max_strikes):
            st = self.pr_status(gb)
            if st is None:
                return False, "PR not found / gh unavailable"
            mergeable = (st.get("mergeable") or "").upper()
            if mergeable == "CONFLICTING":
                self.log(f"    [{group['name']}] CONFLICTING with {self.base} "
                         f"-- spawning resolve worker (strike {strike + 1}/"
                         f"{self.max_strikes})")
                if not self._spawn_group_worker(dispatch.ROLE_RESOLVE, group, gb, {}):
                    return False, "resolve worker failed"
                continue
            # MERGEABLE or UNKNOWN (GitHub still computing) -> proceed; CI gate and
            # the merge call are the real backstop.
            return True, ""
        return False, "still CONFLICTING after resolve attempts"

    def wait_and_fix_ci(self, group: Dict[str, Any], gb: str) -> Tuple[bool, str]:
        """Block on CI; on red, run the ci-fix loop (bounded by strikes)."""
        for strike in range(self.max_strikes + 1):
            ok, failing = self._block_on_checks(group, gb)
            if ok:
                return True, ""
            if failing is None:
                return False, "CI did not complete within budget"
            if strike >= self.max_strikes:
                return False, f"CI still failing after {self.max_strikes} fix " \
                              f"attempts: {', '.join(failing)}"
            self.log(f"    [{group['name']}] CI red ({', '.join(failing)}) "
                     f"-- spawning ci-fix worker (strike {strike + 1}/"
                     f"{self.max_strikes})")
            st = self.pr_status(gb) or {}
            names, log = self.failed_logs(gb, st.get("headRefOid", ""))
            ok_fix = self._spawn_group_worker(
                dispatch.ROLE_CI_FIX, group, gb,
                {"failing_checks": names, "failure_log": log})
            if not ok_fix:
                return False, "ci-fix worker failed"
        return False, "CI fix loop exhausted"

    def _block_on_checks(self, group: Dict[str, Any], gb: str
                         ) -> Tuple[bool, Optional[List[str]]]:
        """Poll until checks finish. Returns (green?, failing|None-if-timed-out).

        Adaptive backoff: poll fast early (catch a quick CI without waiting a full
        fixed interval) and grow the gap toward poll_interval_max (cheap while a
        long suite runs) instead of hammering `gh` at a fixed cadence.
        """
        required = self._required_check_names()
        for poll in range(self.max_polls):
            st = self.pr_status(gb)
            if st is None:
                return False, ["(pr view unavailable)"]
            pending, failing = classify_checks(st.get("statusCheckRollup"), required=required)
            if not pending:
                if failing:
                    return False, failing
                self.log(f"    [{group['name']}] CI green")
                return True, None
            self.log(f"    [{group['name']}] waiting on CI: pending checks remain, "
                     f"{len(failing)} failing so far (poll {poll + 1})")
            self.sleep(min(self.poll_interval_max, self.poll_interval * (1.4 ** poll)))
        return False, None                               # budget exhausted

    def retarget_to_base(self, group: Dict[str, Any], gb: str) -> None:
        """Re-point a dependent group's PR at the real base branch.

        A feature group is opened against its parent group's branch (stacked). The
        parent's branch is deleted when the parent auto-merges (`--delete-branch`),
        which can leave the child PR pointing at a now-gone branch -> unmergeable.
        We process groups parent-before-child, so by the time a dependent is
        verified its parent is merged into `base`; explicitly retargeting the child
        to `base` (idempotent if GitHub already auto-retargeted) keeps it mergeable
        instead of orphaned. Best-effort: a failure here is reconciled by the
        mergeability/resolve loop that follows.

        Uses the REST `PATCH .../pulls/{number}` endpoint (`base` field) instead of
        `gh pr edit --base`: `gh pr edit`'s pre-mutation PR lookup unconditionally
        requests `projectCards` for every invocation, regardless of which flag is
        edited (`cli/cli` `pr_edit.go` `editRun()`), so it fails outright on a
        repo/org with a legacy Projects (classic) board attached -- the same failure
        class fixed for label-add in `pr_labels.py`. `gh pr view` (used by
        `pr_status`) is unaffected: with an explicit `--json` field list it never
        falls back to that default field set. Falls back to `gh pr edit --base`
        only when the PR number or owner/repo can't be resolved (best-effort).
        """
        st = self.pr_status(gb) or {}
        if (st.get("state") or "").upper() == "MERGED":
            return
        number = st.get("number")
        if number is not None and self.gh_repo:
            # `gh api` has no `--repo` flag (unlike `gh pr`), so this bypasses
            # `_gh()` and embeds owner/repo directly in the endpoint path.
            p = self.run(["gh", "api", f"repos/{self.gh_repo}/pulls/{number}",
                          "-X", "PATCH", "-f", f"base={self.base}"])
        else:
            p = self._gh("pr", "edit", gb, "--base", self.base)
        if getattr(p, "returncode", 1) == 0:
            self.log(f"    [{group['name']}] retargeted {gb} base -> {self.base}")

    def _wait_for_external_merge(self, group: Dict[str, Any], gb: str) -> Tuple[bool, str]:
        """Poll `gb`'s PR status until an externally-armed auto-merge completes.

        Same adaptive backoff as `_block_on_checks` (fast early, growing toward
        `poll_interval_max`), bounded by `max_polls` so a stuck external merge
        (e.g. a required check the armed automation is itself waiting on) does
        not hang the run forever.
        """
        for poll in range(self.max_polls):
            st = self.pr_status(gb)
            if st is None:
                return False, "PR not found / gh unavailable while deferring to external auto-merge"
            if (st.get("state") or "").upper() == "MERGED":
                self.log(f"    [{group['name']}] {gb} merged externally")
                return True, ""
            self.sleep(min(self.poll_interval_max, self.poll_interval * (1.4 ** poll)))
        return False, "external auto-merge did not complete within poll budget"

    def _retry_auto_merge_methods(self, gb: str, exclude: Optional[str]
                                  ) -> Tuple[bool, Optional[str], str]:
        """Retry `gh pr merge <gb> --auto --<method> --delete-branch` for each
        method besides `exclude`, in preference order (squash, rebase, merge).

        Shared by both `auto_merge` fallback sites that can be rejected by
        `enablePullRequestAutoMerge` (e.g. `_detect_merge_method` guessed
        "merge" from repo-wide settings, but the target branch's ruleset only
        allows squash). Returns (True, working_method, "") on the first
        method GitHub accepts, or (False, None, last_error) if every
        remaining method is also rejected.
        """
        last_err = ""
        for fallback in ("squash", "rebase", "merge"):
            if fallback == exclude:
                continue
            p = self._gh("pr", "merge", gb, "--auto", f"--{fallback}", "--delete-branch")
            if getattr(p, "returncode", 1) == 0:
                return True, fallback, ""
            last_err = (getattr(p, "stderr", "") or "").strip()
        return False, None, last_err

    def _automerge_label_block_reason(self, gb: str) -> Optional[str]:
        """Fail-closed `go:no-automerge` label check, run once at the top of
        `auto_merge()` before ANY of its internal `gh pr merge` arming paths.

        The label is the source of truth stamped at PR creation time by
        pre_pr_gate (from policy.automerge_eligible()'s risk-tier decision) --
        verify.py stays policy-agnostic and just respects it, the same way
        `go:no-automerge` is already enforced by the external auto-merge.yml
        workflow's OWN arming logic. Before this check, verify.py's arming
        never read PR labels at all, so its fallback paths (most notably the
        required-checks-preflight-query-failed fallback) could arm auto-merge
        past a declared human gate with no policy awareness whatsoever (spec
        023 group PR #388, `go:risk-high` + `go:no-automerge`, merged with no
        human decision -- brief 20260723-174500).

        Returns a human-readable block reason, or None when arming may
        proceed. A label-QUERY failure fails CLOSED (blocks arming) rather
        than silently treating "couldn't check" as "label absent".
        """
        p = self._gh("pr", "view", gb, "--json", "labels")
        if getattr(p, "returncode", 1) != 0:
            return (
                f"could not read PR labels for {gb} "
                f"({(getattr(p, 'stderr', '') or '').strip()[:150]}); "
                "failing closed -- not arming auto-merge"
            )
        try:
            data = json.loads(getattr(p, "stdout", "") or "{}")
        except json.JSONDecodeError:
            return f"could not parse PR label data for {gb}; failing closed -- not arming auto-merge"
        labels = {(lbl.get("name") or "") for lbl in (data.get("labels") or []) if isinstance(lbl, dict)}
        if _NO_AUTOMERGE_LABEL in labels:
            return f"human gate: {gb} carries '{_NO_AUTOMERGE_LABEL}' -- skipping auto-merge"
        return None

    def auto_merge(self, group: Dict[str, Any], gb: str) -> Tuple[bool, str]:
        st = self.pr_status(gb)
        if st and (st.get("state") or "").upper() == "MERGED":
            self.log(f"    [{group['name']}] {gb} already merged")
            return True, ""
        block_reason = self._automerge_label_block_reason(gb)
        if block_reason:
            self.log(f"    [{group['name']}] {block_reason}")
            return False, block_reason
        # Defer instead of racing: `auto_merge()` runs at most once per group per
        # run (see `run_all`), so a non-null `autoMergeRequest` here was armed
        # BEFORE this call -- either by this repo's own CI automation (e.g. a
        # `gh pr merge --auto` workflow, see policy.py's `detect_external_automerge`)
        # or a human/bot via GitHub's native toggle. Calling `gh pr merge`
        # ourselves in that case would race the armed merge and risk applying the
        # wrong method (e.g. this run's repo-wide `merge` pick landing on a
        # dev-target PR the repo's own workflow would have squashed).
        if st and st.get("autoMergeRequest"):
            armed_by = ((st.get("autoMergeRequest") or {}).get("enabledBy") or {}).get(
                "login", "unknown")
            self.log(f"    [{group['name']}] {gb} auto-merge already armed by "
                     f"{armed_by} -- deferring instead of racing")
            return self._wait_for_external_merge(group, gb)
        try:
            eligible, reason = automerge_preflight.required_checks_gate(
                self.repo, self.base, runner=self._preflight_runner
            )
            is_query_error = (not eligible) and automerge_preflight.is_preflight_query_error(reason)
        except ImportError:
            eligible, reason, is_query_error = False, "automerge_preflight module unavailable", True
        # Detect allowed merge method once per run (cached in self._merge_method)
        if self._merge_method is None:
            self._merge_method = self._detect_merge_method()
        method_flag = f"--{self._merge_method}"
        if not eligible:
            if is_query_error:
                # The preflight READ failed (transient gh api hiccup, even after
                # its own retries) -- this is not a confirmed "unsafe to merge"
                # state, so don't quarantine an otherwise-green group over it.
                # `gh pr merge --auto` still has GitHub enforce required
                # checks/rulesets at merge time even though we couldn't read
                # them ourselves.
                self.log(
                    f"    [{group['name']}] required-checks preflight query failed "
                    f"({reason}); falling back to `gh pr merge --auto` -- GitHub "
                    "still enforces protection at merge time"
                )
                p = self._gh("pr", "merge", gb, "--auto", method_flag, "--delete-branch")
                if getattr(p, "returncode", 1) == 0:
                    self.log(
                        f"    [{group['name']}] {gb} queued for auto-merge "
                        f"({self._merge_method}, preflight query failed -- GitHub will "
                        "still enforce required checks before merging)"
                    )
                    self._preflight_fallbacks[group["name"]] = {
                        "pr": gb, "reason": reason, "outcome": "queued",
                        "at": round(time.time(), 3),
                    }
                    return True, "queued"
                err = (getattr(p, "stderr", "") or "").strip()
                # enablePullRequestAutoMerge rejected the resolved method (e.g.
                # _detect_merge_method guessed "merge" from repo-wide settings,
                # but the target branch's ruleset only allows squash -- the
                # exact failure that quarantined an otherwise-green datalena
                # group on 2026-07-22). Retry --auto with each remaining
                # method before quarantining, mirroring the branch-protection
                # fallback below.
                if any(sig in err.lower() for sig in _AUTO_MERGE_METHOD_SIGNALS):
                    original_method = self._merge_method
                    ok_retry, working_method, retry_err = self._retry_auto_merge_methods(
                        gb, original_method)
                    if ok_retry:
                        self._merge_method = working_method
                        self.log(
                            f"    [{group['name']}] {gb} queued for auto-merge "
                            f"({working_method}, method-fallback: preflight query failed "
                            f"and {original_method} rejected by enablePullRequestAutoMerge)"
                        )
                        self._preflight_fallbacks[group["name"]] = {
                            "pr": gb, "reason": reason, "outcome": "queued",
                            "method": working_method, "at": round(time.time(), 3),
                        }
                        return True, "queued"
                    err = retry_err or err
                self.log(f"    [{group['name']}] auto-merge fallback also failed: {err}")
                self._preflight_fallbacks[group["name"]] = {
                    "pr": gb, "reason": reason, "outcome": "fallback_failed",
                    "fallback_error": err, "at": round(time.time(), 3),
                }
                return False, (
                    f"required-checks preflight query failed and the `gh pr merge --auto` "
                    f"fallback also failed: {err}"
                )
            self.log(f"    [{group['name']}] direct auto-merge blocked: {reason}")
            return False, f"required-checks preflight blocked direct auto-merge: {reason}"
        p = self._gh("pr", "merge", gb, method_flag, "--delete-branch")
        if getattr(p, "returncode", 1) == 0:
            self.log(f"    [{group['name']}] auto-merged {gb} ({self._merge_method})")
            return True, ""
        err = (getattr(p, "stderr", "") or "").strip()
        # Branch-protection blocks are not transient errors -- retrying immediately
        # would just fail again. Enable auto-merge so GitHub queues the merge the
        # moment all requirements (e.g. required reviews) are satisfied. Treat
        # "queued for auto-merge" as a terminal success: human review time is
        # unbounded so we cannot block the orchestrator waiting for it.
        if any(sig in err.lower() for sig in _BRANCH_PROTECTION_SIGNALS):
            p2 = self._gh("pr", "merge", gb, "--auto", method_flag, "--delete-branch")
            if getattr(p2, "returncode", 1) == 0:
                self.log(
                    f"    [{group['name']}] {gb} queued for auto-merge "
                    f"({self._merge_method}, branch protection active -- will merge when approved)"
                )
                # Return "queued" so the caller knows GitHub will handle branch deletion
                # via --delete-branch when auto-merge fires; cleanup must not delete the
                # remote branch now or GitHub will auto-close the PR before merging.
                return True, "queued"
            err2 = (getattr(p2, "stderr", "") or "").strip()
            # enablePullRequestAutoMerge rejected the method (e.g. squash-only repo but
            # _detect_merge_method returned "merge"). Retry --auto with each remaining
            # method before quarantining -- the repo automation will merge via the working one.
            if any(sig in err2.lower() for sig in _AUTO_MERGE_METHOD_SIGNALS):
                original_method = self._merge_method
                ok_retry, working_method, _ = self._retry_auto_merge_methods(
                    gb, original_method)
                if ok_retry:
                    self._merge_method = working_method  # cache for subsequent groups
                    self.log(
                        f"    [{group['name']}] {gb} queued for auto-merge "
                        f"({working_method}, method-fallback: {original_method} rejected by "
                        f"enablePullRequestAutoMerge)"
                    )
                    return True, "queued"
            return False, f"auto-merge failed: {err2[:200]}"
        return False, f"auto-merge failed: {err[:200]}"

    def _merge_with_cumulative_gate(self, group: Dict[str, Any], gb: str) -> Tuple[bool, str]:
        """`auto_merge()`, plus the cumulative post-merge gate around a CONFIRMED
        merge (PR #167 root-cause class: independent FEATURE groups within a wave
        verify -- mergeability + CI wait -- CONCURRENTLY, so a semantic dependency
        between two groups that produced no `deps`/shared-file edge is invisible
        to any per-group-isolated check).

        `ensure_mergeable`/`wait_and_fix_ci` (the expensive, multi-minute part)
        still run concurrently across a wave -- only this method's body is
        serialized on `_merge_lock`, so at most one group merges (and is
        re-validated) at a time. `auto_merge()`'s "queued" outcome (GitHub will
        merge asynchronously later, e.g. pending required review) is NOT
        re-validated here -- there is no synchronous landing to check yet, and
        the orchestrator cannot block on unbounded human review time (see
        `auto_merge`'s own docstring for that tradeoff).

        On smoke failure the merge that triggered it has ALREADY landed and
        cannot be undone here (no automatic revert -- too risky to do
        unattended); `_cumulative_regression` is set instead so every group
        still pending in `run_all`'s wave loop is quarantined without
        attempting its own merge, stopping the run from compounding more
        changes onto a base now confirmed broken.
        """
        name = group["name"]
        with self._merge_lock:
            if self._cumulative_regression:
                bad_name, detail = next(iter(self._cumulative_regression.items()))
                return False, (f"blocked: cumulative post-merge regression in group "
                               f"'{bad_name}' -- {detail}")
            ok, reason = self.auto_merge(group, gb)
            if not ok or reason == "queued" or not self.post_merge_smoke_cmd:
                return ok, reason
            smoke_ok, detail = self._run_post_merge_smoke(name)
            if smoke_ok:
                return ok, reason
            self._cumulative_regression[name] = detail
            self.log(
                f"  POST-MERGE REGRESSION [{name}] -- {detail} "
                "(merge already landed on base; remaining groups in this run are "
                "blocked, worktrees kept for inspection)"
            )
            return False, f"post-merge cumulative smoke failed: {detail}"

    def cleanup_group(self, group: Dict[str, Any], gb: str,
                      delivered: Optional[Dict[str, List[str]]] = None,
                      skip_remote_branch_delete: bool = False) -> None:
        """Tear down the merged group's task worktrees + branches (gated).

        Only DELIVERED tasks are torn down. When a group was split (a failed task
        quarantined out of an otherwise-green group, defect B), `delivered` names
        the tasks that actually merged; the split-out task's worktree + branch are
        intentionally KEPT for human review. Falls back to the full group task
        list when no split map is supplied.

        skip_remote_branch_delete: set True when the PR was queued for auto-merge
        via --auto rather than directly merged. In that case GitHub's auto-merge will
        delete the remote branch when it fires (--delete-branch was passed to
        `gh pr merge --auto`). Deleting it ourselves before that happens closes the
        PR without merging it.
        """
        task_ids = (delivered or {}).get(group["name"]) or group.get("tasks") or []
        # All git mutations here touch the shared .git registry -- serialise them
        # so concurrent group cleanups (#15) don't race.
        with self._git_lock:
            for tid in task_ids:
                try:
                    self.wm.remove(tid)
                except worktree.WorktreeError as e:
                    self.log(f"    cleanup: worktree remove {tid} skipped: {e}")
                try:
                    self.wm.delete_branch(tid)
                except worktree.WorktreeError as e:
                    self.log(f"    cleanup: branch delete {tid} skipped: {e}")
            # drop the verify worktree (if one was created) + the local group branch
            vpath = self.worktree_base / f"{self.spec_id}-verify-{group['name']}"
            if vpath.exists():
                self._git("worktree", "remove", str(vpath), "--force")
            if not skip_remote_branch_delete:
                # Delete remote group branch (best-effort). gh pr merge --delete-branch
                # is skipped when the PR is already MERGED on entry to auto_merge, leaving
                # the remote branch as an orphan. Explicit push --delete here covers that
                # gap; errors are swallowed if GitHub or the repo setting already removed it.
                rp = self._git("push", self.remote, "--delete", gb)
                if getattr(rp, "returncode", 1) == 0:
                    self.log(f"    cleanup: deleted remote branch {gb}")
                else:
                    self.log(f"    cleanup: remote branch {gb} already gone (skipped)")
            p = self._git("branch", "-D", gb)
            if getattr(p, "returncode", 1) != 0:
                err = (getattr(p, "stderr", "") or "").strip()
                if "not found" in err.lower() or "no such" in err.lower():
                    self.log(f"    cleanup: branch {gb} already deleted")
            try:
                self.wm.prune()
            except worktree.WorktreeError as e:
                self.log(f"    cleanup: prune skipped: {e}")

    # -- single-group verify seam (AC-008 / TASK-002) ---------------------- #
    _POST_MERGE_SMOKE_PREFIX = "post-merge cumulative smoke failed:"
    _CUMULATIVE_BLOCKED_PREFIX = "blocked: cumulative post-merge regression"

    def verify_one(self, group: Dict[str, Any], group_branch: str,
                   delivered: Optional[Dict[str, List[str]]],
                   merged: List[str], quarantined: Dict[str, str],
                   lock: threading.Lock,
                   self_merged: Optional[Dict[str, str]] = None,
                   armed: Optional[Dict[str, str]] = None,
                   post_merge_regressed: Optional[Dict[str, str]] = None) -> None:
        """Verify exactly one group's PR: retarget (if dependent) →
        ensure_mergeable → wait_and_fix_ci → auto_merge (cumulative-gated) →
        gated cleanup (delivered tasks only). Quarantines and keeps worktrees
        on failure.

        `merged`/`quarantined` are caller-owned accumulators mutated under
        `lock`, safe for concurrent calls within a wave. Consumed by run_all
        (AC-009) and the pipeline scheduler (TASK-004). `self._git_lock`
        guards worktree mutations; in pipeline mode it is the shared
        process-wide registry lock injected via `git_lock=` (AC-012).

        `self_merged`: a distinct accumulator (not folded into `quarantined`)
        for a confirmed worker self-merge violation (`_detect_self_merge`) --
        the merge can't be undone by another strike or retry, so it is
        surfaced separately rather than read as an ordinary strike failure.
        Optional for backward compatibility with existing callers/tests that
        don't pass it; the violation is still logged loudly either way.

        `armed`: a distinct accumulator (not folded into `merged`) for a group
        whose PR was only QUEUED for auto-merge (`auto_merge()` returned
        `(True, "queued")` -- required checks preflight passed/degraded and
        `gh pr merge --auto` was accepted, but GitHub has not actually merged
        it yet). `merged` must only ever contain CONFIRMED merges (state
        already MERGED on entry, or a direct non-`--auto` merge that returned
        success) -- callers journal `merged` as terminal "MERGED", and an
        armed-but-unconfirmed PR can still sit OPEN/BLOCKED indefinitely (e.g.
        a required check stuck red). Optional for backward compatibility;
        when omitted, a queued group is still recorded in `merged` (prior
        behavior) rather than silently dropped.

        `post_merge_regressed`: a distinct accumulator (not folded into
        `quarantined`) for a group whose CONFIRMED merge landed but then
        failed the cumulative post-merge smoke check (`post_merge_smoke_cmd`).
        Unlike `quarantined`, this group's PR IS merged -- there is nothing to
        retry or keep a worktree open for; the accumulator exists purely to
        surface the regression distinctly from an ordinary clean merge.
        Optional for backward compatibility; when omitted the group still
        lands in `quarantined` (not `merged`) so the failure is never silently
        dropped.
        """
        name = group["name"]
        self.log(f"  VERIFY [{name}] {group_branch}")
        # Read under `_merge_lock` -- the lock that actually guards writes to
        # `_cumulative_regression` (see `_merge_with_cumulative_gate`), not the
        # caller's accumulator `lock`. An early, best-effort check: a regression
        # detected mid-CI-wait for THIS group is still caught below, when this
        # group itself reaches `_merge_with_cumulative_gate`.
        with self._merge_lock:
            blocked = dict(self._cumulative_regression)
        if blocked:
            bad_name, detail = next(iter(blocked.items()))
            reason = f"{self._CUMULATIVE_BLOCKED_PREFIX} in group '{bad_name}' -- {detail}"
            with lock:
                quarantined[name] = reason
            self.log(f"  SKIP [{name}] -- {reason} (worktree kept)")
            return
        if group.get("depends_on"):
            self.retarget_to_base(group, group_branch)
        ok, reason = self.ensure_mergeable(group, group_branch)
        if ok:
            ok, reason = self.wait_and_fix_ci(group, group_branch)
        if ok:
            ok, reason = self._merge_with_cumulative_gate(group, group_branch)
        if not ok:
            violation = self._self_merge_violations.get(name)
            is_regression = reason.startswith(self._POST_MERGE_SMOKE_PREFIX)
            with lock:
                if is_regression and post_merge_regressed is not None:
                    post_merge_regressed[name] = reason
                elif violation and self_merged is not None:
                    self_merged[name] = violation
                else:
                    quarantined[name] = reason
            if is_regression and post_merge_regressed is not None:
                self.log(f"  POST-MERGE REGRESSED [{name}] -- merged, then {reason}")
            elif violation and self_merged is not None:
                self.log(f"  SELF-MERGE VIOLATION [{name}] -- {violation} (worktree kept)")
            else:
                self.log(f"  QUARANTINE [{name}] -- {reason} (worktree kept)")
            return
        with lock:
            if reason == "queued" and armed is not None:
                armed[name] = reason
            else:
                merged.append(name)
        # When auto_merge queued via --auto, GitHub handles remote branch deletion
        # when it fires (--delete-branch was passed). Skip our own remote delete or
        # we close the PR before it merges.
        self.cleanup_group(group, group_branch, delivered,
                           skip_remote_branch_delete=(reason == "queued"))

    # -- top-level loop ---------------------------------------------------- #
    def run_all(self, groups: List[Dict[str, Any]],
                group_branch: Dict[str, str],
                delivered: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        # Free every group branch from the main working tree so per-group verify
        # worktrees can check them out (finish_real leaves the repo on the last
        # group branch).
        self._git("checkout", self.base)

        merged: List[str] = []
        quarantined: Dict[str, str] = {}
        self_merged: Dict[str, str] = {}
        armed: Dict[str, str] = {}
        post_merge_regressed: Dict[str, str] = {}
        lock = threading.Lock()

        # Verify in dependency WAVES (base before its dependents). Within a wave
        # the groups are mutually independent, so their PRs -- and crucially their
        # multi-minute CI waits -- are verified CONCURRENTLY instead of serially
        # (#15). Shared .git mutations inside verify_one are serialised by
        # self._git_lock; the CI polling + gh calls overlap.
        pending = [g for g in groups if group_branch.get(g["name"]) is not None]
        while pending:
            ready: List[Dict[str, Any]] = []
            deferred: List[Dict[str, Any]] = []
            for g in pending:
                deps = g.get("depends_on", [])
                dep_q = next((d for d in deps if d in quarantined or d in self_merged), None)
                if dep_q:  # a failed base cascades to its dependents (dropped, not deferred)
                    quarantined[g["name"]] = f"base group '{dep_q}' failed verification"
                    self.log(f"  SKIP [{g['name']:9}] -- {quarantined[g['name']]} (worktree kept)")
                    continue
                if next((d for d in deps if d in group_branch and d not in merged), None):
                    deferred.append(g)  # a dependency is still being verified -> next wave
                else:
                    ready.append(g)
            if not ready:
                for g in deferred:  # no progress possible -> quarantine the remainder
                    quarantined[g["name"]] = "dependency never merged"
                    self.log(f"  SKIP [{g['name']:9}] -- dependency never merged (worktree kept)")
                break
            if len(ready) == 1:
                self.verify_one(ready[0], group_branch[ready[0]["name"]],
                                delivered, merged, quarantined, lock, self_merged, armed,
                                post_merge_regressed)
            else:
                self.log(f"  VERIFY WAVE [parallel x{len(ready)}]: "
                         f"{', '.join(g['name'] for g in ready)}")
                with ThreadPoolExecutor(max_workers=len(ready)) as ex:
                    futs = [ex.submit(self.verify_one, g, group_branch[g["name"]],
                                      delivered, merged, quarantined, lock, self_merged, armed,
                                      post_merge_regressed)
                            for g in ready]
                    for fut in futs:
                        fut.result()
            pending = deferred

        if quarantined:
            self.log("VERIFY: quarantined groups (worktrees kept for inspection):")
            for n, r in quarantined.items():
                self.log(f"  - {n}: {r}")
        if self_merged:
            self.log("VERIFY: SELF-MERGE VIOLATIONS (worktrees kept for inspection):")
            for n, r in self_merged.items():
                self.log(f"  - {n}: {r}")
        if post_merge_regressed:
            self.log("VERIFY: POST-MERGE REGRESSIONS (merged, but broke the cumulative "
                     "base -- human review required, no automatic revert):")
            for n, r in post_merge_regressed.items():
                self.log(f"  - {n}: {r}")
        self.log(f"VERIFY DONE: {len(merged)} merged, "
                 f"{len(armed)} auto-merge armed (unconfirmed), "
                 f"{len(quarantined)} quarantined, "
                 f"{len(self_merged)} self-merge violation(s), "
                 f"{len(post_merge_regressed)} post-merge regression(s).")
        return {
            "merged": merged,
            "automerge_armed": armed,
            "quarantined": quarantined,
            "self_merged": self_merged,
            "post_merge_regressed": post_merge_regressed,
            "automerge_evidence": dict(self._automerge_evidence),
            "preflight_fallbacks": dict(self._preflight_fallbacks),
        }


def verify_and_cleanup(repo: Path, remote: str, base: str, spec_id: str,
                       groups: List[Dict[str, Any]],
                       group_branch: Dict[str, str],
                       delivered: Optional[Dict[str, List[str]]] = None,
                       git_lock: Optional[threading.Lock] = None,
                       **kwargs) -> Dict[str, Any]:
    """Convenience wrapper -- see Verifier for the injectable seams.

    `delivered` (group name -> task ids that actually merged) lets cleanup keep a
    split-out, quarantined task's worktree while tearing down the delivered ones.
    `git_lock` (optional): shared process-wide registry lock; when supplied all
    worktree-registry mutations (add/remove/branch-delete/prune) serialize on it.
    """
    return Verifier(repo, remote, base, spec_id, git_lock=git_lock, **kwargs).run_all(
        groups, group_branch, delivered)
