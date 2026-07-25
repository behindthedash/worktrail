#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- cold-worker dispatch + report-back.

A spawned worker starts COLD (a new agent inherits nothing but its prompt), so
the prompt must carry every pointer it needs. This module:

  build_worker_prompt(role, task, ctx)
      Render the filled worker brief for a role (implement | review | fix |
      cleanup) per design doc section 6. Common fields + per-step action +
      report-back instructions. The worker reads its task file / spec / review
      file from the worktree on disk; the prompt only points at them.

  parse_report_back(text)
      Extract + validate the JSON report-back from a worker's final message
      (the tool result the coordinator sees).

  transition(role, report, retry_count)
      The review/fix loop, at the state level: map a report-back to the task's
      next status. Reused review gate semantics (PASSED -> cleanup,
      FAILED -> fix, 3 strikes -> escalate) match the framework's ralph-loop.

  apply_report(tasks, report, role, ...)
      Find the task and apply the transition + record head_sha / review_status.

Review runs as a DIFFERENT agent than the implementer (locked decision 13.3):
see agent_for().

Pure logic -- no agents, no git, no FS. `python3 dispatch.py demo` shows it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

ROLE_IMPLEMENT = "implement"
ROLE_REVIEW = "review"
ROLE_FIX = "fix"
ROLE_CLEANUP = "cleanup"
ROLES = (ROLE_IMPLEMENT, ROLE_REVIEW, ROLE_FIX, ROLE_CLEANUP)

# Post-PR verify-stage roles. Unlike the four task roles above, these operate on
# a GROUP branch (one open PR), not a single task file: `resolve` reconciles a PR
# that has gone CONFLICTING against its base; `ci-fix` repairs a red CI run. They
# are driven by the verify stage (verify.py), not by the task review/fix loop, so
# they are intentionally NOT in ROLES (which gates the task state machine).
ROLE_RESOLVE = "resolve"
ROLE_CI_FIX = "ci-fix"
# Assembly-time conflict: task branches conflict during group branch construction (before any PR
# exists). The worker resolves the conflict in the integration worktree and commits — no push.
ROLE_ASSEMBLY_RESOLVE = "assembly-resolve"
GROUP_ROLES = (ROLE_RESOLVE, ROLE_CI_FIX, ROLE_ASSEMBLY_RESOLVE)

# Judgment roles (DEC-003, REQ-NR003): task-level judgment calls where the
# task's own per-task agent override and any tier match must NEVER decide the
# agent -- only a per-role override or the run default do -- so a review
# verdict, PR-conflict resolution, or CI fix stays independent of whichever
# agent/tier implemented the task. Consulted by agent_for()'s precedence.
JUDGMENT_ROLES = frozenset({ROLE_REVIEW, ROLE_RESOLVE, ROLE_CI_FIX, ROLE_ASSEMBLY_RESOLVE})

# Locked decision 13.3: review uses a DIFFERENT agent type than the implementer.
# DEFAULT_IMPLEMENT_AGENT is no longer hardcoded — it's resolved from the
# invocation context carried through ctx["default_agent"]. The Go front door
# resolves the agent CLI once per invocation and passes it through all
# downstream paths. An absent default_agent still falls back to "claude" as
# the outermost compatibility layer (pre-existing tests, non-Go callers).
DEFAULT_IMPLEMENT_AGENT: str | None = None
DEFAULT_REVIEWER_AGENT = "code-reviewer"

MAX_REVIEW_RETRIES = 3


# --------------------------------------------------------------------------- #
# Agent selection
# --------------------------------------------------------------------------- #
def _entry(value: Any) -> Dict[str, Any]:
    """Normalize one agent-resolution entry into `{"agent_cli", "agent_model"}`.

    `value` is either a bare agent_cli string (`"codex"`) or a
    `{"agent_cli":.., "agent_model":..}` dict -- the same shape
    `policy.py`'s `_validate_agent_entry()` / `resolve_routing()` produce for
    `routing.roles`/`routing.tiers` entries (TASK-001), so callers can pass
    those dicts straight through. Anything else normalizes to
    `{"agent_cli": None, "agent_model": None}` so callers can treat a missing
    match and a malformed entry identically (fall through to the next tier of
    precedence).
    """
    if isinstance(value, str):
        return {"agent_cli": value, "agent_model": None}
    if isinstance(value, dict):
        return {"agent_cli": value.get("agent_cli"), "agent_model": value.get("agent_model")}
    return {"agent_cli": None, "agent_model": None}


