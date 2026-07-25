#!/usr/bin/env python3
"""Universal pre-PR test gate — deterministic, stdlib-only.

Every /go route that opens a PR (orchestrator delivery groups AND one-off
claude/codex subprocess workers) must pass this gate before `gh pr create`.
When `--run` is supplied, the gate also requires a scope review in the shared
run record and rejects unresolved or blocked scope items.
It resolves the target repo's gate command from `docs/specs/go-policy.yaml`
(`pre_pr_cmd`, falling back to `integrate_smoke_cmd`) and runs it in the
worktree root, streaming output.

When the repo configures `docs_only_paths` (glob patterns mirroring its own
docs-only-bypass CI job), the gate first checks whether every changed path
against the resolved base branch matches one of those patterns. If so, it
skips `pre_pr_cmd` entirely and exits 0. Any ambiguity — no `docs_only_paths`
configured, an unresolvable base ref, an empty diff, or any unmatched path —
falls through unchanged to running the full command; this is a targeted fast
path, not a general skip mechanism.

Before any of that, the gate runs the spec-sync drift check
(`check_spec_sync.py`) against the repo's `docs/specs/` tree whenever that
directory exists — unconditionally, independent of `docs_only_paths`. The
drift this catches (a code PR that ships behavior without updating the spec's
task-plan summary / parent-spec Status header) is exactly the kind of change
a docs-only bypass must not wave through, and it must also catch code-only
diffs that never touch docs/specs/ at all — see check_spec_sync.py's module
docstring. Repos with no docs/specs/ tree are unaffected (skipped, not
failed).

Immediately after that, the gate runs the clarification-integrity check
(`check_clarification_integrity.py`) against spec files **changed in this
diff only** — unlike check_spec_sync.py, deliberately not the whole tree,
since specs that already merged before the Decision Classification gate
existed (PR #428) may legitimately carry the anti-pattern and must not fail
every future PR. See check_clarification_integrity.py's module docstring for
the exact signature.

Exit codes:
  0   command passed, the repo opted out explicitly (`pre_pr_cmd: skip`), or
      the diff was docs-only per `docs_only_paths`
  1   spec-sync drift detected in docs/specs/
  2   unconfigured — no `pre_pr_cmd`/`integrate_smoke_cmd` in the policy
      (default-deny: configure the repo or opt out explicitly; never bypass
      silently)
  3   clarification-integrity drift detected in changed docs/specs/ files
  N   the gate command's own non-zero exit code

On a PASS, passing `--risk` also prints an `AUTOMERGE LABELS:` line — the
`go:risk-<level>` label (plus `go:no-automerge` when `automerge_eligible()`
is false, OR when the base branch has no live GitHub-side required check —
see `automerge_preflight.required_checks_gate()`) to attach via
`gh pr create --label`, so the repo's own auto-merge automation (e.g.
`auto-merge.yml`) can act on that policy verdict as PR metadata instead of
trusting the calling agent to have run Phase 8 by hand.

The AUTOMERGE LABELS line is printed **before** the test command runs, not
only after — the pre-print survives shell-output truncation that can occur
when the test suite exceeds the calling agent's default bash timeout. The
line is reprinted after a PASS for terminal readers.

Usage: pre_pr_gate.py [--repo /path/to/worktree] [--print-cmd]
                       [--risk low|medium|high|critical] [--gates G1,G2]
                       [--target-branch BRANCH] [--run RUN_RECORD]
                       [--labels-only]
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .automerge_preflight import required_checks_gate
from .check_clarification_integrity import check_changed_specs
from .check_spec_sync import check_spec
from .policy import (
    POLICY_RELPATH, automerge_eligible, automerge_labels, load_policy,
)
from .run_record import _load as load_run_record

# Explicit opt-out sentinels for `pre_pr_cmd`. Deliberately narrow: an empty
# string or null means "unconfigured" (exit 2), not "skip".
SKIP_VALUES = {"skip"}
UNCONFIGURED_EXIT = 2
SPEC_SYNC_DRIFT_EXIT = 1
SCOPE_COMPLETENESS_EXIT = 1
CLARIFICATION_INTEGRITY_DRIFT_EXIT = 3
CANDIDATE_BASE_REFS = ("origin/main", "origin/master", "main", "master")


def resolve_cmd(policy: Dict[str, Any]) -> Optional[str]:
    """Resolve the gate command: pre_pr_cmd wins, integrate_smoke_cmd is the fallback."""
    for key in ("pre_pr_cmd", "integrate_smoke_cmd"):
        value = policy.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _matches_any(path: str, patterns: List[str]) -> bool:
    """fnmatch-based glob match with light gitignore-style '**' handling."""
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if path == prefix or path.startswith(prefix + "/"):
                return True
            continue
        if pattern.startswith("**/"):
            suffix = pattern[3:]
            if fnmatch.fnmatch(path, suffix) or fnmatch.fnmatch(path, pattern):
                return True
            continue
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def _resolve_base_ref(repo: Path, policy: Dict[str, Any]) -> Optional[str]:
    """Find a git ref to diff HEAD against. None if nothing resolves."""
    configured = policy.get("base_branch")
    candidates = (
        (f"origin/{configured}", configured) if configured else CANDIDATE_BASE_REFS
    )
    for ref in candidates:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=str(repo), capture_output=True, text=True,
        )
        if result.returncode == 0:
            return ref
    return None


def changed_paths(repo: Path, policy: Dict[str, Any]) -> Optional[List[str]]:
    """Changed paths between HEAD and the resolved base ref's merge-base.

    None means unresolvable (no base ref, no merge-base) — callers must treat
    that as "not docs-only" and fall through to the full gate.
    """
    base_ref = _resolve_base_ref(repo, policy)
    if base_ref is None:
        return None
    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", base_ref],
        cwd=str(repo), capture_output=True, text=True,
    )
    if merge_base.returncode != 0:
        return None
    diff = subprocess.run(
        ["git", "diff", "--name-only", merge_base.stdout.strip(), "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if diff.returncode != 0:
        return None
    return [line for line in diff.stdout.splitlines() if line.strip()]


def spec_sync_drift(repo: Path) -> List[str]:
    """Structural spec-sync failures across every numbered spec dir under
    docs/specs/. Empty list means pass or not-applicable (no docs/specs/)."""
    specs_root = repo / "docs" / "specs"
    if not specs_root.is_dir():
        return []
    failures: List[str] = []
    for spec_dir in sorted(
        d for d in specs_root.iterdir() if d.is_dir() and re.match(r"^\d+-", d.name)
    ):
        failures.extend(check_spec(spec_dir))
    return failures


def is_docs_only(repo: Path, policy: Dict[str, Any]) -> bool:
    """True iff docs_only_paths is configured and every changed path matches it."""
    patterns = policy.get("docs_only_paths") or []
    if not patterns:
        return False
    paths = changed_paths(repo, policy)
    if not paths:
        # Unresolvable diff or no changes at all — fail closed, run the full gate.
        return False
    return all(_matches_any(path, patterns) for path in paths)


def scope_review_failures(run_path: Optional[Path]) -> List[str]:
    """Return unresolved scope-review entries; absent ``--run`` is legacy-compatible."""
    if run_path is None:
        return []
    if not run_path.is_file():
        return [f"run record does not exist: {run_path}"]
    review = load_run_record(run_path).get("scope_review")
    if not review:
        return ["no scope review recorded in the run record"]
    failures: List[str] = []
    for entry in review:
        parts = entry.split(" | ", 2) if isinstance(entry, str) else []
        if len(parts) != 3:
            failures.append(f"malformed scope review entry: {entry}")
            continue
        status, item, detail = parts
        if status == "blocked":
            failures.append(f"blocked scope item: {item} ({detail})")
        elif status == "out-of-scope" and not (
            detail.startswith("different purpose:") or detail.startswith("user approved:")
        ):
            failures.append(
                f"out-of-scope item lacks a different-purpose or user-approved reason: {item}"
            )
        elif status not in {"complete", "out-of-scope"}:
            failures.append(f"unknown scope review status for {item}: {status}")
    return failures


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=".",
                   help="worktree root to gate (default: cwd)")
    p.add_argument("--print-cmd", action="store_true",
                   help="print the resolved command without running it "
                        "(exit 0 when resolvable/skip, 2 when unconfigured)")
    p.add_argument(
        "--risk", default=None, choices=("low", "medium", "high", "critical"),
        help="classifier risk for this PR — when set, a PASS also prints the "
             "AUTOMERGE LABELS line (go:risk-<level>, plus go:no-automerge "
             "when automerge_eligible() is false) to pass to `gh pr create "
             "--label`. Omit to skip label computation entirely.")
    p.add_argument("--gates", default="",
                    help="comma-separated classifier gates for --risk's eligibility check")
    p.add_argument("--target-branch", default="main",
                   help="PR target branch for --risk's eligibility check")
    p.add_argument("--labels-only", action="store_true",
                   help="print resolved PR labels without running the test command")
    p.add_argument("--run", default=None, metavar="RUN_RECORD",
                   help="shared go run record; enables mandatory scope completeness review")
    args = p.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"PRE-PR GATE: FAIL — repo path does not exist: {repo}", file=sys.stderr)
        return UNCONFIGURED_EXIT

    policy = load_policy(repo)

    if args.labels_only:
        if args.risk is None:
            print("--labels-only requires --risk", file=sys.stderr)
            return UNCONFIGURED_EXIT
        gates = [g for g in args.gates.split(",") if g]
        eligible, _reason = automerge_eligible(policy, args.risk, gates, args.target_branch)
        if eligible:
            eligible, _reason = required_checks_gate(repo, args.target_branch)
        print(" ".join(automerge_labels(eligible, args.risk)))
        return 0

    scope_failures = scope_review_failures(Path(args.run) if args.run else None)
    if scope_failures:
        print("PRE-PR GATE: FAIL — scope completeness review required:", file=sys.stderr)
        for failure in scope_failures:
            print(f"  - {failure}", file=sys.stderr)
        print("  Complete in-scope work before handoff/PR, or record a different-purpose or user-approved exclusion.", file=sys.stderr)
        return SCOPE_COMPLETENESS_EXIT

    if not args.print_cmd:
        drift = spec_sync_drift(repo)
        if drift:
            print("PRE-PR GATE: FAIL — spec/task drift detected in docs/specs/:",
                  file=sys.stderr)
            for failure in drift:
                print(f"  - {failure}", file=sys.stderr)
            print("  Run /developer-kit-specs:specs.sync on the affected spec(s) to fix.",
                  file=sys.stderr)
            return SPEC_SYNC_DRIFT_EXIT

        clarification_failures = check_changed_specs(
            repo, changed_paths(repo, policy) or []
        )
        if clarification_failures:
            print("PRE-PR GATE: FAIL — clarification-integrity issue(s) detected in "
                  "changed docs/specs/ files:", file=sys.stderr)
            for failure in clarification_failures:
                print(f"  - {failure}", file=sys.stderr)
            print("  An owner-escalated marker was resolved by inference and declared "
                  "clean — see Decision Classification in specs.spec-check.md.",
                  file=sys.stderr)
            return CLARIFICATION_INTEGRITY_DRIFT_EXIT

    if not args.print_cmd and is_docs_only(repo, policy):
        print("PRE-PR GATE: DOCS-ONLY SKIP — every changed path matched "
              f"docs_only_paths in {POLICY_RELPATH}. Record this skip in the "
              "PR body's 'Pre-PR Test Gate' section.")
        return 0

    cmd = resolve_cmd(policy)

    if cmd is not None and cmd.lower() in SKIP_VALUES:
        print(f"PRE-PR GATE: SKIP — explicit 'pre_pr_cmd: {cmd}' in {POLICY_RELPATH}. "
              "Record this skip in the PR body's 'Pre-PR Test Gate' section.")
        return 0

    if cmd is None:
        print("PRE-PR GATE: FAIL — unconfigured (default-deny).", file=sys.stderr)
        print(f"  No pre_pr_cmd or integrate_smoke_cmd in {repo / POLICY_RELPATH}.",
              file=sys.stderr)
        print("  Every /go route must pass a repo-defined test gate before opening a PR.",
              file=sys.stderr)
        print('  Fix: add   pre_pr_cmd: "<fast command mirroring the CI merge gate>"',
              file=sys.stderr)
        print("  to that file (or   pre_pr_cmd: skip   to opt the repo out explicitly).",
              file=sys.stderr)
        return UNCONFIGURED_EXIT

    if args.print_cmd:
        print(cmd)
        return 0

    print(f"PRE-PR GATE: running in {repo}")
    print(f"  $ {cmd}", flush=True)

    if args.risk is not None:
        gates = [g for g in args.gates.split(",") if g]
        eligible, reason = automerge_eligible(policy, args.risk, gates, args.target_branch)
        if eligible:
            eligible, reason = required_checks_gate(repo, args.target_branch)
        labels = automerge_labels(eligible, args.risk)
        label_line = f"AUTOMERGE LABELS: {' '.join(labels)}  (eligible={eligible}: {reason})"
        hint_line = f"  Pass to PR creation: gh pr create {' '.join(f'--label {l}' for l in labels)} ..."
    else:
        label_line = None
        hint_line = None
        reason = None

    result = subprocess.run(["bash", "-c", cmd], cwd=str(repo))
    if result.returncode == 0:
        print("PRE-PR GATE: PASS")
        if args.risk is not None and label_line:
            print(label_line, flush=True)
            print(hint_line, flush=True)
        return 0
    print(f"PRE-PR GATE: FAIL (exit {result.returncode}) — do NOT open the PR. "
          "Fix the failures and re-run the gate.", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
