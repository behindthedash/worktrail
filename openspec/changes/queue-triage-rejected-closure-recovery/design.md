## Context

See proposal.md for the gap. The mechanics a recovery has to respect, verified in this checkout:

- **The landing PR exists only in memory.** `_worktree_pr_close()` computes `pr_url` from
  `land_pr`'s `LandOutcome` and passes it to `done(..., triaged_to=pr_url)`. When `done()` refuses,
  the code path returns before any write, so neither `triaged-to` nor anything else lands on the
  brief. The one copy of the URL is `outcome.pr_url` in the action-log entry `cmd_apply` prints.
- **`done()`'s gates are what rejected it.** A note asserting a re-verification result with no
  shown transcript is refused (`_reverification_claim_missing_evidence`, called at
  `work_queue.py:1651`, regex at :193), and queue-triage's closure note *is* the evaluator's
  evidence prose. A consolidation-batch brief is refused when the note does not name and evidence
  every listed sub-item (`_consolidation_closure_missing_evidence`, `work_queue.py:251-272`).
  Both refusals return before the closure-note append and the frontmatter write
  (`work_queue.py:1700-1760`).
- **A triage closure is `triaged_to`.** `done(triaged_to=...)` marks a triage closure, exempting
  the brief from the Route-C continue-vs-planning gate (`work_queue.py:1633-1641`) and stamping
  `triaged-to:` alongside `status: done`. The PR reference is the evidence a triage closure
  carries.
- **`npm`-free helpers already exist.** `_set_fm_fields(path, {k: v})` sets scalar frontmatter
  fields in place through the canonical serializer's key-splice (`work_queue.py:605-645`), and
  `validate_brief` requires only `status` by default, so two extra scalar fields are safe.
  `pr_ledger.parse_pr_url` (`pr_ledger.py:118`) is the shape check the ledger itself applies to a
  URL.
- **The dashboard sees only `status: picked`.** `inflight_briefs` (`dashboard.py:2498`) globs
  `picked/*.md`, parses frontmatter, and hides anything claimed inside `stale_hours` (default 48).
  `build_category_items` (`dashboard.py:2982`) turns each into a `type: inflight` item with
  `action: "resume"` (`:3071-3093`); the `action` string is the only thing the front door uses to
  pick a dispatch, and `skills/worktrail-go/SKILL.md`'s action table is its consumer.

## Goals / Non-Goals

**Goals:**

- One command that closes a rejected-closure brief against the PR its landing already recorded,
  with no PR archaeology and no re-triage.
- Make that landing recoverable from the queue alone, so the unattended path (the drain prepass,
  where the action-log entry is printed and lost) is as recoverable as an interactive pickup.
- Fail closed: every refusal leaves the brief byte-identical, and the brief is never returned to
  `queue/`.
- Make the dashboard say what the brief needs ("recover this closure against PR X") instead of
  mislabelling it an abandoned session.

**Non-Goals:**

- Re-running the triage evaluation, re-landing anything, or opening/closing a PR.
- Satisfying a closure gate on the operator's behalf — no synthesized transcript or note.
- Changing `done()`'s gates, `_worktree_pr_close()`'s no-PR release path, or the `stale-close` /
  `duplicate-of` claim+done path (`queue_triage.py:2285`, whose release is deliberate).
- A bulk sweep over every picked brief, or clearing the recorded landing on success.

## Decisions

- **Record the landing on the brief at rejection time; never reconstruct it later.** The recovery
  reads `closure-rejected-pr` / `closure-rejected-reason` from the brief the same `apply` call is
  already holding claimed. Alternatives: persisting the action log (no caller has a durable sink —
  `drain.run_intake_triage_prepass` prints and raises, `cmd_apply` prints and returns, so a new
  sink would have to be invented for every caller, and the non-drain caller would still be
  stuck); recovering the PR from the shared ledger by branch (`pr_ledger.register` records
  `branch` and `repo` but no brief id, and a `propose-change` branch is
  `queue-triage/propose-<proposed_change_name>` — `_planned_fold_propose_branch`,
  `queue_triage.py:2865-2874` — so only the `fold-into-change` half of the verdict would be
  lookup-able at all); making the operator pass `--pr-url` (that is the manual reconstruction the
  change exists to remove).

- **The stamp names a past event, not a pending to-do.** `closure-rejected-pr` (the URL `land_pr`
  returned) and `closure-rejected-reason` (the `done()` status that refused) stay true after the
  recovery and read as provenance next to `triaged-to` — which is why nothing clears them, and
  why the recovery performs no second write. A `pending-closure-*` pair that the recovery cleared
  was rejected: it costs an extra write on the success path, and a crash between the two writes
  leaves a `done` brief advertising a pending closure that no command will ever look at again.

