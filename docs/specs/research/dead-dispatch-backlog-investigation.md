# Investigation — bridge-health-guard `dead_dispatch` finding: 145+ run records never terminal

Route I (investigation) note. Source brief:
`20260821-084314-bridge-health-guard-invariant-failing` (repo unset — resolved to `worktrail`
as the owner of the fix; the check itself lives in `devops/scripts/bridge-health-guard.py`).

Status: **investigation only — no code changes.**

## Problem, as reported

`bridge-health-guard.py`'s `check_dead_dispatch()` (devops repo) reported 145
`worktrail-go` run records under `~/.worktrail/runs/*/*.yaml` that never reached a
terminal `final_status` and have no heartbeat in >2h.

## Verified observations

1. **`check_dead_dispatch()` is brand new.** It was added by PR #231 (devops repo),
   closing the immediately-prior brief
   `20260820-082352-bridge-health-guard-py-has.md` (claimed/closed 2026-08-20). Today's
   brief is the *first-ever run* of this check against the full historical run-record
   corpus.
2. **Total run records on this host: 1491** (`find ~/.worktrail/runs -mindepth 2
   -maxdepth 2 -name '*.yaml' | wc -l`). **148 currently have `final_status: null`**
   (checked live at investigation time; the brief's snapshot at 08:43 read 145 — the
   count is still climbing, see observation 5). Oldest un-terminated record:
   `datalena/go-20260612-134215.yaml` (started 2026-06-12). Per-repo breakdown of the
   148: datalena 84, gracefully-giving-back 21, worktrail 20, developer-kit 7,
   `client-id-20260727` worktree containers 7, pullhook 2, devops 2, others 5.
3. **`status` distribution of the 148: route_selected 121, executing 16, pr_open 5,
   validating 2, merge_gate 2, intake 1, ci_watch 1.** `route_selected` — the status
   `run_record.py`'s `cmd_start()` unconditionally assigns to every freshly-started
   record (verified: `record["status"] = "route_selected"`, `run_record.py` line 373,
   never parameterized) — is the overwhelming majority.