def agent_for(
    role: str,
    task: Dict[str, Any],
    reviewer_agent: str = DEFAULT_REVIEWER_AGENT,
    default_agent: str | None = None,
    *,
    role_agent_map: Optional[Dict[str, Any]] = None,
    tier_map: Optional[Dict[Tuple[Any, Any], Any]] = None,
) -> Dict[str, Any]:
    """Resolve `{"agent_cli", "agent_model"}` for one spawn -- the canonical
    per-spawn agent-resolution function (REQ-015/016/017). Pure, stdlib-only,
    deterministic (REQ-NR002): same inputs always produce the same output.
    Called by TASK-007's `LiveSpawn.__call__` for every task/group spawn.

    Args:
        role: one of ROLES (implement/review/fix/cleanup) or GROUP_ROLES
            (resolve/ci-fix/assembly-resolve).
        task: the task dict; `task["agent"]` is its explicit per-task
            override, `task.get("complexity")`/`task.get("domain")` its tier
            key.
        reviewer_agent: the review role's run default (independent reviewer,
            locked decision 13.3) -- unchanged pre-spec parameter.
        default_agent: the run-level default for every other role.
        role_agent_map: `{role_name: agent-entry}` -- `routing.roles` /
            `--role-agent-map` (TASK-001), keyed by role name (`"implement"`,
            `"review"`, ...).
        tier_map: `{(complexity, domain): agent-entry}` -- a resolved
            `routing.tiers` match table keyed by the task's own
            `(complexity, domain)` pair.

    Precedence for JUDGMENT_ROLES (review/resolve/ci-fix/assembly-resolve --
    DEC-003, REQ-017, REQ-NR003): only (2) role_agent_map override, else
    (4) run default (reviewer_agent for review, default_agent/"claude"
    otherwise) are ever consulted. The task's own per-task override and
    tier_map are NEVER consulted for these roles, regardless of what they
    contain.

    Precedence for implement/fix/cleanup (REQ-015): (1) task["agent"]
    explicit per-task override, (2) role_agent_map override, (3) tier_map
    match on (complexity, domain), (4) run default (default_agent or
    "claude").

    With no role_agent_map, no tier_map, and no per-task override, this
    returns exactly the run default for every role -- pre-spec behavior
    preserved (REQ-016).
    """
    role_agent_map = role_agent_map or {}

    if role in JUDGMENT_ROLES:
        override = _entry(role_agent_map[role]) if role in role_agent_map else None
        if override and override["agent_cli"]:
            return override
        if role == ROLE_REVIEW:
            return {"agent_cli": reviewer_agent, "agent_model": None}  # independent reviewer (13.3)
        return {"agent_cli": default_agent or "claude", "agent_model": None}

    # implement / fix / cleanup
    if task.get("agent"):
        return {"agent_cli": task["agent"], "agent_model": None}
    if role in role_agent_map:
        override = _entry(role_agent_map[role])
        if override["agent_cli"]:
            return override
    if tier_map:
        match = tier_map.get((task.get("complexity"), task.get("domain")))
        if match is not None:
            resolved = _entry(match)
            if resolved["agent_cli"]:
                return resolved
    return {"agent_cli": default_agent or "claude", "agent_model": None}


