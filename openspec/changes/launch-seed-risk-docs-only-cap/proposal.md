## Why

`pre_pr_gate.resolve_pr_labels()` caps `risk` to `"low"` when `is_docs_only(repo, policy)` is
true (`pre_pr_gate.py:422-423`). The cap's premise (added by #102 for the #86 `authz`
false-positive) is real diff ground truth: the classifier's verdict is a keyword match on
free-text prose with no visibility into the diff, so a docs-only diff proves the elevated tier
was a prose artifact. But the cap is applied wherever `resolve_pr_labels()` is called with
whatever diff `--repo` happens to hold — and for the orchestrator's launch seed that diff is
not the labeled artifact's at all.

The launch seed is `PR_LABELS=$(worktrail-pre-pr-gate --repo "$SPEC_ROOT" --risk "$RISK_LEVEL"
--gates "$GATES" --route "$ROUTE" --target-branch "$BASE" --labels-only)`
(`skills/worktrail-go/references/subagent-prompts.md:738`). For the `new` and `modify`
pipelines `$SPEC_ROOT` is `$WT`, the change-authoring worktree (`subagent-prompts.md:1822-1823`;
only `implement` uses `$REPO`) — a `chg/<change-id>` / `spec/<spec-id>` branch off `$BASE` with,
by construction, only the change's own spec documents committed
(`openspec/changes/<id>/**` or `docs/specs/<id>/**`; `openspec/**` and `docs/**` are both in
`docs_only_paths`, `.worktrail/policy.yaml:40-46`). So `changed_paths()` diffs
merge-base(HEAD, base)..HEAD = the spec commit, `is_docs_only()` is true by construction, and
the cap fires unconditionally: **every seeded run's risk is forced to `low`**, whatever
`$RISK_LEVEL` the classifier produced. That seed is then the run's only risk input — the
group-PR step extracts it (`_extract_risk_from_labels`, `integrate.py:1902`) and, exactly
because a seeded `go:risk-*` label was present, forwards `labels=None` with that tier as `risk`
into `land_pr.open_or_update_pull_request` (`integrate.py:1903`/`:1907`), whose `labels is None`
branch recomputes labels from that risk (`land_pr.py:636`) against the canonical checkout —
where the cap cannot fire, and a cap could only lower risk anyway.

Both halves are on record. Run `go-20261003-211946` (route F, change
`tail-rewrite-preserves-group-records`, run record `risk_level: medium`, worktree
`~/projects/worktrail-worktrees/quarantined-group-resume-chg-tail-rewrite-preserves-group-records`)
launched with `PR_LABELS=go:risk-low`, and PR #1415 — a `src/worktrail/orchestrator/live.py` +
two-tests change, i.e. not docs-only at all — merged 2026-10-04 carrying exactly
`labels: [go:risk-low]`. Sibling brief `20261003-224646-worktrail-pre-pr-gate-risk` filed it
with **"Cause: UNKNOWN — not reproducible after the fact"**, because by the time it was
investigated the change worktree had been deleted and the identical command against the
canonical checkout returned `go:risk-medium`; the deleted worktree *was* the trigger, and this
change names the mechanism.

Impact: the cap never raises risk, so a `high`/`critical` verdict on a code-heavy run is seeded
`low` too — and `auto-merge.yml` arms auto-merge on a `go:risk-low`/`go:risk-medium` label with
no `go:no-automerge`, so a change the policy deliberately gated at `max_risk: medium`
(`.worktrail/policy.yaml:47-49`) would merge with no human. Even when the tier gap is harmless
(medium → low, as in #1415), the PR is labeled and audited at the wrong tier, and
`worktrail-reconcile-pr-labels` only ever *adds* a `go:risk-*` label to a PR that has none — it
never corrects one, so the wrong label is permanent.

## What Changes

- `resolve_pr_labels()` gains `docs_only_cap: bool = True` — the cap applies only when the
  caller confirms the inspected diff is the labeled change's own diff. Default stays `True`, so
  every existing caller (`land_pr`, `preflight`'s marker, the in-worktree `--risk` path) is
  unchanged.
- `pre_pr_gate.py` gains `--no-docs-only-cap`, meaningful only with `--labels-only` (asked for
  alone it is refused, so the in-worktree gate path can never stop capping). The launch seed
  passes it: the seed has no artifact, so the classifier's verdict is seeded verbatim — the
  documented fail-loud default for prose false positives on a diff that is not confirmed
  docs-only (`classify.py:381-395`).
- The cap reports itself: when it actually lowers the risk it prints one line to standard error
  naming the lowering; stdout (the parsed label set) is unchanged. This is the observability
  half of the same defect — the sibling brief spent a session on it precisely because a silent
  `medium -> low` downgrade leaves no trace anywhere.
- The launch reference and the flag are locked together by a test
  (`test_skill_prose_enforcement_coverage.py`), which also registers
  `worktrail-go/references/subagent-prompts.md` in the label-family prose registry the file
  now mentions.

## Capabilities

### New Capabilities
- `docs-only-risk-cap-scope`: the docs-only risk cap applies only to the labeled change's own
  diff; a launch seed (which has none) is never capped, and a capped risk is reported.

### Modified Capabilities

## Impact

- `src/worktrail/router/pre_pr_gate.py`: `resolve_pr_labels()` signature, `main()`'s
  `--labels-only` branch and flag validation, docstrings/module docstring.
- `skills/worktrail-go/references/subagent-prompts.md`: the launch block's seed invocation
  (line 738) and a comment recording why it must not be diff-capped.
- `tests/router/test_pre_pr_gate.py` (`TestDocsOnlyRiskCap`: opt-out, unchanged default,
  refusal without `--labels-only`, the stderr report) and
  `tests/router/test_skill_prose_enforcement_coverage.py` (new proof + registry entry).
- `.worktrail/policy.yaml`: the `docs_only_paths` comment block, which currently states the cap
  unconditionally.
- Not modified: `integrate.py`, `land_pr.py`, `preflight.py`, and every existing
  `resolve_pr_labels()` caller's behavior. Non-goal: making a group PR's risk diff-corrected at
  PR time (the seed's `risk` is recomputed there in a context whose diff is the canonical
  checkout's) — that stays the fail-loud default this change restores.
