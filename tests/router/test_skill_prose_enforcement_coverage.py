#!/usr/bin/env python3
"""Regression test extending the enforcement-coverage pattern to SKILL.md prose.

`test_gate_enforcement_coverage.py` (classify.py `gates.append(...)` strings) and
`test_pr_creation_callsite_enforcement_coverage.py` (`gh pr create` call sites) both
close a "policy decision computed correctly, nothing proven to consume it" failure
shape -- but both extract exclusively from Python source via `ast.walk`. Neither
catches the shape the `go:risk-*`/`go:no-automerge` PR-label bug actually took for
most of its six recurrences (#74/#80/#82/#128/#137/#281, finally code-enforced inside
`run_record.py`'s own `finish()` in #283): a SKILL.md paragraph narrating a
corrective action as mandatory, with nothing in code enforcing it.

See docs/specs/research/skill-prose-enforcement-coverage-design.md for the full
investigation into a detection mechanism, including a generic mandate-cue +
named-action prose scan that was prototyped and rejected on evidence (near-zero
recall at safe precision, one confirmed false pairing). The mechanism built here
instead:

1. `extract_label_correction_mentions()` scans every `skills/**/*.md` file for a
   closed vocabulary of literal strings that only appear when a file is discussing
   the `go:risk-*`/`go:no-automerge` PR-label correction specifically (the exact
   family responsible for all six recurrences) -- file-level granularity, mirroring
   `test_pr_creation_callsite_enforcement_coverage.py`'s `CALLSITE_CONSUMERS`, not
   paragraph/sentence-level pairing (which the prototype showed is unreliable for
   markdown prose).
2. `FILE_CONSUMERS` registers a proof callable per file that behaviorally or
   structurally demonstrates the mechanism the file describes is actually
   code-enforced, not prose-only reliance -- exercised by
   `test_registered_consumers_actually_enforce`.

Extending coverage to a *new* recurring failure family is the same one-line change
`GATE_CONSUMERS` already requires for a new gate string: add the family's literal
markers to `LABEL_FAMILY_MARKERS`. This test does not catch a brand-new corrective-
action family described in prose for the first time before its vocabulary is added
here -- see the design note's "What this does not catch" section.
"""
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router import check_review_threads as check_review_threads_mod
from worktrail.router import pre_pr_gate as pre_pr_gate_mod
from worktrail.router import reconcile_pr_labels as reconcile_pr_labels_mod
from worktrail.router.run_record import main as run_record_main

SKILLS_ROOT = Path(check_review_threads_mod.__file__).resolve().parents[3] / "skills"

# Literal strings that only appear in a skills/**/*.md file when it is discussing
# the go:risk-*/go:no-automerge PR-label correction specifically -- the exact
# vocabulary of the recurring failure family (#74/#80/#82/#128/#137/#281).
LABEL_FAMILY_MARKERS = (
    "go:risk-",
    "go:no-automerge",
    "ensure_pr_risk_label",
    "ensure_pr_no_automerge_label",
)


def extract_label_correction_mentions() -> set:
    """Every skills/**/*.md file (path relative to `skills/`) mentioning the
    go:risk-*/go:no-automerge PR-label correction family."""
    found = set()
    for path in sorted(SKILLS_ROOT.rglob("*.md")):
        text = path.read_text()
        if any(marker in text for marker in LABEL_FAMILY_MARKERS):
            found.add(str(path.relative_to(SKILLS_ROOT)))
    return found


def _proves_ci_watch_loop_stamps_no_automerge_on_blocking():
    """ci-watch-loop.md claims 'the tool itself also stamps go:no-automerge on
    the PR the moment blocking goes true' -- check_review_threads.check()'s
    compiled bytecode must actually reference ensure_pr_no_automerge_label
    (not just mention it in a docstring/comment, which co_names excludes).
    This proves the call exists in the function body; it does not re-verify
    check()'s own `if result["blocking"] and not dry_run:` guard live (that
    would require mocking the GitHub GraphQL calls check() makes internally)."""
    names = check_review_threads_mod.check.__code__.co_names
    if "ensure_pr_no_automerge_label" not in names:
        raise AssertionError(
            "check_review_threads.check() no longer references "
            "ensure_pr_no_automerge_label -- ci-watch-loop.md's claim that "
            "'the tool itself' stamps go:no-automerge is now prose-only")


def _proves_pre_pr_gate_computes_automerge_labels():
    """routes.md's PR template Auto-Merge Eligibility section claims the
    go:risk-*/go:no-automerge labels come from 'pre_pr_gate.py --risk output'
    -- resolve_pr_labels() (the single source of truth --labels-only, --risk,
    and preflight.py's marker all share, per its own docstring) must actually
    call both automerge_eligible() and automerge_labels()."""
    names = pre_pr_gate_mod.resolve_pr_labels.__code__.co_names
    missing = {"automerge_eligible", "automerge_labels"} - set(names)
    if missing:
        raise AssertionError(
            f"pre_pr_gate.resolve_pr_labels() no longer references {sorted(missing)} "
            "-- routes.md's Auto-Merge Eligibility template claim is now prose-only")