# --------------------------------------------------------------------------- #
# Prompt building (cold-worker brief, design doc section 6)
# --------------------------------------------------------------------------- #
_ROLE_ACTION = {
    ROLE_IMPLEMENT: (
        "Implement ONLY the files in scope, per the task's Acceptance Criteria and "
        "Files-to-Create. Write the tests the task specifies and run them. Commit. "
        "If a dependency's files are listed above under 'Read first', verify with "
        "`ls` that they are present in this worktree before concluding they are "
        "missing -- report context_quality: insufficient with a specific "
        "missing_context entry only after that check fails, never on assumption."
    ),
    ROLE_REVIEW: (
        "Run `git diff {base_commit}..HEAD` to see what was built. "
        "Review the diff against ONLY the success-criteria and AC in your task file — "
        "these are your complete checklist. "
        "Do NOT read the spec, data-model, contracts, or knowledge-graph. "
        "Open `{spec_folder}tasks/{task_id}.md` and, for each `- [ ]` under "
        "`## Acceptance Criteria` and `## Definition of Done (DoD)`, tick it to "
        "`- [x]` ONLY when the diff evidence confirms that item is genuinely met -- "
        "leave any unconfirmed item unticked and record it as a finding. "
        "Write `{spec_folder}reviews/{task_id}-review.md` with "
        "frontmatter `review_status: PASSED|FAILED`, `critical_issues:`, `major_issues:`. "
        "`review_status: PASSED` requires EVERY AC/DoD checkbox ticked -- a single "
        "unticked AC/DoD item after your review is itself sufficient grounds for "
        "`FAILED` plus a finding for that item. "
        "Commit the task-file checkbox edit alongside the review file."
    ),
    ROLE_FIX: (
        "Read `{spec_folder}reviews/{task_id}-review.md` and fix ONLY the listed findings. "
        "Re-run the task's tests. Commit."
    ),
    ROLE_CLEANUP: (
        "Run cleanup on ONLY the files this task changed: remove debug logs + "
        "unused imports. No behavior changes. "
        "Format ONLY if the repo actually CONFIGURES a formatter -- a `format`/"
        "`lint:fix` script in package.json, or a config file "
        "(.prettierrc*/.editorconfig/pyproject [tool.black|tool.ruff]). If so, run "
        "THAT command, scoped to the changed files. NEVER run a bare global "
        "formatter (`npx prettier --write`, `black .`) on a repo with no config: "
        "it reflows whole files and flips quote/semicolon style, burying the real "
        "diff. If no formatter is configured, skip formatting. "
        "Before committing, set the `status` frontmatter field of "
        "`{spec_folder}tasks/{task_id}.md` to `completed` — change ONLY that field, "
        "leave every other line byte-for-byte unchanged. "
        "(Exception to the 'Do NOT modify docs/specs/** status' hard rule: this "
        "write-back is explicitly permitted for your own task file's `status` field "
        "only; no other docs/specs/** file or status may be altered.) Commit."
    ),
}


