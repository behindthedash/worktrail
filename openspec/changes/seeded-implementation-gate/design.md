## Context

See proposal.md - Why. `seed_backlog.py` already has two finders (`find_needs_tasks_specs`,
`find_epic_gaps`), a format-agnostic dedup layer (`existing_seed_keys`), and a bounded/deterministic
orchestration function (`seed_backlog`) that `worktrail-drain` calls pre-loop. `dashboard.py`
already computes a `ready-to-implement` stage for both devkit and OpenSpec-format specs (task DAG
complete, real pending impl work, stale/stuck cases excluded — see `detect_stage()` and
`_safe_detect_openspec()`). `policy.py` already validates one boolean gate the same shape this
needs (`automerge.enabled`, default `False`, forced back to `False` on a non-bool value).

## Goals / Non-Goals

**Goals:**
- Reuse every existing mechanism (dashboard scan, dedup, cap/order/log, `create_handoff`) rather
  than parallel-inventing a second seeding pipeline for this one finder.
- Make the opt-in impossible to trigger by accident: absent or malformed policy value must
  resolve to `False`, matching the existing `automerge.enabled` precedent so operators only learn
  one validation convention for "boolean policy gate."

**Non-Goals:**
- Auto-enabling `allow_seeded_implementation` for any repo. Every repo starts `false`; turning it
  on is a one-line policy PR the operator authors deliberately, not something this change ships.
- Changing `find_needs_tasks_specs` or `find_epic_gaps` behavior, their key shapes, or the
  drain's `--no-seed-backlog` opt-out — the new finder is strictly additive and shares their
  opt-out (there is no separate `--no-seed-implementation` flag; a repo that wants planning
  seeding but not implementation seeding uses the policy key, not a CLI flag, since the policy
  key is per-repo and CLI flags are per-invocation).
- Reconciling `ready-to-implement`'s existing dashboard semantics with anything new. The stage
  already excludes stale-bookkeeping and orchestrator-stuck specs; this change consumes that
  stage as-is rather than re-deriving "is the spec PR merged" itself.

## Decisions

**Gate at candidate-generation time, not at brief-creation time.** `find_ready_specs(repos_root,
go_repo, allow_repo_check)` takes the same `(repos_root, go_repo)` shape as the other two finders
plus a policy lookup; `seed_backlog()` calls `load_policy(repo_path)` per discovered repo name
(mirroring `_base_branch_for`'s existing per-repo `load_policy` call) and only includes a repo's
`ready-to-implement` specs in the candidate list when the key is `true`. Alternative considered:
generate all candidates unconditionally and filter centrally in `seed_backlog()`. Rejected —
`find_needs_tasks_specs`/`find_epic_gaps` are themselves repo-list-then-scan functions; keeping
the same shape for the third finder means `seed_backlog()`'s orchestration body stays a flat
concatenation of three finder calls, not a mix of "finder calls" and "finder call + inline
filter."

**Reuse `dashboard.scan()` for the ready check, not a new git/task probe.** `find_ready_specs`
calls the same `dashboard.scan(repo_path / "docs" / "specs")` the `needs-tasks` finder already
calls (it transparently covers OpenSpec changes too — see `scan()`'s fallback to
`openspec/changes/` when `docs/specs` doesn't exist) and filters rows where
`row.get("stage") == "ready-to-implement"`. This is the same function, same call shape, same
row projection the existing finder already depends on — no new dependency on `git`,
`_pending_impl_stale`, or the run-journal reader; those are internal to `detect_stage()` and out
of scope for the finder layer.

**Policy validation mirrors `automerge.enabled`, not a new pattern.** Add
`"allow_seeded_implementation": False` to `policy.py`'s `DEFAULTS`, add it to `KNOWN_KEYS`
(automatic via `set(DEFAULTS)`), and add one clamp block next to the existing
`automerge.enabled` bool-forcing check: a non-bool value logs a `_meta.warnings` entry and is
forced to `False`. Alternative considered: a tri-state (`"true"`/`"false"`/unset) or an allowlist
of spec-id prefixes. Rejected — the proposal's ask is a single opt-in switch per repo, matching
`allow_seeded_implementation`'s literal name; scoping seeding further (per-spec, per-epic) is not
requested and would need its own key design if a real need surfaces later.

**Brief kwargs builder mirrors `_needs_tasks_brief_kwargs`.** A new `_ready_brief_kwargs(finding)`
function returns the `focus`/`context`/`recommended_route`/`implementation_intent`/`target_spec`
dict, called from `seed_backlog()`'s existing `kwargs = (... if finding["kind"] == "needs-tasks"
else ...)` dispatch, extended with a third branch for `finding["kind"] == "ready-to-implement"`.
The focus text tells the picking session the spec already has an approved, merged task DAG and to
run the orchestrator directly — mirroring how `_needs_tasks_brief_kwargs`'s focus tells the
picking session to run spec-to-tasks without re-authoring the spec.

**Seed key has no progress suffix, unlike the epic key.** `<repo>:impl:<spec-id>` — stable, like
the spec key, not `cited=<n>`-suffixed like the epic key. Rationale: the epic key's suffix exists
because a *new* citing spec is genuine forward progress that should re-arm seeding for the next
feature. A `ready-to-implement` spec has no analogous sub-progress signal to key on — if a
previously-seeded Route D brief was claimed and finished without landing the implementation
(e.g., a `stale-bookkeeping`/`orchestrator-stuck` outcome), that is exactly the "needs a human,
not a retry storm" case the existing spec-key rationale already documents in
`existing_seed_keys()`'s docstring; the same stable-key argument applies unchanged.

**Ordering: needs-tasks, then epics, then ready-to-implement.** Appended after the two existing
kinds rather than interleaved. Rationale: this finder is opt-in and the newest of the three; a
repo that has not opted in never has ready-to-implement candidates in the list at all, so
existing repos' candidate ordering (and therefore which candidates get capped out of a sweep) is
completely unchanged. For an opted-in repo, planning debt (needs-tasks, epics) draining before
implementation work is the more conservative default — it keeps the queue from being dominated by
implementation briefs while cheaper planning-only work sits capped-out.

## Risks / Trade-offs

- **A misconfigured `allow_seeded_implementation: true` on a repo with many `ready-to-implement`
  specs could flood the queue with implementation briefs.** Mitigated by the existing
  `DEFAULT_MAX_SEEDS` cap (shared across all three finders, not a separate per-kind budget) and
  by the fact this is an explicit, deliberate policy change the operator authors — not a default
  any repo acquires silently.
- **`ready-to-implement` already excludes stale-bookkeeping and orchestrator-stuck specs, but a
  spec could still flip stage between sweeps** (e.g., a human starts the orchestrator manually
  right as a sweep runs). No new risk versus the existing `needs-tasks` finder's identical
  window; `create_handoff`'s per-candidate try/except (already in `seed_backlog()`) means one
  stale candidate never blocks the rest of the sweep, and Route D's own orchestrator dispatch
  handles "nothing to do" harmlessly.