4. **The `updated_at` heartbeat field `cmd_liveness()` depends on did not exist before
   2026-08-16.** `git log -S'"updated_at"' -- src/worktrail/router/run_record.py` finds
   exactly one commit, `20c0b86` (PR #479, 2026-08-16), which both introduced the field
   in `_save()` and taught `cmd_liveness()` to read it. A record with the key entirely
   absent is unconditionally treated as **not fresh** (`run_record.py` ~line 941: "as
   stale rather than guessing fresh"), independent of age. So every un-terminated record
   started before 2026-08-16 (137 of the 148) was *structurally guaranteed* to be
   flagged the first time `check_dead_dispatch()` ever ran, regardless of whether the
   dispatch that created it "died silently" or was simply never resumed. This alone
   explains the bulk of the finding as a one-time backlog surfaced by the check's first
   invocation, not 137 new silent crashes.
5. **A second, live, still-firing root cause was found and reproduced inside this very
   investigation run:** `worktrail-go`'s own `SKILL.md` Phase 6 ("Run Record — Start")
   unconditionally calls `worktrail-run-record start` for *every* dispatch — before
   Phase 7 hands off to `worktrail-sdd-workflow`. For `dispatch_mode: native-skill` (the
   default and only mode available in this Claude Code session,
   `native_skill_available: true`), the **Dispatch Contract** section of
   `worktrail-go/SKILL.md` calls `Skill("worktrail-sdd-workflow", args="handoff:<id>
   route:<X> by:<dispatch-id>")` with **no run-record path token at all**. Only the
   separate "adapter" dispatch path (`dispatch_mode: adapter`, headless
   claude/codex/opencode subprocess) threads `--run "$RUN"` through
   `worktrail-go-seed`'s five-label seeded-dispatch prompt. `worktrail-sdd-workflow`'s
   own Phase 1 intake detects seeded-dispatch only by the presence of all five labels
   (`Repo:`, `Base branch:`, `Route:`, `Spec:`, `Run record path:`); a bare
   `handoff:<id> route:<X> by:<dispatch-id>` string does not match, so it falls into the
   **handoff-seed** entry path instead — which (per `subagent-prompts.md`
   `#handoff-seed` Step 6, "route playbooks still own execution") falls through to the
   *same* top-level Phase 6 and calls `worktrail-run-record start` a **second time**,
   independently of whatever record the parent `worktrail-go` dispatch already opened.
   **Reproduced directly in this run:** `worktrail-go`'s Phase 6 opened
   `worktrail/go-20260821-093317.yaml` (`status: route_selected`, verified by reading the
   file) before dispatching to this skill; had this investigation followed
   `worktrail-sdd-workflow`'s own Phase 6 literally, it would have opened a *second*,
   distinct run record for the identical dispatch, permanently orphaning the first at
   `route_selected` the moment execution continued in the second. This run instead
   reused `go-20260821-093317.yaml` throughout as a manual workaround (see "Note on this
   run's own record" below) — precisely to avoid adding another entry to the same
   backlog it is investigating.
6. **This mechanism, not just historical debt, explains the dominant `route_selected`
   status found in observation 3.** Every native-skill `/go` dispatch that reaches Phase
   7 leaves its parent-created run record stuck at the `cmd_start()` default
   (`route_selected`) forever — the child record is the only one anything ever calls
   `finish()` on. It also explains why the backlog is still growing live: 5 of the 148
   records have `started_at >= 2026-08-20` (the day `check_dead_dispatch()` itself
   merged), and 2 more appeared in the minutes immediately before this run's own record
   was created, all in the `worktrail` repo, all `route_selected` — consistent with
   ordinary concurrent `/go` usage on this host each leaving one orphan behind, not with
   a burst of crashes.
7. **No existing tool bulk-closes a generic abandoned run record.** `run_record.py`
   does have a `reconcile` subcommand (added PR #212), but its `_is_stale()` check is
   scoped to the *spec_id claim conflict* use case: it requires a truthy `base_branch`
   and checks whether the record's worktree/commit still resolves in the target repo,
   explicitly treating "no `base_branch`" as **live, not stale** (`run_record.py` line
   831 comment: "A record with no `base_branch` is treated as live"). Most of the 148
   backlog records carry `base_branch: null` (confirmed on the sampled files read
   directly), so `reconcile` would refuse to close them even if run against every one.

## Unknowns / missing evidence

- Whether any of the 148 records are in fact still-legitimate in-progress work on
  another machine or a different agent session this investigation didn't observe —
  not checked per-record; the `dispatch_id`/liveness fields exist for exactly this
  purpose but a full per-record liveness re-verification was out of scope for this
  investigation (`check_dead_dispatch()` already re-verifies via
  `worktrail-run-record liveness` before flagging, so false-positives from a
  still-live process are already unlikely, not zero).
- What the intended lifecycle semantics are for a `/go` dispatch a human simply walks
  away from mid-session (no crash, no resumption) — is that expected to eventually
  reach a terminal `final_status` at all, and if so which of the ten states fits, or is
  "abandoned, un-terminated forever" an accepted steady state that
  `check_dead_dispatch()` should instead learn to exclude past some age? Not stated
  anywhere in `go-design.md`/`routes.md`/`run_record.py`'s own docs; this is a product
  decision, not something derivable from the code.

## Recommended next route

**Two independent follow-ups, different routes, do not combine into this run:**

1. **Route J (workflow evolution)** — thread the parent's run-record path through the
   native-skill dispatch contract (`worktrail-go/SKILL.md` Dispatch Contract +
   `worktrail-sdd-workflow/SKILL.md` Phase 1 intake), mirroring the adapter path's
   `--run "$RUN"`/seeded-dispatch pattern, so a handoff-seed or direct-intent dispatch
   reuses the already-open record instead of opening a second one. This is the fix for
   observation 5/6 — the live, still-growing cause. **Per Route J's own rule ("never
   self-modify the active workflow mid-run based on a single run's evidence"), this
   investigation deliberately does not implement it now** — it must be proposed as a
   separate Route J run against the `worktrail` repo, with cassette/test coverage per
   `routes.md` §J.
2. **Route F or C (repo TBD by whoever picks it up) — one-time backlog cleanup.**
   Closing the 148 (137 pre-existing-before-heartbeat-field, growing) orphaned records
   with some real terminal `final_status` is mechanical *once the target status is
   decided*, but that decision (which of the ten states fits an abandoned/never-resumed
   record, and what age threshold — if any — makes a record "safe to auto-close" versus
   "still plausibly resumable") is a product decision this investigation does not make.
   Recommend Route C only if that policy question needs a written design decision
   before any code lands; Route F if a human simply states the answer and the
   implementation is then a small mechanical sweep. Do not open a follow-up handoff for
   this — the two next actions are fully specified above.

## Note on this run's own record

This investigation run itself was dispatched via `worktrail-go`'s native-skill path and
therefore hit the exact mechanism in observation 5. To avoid adding a 149th entry to the
backlog under investigation, this run manually reused the parent's already-open record
(`worktrail/go-20260821-093317.yaml`) end-to-end instead of letting
`worktrail-sdd-workflow`'s own Phase 6 open a second one — a manual workaround for this
one run, not a fix; the Route J item above is what actually closes the gap for every
future dispatch.
