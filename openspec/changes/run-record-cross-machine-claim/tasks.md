## 1. Core git-ref claim primitive

- [ ] 1.1 Add `_claim_ref(specification)` helper returning `refs/worktrail-claims/<_claim_slug(specification)>`, reusing the existing `_claim_slug()` sanitizer.
- [ ] 1.2 Add `_push_remote_claim(repo_dir, ref, run_id, ttl_seconds, expect_sha=None)`: build the JSON payload (`run_id`, `claimed_at`, `hostname`, `ttl_seconds`), create an empty commit via `git commit-tree` against the empty tree, push it with `git push origin <sha>:<ref> --force-with-lease=<ref>:<expect_sha-or-empty>`, then verify via `git ls-remote origin <ref>` that the remote SHA matches what was just pushed. Return the pushed SHA on success; raise/return a typed failure on push rejection, verify mismatch, or any git/network error.
- [ ] 1.3 Add `_read_remote_claim(repo_dir, ref)`: `git ls-remote origin <ref>` for the current SHA (return `None` if absent), then `git fetch origin <ref>` + `git log -1 --format=%B <sha>` to read the JSON payload. Treat a fetch/parse failure as "claim state unknown" (surfaced distinctly from "no claim exists").
- [ ] 1.4 Add `_delete_remote_claim(repo_dir, ref)`: best-effort `git push origin --delete <ref>`; swallow and log failures, never raise.

## 2. Wire into `claim` and `finish`

- [ ] 2.1 Add `--remote` (store_true) and `--remote-ttl-seconds` (default `86400`, per design.md's data-backed default) to the `claim` subparser.
- [ ] 2.2 In `cmd_claim`, after the existing local-lock acquisition succeeds and before the existing `_active_conflicts` re-check, when `--remote` is set: read the existing remote claim (1.3); if absent, push fresh (1.2, `expect_sha=None`/empty); if present and non-stale (`now - claimed_at <= ttl_seconds`), release the local lock and return `{"status": "already-claimed", "scope": "remote", ...payload}`; if present and stale, attempt reclaim (1.2, `expect_sha=<stale-sha>`) — on reclaim failure (raced by a third machine), release the local lock and return the same already-claimed/remote shape.
- [ ] 2.3 On successful remote push, record `"remote": true` in the local lock file's JSON payload (alongside existing `run_id`, `path`, `claimed_at`) so `finish` can tell which runs need remote-ref release.
- [ ] 2.4 In `cmd_finish`, after the existing local-lock release, when the released lock's payload had `"remote": true`, call `_delete_remote_claim` (best-effort, does not affect `finish`'s exit code or JSON output on failure).
- [ ] 2.5 Update the module docstring's `claim` subcommand description and the `finish` description to document the new remote layer, consistent with the existing docstring style.

## 3. Tests

- [ ] 3.1 Add a pytest fixture that creates a local bare git repo (`git init --bare`) as a stand-in `origin`, plus a working clone, so remote-claim tests need no network access.
- [ ] 3.2 Test: first `claim --remote` on a fresh spec_id succeeds, pushes the ref, and the local lock records `"remote": true`.
- [ ] 3.3 Test: a second `claim --remote` for the same repo+specification while the first's claim is fresh fails with `{"status": "already-claimed", "scope": "remote"}` and leaves no local lock behind for the second caller.
- [ ] 3.4 Test: `claim --remote` with `--remote-ttl-seconds` set very low (e.g. `0`), followed by a second `claim --remote` after a short sleep, succeeds via the stale-reclaim path.
- [ ] 3.5 Test: two concurrent reclaim attempts against the same stale SHA — simulate by pre-computing the stale SHA and issuing both `_push_remote_claim` calls with that `expect_sha`; assert exactly one succeeds.
- [ ] 3.6 Test: `finish` on a run that claimed with `--remote` deletes the remote ref (assert via `git ls-remote` on the bare repo returns empty).
- [ ] 3.7 Test: `finish` on a run whose remote delete fails (point at an unreachable/removed bare repo path) still completes successfully and returns the normal `finish` JSON output.
- [ ] 3.8 Test: `claim` without `--remote` makes no git network calls and behaves identically to pre-change behavior (regression guard against this change accidentally changing default behavior).

## 4. Documentation

- [ ] 4.1 Do NOT wire `--remote` into `#active-conflicts-scan` in `subagent-prompts.md` or change any policy default in this change (design.md Non-Goals) — leave a one-line note in this change's own proposal/design only; the follow-up decision belongs to a separate change.
