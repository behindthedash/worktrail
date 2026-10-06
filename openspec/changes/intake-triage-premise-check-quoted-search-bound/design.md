## Context

The premise check runs before every repo-resolved brief-triage evaluation and its results are
persisted on the verdict (`premise_check`) and rendered to the evaluator. See `proposal.md` —
Why for the defect, the reproduction evidence, and the measured needle class.

The check already has a bounded-subprocess mechanism for one needle kind: `_check_command`
runs under `run_premise_check`'s `timeout_s` (default 120) and converts
`subprocess.TimeoutExpired` into `{"confirmed": false, "detail": "timed out after <n>s"}`
(`src/worktrail/workqueue/premise_check.py:386`). The two quoted-search helpers,
`_git_grep_whole_string` (`premise_check.py:174`) and `_git_grep_fragments`
(`premise_check.py:201`), spawn `git grep -nIF -e <needle>` with no `timeout=` argument and
no exception handling, so the quoted search is the one pattern-driven subprocess path with
no bound.

Quoted needles are extracted by `_QUOTED_RE` (`premise_check.py:93`), which pairs
apostrophes/quotes/backticks across arbitrary prose including newlines. A needle containing
a blank line is therefore *routine*, not adversarial: the incident needle (#0 of brief
`20261005-105710-out-of-band-work-falsely`) is an apostrophe-paired prose span, 1124 chars
with a blank middle line.

Sibling changes already merged into this capability (both archived; the 2026-10-05 sibling
check found no other change work on `intake-triage`): `premise-check-path-needle-shape-filter`
(path-shape rule for the basename fallback) and
`queue-triage-fix-absence-claim-premise-confirmation` (absence-claim polarity). This delta
touches only the quoted-search path; those decisions are adopted unchanged, not re-derived.

## Goals / Non-Goals

**Goals:**

- No quoted needle can run an unbounded subprocess: every `git grep` invocation from the
  quoted search carries the check's bounded timeout, and a timeout is an unconfirmed needle
  that lets the evaluation proceed — not an exception escaping the check.
- The degenerate needle class (text containing an empty line) is refused *before any
  process spawns*, with a detail that states why, rather than discovered at the cost of a
  timeout's worth of CPU per search.
- One bound, no new surface: the existing `timeout_s` parameter is threaded through; no new
  CLI flag, policy key, or module-level constant.

**Non-Goals:**

- Changing `_fragments` splitting semantics (ellipses, `: ` separators), the
  whole-string-first-then-fragments order, the `_basename_matches`/`git ls-files` basename
  path, or the path and command needle paths.
- A needle-length bound (rejected in D2).
- A structured error code in `detail`; the `{kind, needle, confirmed, detail}` result shape
  is unchanged.
- Salvaging anything from an empty-line needle (e.g. searching its clean fragments —
  rejected in D2).

## Decisions

### D1: Bound the quoted search with the existing `timeout_s`, mirroring the command path

`_git_grep_whole_string` and `_git_grep_fragments` take `timeout_s` and pass it to their
`subprocess.run` calls; `_check_quoted` catches `subprocess.TimeoutExpired` once around
both helpers and returns `confirmed: false` with a timeout detail. Because the catch is in
`_check_quoted`, a timeout in the whole-string search or in any fragment search yields one
unconfirmed entry for the needle — matching the requirement's "that needle recorded
`confirmed: false` with a timeout detail" — and the fragment loop ends rather than retrying
the next fragment against a pathological needle.

Rationale for reusing `timeout_s` (default 120): the command path already defines what "the
check's bounded timeout" means, callers that tighten `timeout_s` (tests pass 5) get the
quoted search bounded to the same budget, and there is no second knob to document, test, or
drift. Legitimate quoted searches measured ~0.1–0.2s, so the default is a backstop, never a
budget the healthy path approaches.

Rejected: a new `--premise-grep-timeout` CLI knob (the originating brief's first
suggestion). It would add a second timeout surface with no measured need — the one
pathological class is refused outright by D2, so the timeout only backstops the unforeseen;
a per-search knob invites tuning a value that should never be reached.
Rejected: a module-level constant. It silently desynchronizes from the command budget and
cannot be exercised by callers' own `timeout_s`.

### D2: Refuse a quoted needle whose text contains an empty line, before any grep runs

