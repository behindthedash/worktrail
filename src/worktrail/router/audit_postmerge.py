#!/usr/bin/env python3
"""audit_postmerge.py — fleet-wide post-merge reconciliation audit.

`verify.py`'s `classify_checks()` only ever runs while a PR's own orchestrator
run is still live, polling that one PR's `statusCheckRollup` until it goes
green or the run gives up. Nothing re-checks a PR after it merges — a check
that starts failing *after* merge (a flaky re-run, a required check added to
the branch ruleset post-merge, a rollup that was still pending when the
orchestrator's own poll budget ran out and it merged anyway) is never
flagged again. This module closes that gap with a periodic sweep across
every worktrail-managed repo's recently-merged PRs, re-classifying each PR's
current `statusCheckRollup` with the exact same `classify_checks()` every
in-flight verify run already uses, so a merged-but-red PR surfaces the same
way a not-yet-merged one would.

Repo discovery reuses `reconcile_pr_labels.py`'s `discover_managed_repos()`
rather than re-implementing the "which repos has this machine opted into
GO/worktrail" scan a second time.
"""
from __future__ import annotations

from .reconcile_pr_labels import discover_managed_repos
from ..orchestrator.verify import classify_checks

__all__ = ["discover_managed_repos", "classify_checks"]