- **Recovery closes with `triaged_to=<recorded PR>` and, by default, no note.** The reason these
  closures get rejected is that queue-triage hands the evaluator's evidence prose to `done()` as
  the closure note; the triage closure's real justification is the landed PR, which
  `triaged_to` already carries. With `note=None` the reverification gate is not consulted at all
  (`if note and _reverification_claim_missing_evidence(note)`, `work_queue.py:1651`), so an
  ordinary fold/propose brief closes on its first recovery attempt. `--evidence` is the escape
  hatch for the briefs whose gate still refuses: a consolidation batch needs a note naming and
  evidencing each sub-item, which only the operator can supply. Rejected: defaulting the note to
  the recorded evidence prose (`v.evidence` is *deterministically* the note the gate already
  refused) and synthesizing a note (`openspec/changes/archive/2026-10-03-…-no-rollback`'s own
  explicit non-goal — a manufactured transcript is worse than a stranded brief).

- **Refusals are named and write nothing.** `resolve()` inside `picked_dir()` plus a second
  `resolve()` inside `queue_dir()` distinguishes "not claimed" from "no such brief"; a resolved
  brief whose frontmatter is not `status: picked` is refused as not-claimed; a brief with no
  `closure-rejected-pr` is refused as `no-recorded-landing` (it is a stalled session, which is
  `resume`'s business); a recorded value that `pr_ledger.parse_pr_url` rejects is refused as
  `invalid-landing`, so a hand-mangled brief can never be closed against a non-PR URL. When
  `done()` itself refuses again, that status and its message are surfaced verbatim — the message
  is what tells the operator which evidence to paste — and the brief is untouched. `release()` is
  never called on this path: returning the brief to `queue/` is exactly the behavior #1414
  removed, and recovery must not reintroduce it.

- **The recorded landing is surfaced regardless of claim age.** `stale_hours` exists to hide a
  brief an active session may still own (`dashboard.py:2501-2516`). A rejected-closure brief's
  owner has exited — `done()` already ran and refused — so hiding it for 48h is the very delay
  this change removes. A brief carrying a recorded landing therefore bypasses the freshness
  filter, and is additionally annotated so the description can name the PR.

- **A `recover-closure` action, not a new item type.** The entry is still an in-flight brief; only
  its remedy is different, so `type` stays `inflight` and only `action` changes. The front door
  already dispatches on `action` alone, so a new verb plus one row in the skill's action table is
  the whole wiring. The skill's interactive triage step gets the same treatment so an attended
  pickup self-heals: it already reports the landed `pr_url` (`SKILL.md:356-364`), and a
  `rolled_back: false` entry now names the recovery instead of leaving the operator to notice.

- **The command hangs off `worktrail-queue-triage`, not the skill-dispatch triage flags.** It
  spawns nothing — it resolves a brief, calls `done()`, prints a result — so it needs no
  harness/agent resolution, and `worktrail-queue-triage` is the console script that already owns
  triage's apply semantics. Routing it through `worktrail-skill-dispatch`'s
  `--evaluate-brief-triage`/`--apply-brief-triage` family (which exists to carry an
  invocation-context agent into a dispatch) would add a dispatch layer that does nothing.

## Risks / Trade-offs

- [A second field pair widens the brief frontmatter vocabulary] → both are written through
  `_set_fm_fields`, `validate_brief` ignores unknown keys, and the recovery is the only reader —
  a `closure-rejected-pr` on a brief nothing recovers is inert.
- [Recovery closes a brief against a PR that was later closed unmerged] → the recorded URL is
  `land_pr`'s own returned `pr_url`, i.e. a PR that was created; `land_pr` only returns a URL
  after the PR exists, and the triage closure's claim is "this brief's work went to that PR",
  which stays true even if the PR is later edited. No live `gh` re-check is made deliberately:
  it would make recovery network-dependent and non-hermetic for a claim the recorded URL already
  establishes.
- [The default note closes an ordinary brief with no human wording] → that is the intended
  contract: the closure is a triage closure whose evidence is the landed PR, and every refusal
  path (consolidation batches included) still surfaces the gate's own demand rather than
  bypassing it.
- [Bypassing the freshness filter could surface a brief a live session still owns] → the filter's
  premise ("no completion yet, so the owner may be alive") is false once a recorded landing
  exists; the worst case is a redundant recovery run, which refuses or closes an already-closed
  brief.
- [An operator edits the recorded URL by hand] → the shape check refuses anything that is not a
  pull request URL, and the value is only ever a `triaged_to` on the resulting closure.

## Migration Plan

None. A new subcommand, two optional frontmatter fields, and one dashboard action; no
configuration, journal schema, or data migration. Existing picked briefs carry no
`closure-rejected-pr`, so they keep surfacing as `resume` exactly as today, and a brief rejected
before this change lands simply cannot be auto-recovered (its PR was never recorded) — the
operator closes it the way they do now. Rollback is a code revert; the two fields on any brief
already stamped are inert to the reverted code.