def build_worker_prompt(
    role: str,
    task: Dict[str, Any],
    ctx: Dict[str, Any],
    extra_reads: List[str] | None = None,
    by_id: Dict[str, Any] | None = None,
    external_deps_by_ref: Dict[str, Any] | None = None,
) -> str:
    """Render the filled cold-worker brief. ctx: spec_id, spec_folder,
    worktree_path, branch, base_commit (review), reviewer_agent (optional),
    default_agent (optional — resolved from invocation context; defaults to
    "claude" for backward compatibility).
    extra_reads: additional paths injected into 'Read first, in order' when a
    prior worker reported context_quality=insufficient (adaptive read-widening).
    by_id: task-id -> task dict lookup for the whole spec, used (ROLE_IMPLEMENT
    only) to surface a declared dependency's delivered files so the worker can
    verify they exist before assuming they're missing.
    external_deps_by_ref: ref (`<spec-id>/<task-id>`, exactly as it appears in
    task["external_deps"]) -> the resolved sibling task's dict, used
    (ROLE_IMPLEMENT only) the same way as by_id but for cross-spec
    `external-dependencies:` entries. The caller must resolve each ref via
    loader.resolve_external_dependency first and only include an entry here
    when `satisfied` is True -- an absent ref (unresolved or unsatisfied) is
    silently skipped here, same as an unknown dep_id in by_id, so a task's
    prompt never implies a sibling's files are already present when they
    aren't (contracts/worker-context-worktree-stacking.md Part A)."""
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    tid = task["id"]
    scope = ", ".join(task.get("files", [])) or "(see task file)"
    action = _ROLE_ACTION[role].format(
        task_id=tid,
        base_commit=ctx.get("base_commit", "HEAD"),
        spec_folder=ctx["spec_folder"],
    )
    # Role-differentiated reads: each role loads only what it actually needs.
    # Review and fix workers skip the full spec/plan (~60-80 KB saved per call).
    # Implement workers default to task-file-only: spec-to-tasks generates tasks
    # with full AC, Technical Context, file lists, and DoD — redundant spec reads
    # inflate input tokens on every worker even with cache hits. Tasks with
    # needs-spec: true frontmatter opt back in when the task file is thin.
    if role == ROLE_IMPLEMENT:
        reads = [
            f"{ctx['spec_folder']}tasks/{tid}.md   "
            f"(your brief: AC, Technical Context, Files, Test Instructions, DoD — "
            f"do NOT read spec, data-model, contracts, or knowledge-graph unless "
            f"your task file has needs-spec: true in its frontmatter)",
        ]
        if task.get("needs_spec"):
            reads.append(
                f"{ctx['spec_folder']}spec.md + data-model.md + contracts/   "
                f"(read only the sections referenced by this task's imp-requirements; skip unrelated phases)"
            )
            reads.append(
                f"{ctx['spec_folder']}knowledge-graph.json   (existing patterns -- do NOT re-explore the repo)"
            )
        if task.get("deps") and by_id:
            for dep_id in task["deps"]:
                dep_task = by_id.get(dep_id)
                if not dep_task:
                    continue
                for dep_file in dep_task.get("files", []):
                    reads.append(
                        f"{dep_file}   (delivered by {dep_id} -- already in this worktree; "
                        f"verify with `ls {dep_file}` before assuming missing)"
                    )
        if task.get("external_deps") and external_deps_by_ref:
            for ref in task["external_deps"]:
                sibling_task = external_deps_by_ref.get(ref)
                if not sibling_task:
                    continue
                for dep_file in sibling_task.get("files", []):
                    reads.append(
                        f"{dep_file}   (delivered by {ref} -- already in this worktree; "
                        f"verify with `ls {dep_file}` before assuming missing)"
                    )
    elif role == ROLE_REVIEW:
        reads = [
            f"{ctx['spec_folder']}tasks/{tid}.md   "
            f"(success-criteria and AC — your complete checklist; "
            f"do NOT read spec, data-model, contracts, or knowledge-graph)",
        ]
    elif role == ROLE_FIX:
        reads = [
            f"{ctx['spec_folder']}reviews/{tid}-review.md   (the findings to fix — read this first)",
            f"{ctx['spec_folder']}tasks/{tid}.md   "
            f"(AC only — verify your fixes meet acceptance criteria; "
            f"do NOT read spec, data-model, contracts, or knowledge-graph)",
        ]
    else:  # ROLE_CLEANUP
        reads = [
            f"{ctx['spec_folder']}tasks/{tid}.md   (files scope only — which files were changed)",
        ]

    if extra_reads:
        reads.extend(
            f"{p}   (widened context: prior worker reported this missing — read before fixing)"
            for p in extra_reads
        )

    return "\n".join(
        [
            f"You are the {role.upper()} worker for {tid} of spec {ctx['spec_id']}.",
            f"Agent: {agent_for(role, task, ctx.get('reviewer_agent', DEFAULT_REVIEWER_AGENT), ctx.get('default_agent'))['agent_cli']}",
            "",
            f"Worktree (operate ONLY here): {ctx['worktree_path']}",
            f"Branch (already checked out): {ctx['branch']}",
            "",
            "Read first, in order:",
            *[f"  {i}. {r}" for i, r in enumerate(reads, 1)],
            "",
            f"Scope (only touch these): {scope}",
            f"Task: {action}",
            "",
            "Hard rules:",
            "  - Touch no files outside scope. Do NOT modify docs/specs/** status.",
            "  - Never commit files under docs/specs/*/reviews/ — they are gitignored "
            "point-in-time snapshots. If your task writes a review file there, do not "
            "stage or commit it.",
            "  - Do NOT modify package.json or package-lock.json (toolchain is already installed).",
            "  - Do NOT edit orchestrator state or run git worktree commands.",
            "  - Do NOT hand-roll a background-wait loop (while true / until / sleep) "
            "or poll for a build/test/CI to finish: run commands to completion, then "
            "report back. The orchestrator does the waiting, not you.",
            "  - If `tsconfig.json` exists in the repo root, run "
            "`tsc -p tsconfig.json --noEmit` before committing. Fix ALL type errors "
            "it reports before pushing — CI will surface the same errors one at a time.",
            "  - Commit your work with a clear message.",
            "",
            "End your reply with EXACTLY one fenced ```json block (the report-back):",
            '  {"task": "%s", "step": "%s", "status": "success|failed",' % (tid, role),
            '   "head_sha": "<sha>", "files_touched": [...], "tests": "passed|failed|none",',
            '   "review_status": "PASSED|FAILED|null", "critical_issues": 0, "major_issues": 0,',
            '   "context_quality": "sufficient|too_much|insufficient",',
            '   "missing_context": [],',
            '   "notes": "<one line>"}',
            *(
                # Clarify the status/review_status contract to prevent workers from
                # setting status:"failed" to mean "code has defects", which bypasses
                # the fix→retry loop. status signals whether the worker ran; code
                # verdict belongs exclusively in review_status.
                [
                    '  REVIEW FIELD GUIDE: set status="success" whenever the review ran'
                    ' (even when review_status="FAILED"). Set status="failed" ONLY if'
                    " the review itself crashed or could not execute. Code quality verdict"
                    " goes in review_status (PASSED|FAILED), never in status."
                ]
                if role == ROLE_REVIEW
                else []
            ),
        ]
    )


