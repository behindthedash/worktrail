## Context

See proposal.md - Why. `_is_path_token()` in `src/worktrail/router/check_brief_staleness.py`
treats any token ending in `.` + 1-10 alphanumeric characters as a path-probe file extension.
Common prose abbreviations happen to match that shape after `_strip_punct()` runs (trailing `.`
is one of the stripped characters):

- `e.g` (from `e.g` or `e.g.`) → apparent extension `.g`
- `i.e` (from `i.e` or `i.e.`) → apparent extension `.e`
- `a.k.a` (from `a.k.a` or `a.k.a.`) → apparent extension `.a`

`etc` and `vs` are already unaffected today: `_strip_punct()` removes their trailing `.`
entirely (`etc.` → `etc`, `vs.` → `vs`), leaving no `.` for `_EXT_RE` to match, so they never
reach the extension branch in the first place.

## Goals / Non-Goals

**Goals:**
- Stop `e.g`, `i.e`, and `a.k.a` (and, defensively, `etc`/`vs`) from qualifying as path probes,
  regardless of case or which trailing punctuation the source prose used.
- Keep every existing path-probe scenario passing, in particular short legitimate extensions
  (`.py`, `.md`, `.sh`, and single-character extensions like `.c`/`.h`).

**Non-Goals:**
- No general-purpose abbreviation or NLP detection. This is a small, fixed, reviewable denylist
  of the specific abbreviations named in the proposal — not an attempt to recognize prose
  abbreviations in general.
- No change to `_EXT_RE`'s length bound (`{1,10}`). Shortening it (e.g. requiring 2+ characters)
  would also reject real single-character extensions such as `.c`, `.h`, `.r`, `.m`, `.v`, `.y`
  — a strictly worse trade than a denylist of the specific known-bad tokens.
- No change to `_is_symbol_token()`, `_is_flag_token()`, or any other extraction rule.

## Decisions

**Denylist checked against the already-stripped, lowercased token, inside `_is_path_token()`.**
`extract_probes()` already calls `_strip_punct(raw)` before calling `_is_path_token(token)` for
both the backtick-quoted and unquoted-prose extraction passes (`check_brief_staleness.py:248`,
`:262`), so `_is_path_token()` always receives punctuation-stripped input already. Adding the
denylist check inside `_is_path_token()` — rather than duplicating it at both call sites —
keeps the single existing choke point for "is this token path-shaped" authoritative, matching
the file's existing pattern where every other exclusion (`#`, parens/brackets, absolute paths,
no-letter task ids) lives in that one function.

A module-level frozenset, `_PATH_TOKEN_DENYLIST = {"e.g", "i.e", "etc", "vs", "a.k.a"}`, checked
via `token.lower() in _PATH_TOKEN_DENYLIST`. Alternatives considered:

- **Require 2+ characters after the dot (`_EXT_RE` length bound).** Rejected: `e.g`, `i.e`, and
  `a.k.a` all reduce to single-character apparent extensions, so this would work for exactly
  these three — but it also rejects genuine single-character extensions (`.c`, `.h`, `.r`, `.m`,
  `.v`, `.y`), which are real and not rare in this codebase's own domain (e.g. C/Verilog/R
  source files could plausibly be named in a brief). A denylist targets the known-bad tokens
  without narrowing what a legitimate extension can look like.
- **Require the pre-dot stem to look like a plausible filename stem (reject 1-2 letter stems).**
  Rejected as the primary mechanism: it's a heuristic with its own false-positive surface (a
  real one- or two-letter filename stem is unusual but not impossible, e.g. a script named
  `x.py`), and it's strictly more code than a fixed denylist for a fixed, named list of
  abbreviations. The proposal's motivating incident is about specific, common English
  abbreviations, not short stems in general.
- **Denylist of abbreviations (chosen).** Directly targets the tokens named in the incident and
  the proposal, with no effect on any other token shape. `etc` and `vs` are included even though
  they're already unaffected by today's bug, so the rule is self-documenting and doesn't leave a
  reader wondering why two of the five named abbreviations are missing from the list.

**Placed as an additional clause in the existing "SHALL NOT qualify" exclusion list**, not a new
requirement. This is a narrowing of the same path-probe-shape rule the spec already documents,
not new probe-extraction behavior — hence a MODIFIED requirement in the delta spec, not an
ADDED one.

## Risks / Trade-offs

- **Fixed list won't catch every abbreviation someone might write** (e.g. `cf.`, `viz.`, `et
  al.`) → Accepted: the proposal names five specific abbreviations from the observed incident;
  expanding the list later is a one-line change if another false-positive surfaces, and the
  denylist's existence and rationale (documented in the spec and this file) make that easy to
  find and extend.
- **A real file could theoretically be named exactly `e.g`, `i.e`, `etc`, `vs`, or `a.k.a`
  (extension-less or with those exact stems+extensions)** → Accepted: these are not realistic
  filenames in this codebase or in general practice; the false-negative cost (missing a
  vanishingly unlikely real path probe) is far smaller than the false-positive cost this change
  fixes (routine prose triggering the staleness guard).
