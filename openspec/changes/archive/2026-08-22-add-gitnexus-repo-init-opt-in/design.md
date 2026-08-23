## Context

`src/worktrail/onboarding/repo_init.py`'s `propose` subcommand already has a proven shape for
a one-time, bootstrap-only opt-in: `--with-aspens` calls `enable_aspens(repo)`, which installs
a CLI and runs a one-shot init command, verifying success via filesystem postcondition rather
than trusting the subprocess exit code. See proposal.md - Why for the motivation.

GitNexus is devops-owned tooling with a global registry at `~/.gitnexus/registry.json`.
Running `gitnexus analyze <path>` writes a registry entry as a side effect of indexing --
confirmed by inspecting the registry after a real indexing run. There is no separate
"register" step and no config file worktrail needs to write. devops's `refresh-all.sh` cron
(every 5 minutes) already re-indexes every path found in that registry, regardless of how the
entry got there. This design decision was investigated and resolved before this proposal was
authored and is not reopened here.

## Goals / Non-Goals

**Goals:**
- Mirror `--with-aspens`'s exact shape (flag name style, function signature, result-list
  wiring, best-effort/verify-postcondition semantics) for a GitNexus equivalent.
- Get a freshly bootstrapped repo registered in GitNexus's global registry at propose time, so
  devops's existing cron picks it up on its next cycle with no devops-side changes.

**Non-Goals:**
- Building a full `worktrail.addons.base.AddOn` implementation for GitNexus. That framework
  exists for add-ons with an ongoing per-task `run()` step (e.g. aspens's post-commit sync).
  GitNexus's bootstrap need is a single one-shot command with no per-task concern, so it does
  not belong in that framework.
- Any change to `.worktrail/policy.yaml`'s schema or `default_policy_yaml()`. `add_ons.aspens`
  exists because `worktrail.addons.runner` reads that key to decide which add-ons to run
  per-task; there is nothing analogous for GitNexus to declare.
- Any devops-side change. The registry is the integration surface; `refresh-all.sh` already
  discovers new entries generically.
- Running GitNexus's per-repo AGENTS.md/CLAUDE.md/skills-file injection during bootstrap --
  `propose` just wrote/split those files in the same run, and letting GitNexus rewrite them
  immediately after would be an unwanted, hard-to-attribute side effect.

## Decisions

**Plain function, not an `AddOn` subclass.** `enable_gitnexus(repo)` lives directly in
`repo_init.py`, called only from `cmd_propose`, with no `worktrail/addons/gitnexus.py` file.
Alternative considered: implement `AddOn.install()`/`.configure()` like `AspensAddOn` for
API-shape consistency. Rejected because that protocol's contract implies an ongoing `run()`
step that `worktrail.addons.runner` would invoke per task; GitNexus has none, so a conforming
implementation would need a no-op `run()` that exists only to satisfy the interface -- pure
overhead with no caller benefit.

**Idempotency check: `.gitnexus/` directory existence.** Mirrors `enable_aspens`'s
`.aspens.json`-exists check. Alternative considered: querying `~/.gitnexus/registry.json` for
an entry matching the repo path. Rejected as a second source of truth to keep in sync with the
filesystem state `gitnexus analyze` itself produces, and as an unnecessary dependency on the
registry's on-disk format from worktrail's side; the local `.gitnexus/` directory is the same
postcondition `gitnexus analyze` itself would need to have created to be useful.

**Command: `gitnexus analyze --embeddings --index-only <repo>`.** `--index-only` matches
devops's own `refresh-all.sh` convention for skipping AGENTS.md/CLAUDE.md/skills injection
(see Non-Goals). `--embeddings` is included because bootstrap is a repo's only guaranteed
one-shot indexing moment; omitting it here would leave embeddings permanently missing for any
repo whose cron re-indexes are incremental-only.

**Failure handling: best-effort, warn, don't fail `propose`.** Matches `enable_aspens` exactly
-- swallow `subprocess.TimeoutExpired`/`OSError`, verify via postcondition, return a warning
string on failure rather than raising. `propose`'s job is to get a repo bootstrapped; a missing
or broken `gitnexus` CLI on the operator's machine (or a slow/failed first index) should not
block AGENTS.md/rulesets/policy-file writes that have nothing to do with GitNexus.

## Risks / Trade-offs

[A full `gitnexus analyze --embeddings` run on a large repo could take long enough to make
`propose` feel slow] → Mirrors `enable_aspens`'s existing best-effort/timeout posture; the flag
is opt-in (`--with-gitnexus`), so a caller who wants a fast `propose` simply omits it.

[`--index-only` is devops's own convention, not a documented GitNexus CLI contract enforced by
worktrail] → If devops changes what `--index-only` means, `enable_gitnexus`'s comment
referencing "matches `refresh-all.sh`'s convention" would go stale silently. Accepted: the same
risk already exists for `refresh-all.sh` itself and is outside worktrail's control; documenting
the reference in code comments and this design doc is the mitigation available at this layer.