The guard is needle-level and first: if splitting the needle's text on `\n` yields any empty
line (internal blank line, leading newline, or trailing newline — all three were measured to
wedge), `_check_quoted` returns `confirmed: false` with a detail stating it was not searched
because its text contains an empty line. Neither the whole-string search nor the fragment
fallback runs; the guard is one rule, not a filter applied per search stage.

Rationale: for this class the search cannot do anything useful. `git grep -F` with an
embedded empty line behaves as an alternation containing an empty alternative, which matches
*every line*: verified in a scratch repo, `git grep -nIF -e $'zzqxx\n\nwwvvu'` with neither
token present prints every line in the repo and exits 0 — so `_git_grep_whole_string`, which
reads rc=0 plus non-empty stdout as a hit, would spuriously confirm the needle, and in a
large tree the same form is pathological (still running at a 10s kill in this checkout; the
defect record measured >60s CPU). A timeout alone would leave both problems: up to
`timeout_s` of CPU burned per empty-line needle (multiplied across the fragment loop), and
on a small enough tree the search could *complete* into the spurious confirmation. Refusing
the class loses no true positive, because a fixed string spanning a blank line can never
occur within `git grep`'s line-oriented match — every "hit" such a search can produce is the
empty-alternative artifact.

Rejected: a needle-length bound (the brief's other suggestion). The defect axis is the empty
line, not length: a needle only thirteen characters long (`short\n\nshort2`) wedges, while a
single-line 895-character needle completes in ~0.1s — a length bound would refuse long
legitimate quoted log lines (the fragment fallback exists precisely for long quotes) while
still admitting short empty-line needles.
Rejected: searching the clean fragments of an empty-line needle instead of refusing it.
There is no measured benefit (the fragment search of such a needle is not what recovers
anything the whole-string search couldn't), it reintroduces multi-grep cost per needle, and
it would need a second rule for what counts as "clean" — two rules where the requirement
asks for one. Keep the guard needle-level and single.

### D3: The guard and the bound are separate, and both are required

The guard handles the known degenerate class at zero cost; the timeout backstops any other
pathological needle class (the one D2 does not know about). Implementing only the timeout
leaves the incident class burning CPU and able to spuriously confirm on small trees;
implementing only the guard leaves every other quoted search unbounded. The requirement
text states both, and the tests cover both independently.

## Risks / Trade-offs

- [An empty-line needle whose text is present in the checkout is now `confirmed: false`
  rather than possibly `confirmed: true`] → That `confirmed: true` can only ever be the
  empty-alternative artifact (all-lines match), never a verbatim match; refusing the class
  trades a spurious confirmation for an honest unconfirmed entry.
- [A pathological quoted needle not covered by the guard still costs up to `timeout_s` per
  grep invocation (whole string + each fragment)] → The timeout converts unbounded cost into
  a bounded worst case, and the one measured unbounded class is D2's. No aggregate budget is
  introduced: the requirement bounds each search with the check's timeout, and per-invocation
  bounding keeps the diff minimal.
- [New `detail` strings] → Nothing parses quoted-needle details structurally;
  `_work_directly_accepted()` reads `confirmed` generically, and unconfirmed quoted entries
  already occur ("no match for whole string or fragments"). Well-formed needles keep their
  existing details byte-for-byte.

## Testing

Unit coverage in `tests/workqueue/test_premise_check.py`, using the file's existing real
`git init`'d `repo` fixture and its `monkeypatch`-based `subprocess.run` capture style:

- a quoted needle containing an empty line is not searched — no `git grep` subprocess is
  spawned — and is not confirmed even when the repo contains the needle's non-empty text;
- a `subprocess.TimeoutExpired` raised during the quoted search yields `confirmed: false`
  with a timeout detail (mirroring `test_timeout_expired_is_unconfirmed_with_timeout_detail`
  for the command needle);
- every `git grep` subprocess call made by both the whole-string and fragment searches
  carries the caller's `timeout_s` (assert the captured `timeout` keyword equals a
  non-default value passed to `run_premise_check`).

End-to-end: run the module's premise check against the originating brief's focus in this
checkout and observe all eight needles complete quickly, needle #0 (the 1124-char
blank-line needle) recorded unconfirmed by the guard; then `openspec validate --strict` and
`worktrail-compile` on the change.
