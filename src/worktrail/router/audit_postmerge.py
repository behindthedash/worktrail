#!/usr/bin/env python3
"""audit_postmerge.py — scheduled fleet-wide post-merge CI reconciliation audit.

Sweeps every worktrail-managed repo's recently-merged PRs for check-rollup
outcomes that landed on `main` after merge (a required check that reported
failing/pending too late to block the merge itself), and records them for
`router/dashboard.py` to surface.

Deliberately reuses two existing building blocks rather than reimplementing
them:

- `discover_managed_repos` (`reconcile_pr_labels.py`) — the same
  opted-into-worktrail repo discovery (`docs/specs/go-policy.yaml` presence)
  every other fleet-wide sweep in this package already uses.
- `classify_checks` (`orchestrator/verify.py`) — the same required/informational
  check-rollup classification the live merge-gating path uses, so a PR this
  audit flags is judged by the identical rule that governed its own merge.

Neither is duplicated here.
"""

from worktrail.router.reconcile_pr_labels import discover_managed_repos
from worktrail.orchestrator.verify import classify_checks

__all__ = ["discover_managed_repos", "classify_checks"]
