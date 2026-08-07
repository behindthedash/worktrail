## Why

`worktrail-run-record claim` (PR #191) closes the same-machine TOCTOU race on
`spec_id` by combining the active-conflicts exclusivity check and the
`specification` write into one `O_CREAT|O_EXCL` file-lock step under
`<run's repo dir>/.claims/`. That lock lives under `~/.go/runs` (or the
policy-configured `run_record_dir`), which is local to one machine. Two
different machines racing on the same `spec_id` each acquire their own local
lock successfully and never see each other — the exact cross-machine gap
PR #178's design.md named as motivating context and explicitly deferred
("especially cross-machine where `~/.go/runs` isn't shared"), and the one
`#sibling-worktree-check`'s own advisory glob check already documents as
out of scope ("Cross-machine detection is out of scope for this local
check... A sibling on another machine that hasn't pushed its branch yet is
invisible here"). Multi-machine same-project orchestration is an explicitly
supported pattern in this workspace's architecture doctrine
(`~/architect/20-runtime-and-layers.md`), so this gap is live, not
theoretical — memory `project_orchestrator_concurrent_spec_collision`
records a prior real incident of two machines implementing the same spec.

## What Changes

- Add a git-ref-backed claim mechanism for `spec_id`, layered on top of the
  existing local file-lock claim (not a replacement): an atomic branch-ref
  push to `origin` acts as the cross-machine mutex, verified via git's own
  create-or-compare-and-swap ref-update semantics (`git push
  --force-with-lease=<ref>:<expected>`), so a ref creation only succeeds if
  no other machine's push has already created it.
- `worktrail-run-record claim` gains a `--remote` opt-in flag (default off,
  preserving today's local-only behavior for repos with no `origin` or where
  the operator hasn't opted in) that, after acquiring the local lock,
  attempts the remote ref claim before committing to the `specification`
  write. A remote-claim conflict fails the whole `claim` call the same way a
  local-claim conflict does today (`{"status": "already-claimed", ...}`),
  releasing the local lock it just took.
- `finish` deletes the remote claim ref (best-effort, non-fatal) alongside
  its existing local-lock release, when the run's claim was made with
  `--remote`.
- Stale remote-claim recovery: since a claiming run's local run record is not
  readable from a different machine, staleness for the *remote* claim is
  TTL-based (a configurable age threshold embedded in the claim ref's commit
  message, not the local run record staleness check `claim` already uses for
  the file lock). A stale remote claim is reclaimable via the same
  `--force-with-lease` compare-and-swap, keyed on the exact stale SHA read
  from the remote, so a concurrent reclaim by a third machine still can't
  race past this check.

## Capabilities

### New Capabilities

- `run-record-cross-machine-claim`: git-ref-backed mutex for `spec_id`
  claims, layered on the existing local-file-lock `run_record.py claim`,
  closing the cross-machine race the local lock cannot see.

### Modified Capabilities

(none — `run_record.py claim`'s existing local-lock behavior and CLI
contract are unchanged when `--remote` is omitted)

## Impact

- `src/worktrail/router/run_record.py` — `cmd_claim`, `cmd_finish`, new
  helper functions for the remote ref claim/release/staleness-reclaim, new
  `--remote` (and TTL/ref-namespace config) CLI arguments.
- `skills/worktrail-go/references/subagent-prompts.md` —
  `#sibling-worktree-check`'s existing note that cross-machine detection is
  out of scope for the *advisory glob* check stays accurate (this change
  does not touch that check); the `#active-conflicts-scan` anchor gains a
  note once `--remote` is wired into the call site (deferred to a follow-up
  — see design.md Non-Goals for what stays out of this change).
- `tests/` — new coverage for the remote claim/release/reclaim paths against
  a local bare git repo standing in for `origin` (no network dependency).
