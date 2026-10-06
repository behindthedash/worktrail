## Context

`resolve_pr_labels()` is the single label computation (`pre_pr_gate.py:392`), shared by every
label consumer. Four of its five call sites run against a diff that *is* the labeled
artifact's; the fifth is the launch seed:

| Call site | `--repo` / `repo` | Inspected diff | Cap correct? |
|---|---|---|---|
| `pre_pr_gate.py main()`, `--risk` (Phase 8, before `gh pr create`) | the task/worker worktree | the PR's own changes | yes |
| `preflight.py:782` (marker) | the worktree | the PR's own changes | yes |
| `land_pr.py:1532` (resume) | the requesting repo | the PR's own changes | yes |
| `land_pr.py:636` (`labels is None`, the orchestrator's group-PR recompute) | canonical checkout | empty (on `main`) | never fires — the seeded tier passes through |
| `pre_pr_gate.py main()`, `--labels-only` — **the launch seed** | the change-authoring worktree | the change's own spec docs | **no — misfires** |

The seed is the only caller that labels something that does not exist yet, and the only one
whose `--repo` diff is guaranteed docs-only: the change worktree (`git worktree add -b
chg/<id> "$WT" "$BASE"`, `subagent-prompts.md:1321-1323`) holds exactly the committed spec
docs, under `openspec/**` (or `docs/specs/**`) — both matched by `docs_only_paths`. The cap
therefore fires on every `new`/`modify`-pipeline launch, unconditionally, and forces the seed
to `go:risk-low` (`go:no-automerge` omitted when policy is otherwise eligible).

Everything downstream consumes that seed and cannot repair it: `integrate.py:1902-1903` derives
`risk` from the seed labels (and forwards the label list verbatim when the seed carried no
`go:risk-*`), `land_pr.open_or_update_pull_request` recomputes labels from that risk at
`land_pr.py:636` in the canonical checkout — where `is_docs_only` is false, so the cap cannot
fire and nothing can raise the tier. `worktrail-reconcile-pr-labels` deliberately only adds a
`go:risk-*` label to a PR carrying none, so a wrong-but-present label is permanent.

Incident evidence (both halves verified during this proposal): run `go-20261003-211946`
(`risk_level: medium`, route F) seeded `go:risk-low`; PR #1415, changing
`src/worktrail/orchestrator/live.py` and two test files, merged with exactly `[go:risk-low]`.
Sibling brief `20261003-224646` recorded the symptom as "Cause: UNKNOWN — not reproducible
after the fact" — it re-ran the identical command only after the change worktree had been
deleted, at which point the diff is empty and `is_docs_only()` fail-closes to false.

## Goals / Non-Goals

- Goal: the cap can never fire on a diff that is not the labeled change's own — the launch seed
  seeds the classifier's verdict verbatim.
- Goal: the default behavior of every existing `resolve_pr_labels()` caller is unchanged; the
  cap still corrects a genuinely docs-only PR (the #86/#102 fix is preserved).
- Goal: a capped risk stops being invisible — the incident's cost was the silence as much as
  the wrong tier.
- Non-goal: making group PRs *diff-corrected* at PR time (computing the cap against the group
  branch's own diff in `land_pr`). That is a real improvement but a different change: it means
  threading a head ref through `resolve_pr_labels`/`changed_paths` into the most load-bearing
  landing path, and the fail-loud direction is the documented default when the diff cannot
  confirm docs-only (`classify.py:389-393`).
- Non-goal: touching `is_docs_only()`/`changed_paths()` themselves. The diff resolution is
  right; the caller's provenance was the defect.
- Non-goal: correcting PR #1415's label after the fact (`reconcile-pr-labels` never corrects an
  existing `go:risk-*` label by design, and the PR is closed).

## Decisions

- **The opt-out is explicit and named after the mechanism it disables** —
  `--no-docs-only-cap`, threading `docs_only_cap=False` into `resolve_pr_labels()`. The
  alternative (making `--labels-only` implicitly skip the cap) was rejected: `--labels-only` is
  documented as computing "the exact label set `--labels-only` and `--risk` both compute", and
  `test_pre_pr_gate_parity.py` pins that `--labels-only` returns `resolve_pr_labels()` — a
  future caller with a real diff would silently lose the correction. The flag keeps the
  function's default identical for every other call site and puts the seed's declared difference
  where a reader can see it.
- **The flag is refused outside `--labels-only`.** The `--risk` path *is* the in-worktree gate
  with the artifact's own diff; letting it disable the cap would regress #86/#102. Refusal is
  `UNCONFIGURED_EXIT` with a message, matching the existing `--labels-only requires --risk`
  guard. Fail-closed: a caller that wants the cap off must also say which mode it is in.
- **The seed skips the cap; it does not attempt a substitute correction.** At seed time there
  is no artifact to diff, and the run's eventual diff cannot be known. The classifier's verdict
  therefore passes through unchanged — including an elevated tier on a run that turns out to be
  docs-only, which is exactly the fail-loud default the classifier's own comment documents
  ("an unrelated-but-real code change should get a human's attention at least once rather than
  have its risk silently downgraded from prose analysis alone").
- **The cap reports the lowering on stderr, never stdout.** `--labels-only` prints the label
  set on stdout and the launch block captures it with `$(...)`; a stdout line would corrupt the
  parse. One stderr line naming `from -> low` is enough for the launch log to show why a seed
  came out lower than the run record's `risk_level` — the exact reconstruction the sibling brief
  could not perform. Nothing is printed when the cap does not lower the risk.
- **The launch reference is registered in the label-family prose registry.** Adding the flag to
  `subagent-prompts.md` necessarily puts `go:risk-` in that file, which
  `test_skill_prose_enforcement_coverage.py`'s marker scan turns into a build failure until the
  file has a registered proof. The proof added is the lockstep the fix needs anyway: the launch
  reference still passes `--no-docs-only-cap`, the flag exists on the CLI, and `main()` threads
  it as `docs_only_cap=False`. Dropping the flag from the skill text now fails CI instead of
  silently re-arming the misfire.

## Risks / Trade-offs

- A run whose request prose false-positives (e.g. "authz" in an incidental spec-folder name)
  and whose tasks turn out to touch only docs now seeds the elevated tier → `go:no-automerge` →
  a hand merge. That is the intended fail-loud direction; before this change the same run
  silently auto-merged anything at or below `max_risk`.
- The flag adds a second way to call the label computation. It is bounded by the refusal
  outside `--labels-only` plus the lockstep test — a future caller cannot reach
  `docs_only_cap=False` without saying `--labels-only --no-docs-only-cap` out loud.
- `main()`'s AST-discovered direct-call registry (`test_pre_pr_gate_parity.py`'s `GATE_PARITY`)
  is unaffected: the new validation is plain argparse state, and `is_docs_only` stays called
  from inside `resolve_pr_labels()` rather than from `main()`.
