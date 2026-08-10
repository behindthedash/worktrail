#!/usr/bin/env python3
"""Shared closed vocabulary for the go:risk-*/go:no-automerge PR-label
correction family -- the exact failure shape #74/#80/#82/#128/#137/#281 took
for most of its six recurrences, finally code-enforced inside
`run_record.py`'s own `finish()` in #283.

Extracted from tests/router/test_skill_prose_enforcement_coverage.py so both
the hard-gate coverage test and the WARN-only advisory scanner
(skill_prose_advisory_scan.py) share one vocabulary instead of drifting
apart. The advisory scanner excludes any paragraph already mentioning this
family so it never re-flags what the hard gate already enforces -- including
the one confirmed false pairing
docs/specs/research/skill-prose-enforcement-coverage-design.md records for
`skills/worktrail-go/SKILL.md`'s Phase-8 paragraph.
"""

LABEL_FAMILY_MARKERS = (
    "go:risk-",
    "go:no-automerge",
    "ensure_pr_risk_label",
    "ensure_pr_no_automerge_label",
)
