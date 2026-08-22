#!/usr/bin/env python3
"""
Phase 5.5's predicate re-check: re-derives whether a brief's captured
staleness *predicate* is still true, before `check_brief_staleness.py`'s
probe-extraction/git-search runs and before any operator prompt.

This module is deliberately a sibling of `check_brief_staleness.py`, not a
new mode inside it: `check_brief_staleness.check()` is prose-probe-shaped
(text in, matches out), while the predicate re-check is
frontmatter-and-registry-shaped (a brief's `drift-source` in, a per-finding
classification out). See
`openspec/changes/stale-brief-precheck-refutation-tier/design.md` -
Decisions.

`PREDICATE_RECHECKS` maps a brief's `drift-source` value to the function
that re-derives its predicate, keyed so a future sweep can register its own
predicate without touching `recheck()`'s own control flow. Only the
`checkbox-drift-sweep` predicate is registered so far.

`recheck()` never raises to its caller -- any condition it cannot answer
(no predicate captured, an unrecognized `drift-source`, a registered
predicate that itself fails) degrades to a non-terminal outcome so Phase 5.5
falls through to today's unmodified brief-staleness flow.

The two terminal outcomes each reuse an existing queue/run-record pattern
rather than inventing a new one (see design.md - Decisions): `"resolved"`
reuses `work_queue.py done`'s `--note`, and `"still-true"` reuses the
post-Phase-6 `worktrail-run-record append "$RUN" decisions "..."` pattern the
probe-based "proceed" outcome already uses in `brief-staleness-check.md`.
`format_still_true_evidence` builds that exact line from `recheck()`'s
`still_true` list so Phase 5.5's skill doc has one canonical string to call
instead of composing prose ad hoc:

    Predicate re-check (checkbox-drift-sweep) found the staleness predicate
    still true for 2 finding(s): docs/specs/x/tasks/TASK-001.md,
    docs/specs/x/tasks/TASK-004.md. Proceeded automatically without an
    operator prompt.

Unlike the probe-based line it mirrors, there is no commit SHA or PR number
to cite -- the probe search never runs on this path -- so the still-true
task-file paths themselves are the cited evidence.

`format_resolved_closure_note` builds the mirror-image string for the
`"resolved"` outcome, passed as `work_queue.py done`'s `--note` the same way
the probe-based "close as already-delivered" branch's note is:

    worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note \\
      "Closed as already-delivered: predicate re-check (checkbox-drift-sweep)
      found the staleness predicate resolved for 2 finding(s):
      docs/specs/x/tasks/TASK-001.md, docs/specs/x/tasks/TASK-004.md. Surfaced
      by the Phase 5.5 predicate re-check; closed automatically without an
      operator prompt."

As with the still-true evidence line, the predicate re-check itself -- not a
commit SHA or PR number -- is the cited evidence, since the probe search
never runs on this path.

## Command predicates (`predicate-kind: command`)

A brief whose `drift-source` has no named predicate registered in
`PREDICATE_RECHECKS` may instead declare `predicate-kind: command` in its
frontmatter. This is a generic capture contract for a sweep whose detector
this codebase cannot import (a separate repo, a separate language): each of
the brief's `drift-findings` entries carries, in addition to the `path`
every finding already requires, a `predicate-cmd` field -- an argument
vector (a non-empty `list[str]`, never a shell string) that re-derives
whether that finding's condition still holds.

Polarity is fixed and encoded in the field name, mirroring `grep -q` and
`test`: `predicate-cmd` exits `0` when the predicate *still holds*
("still-true") and `1` when it has been resolved. Any other exit status, a
timeout, or a missing executable is an error for the whole brief's
re-check -- output text is never consulted, only the exit status.

Execution is bounded: `shell=False` always (arguments are never
shell-split or shell-interpreted, even if they contain shell
metacharacters), the working directory is pinned to the brief's `--repo`,
each command gets a 30s wall-clock timeout, and at most 20 commands are
executed per brief -- checked before the first command runs, so an
oversized brief costs zero executions. Captured stdout and stderr are
merged and truncated per finding, with truncation marked so a reader never
mistakes a clipped excerpt for the whole output.

`recheck()`'s dispatch order for a brief that carries a `drift-source` is:
1. a named predicate registered in `PREDICATE_RECHECKS` for that
   `drift-source`, if any;
2. else, `_recheck_command_predicate` if `predicate-kind == "command"`;
3. else, `outcome="unrecognized"`.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List

from ..taskformats.devkit.checkbox_audit import (
    COMPLETION_AUDIT_SECTIONS,
    _all_checkboxes_checked,
    read_task_file,
)

# Decision 5 (design.md): bounds on command-predicate execution.
_MAX_COMMANDS_PER_BRIEF = 20
_COMMAND_TIMEOUT_SECONDS = 30
_MAX_EVIDENCE_OUTPUT_CHARS = 4000
_TRUNCATION_MARKER = " ...[truncated]"


def _recheck_checkbox_drift(
    repo: Path, findings: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """Re-derive each `drift-findings` entry against the task file's current
    on-disk state.

    A finding is "still-true" when its task file is still `status:
    completed` with at least one unchecked box in
    `COMPLETION_AUDIT_SECTIONS`, and "resolved" when the file is fully
    checked or no longer `status: completed`. A finding whose task file
    can no longer be read (missing, or unparseable frontmatter) raises,
    so the caller degrades the whole brief's recheck to
    `outcome="error"` rather than reporting a partial result.
    """
    still_true: List[str] = []
    resolved: List[str] = []
    for finding in findings:
        rel_path = finding["path"]
        task_path = Path(rel_path)
        if not task_path.is_absolute():
            task_path = repo / task_path
        frontmatter, error, body = read_task_file(task_path)
        if error is not None or frontmatter is None:
            raise ValueError(f"cannot read task file {rel_path!r}: {error}")
        if frontmatter.get("status") == "completed" and not _all_checkboxes_checked(
            body, sections=COMPLETION_AUDIT_SECTIONS
        ):
            still_true.append(rel_path)
        else:
            resolved.append(rel_path)
    return {"still_true": still_true, "resolved": resolved}


def _truncate_output(output: str) -> str:
    if len(output) <= _MAX_EVIDENCE_OUTPUT_CHARS:
        return output
    return output[:_MAX_EVIDENCE_OUTPUT_CHARS] + _TRUNCATION_MARKER


def _recheck_command_predicate(
    repo: Path, findings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Re-derive each finding by executing its captured `predicate-cmd`.

    Every finding is validated (a `path`, and a `predicate-cmd` that is a
    non-empty list of strings) and the per-brief command cap is enforced
    before any command runs, so a malformed or oversized brief costs zero
    executions. Each validated command is then run with `shell=False`,
    `cwd=repo`, and a 30s timeout; exit `0` classifies the finding as
    still-true, exit `1` as resolved, and anything else -- an unexpected
    exit status, a timeout, or a missing executable -- raises so the
    caller (`recheck()`) degrades the whole brief to `outcome="error"`.
    """
    validated: List[tuple] = []
    for finding in findings:
        path = finding.get("path")
        if path is None:
            raise ValueError(f"finding missing 'path': {finding!r}")
        predicate_cmd = finding.get("predicate-cmd")
        if (
            not isinstance(predicate_cmd, list)
            or not predicate_cmd
            or not all(isinstance(arg, str) for arg in predicate_cmd)
        ):
            raise ValueError(
                f"predicate-cmd for {path!r} must be a non-empty list of "
                f"strings, got {predicate_cmd!r}"
            )
        validated.append((path, predicate_cmd))

    if len(validated) > _MAX_COMMANDS_PER_BRIEF:
        raise ValueError(
            f"{len(validated)} predicate-cmd findings exceed the per-brief "
            f"cap of {_MAX_COMMANDS_PER_BRIEF}"
        )

    still_true: List[str] = []
    resolved: List[str] = []
    evidence: List[Dict[str, Any]] = []
    for path, argv in validated:
        command = shlex.join(argv)
        try:
            proc = subprocess.run(
                argv,
                shell=False,
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"predicate-cmd for {path!r} timed out after "
                f"{_COMMAND_TIMEOUT_SECONDS}s: {command}"
            ) from exc
        except OSError as exc:
            raise ValueError(
                f"predicate-cmd for {path!r} failed to execute: {command}: {exc}"
            ) from exc

        output = _truncate_output(proc.stdout + proc.stderr)
        evidence.append(
            {"path": path, "command": command, "exit": proc.returncode, "output": output}
        )
        if proc.returncode == 0:
            still_true.append(path)
        elif proc.returncode == 1:
            resolved.append(path)
        else:
            raise ValueError(
                f"predicate-cmd for {path!r} exited {proc.returncode} "
                f"(expected 0 or 1): {command}"
            )

    return {"still_true": still_true, "resolved": resolved, "evidence": evidence}


