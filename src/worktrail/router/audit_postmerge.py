#!/usr/bin/env python3
"""audit_postmerge.py — scheduled fleet-wide post-merge reconciliation audit.

Sweeps every worktrail-managed repo's recently-merged PRs and flags any whose
`statusCheckRollup` shows a failing required check — catching CI results that
only landed *after* merge (or were never observed by the orchestrator's own
pre-merge gate) so drift is surfaced instead of silently going unnoticed.

Reuses rather than reimplements:
  - `discover_managed_repos()` (`reconcile_pr_labels.py`) for fleet discovery,
    so "which repos are in scope" has exactly one definition.
  - `classify_checks()` (`orchestrator/verify.py`) for check-rollup
    classification, so "which checks gate a merge" has exactly one
    definition and this audit can never drift from the orchestrator's own
    pre-merge posture.
"""
from __future__ import annotations

from .reconcile_pr_labels import discover_managed_repos
from ..orchestrator.verify import classify_checks

__all__ = ["discover_managed_repos", "classify_checks"]
