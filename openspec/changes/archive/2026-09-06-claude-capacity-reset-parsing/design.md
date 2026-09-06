# Design

## D1 -- A second pattern in `parse_explicit_reset`, not a second function

The two callers (`spawnlib.py:1252`, `drain.py:2608`) both want the same thing: "does this
output state when capacity comes back?" Adding a second entry point would force both to call
two functions and combine the answers, and any third caller would silently get only one wording.
So `parse_explicit_reset` keeps its signature and tries the dated Codex pattern first (it is
strictly more informative -- it carries a real date), then the lenient Claude pattern.

Rejected: reusing `spawnlib.parse_session_limit_reset`. It returns a **naive local**
`datetime`, while everything in the capacity cache is UTC-aware; it hard-requires `hit your
session limit` in the text, which the weekly wording does not contain; and it requires `H:MM`,
so `resets 2pm` does not match. Making it serve both would change its contract for the
in-spawn parking path, which is not what this change is about. The two parsers stay separate
and the non-goal is stated in the proposal.

## D2 -- The lenient pattern is guarded by notice size; the dated one is not

`try again at Aug 8th, 2026 2:17 AM` names a specific instant and is vanishingly unlikely to
appear incidentally, so gating it on text length would be a behaviour regression for the Codex
path (a cap notice interleaved with worker output would stop parsing). `resets 2pm` is the
opposite: it is short, generic, and appears in this repo's own source, tests, and specs.
`spawnlib` already paid for that lesson (`_SESSION_LIMIT_NOTICE_MAX_CHARS`, comment at
`:163-169`: workers editing that very file matched its docstring example and parked runs for 16
hours). This change adopts the same bound and the same constant value (600 chars, measured on
`text.strip()`) for the lenient pattern only.

## D3 -- Timezone resolution: honour the stated zone, fall back to local

`resets 2pm (America/Los_Angeles)` states its zone, and the orchestrator does not necessarily
run in it -- assuming local would put the gate up to a day off. The parenthesised token is
resolved with `zoneinfo.ZoneInfo`; `ZoneInfoNotFoundError` (no system tzdata, or a non-IANA
label such as `PT`) falls back to the local-wall-clock assumption the dated path already makes,
so the parse never fails outright over a zone it cannot name.

## D4 -- Roll forward, and anchor the roll in the stated zone

The notice gives no date, so a bare clock time is resolved to its next occurrence: the *same
day* if still ahead, otherwise the *next day* -- the rule `parse_session_limit_reset` already
uses. "Ahead" is judged in the stated timezone, not local, so a 2pm-Pacific reset seen at 11pm
UTC (4pm Pacific) correctly rolls to tomorrow rather than resolving to a moment already past.

This makes a lenient parse *always* return a future instant, which is why
`drain.py:2608`'s lack of `spawnlib`'s past-reset guard is not a new hazard here.

A weekly cap can of course reset days out rather than tomorrow; a date-less notice cannot say
so and this change does not guess. Under-shooting to the next occurrence of the stated clock
time is safe and strictly better than today's 1-hour cooldown: the gate expires, one spawn
re-probes, and if the cap is still live the same notice re-gates it another day forward.
