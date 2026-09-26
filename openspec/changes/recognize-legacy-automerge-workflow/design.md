## Context

The current canonical auto-merge workflow path was introduced after many repositories had
already been onboarded with `.github/workflows/auto-merge.yml`. `detect_state`, `cmd_propose`,
`compute_drift`, `cmd_apply`, and the PR-label doc template currently use only the canonical
path. The result is duplicate workflow creation on the documented `propose` remediation path.

## Decisions

**D1 — Recognize the legacy filename as an equivalent managed workflow.** Define a legacy path
constant and centralize selection of the effective path. A repository with only the legacy path
is already onboarded for this artifact: `propose` skips it, `apply` creates the shared labels,
and content drift compares that file to `build_automerge_workflow()`.

**D2 — Canonical wins only when both files exist.** The canonical name remains the fresh-scaffold
target. If both paths are present, no file is removed or rewritten; the canonical path is the
effective path for newly generated prose. Drift evaluates both extant files independently, so a
diverged duplicate is still visible. This is deterministic and does not make an implicit
migration decision for an operator.

**D3 — Render the workflow path into new PR-label prose.** Replace the template's hard-coded
workflow filename with a marker filled by `build_pull_requests_doc`. Keep the canonical path as
the builder's default so existing direct callers stay correct. `cmd_propose` passes the effective
path only when it writes an absent doc. Existing docs remain write-if-absent and are not added to
drift, preserving the established hand-tailoring policy.

**D4 — Preserve current result compatibility while making state inspectable.** Retain
`automerge_workflow_exists` as the aggregate "either path exists" value used by callers, and add
per-path state necessary for path selection and diagnostics. A legacy-only skip reports the
legacy filename rather than claiming the canonical file exists.

## Risks

- A repository that already contains both workflows can still execute both GitHub Actions. This
  change deliberately does not delete either; independent drift entries and canonical preference
  make the state visible while leaving migration to an explicit operator decision.
- The legacy file may have been deliberately customized. It is skipped byte-for-byte and only
  reported as drift, matching every other generated-workflow drift rule.