def _proves_run_record_finish_applies_risk_label_unconditionally():
    """sdd-workflow SKILL.md Phase 8 claims 'the go:risk-* label correction is
    code-enforced inside finish itself ... there is no longer a separate
    worktrail-ensure-pr-label step to remember' (the exact #283 fix). Prove
    behaviorally: finish a run carrying a pull_request field and confirm
    ensure_pr_risk_label is actually invoked, mirroring
    test_gate_enforcement_coverage.py's _proves_no_implementation_without_approval
    pattern (real run_record_main() start/finish calls, not source inspection)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = StringIO()
        with patch("sys.stdout", out):
            rc = run_record_main([
                "start", "--repo", "/tmp/fake-repo", "--request", "t",
                "--route", "F", "--risk", "low", "--dir", tmp,
            ])
        if rc != 0:
            raise AssertionError("run_record.py start failed")
        run_path = json.loads(out.getvalue())["path"]
        with patch("worktrail.router.pr_labels.ensure_pr_risk_label") as mock_ensure:
            run_record_main([
                "finish", run_path, "--status", "completed_pr_open",
                "--pr", "https://github.com/example/repo/pull/1",
            ])
        if not mock_ensure.called:
            raise AssertionError(
                "run_record.py finish did not call ensure_pr_risk_label for a "
                "run carrying a pull_request field -- sdd-workflow SKILL.md's "
                "'code-enforced inside finish itself' claim is now prose-only")


def _proves_reconcile_repo_self_heals_both_labels():
    """worktrail-go SKILL.md claims worktrail-reconcile-pr-labels's periodic
    sweep 'can recompute full automerge_eligible() per PR and self-heal
    drifted go:no-automerge labels the same way it already self-heals
    go:risk-* labels'. reconcile_repo() (the sweep's per-repo body) must
    actually reference automerge_eligible() and both label-correction
    functions -- structural proof, not a live sweep run (which would need a
    real repo with open PRs and a run-record index)."""
    names = reconcile_pr_labels_mod.reconcile_repo.__code__.co_names
    required = {"automerge_eligible", "ensure_pr_risk_label", "ensure_pr_no_automerge_label"}
    missing = required - set(names)
    if missing:
        raise AssertionError(
            f"reconcile_pr_labels.reconcile_repo() no longer references {sorted(missing)} "
            "-- worktrail-go SKILL.md's self-heal claim is now prose-only")


# skills/-relative path -> callable proving the file's go:risk-*/go:no-automerge
# claim is actually code-enforced. Every file extract_label_correction_mentions()
# finds MUST have an entry here.
FILE_CONSUMERS = {
    "worktrail-go/SKILL.md": _proves_reconcile_repo_self_heals_both_labels,
    "worktrail-go/references/routes.md": _proves_pre_pr_gate_computes_automerge_labels,
    "worktrail-go/references/ci-watch-loop.md": _proves_ci_watch_loop_stamps_no_automerge_on_blocking,
    "worktrail-sdd-workflow/SKILL.md": _proves_run_record_finish_applies_risk_label_unconditionally,
}

KNOWN_FILES = set(FILE_CONSUMERS)


class TestSkillProseEnforcementCoverage(unittest.TestCase):

    def test_every_label_correction_mention_has_a_registered_consumer(self):
        found = extract_label_correction_mentions()
        self.assertTrue(
            found, "extraction found no skills/**/*.md mentions of the "
                   "go:risk-*/go:no-automerge label family -- either the "
                   "vocabulary or the docs moved/renamed")
        unreviewed = found - KNOWN_FILES
        self.assertFalse(
            unreviewed,
            f"new skills/**/*.md file(s) {sorted(unreviewed)} mention the "
            "go:risk-*/go:no-automerge label-correction family with no "
            "registered proof in FILE_CONSUMERS -- confirm the mechanism the "
            "file describes is actually code-enforced (see "
            "docs/specs/research/skill-prose-enforcement-coverage-design.md), "
            "then add a proof function here (see "
            "test_gate_enforcement_coverage.py for the established pattern)")
        stale = KNOWN_FILES - found
        self.assertFalse(
            stale,
            f"FILE_CONSUMERS registers {sorted(stale)}, which no longer "
            "mentions the label-correction family -- remove the stale entry")

    def test_registered_consumers_actually_enforce(self):
        for path, proof in FILE_CONSUMERS.items():
            with self.subTest(path=path):
                proof()


if __name__ == "__main__":
    unittest.main()
