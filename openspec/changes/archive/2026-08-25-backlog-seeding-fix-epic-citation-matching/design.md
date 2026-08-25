## Context

See `proposal.md` for motivation and the capability spec for normative behavior.
`_citing_spec_ids(repo, epic_id)` currently does `epic_id in md.read_text(...)` — a bare
substring check against the literal epic id string — for every top-level `.md` file under a
`docs/specs/<id>/` folder and every `.md` file under `openspec/{specs,changes}/**`. This is the
only citation signal `find_epic_gaps()` has, and epic authors do not reliably write the full
epic id into a citing spec/change's own markdown; they write prose ("Epic 002 Feature 2 adds
this conservative runtime boundary") or rely on the feature's `**Future spec id:**` value
already matching the folder name.

## Goals / Non-Goals

**Goals:**

- Recognize `Epic <NNN> Feature <M>` prose as a citation, since it is the shape real change
  proposals actually use.
- Recognize a citing file naming any of the epic's own documented future spec ids.
- Keep the literal epic id string match unchanged as an always-present signal.
- Keep `_citing_spec_ids()`'s scan behavior (both spec formats, archived changes, dedup by
  folder name) untouched — only the per-file match test changes.

**Non-Goals:**

- Attributing a specific citation to a specific feature number (e.g. proving spec X cites
  *Feature 2* specifically, not just *some* feature of the epic). `find_epic_gaps()` only ever
  needs a citation *count*, not a per-feature mapping — Route B doctrine already says features
  are assigned ids only at Route C pickup, so a stronger per-feature contract isn't load-bearing
  here.
- Fuzzy or partial epic-id matching (e.g. matching `002` alone with no `Epic`/`Feature` framing)
  — too permissive, would risk counting unrelated mentions of a bare three-digit number as a
  citation.
- Changing the `docs/specs/epics/<NNN>-<slug>.md` epic-authoring format; `**Future spec id:**`
  is already the documented convention (see the real epic files in this repo and in `datalena`).

## Decisions

### Add citation signals as precompiled regex patterns, computed once per epic

`_epic_citation_patterns(epic_id, epic_text)` returns a list of compiled patterns: the literal
epic id (unchanged), an `Epic <NNN> Feature <M>` pattern derived from the epic id's leading
three digits, and one literal pattern per `**Future spec id:** `id`` value found in the epic's
own markdown. `_citing_spec_ids()` takes this pattern list instead of the bare epic id string
and counts a file as citing when *any* pattern matches (`any(p.search(text) for p in
patterns)`). Computing patterns once per epic (in `find_epic_gaps()`, which already holds the
epic's full text) rather than per-candidate-file avoids re-deriving the epic number and
re-scanning the epic text for every spec/change folder checked.

Alternative rejected: keep `_citing_spec_ids()` taking a bare epic id and re-derive the epic
number and future-spec-ids inside it on every call. This would need the epic's own text passed
in either way (future-spec-ids can only be read from it), so it buys nothing over passing
precompiled patterns and would recompute the same regexes for every citing-candidate file
instead of once per epic.

### `Epic <NNN> Feature <M>` matches any feature number, not just an epic's actual feature count

The pattern is `\bEpic\s+<NNN>\s+Feature\s+\d+\b` — `\d+` matches any digits, not bounded by the
epic's own decomposed feature count. A citation naming a feature number beyond the epic's actual
decomposition cannot happen in practice (a change wouldn't cite a feature that doesn't exist),
and bounding the pattern to `1..features` would require passing the feature count in as well as
the epic id/text for no behavioral benefit — the false-positive risk this pattern exists to
avoid (a bare `\bEpic\s+<NNN>\b` matching unrelated epic mentions) is already closed by requiring
the `Feature <M>` suffix.

### Future-spec-id matching is a literal substring, not scoped to a specific feature

Once a future spec id is extracted from the epic text, it is matched as a literal string
anywhere in a citing candidate's markdown — the same strength as the epic id's own literal
match, and for the same reason: a future spec id is deliberately a distinctive, epic-scoped slug
(e.g. `work-queue-conservative-dependency-resolution`), so an unrelated false-positive match is
not a realistic concern.

## Risks / Trade-offs

- **Prose pattern too narrow:** `Epic <NNN> Feature <M>` requires that exact word order and
  spacing (case-insensitive). A citation phrased differently ("Feature 2 of Epic 002") would
  still be missed. Accepted for this change — the reported live incident and this repo's own
  citing changes consistently use `Epic <NNN> Feature <M>` order; broadening further raises
  false-positive risk for a shape not yet observed in practice.
- **Future-spec-id extraction depends on the `**Future spec id:** `id`` convention holding.** An
  epic authored without backtick-quoting its future spec ids gets no benefit from that signal,
  but the literal-id and prose signals are unaffected.

## Migration Plan

None — pure behavior widening for a private (`_`-prefixed) function with one caller. No stored
data, seed-key format, or public API changes.

## Open Questions

None.
