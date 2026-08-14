## Context

`consolidate_cluster.py draft_consolidated_brief()` already reads each resolvable member's
frontmatter to pull `focus`, `recommended-route`, and `change-kind` into a `members: [...]`
list on the returned draft (`consolidate_cluster.py:243-267`). `_build_consolidated_brief_content()`
then stamps `created: {now.isoformat(...)}` on the written brief (`consolidate_cluster.py:358`) —
this is the consolidation timestamp, not any source brief's true creation time.

`check_brief_staleness.py`'s CLI path reads a brief's `created:` via `_read_brief()`
(`check_brief_staleness.py:744-773`), which calls `read_frontmatter()` (a `yaml.safe_load`
over the fenced block). PyYAML auto-detects an unquoted ISO-8601-shaped scalar as a
`datetime.datetime`/`datetime.date` object, not a string — `check()`'s own `_normalize_since()`
already handles that (`check_brief_staleness.py:341-347`), so `_read_brief()` must handle both
forms identically when it later reads `original-created:`.

See proposal.md for the motivating false-negative and the live incident (PR #11 excluded from
brief `20260813-115343-consolidated-8376e7`'s staleness check).

## Goals / Non-Goals

**Goals:**
- Preserve, on a consolidated brief, the earliest true creation time of any member it merged.
- Make `check_brief_staleness.py` prefer that preserved timestamp over the brief's own
  (consolidation-time) `created:` when computing the search boundary.
- Keep `created:` semantically stable (consolidation time) for every other reader of that
  field — no change to its meaning, only an additional field.

**Non-Goals:**
- Backfilling `original-created:` onto consolidated briefs already written before this change.
  Existing consolidated briefs keep their current (consolidation-time-only) `created:` and are
  unaffected; only briefs consolidated after this change carries the new field.
- Changing `_read_brief()`'s or `draft_consolidated_brief()`'s public call signature.
- Touching `resolvable_members()`, `build_preview()`, or `execute_consolidation()`'s claim/done
  flow — only the draft-building and content-rendering steps change.

## Decisions

**Track the earliest `created:` inside `draft_consolidated_brief()`, not as a separate pass.**
The function already opens and parses every resolvable member's frontmatter to build `members`;
reading `created:` there and tracking a running minimum costs one more dict lookup per member,
with no extra file I/O. Alternative considered: a separate helper that re-reads all members
just for `created:` — rejected, since it would re-open every member file a second time for no
benefit.

**Represent the result as `original_created` (Python) / `original-created:` (frontmatter),
parallel to `created`/`created:`.** A hyphenated frontmatter key matches this repo's existing
convention (`recommended-route`, `change-kind`); the Python dict key uses an underscore per
Python identifier convention, matching how `created` itself is never a dict key inside
`draft_consolidated_brief`'s return today (it doesn't carry `created` at all — this is a new
field, not a rename). Alternative considered: overwrite `created:` with the earliest member
timestamp directly — rejected, because `created:` is documented and load-bearing elsewhere
(e.g. cluster telemetry, `_default_base_dir`-adjacent tooling) as "when was this queue entry
written", and consolidation time is a real, distinct fact worth keeping.

**Parse each member's `created:` leniently, degrading to "no contribution" on failure.**
Reuses the same defensive posture `draft_consolidated_brief()` already applies to the rest of
member parsing (`except Exception: continue`, see module docstring: "Any unexpected exception
inside preview-phase helpers ... degrades to excluding the affected member rather than
raising"). A member with a missing or unparseable `created:` simply does not contribute to the
earliest-timestamp computation; if no member's `created:` parses, `original_created` is omitted
entirely and the written brief falls back to today's behavior (no `original-created:` field,
`check_brief_staleness.py` reads `created:` as it always has).

**Accept both quoted-string and PyYAML-native-datetime forms when parsing a member's
`created:`.** `_parse_frontmatter()` in this module already restricts itself to
`(str, int, float, bool)` scalars and drops everything else (`consolidate_cluster.py:101-105`),
which would silently drop a native-`datetime`-typed `created:` value. Reading `created:`
for this feature therefore goes around `_parse_frontmatter()` and instead reads the raw parsed
YAML mapping (via `split_frontmatter`, already imported) so a `datetime.datetime`/`datetime.date`
value survives; both that case and a quoted-string case normalize to a comparable
`datetime.datetime` using the same UTC-aware parsing approach `check_brief_staleness.py`'s
`_to_utc_datetime()` already uses (duplicated locally rather than imported cross-module — see
the module docstring's existing "no cross-skill Python import" boundary between `router/` and
its siblings; `check_brief_staleness.py` is a router-owned sibling module, but this module does
not currently import from it and this change keeps that boundary rather than introducing a new
inter-module dependency for one helper).

**`check_brief_staleness.py._read_brief()` reads `original-created:` directly via
`read_frontmatter()`, preferring it over `created:` when present and non-empty.** This is a
two-line change at the point `_read_brief()` already extracts `fm.get("created")` — add
`fm.get("original-created") or fm.get("created")` (native PyYAML datetime or string, either
way — `check()`'s existing `_normalize_since()` already accepts both). No change to `check()`
itself: it already accepts `since: Any` and normalizes whatever it's given, so it's agnostic to
which frontmatter field the value came from.

## Risks / Trade-offs

**[Risk] A future third consolidation-of-a-consolidation could compound minor drift if a
member's own `original-created:` isn't itself propagated.** → Mitigation: when reading a
member's timestamp for the earliest-tracking computation, also prefer that member's own
`original-created:` over its `created:` (same preference order `check_brief_staleness.py`
applies) — so consolidating an already-consolidated brief still propagates the true original
timestamp, not an intermediate consolidation time. This is a small addition to the same
per-member read, not a second pass.

**[Risk] Existing consolidated briefs in the live queue lack `original-created:` and keep their
current false-negative exposure.** → Mitigation: explicitly out of scope (see Non-Goals) — this
change fixes the propagation going forward; backfilling is a separate, human-reviewable
decision (which timestamp is authoritative for an already-written brief is not always
mechanically recoverable) and not part of this fix.

## Migration Plan

No migration needed. This is purely additive: a new optional frontmatter field, read with a
fallback. No existing brief's `check_brief_staleness.py` behavior changes, since none carry
`original-created:` yet. Roll out is a normal PR merge; no data migration, no flag.