# --------------------------------------------------------------------------- #
# Group-level prompt building (post-PR verify workers: resolve | ci-fix)
# --------------------------------------------------------------------------- #
def build_group_prompt(role: str, group: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    """Render the cold-worker brief for a GROUP-level verify worker.

    role  -- ROLE_RESOLVE (PR conflicts with base) or ROLE_CI_FIX (CI is red).
    group -- the plan_groups() group dict: name, tasks, reqs, depends_on.
    ctx   -- spec_id, group_branch, base_branch, remote, worktree_path, and for
             ci-fix: failing_checks (str) + failure_log (str, may be a tail).

    The worker runs IN the group's worktree (already on group_branch) and must
    push its fix to `remote group_branch` so the open PR updates. It reports back
    with the SAME fenced-JSON contract as a task worker so verify.py can reuse
    parse_report_back().
    """
    if role not in GROUP_ROLES:
        raise ValueError(f"unknown group role: {role}")
    name = group["name"]
    gb = ctx["group_branch"]
    base = ctx["base_branch"]
    remote = ctx.get("remote", "origin")
    tasks = ", ".join(group.get("tasks", [])) or "(group)"

    if role == ROLE_RESOLVE:
        action = [
            f"The open PR for group `{name}` (branch `{gb}`, tasks {tasks}) is "
            f"CONFLICTING with its base `{base}`.",
            f"In this worktree (already on `{gb}`):",
            f"  1. `git fetch {remote}` then merge `{remote}/{base}` into `{gb}`.",
            "  2. Resolve every conflict MINIMALLY, preserving the intent of BOTH "
            "sides (the base advanced; keep your group's changes and the base's).",
            "  3. Run the affected tests/build to confirm nothing regressed.",
            f"  4. Commit the merge and `git push {remote} {gb}`.",
        ]
        hard_rules_extra = [
            f"  - Push to `{remote} {gb}` so the existing PR updates (do NOT open a "
            "new PR or merge it yourself).",
            "  - Do NOT wait for CI or hand-roll a background-wait loop (while true / "
            "until / sleep) after pushing: push, then report back. The orchestrator "
            "re-polls CI on the 3-strikes budget; it does the waiting, not you.",
        ]
    elif role == ROLE_ASSEMBLY_RESOLVE:
        conflicting = ctx.get("conflicting_branch", "(unknown task branch)")
        action = [
            f"The merge of task branch `{conflicting}` into the integration branch for "
            f"group `{name}` (tasks {tasks}) has CONFLICTS.",
            "The worktree is already in a conflicted `git merge` state.",
            "  1. `git diff --name-only --diff-filter=U` to list conflicted files.",
            "  2. Open each file and resolve every conflict MINIMALLY, preserving the "
            "intent of BOTH sides (keep both tasks' changes where possible).",
            "  3. `git add <resolved files>` then `git merge --continue` to complete the merge.",
            "  4. Run the affected tests to confirm nothing regressed.",
        ]
        hard_rules_extra = [
            "  - Do NOT push or open a PR — the branch is build-only at this stage; "
            "the orchestrator will push and open the PR after all tasks are merged.",
            "  - Do NOT wait for CI or hand-roll a background-wait loop.",
        ]
    else:  # ROLE_CI_FIX
        action = [
            f"CI is FAILING on the open PR for group `{name}` (branch `{gb}`, " f"tasks {tasks}).",
            f"Failing checks: {ctx.get('failing_checks', '(unknown)')}",
            "Failure log (tail):",
            "------------------------------------------------------------------",
            (ctx.get("failure_log") or "(no log captured)").strip()[-4000:],
            "------------------------------------------------------------------",
            f"In this worktree (already on `{gb}`):",
            "  1. Diagnose the ROOT CAUSE from the log and the diff. Use the minimum "
            "change that fixes the root cause — do NOT add longer timeouts as a proxy "
            "fix for race conditions or flaky async behaviour.",
            "  2. Fix it with the SMALLEST change that makes CI pass. Do not "
            "refactor unrelated code or change unrelated behavior.",
            "  3. Reproduce the check locally (run the same test/build) and "
            "confirm it now passes.",
            f"  4. Commit and `git push {remote} {gb}`.",
            "",
            "Common React testing pitfall: `mockResolvedValueOnce` is consumed in "
            "arrival order — background fetches (setTimeout, deps-less useEffect, "
            "image loaders) can steal mocks before the component under test calls. "
            "Use `mockImplementation(url => ...)` with URL-based routing instead.",
        ]
        hard_rules_extra = [
            f"  - Push to `{remote} {gb}` so the existing PR updates (do NOT open a "
            "new PR or merge it yourself).",
            "  - Do NOT wait for CI or hand-roll a background-wait loop (while true / "
            "until / sleep) after pushing: push, then report back. The orchestrator "
            "re-polls CI on the 3-strikes budget; it does the waiting, not you.",
        ]

    branch_line = (
        f"Branch (already in conflicted merge state): {gb}"
        if role == ROLE_ASSEMBLY_RESOLVE
        else f"Branch (already checked out): {gb}"
    )

    return "\n".join(
        [
            f"You are the {role.upper()} worker for group `{name}` of spec " f"{ctx['spec_id']}.",
            "",
            f"Worktree (operate ONLY here): {ctx['worktree_path']}",
            branch_line,
            "",
            *action,
            "",
            "Hard rules:",
            "  - Make the smallest change that resolves the problem.",
            "  - Do NOT modify docs/specs/** status or orchestrator state.",
            "  - Do NOT run `gh pr merge`, enable auto-merge, or take any merge action "
            "yourself, even if a merge/auto-merge command itself is what's failing — "
            "that is the orchestrator's job, not yours.",
            "  - Do NOT modify `.github/workflows/**` or any other shared CI/merge "
            "configuration as a side effect of fixing this task. If the failure "
            'genuinely requires a workflow change, report back status="failed" with '
            "that in `notes` instead of patching it yourself.",
            *hard_rules_extra,
            "",
            "End your reply with EXACTLY one fenced ```json block (the report-back):",
            '  {"task": "%s", "step": "%s", "status": "success|failed",' % (name, role),
            '   "head_sha": "<sha>", "files_touched": [...], "tests": "passed|failed|none",',
            '   "notes": "<one line>"}',
        ]
    )


# --------------------------------------------------------------------------- #
# Report-back parsing
# --------------------------------------------------------------------------- #
def parse_report_back(text: str) -> Dict[str, Any]:
    """Extract + validate the report-back JSON from a worker's final message."""
    fenced = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if fenced:
        blob = fenced[-1].strip()
    else:  # fallback: last {...}
        start, end = text.rfind("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no report-back JSON block found")
        blob = text[start : end + 1]
    try:
        data, _ = json.JSONDecoder().raw_decode(blob)
    except json.JSONDecodeError as e:
        raise ValueError(f"report-back JSON invalid: {e}")
    missing = [k for k in ("task", "step", "status") if k not in data]
    if missing:
        raise ValueError(f"report-back missing required fields: {missing}")
    if data["status"] not in ("success", "failed"):
        raise ValueError(f"report-back bad status: {data['status']!r}")
    return data


# --------------------------------------------------------------------------- #
# Transition (the review/fix loop, at the state level)
# --------------------------------------------------------------------------- #
def transition(
    role: str, report: Dict[str, Any], retry_count: int, max_retries: int = MAX_REVIEW_RETRIES
) -> Tuple[str, int]:
    """Map a report-back to the task's next status. Returns (status, retry)."""
    # For review, route on review_status regardless of the status field. A review
    # worker that finds defects may return status:"failed" (treating it as a code-
    # quality verdict), but status only signals worker execution failure for non-review
    # roles. Checking review_status first prevents the fix cycle from being bypassed.
    if role == ROLE_REVIEW:
        rs = (report.get("review_status") or "").upper()
        if rs not in ("PASSED", "FAILED"):
            raise ValueError(
                "successful review report missing valid review_status "
                "(PASSED|FAILED) -- treat as malformed, re-dispatch review"
            )
        if rs == "PASSED":
            return "cleaning", 0
        retry_count += 1
        if retry_count >= max_retries:
            return "escalated", retry_count  # 3 strikes -> circuit breaker
        return "fixing", retry_count
    if report["status"] == "failed":
        return "failed", retry_count
    if role in (ROLE_IMPLEMENT, ROLE_FIX):
        return "reviewing", retry_count
    if role == ROLE_CLEANUP:
        return "done", retry_count
    raise ValueError(f"unknown role: {role}")


def apply_report(
    tasks: List[Dict[str, Any]],
    report: Dict[str, Any],
    role: str,
    max_retries: int = MAX_REVIEW_RETRIES,
) -> Tuple[str, str]:
    """Find the report's task in coordinator state and apply the transition.
    Returns (old_status, new_status)."""
    task = next((t for t in tasks if t["id"] == report["task"]), None)
    if task is None:
        raise ValueError(f"report references unknown task {report['task']!r}")
    old = task.get("status", "pending")
    new, retry = transition(role, report, task.get("retry_count", 0), max_retries)
    task["status"] = new
    task["retry_count"] = retry
    if report.get("head_sha"):
        task["head_sha"] = report["head_sha"]
    if role == ROLE_REVIEW and report.get("review_status"):
        task["review_status"] = report["review_status"].upper()
    return old, new


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
def _demo() -> None:
    task = {
        "id": "TASK-002",
        "deps": ["TASK-001"],
        "files": ["src/orders/service.ts", "src/orders/service.spec.ts"],
        "status": "implementing",
        "retry_count": 0,
        "agent": "claude",
    }
    ctx = {
        "spec_id": "025-feature",
        "spec_folder": "docs/specs/025-feature/",
        "worktree_path": "/repos/app-worktrees/025-feature-task-002",
        "branch": "025-feature/task-002",
        "base_commit": "abc1234",
    }

    print("=" * 64)
    print("IMPLEMENT worker prompt")
    print("=" * 64)
    print(build_worker_prompt(ROLE_IMPLEMENT, task, ctx))

    print("\n" + "=" * 64)
    print(
        f"REVIEW worker prompt  (routes to DIFFERENT agent: "
        f"{agent_for(ROLE_REVIEW, task)['agent_cli']})"
    )
    print("=" * 64)
    print(build_worker_prompt(ROLE_REVIEW, task, ctx))

    print("\n" + "=" * 64)
    print("REPORT-BACK -> STATE TRANSITIONS")
    print("=" * 64)
    samples = [
        (
            ROLE_IMPLEMENT,
            'done.\n```json\n{"task":"TASK-002","step":"implement",'
            '"status":"success","head_sha":"de1f7a0","tests":"passed"}\n```',
        ),
        (
            ROLE_REVIEW,
            '```json\n{"task":"TASK-002","step":"review","status":"success",'
            '"review_status":"FAILED","critical_issues":1,"major_issues":2}\n```',
        ),
        (
            ROLE_FIX,
            '```json\n{"task":"TASK-002","step":"fix","status":"success",'
            '"head_sha":"9c2b1aa"}\n```',
        ),
        (
            ROLE_REVIEW,
            '```json\n{"task":"TASK-002","step":"review","status":"success",'
            '"review_status":"PASSED"}\n```',
        ),
        (
            ROLE_CLEANUP,
            '```json\n{"task":"TASK-002","step":"cleanup","status":"success",'
            '"head_sha":"f00dfee"}\n```',
        ),
    ]
    state = [dict(task)]
    for role, raw in samples:
        rep = parse_report_back(raw)
        old, new = apply_report(state, rep, role)
        extra = f"  review_status={rep.get('review_status')}" if role == ROLE_REVIEW else ""
        print(
            f"  {role:9} report parsed -> {old:12} -> {new:10}"
            f"  (retry={state[0]['retry_count']}){extra}"
        )

    print("\n--- malformed report-back is rejected ---")
    try:
        parse_report_back("I finished the task, looks good!")
    except ValueError as e:
        print(f"  rejected: {e}")
    try:
        apply_report(
            [dict(task)],
            parse_report_back(
                '```json\n{"task":"TASK-002","step":"review",' '"status":"success"}\n```'
            ),
            ROLE_REVIEW,
        )
    except ValueError as e:
        print(f"  rejected: {e}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cold-worker dispatch + report-back")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="Show prompt building + report-back transitions")
    args = p.parse_args(argv)
    if args.cmd == "demo":
        _demo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