# drift-source -> recheck function. Populated as individual sweep predicates
# are implemented; an unpopulated (or unmatched) drift-source falls through
# to `predicate-kind == "command"` and then to outcome="unrecognized" in
# `recheck()` below (design.md - Decision 1).
PREDICATE_RECHECKS: Dict[str, Callable[[Path, List[Dict[str, Any]]], Dict[str, List[str]]]] = {
    "checkbox-drift-sweep": _recheck_checkbox_drift,
}


def recheck(repo: Path, frontmatter: Dict[str, Any]) -> Dict[str, object]:
    """Never raises. Returns:
    {"attempted": bool, "drift_source": str|None, "outcome": "no-predicate"|
     "unrecognized"|"error"|"still-true"|"resolved", "still_true": [...],
     "resolved": [...], "evidence": [...], "error": str|None}
    """
    drift_source = frontmatter.get("drift-source")
    if drift_source is None:
        return {
            "attempted": False,
            "drift_source": None,
            "outcome": "no-predicate",
            "still_true": [],
            "resolved": [],
            "evidence": [],
            "error": None,
        }

    # Dispatch order (design.md - Decision 1): a named predicate registered
    # for this drift-source wins; else fall back to the generic command
    # predicate if declared; else the drift-source is unrecognized.
    recheck_fn = PREDICATE_RECHECKS.get(drift_source)
    if recheck_fn is None:
        if frontmatter.get("predicate-kind") == "command":
            recheck_fn = _recheck_command_predicate
        else:
            return {
                "attempted": False,
                "drift_source": drift_source,
                "outcome": "unrecognized",
                "still_true": [],
                "resolved": [],
                "evidence": [],
                "error": None,
            }

    findings = frontmatter.get("drift-findings")
    if not findings:
        return {
            "attempted": True,
            "drift_source": drift_source,
            "outcome": "error",
            "still_true": [],
            "resolved": [],
            "evidence": [],
            "error": "drift-findings is missing or empty",
        }

    try:
        result = recheck_fn(repo, findings)
    except Exception as exc:  # noqa: BLE001 - degrade any predicate failure to outcome="error"
        return {
            "attempted": True,
            "drift_source": drift_source,
            "outcome": "error",
            "still_true": [],
            "resolved": [],
            "evidence": [],
            "error": str(exc),
        }

    still_true = result.get("still_true", [])
    resolved = result.get("resolved", [])
    evidence = result.get("evidence", [])
    outcome = "still-true" if still_true else "resolved"
    return {
        "attempted": True,
        "drift_source": drift_source,
        "outcome": outcome,
        "still_true": still_true,
        "resolved": resolved,
        "evidence": evidence,
        "error": None,
    }


