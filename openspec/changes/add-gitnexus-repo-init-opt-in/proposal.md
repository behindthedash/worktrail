## Why

Every repo onboarded via `worktrail-repo-init propose` still needs someone to remember to
run `gitnexus analyze` by hand afterward to get cross-file query/impact/context support --
there is no bootstrap-time opt-in the way there already is for the `aspens` skill-doc-sync
add-on (`--with-aspens`). GitNexus's own registry-driven refresh cron
(`~/.gitnexus/refresh-all.sh`) already re-indexes any path it finds registered, so the only
missing piece is getting a freshly bootstrapped repo indexed (and thus registered) once, at
propose time.

## What Changes

- Add a new `--with-gitnexus` boolean flag to the `propose` subcommand, mirroring
  `--with-aspens`'s shape exactly.
- Add `enable_gitnexus(repo)` to `src/worktrail/onboarding/repo_init.py`: a plain function
  (not a `worktrail.addons.base.AddOn` implementation, since GitNexus has no ongoing
  per-task `run()` step) that shells out to `gitnexus analyze --embeddings --index-only
  <repo>` once, idempotently (skipped if `.gitnexus/` already exists), best-effort
  (subprocess failures/timeouts become a warning, not a hard failure), verifying success by
  checking the `.gitnexus/` directory now exists rather than trusting the exit code.
- Wire `enable_gitnexus` into `cmd_propose` the same way `enable_aspens` is wired: append to
  the `written`/`skipped`/`warnings` result lists.
- No changes to `default_policy_yaml()` or `.worktrail/policy.yaml`'s `add_ons` block --
  GitNexus indexing is a one-shot bootstrap action with no per-task sync step for
  `worktrail.addons.runner` to read a policy key for.
- Update `worktrail-repo-init`'s SKILL.md (and this repo's own AGENTS.md skills-table row,
  if it already enumerates `--with-aspens`-style flags) to mention `--with-gitnexus`.

## Capabilities

### New Capabilities
- `gitnexus-repo-init-addon`: bootstrap-time opt-in GitNexus indexing for a repo onboarded
  via `worktrail-repo-init propose --with-gitnexus`.

### Modified Capabilities
(none -- this is a new, additive opt-in flag; no existing spec's requirements change)

## Impact

- `src/worktrail/onboarding/repo_init.py`: new `enable_gitnexus()` function, new
  `--with-gitnexus` argparse flag, `cmd_propose` wiring.
- `tests/onboarding/test_repo_init.py`: new tests mirroring `EnableAspensTests`/the
  `--with-aspens` propose tests (idempotency, flag wiring, subprocess-failure warning path),
  using a mocked `gitnexus` invocation.
- `skills/worktrail-repo-init/SKILL.md` (and this repo's own `AGENTS.md`, if applicable):
  documentation of the new flag.
- No changes outside this repo -- devops's `refresh-all.sh` cron already re-indexes any
  registry entry generically, regardless of how it was created.
