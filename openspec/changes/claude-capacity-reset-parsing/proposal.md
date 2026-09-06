## Why

`agent_capacity.parse_explicit_reset` is the only path by which a provider-stated reset time
becomes a `reset_source: "provider"` gate (`spawnlib.py:1252-1266`, `drain.py:2608-2613`). Its
sole pattern, `_EXPLICIT_RESET_RE` (`agent_capacity.py:397-400`), matches **only** Codex's
wording: `try again at <Mon> <D>, <YYYY> H:MM AM`.

Claude's own cap notice does not use that form. `classify_failure` already recognises it --
`"weekly limit"` is a billing token specifically because of Claude's live wording, quoted in the
comment at `agent_capacity.py:369`: `You've hit your weekly limit · resets 2pm
(America/Los_Angeles)`. But that string appears in this module *only as a comment*, never as a
pattern. So a Claude weekly cap is classified `billing` and then gated by
`DEFAULT_COOLDOWNS["billing"]` -- **one hour** -- against a reset that may be days away. Every
hour for the rest of the week the gate expires, the cell is re-selected, the same cap is hit,
and one more spawn is burned. The gate is also `cooldown`-derived, so the probe cadence
(`PROBE_INTERVAL_S`, 900s) re-probes it as well.

The wording is demonstrably parseable: `spawnlib.py:154-175` has a separate lenient
`resets ... H:MM(am|pm)` parser (`_SESSION_LIMIT_RE` / `parse_session_limit_reset`) used for
in-spawn session-limit parking. That parser is not shared with `agent_capacity`, does not
accept a bare-hour clock (`2pm`), and ignores the parenthesised IANA zone.

No active change under `openspec/changes/` covers capacity reset parsing.

## What Changes

- **`parse_explicit_reset` gains a second pattern** for the Claude cap wording: a `resets`
  (optionally `resets at`) clock time with an optional minute component (`2pm`, `2:00pm`,
  `3:00 PM`) and an optional parenthesised IANA timezone.
- **The stated timezone is honoured when it resolves**, so `resets 2pm (America/Los_Angeles)`
  gates until 2pm Pacific, not 2pm wherever the orchestrator happens to run. An unresolvable or
  absent zone falls back to today's existing local-wall-clock assumption.
- **A clock-only reset rolls forward** to the next occurrence of that time, matching
  `parse_session_limit_reset`'s existing rule -- the notice states no date.
- **The lenient pattern only applies to notice-sized text.** Codex's dated form is
  self-identifying; `resets 2pm` is not, and `spawnlib` already learned (comment at `:163-169`,
  a 16-hour false park in 2026-07-23) that a worker transcript *quoting* this wording will match
  it. The existing dated pattern keeps matching text of any length, unchanged.
- **Callers are untouched.** `spawnlib` and `drain` keep calling the same function; a Claude
  weekly cap now simply yields a `provider`-derived gate at the stated reset instead of a
  1-hour `cooldown` gate.

## Capabilities

### New Capabilities

- `agent-capacity-reset-parsing`: which provider cap-notice wordings yield an explicit reset
  timestamp, how a date-less clock time and a stated timezone are resolved, and when a match is
  refused because the text is a transcript rather than a notice.

### Modified Capabilities

(none -- `grep -rln "explicit reset\|reset_source" openspec/specs/` matches only
`model-tier-routing` and `drain-concurrent-workers`, neither of which specifies cap-notice
parsing.)

## Impact

- **Code**: `src/worktrail/orchestrator/agent_capacity.py` (one new regex + resolution helper
  inside `parse_explicit_reset`).
- **Tests**: `tests/orchestrator/test_agent_capacity.py` (extended).
- **Behaviour**: a Claude weekly/session cap produces a gate at the stated reset with
  `reset_source: "provider"`, which `_probeable` deliberately leaves alone. No new env knobs, no
  cache-schema change, no caller change.
- **Non-goals**: sharing or removing `spawnlib.parse_session_limit_reset` (it drives in-spawn
  parking, a different decision, and rewiring it is not needed to close this gap);
  `drain.py:2608`'s missing already-in-the-past guard (`spawnlib.py:1245-1256` has one;
  drain does not -- out of scope here, and the roll-forward rule above means a clock-only parse
  is always in the future anyway); and the brief's second part, that a Fable cap is
  misclassified `startup`. That part is self-labelled HYPOTHESIS and is unverified: the billing
  check at `:364-386` precedes the startup check, so it can only misfire if the Fable wording
  contains none of the billing tokens. Fixing it requires the literal notice string first, which
  nobody has captured. It stays a separate change.