def format_still_true_evidence(result: Dict[str, object]) -> str:
    """Build the exact evidence line for `recheck()`'s `"still-true"` outcome.

    Callers append this via the same post-Phase-6 pattern the probe-based
    "proceed" outcome uses (`worktrail-run-record append "$RUN" decisions
    "<this string>"`), so the run record reads the same way regardless of
    which path decided to proceed. There is no commit SHA or PR number to
    cite here -- the probe search never runs on this path -- so the
    still-true task-file paths from `result["still_true"]` are the cited
    evidence instead.
    """
    still_true = result["still_true"]
    paths = ", ".join(still_true)
    return (
        f"Predicate re-check ({result['drift_source']}) found the staleness "
        f"predicate still true for {len(still_true)} finding(s): {paths}. "
        "Proceeded automatically without an operator prompt."
    )


def format_resolved_closure_note(result: Dict[str, object]) -> str:
    """Build the exact closure note for `recheck()`'s `"resolved"` outcome.

    Callers pass this as `work_queue.py done`'s `--note` the same way the
    probe-based "close as already-delivered" branch does
    (`worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note
    "<this string>"`), so both closure paths read the same way in the
    queue's history regardless of which one fired. Unlike that sibling note,
    there is no commit SHA or PR number to cite -- the probe search never
    runs on this path -- so the predicate re-check itself, named explicitly,
    is the cited evidence, alongside the resolved task-file paths from
    `result["resolved"]`.
    """
    resolved = result["resolved"]
    paths = ", ".join(resolved)
    return (
        "Closed as already-delivered: predicate re-check "
        f"({result['drift_source']}) found the staleness predicate resolved "
        f"for {len(resolved)} finding(s): {paths}. Surfaced by the Phase 5.5 "
        "predicate re-check; closed automatically without an operator "
        "prompt."
    )


# --- CLI --------------------------------------------------------------------

def _format_human(res: Dict[str, object]) -> str:
    if not res["attempted"]:
        return f"not attempted: outcome={res['outcome']}"
    if res["outcome"] == "error":
        return f"error: {res.get('error')}"
    return (
        f"outcome={res['outcome']} drift_source={res['drift_source']!r} "
        f"still_true={res['still_true']} resolved={res['resolved']}"
    )


def main(argv=None) -> int:
    import argparse

    from ..shared.brief_frontmatter import read_frontmatter

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    frontmatter = read_frontmatter(Path(args.brief))
    res = recheck(Path(args.repo), frontmatter)

    if args.json:
        print(json.dumps(res))
    else:
        print(_format_human(res))

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
