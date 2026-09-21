## Why

`load_policy()` validates policy keys ad-hoc, one hand-written branch per key. Confirmed by
reading `src/worktrail/router/policy.py:1280-1390` on 2026-09-20: there are explicit checks for
`automerge.max_risk`, `automerge.enabled`, `allow_seeded_implementation`, `agent_cli`,
`fallback_agent_cli`, `agent_model`, seven integer keys, `pre_commit_cmd`,
`max_active_changes`, the two `triage_*` keys, `merge_method_by_base` and `promotion_pairs` --
and nothing at all for the rest of `DEFAULTS`. There is no generic type-vs-`DEFAULTS` sweep, so
a value of the wrong type under an unlisted key is threaded straight through to its consumer.

That is not hypothetical. PR #1302 ("fix(policy): parse inline flow sequences so
`target_branches: []` stops disabling auto-merge", merged 2026-09-20) fixed exactly one
spelling: `_parse_scalar` now understands an inline flow sequence, so `target_branches: []`
loads as `[]` instead of the truthy string `'[]'`. Every other list-valued key has the same
shape of exposure through other spellings -- `protected_paths`, `require_human_routes`,
`docs_only_paths`, `migration_path_patterns` -- and so do the string keys
(`pre_pr_cmd`, `integrate_smoke_cmd`, `post_merge_smoke_cmd`, `worktree_bootstrap_cmd`,
`base_branch`, `release_gate`, `auth_testing`, `run_record_dir`) and `agent_learning`.

The failure mode is silent and safety-relevant in both directions. A string where a list is
expected is iterated character-by-character: `protected_paths: "docs/**"` becomes the seven
patterns `d`, `o`, `c`, ... in `_protected_path_match`, so a repo that meant to protect its docs
gets a policy that protects nothing recognizable. A non-string where a command is expected
reaches the shell-invoking gate as a non-string. Nothing warns: `_meta["warnings"]` is the one
channel that surfaces policy problems to an operator, and for these keys it stays empty.

Fixing spellings one at a time, as #1302 did, leaves the class open. The types are already
declared -- `DEFAULTS` holds one correctly-typed default per key -- so the sweep can be derived
from them rather than hand-written a fourteenth time.

## What Changes

- Add a generic type sweep to `load_policy()`, driven by `DEFAULTS`: for each flat policy key,
  the expected type is the type of its default, and a value of the wrong type is replaced by
  that default with a `_meta["warnings"]` entry naming the key, the expected type and the
  offending value. `automerge.target_branches` is swept the same way.
- Keys whose default is `None` carry no inferrable type, so they get an explicit
  key-to-expected-type table (`POLICY_KEY_TYPES`) alongside `DEFAULTS`; a key present in
  `DEFAULTS` but absent from that table is a build failure, not a silently unswept key.
- The sweep runs *before* the existing per-key validation, so every existing warning message,
  clamp and default stays exactly as it is today -- the sweep only covers keys nothing else
  covers, and hands the already-validated keys on unchanged when their coarse type is right.
- `routing` and `add_ons` are out of scope: both are re-parsed with real YAML and already have
  dedicated validators (`_validate_routing`, `_resolve_add_ons`).
- A ratchet test asserts every `DEFAULTS` key is covered, so the next key added to the policy
  cannot reintroduce the gap.

## Capabilities

### New Capabilities
- `policy-key-type-validation`: every policy key's value is type-checked against its declared
  default before any consumer sees it.

### Modified Capabilities

## Impact

- `src/worktrail/router/policy.py` (`POLICY_KEY_TYPES` table + sweep in `load_policy`).
- `tests/router/test_policy_key_types.py` (new).
- Behavior change is confined to policy files that are already wrong: a mistyped value now
  falls back to its documented default and warns, instead of reaching its consumer. A
  correctly-typed policy file resolves byte-for-byte identically.
