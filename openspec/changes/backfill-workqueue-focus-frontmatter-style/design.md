## Context

See proposal.md - Why. `serialize_frontmatter()`/`is_canonical_style()` (`shared/brief_frontmatter.py`,
PR #582) are whole-frontmatter-block operations: `serialize_frontmatter(dict)` renders an
entire frontmatter mapping, and `is_canonical_style(content)` compares a file's whole raw
frontmatter block against what re-serializing its parsed dict would produce — any field's style
drift (not just `focus:`) makes it return `False`. This change's scope is narrower: fix only
the `focus:` scalar's style across the corpus, leave every other field exactly as it is today,
even on a file whose only defect is unrelated to `focus:`. `backfill_created_quoting.py`
(PR #570) is the closest precedent: a preview/execute script that rewrites one named field's
line in place and verifies everything else is untouched, rather than reserializing the whole
block.

## Goals / Non-Goals

**Goals:**
- Backfill every `queue/*.md` and `picked/*.md` brief whose `focus:` frontmatter scalar is not
  already in `serialize_frontmatter()`'s canonical `|-` literal-block style, so it is.
- Guarantee, by construction (not just by a post-hoc diff check), that nothing outside the
  `focus:` scalar's own span changes — same key order, same styling on every other field
  (including any that are themselves still non-canonical for unrelated reasons), same body.
- Preview/execute split with the same safety properties as `backfill_created_quoting.py`:
  re-passed preview JSON (never a stale re-scan), explicit `--confirm`/`--decline`, per-file
  post-write validation with rollback on failure, and idempotent re-runs.

**Non-Goals:**
- Fixing any other field's style (e.g. a hypothetical still-unquoted `created:` some file might
  carry) — out of scope for this change; a file like that keeps that defect and only gets its
  `focus:` fixed.
- Changing `shared/brief_frontmatter.py`, `create_handoff.py`, or `consolidate_cluster.py` —
  already canonical writers per #582; no code path there needs to change.
- Running against anything other than `$WORK_QUEUE_DIR`'s live `queue/`/`picked/` corpus (no
  repo-tracked fixture corpus exists to migrate).

## Decisions

**Splice the `focus:` value's exact character span, rather than reserializing the whole
frontmatter block and diff-checking the result.** Use `yaml.compose()` (not `yaml.safe_load()`)
on the frontmatter block's raw text to get a node tree that retains source position marks;
walk the root mapping node for the `focus` key's value node, and take its
`start_mark.index`/`end_mark.index` as the exact character span in the raw block that
represents the current `focus:` value (block-indicator line through the last content line, for
every existing style — plain, single/double-quoted, folded, or already-literal). Replace only
that span with the value portion of `serialize_frontmatter({"focus": <parsed value>})`'s output
(stripping the leading `focus: ` key prefix, since the span excludes the key itself). Every
character before and after that span — every other frontmatter line, the closing `---` fence,
and the entire body — is copied through unmodified; there is no separate "verify nothing else
changed" step because nothing else is ever written.
- *Alternative considered*: reserialize the whole dict via `serialize_frontmatter(parsed)` (as
  `create_handoff.py`/`consolidate_cluster.py` do when writing a brand-new brief) and assert the
  result differs from the original only in the `focus:` region. Rejected: a whole-block
  reserialize also silently normalizes any other field's style PyYAML would render differently
  from the file's current bytes (key order is preserved since `dict` insertion order from
  `safe_load` matches source order, but scalar style on other fields is not guaranteed
  byte-identical, e.g. a legacy file with some other still-non-canonical field). That would
  make the backfill quietly do more than its stated scope, and would need its own diff-parsing
  logic to detect and reject such cases — strictly more code than composing the narrower,
  span-exact splice directly.
- *Alternative considered*: line-based replacement like `backfill_created_quoting.py`'s
  `_requote()` (find the line starting with `focus:`, replace just that line). Rejected: unlike
  `created:`, a non-canonical `focus:` scalar routinely spans multiple lines today (folded
  double-quoted continuations, embedded newlines) — the exact defect this backfill exists to
  fix — so a single-line replacement cannot express the fix. `yaml.compose()`'s marks give an
  exact multi-line span without hand-parsing YAML's block/flow scalar continuation rules.

**Preview criterion is a per-field comparison, not `is_canonical_style()`.** A file is proposed
only when its current `focus:` span, re-rendered, would differ from what
`serialize_frontmatter({"focus": value})` produces for the same parsed value. This is
deliberately narrower than `is_canonical_style(content)` (used by #582's writers to gate their
own new-brief writes), which fails on *any* field's style drift. Using the whole-block check
here would incorrectly propose files whose `focus:` is already canonical but some other field
isn't (out of scope — nothing to fix on `focus:`), and would need extra logic to avoid
conflating that case with a real `focus:` defect.

**Files with no `focus:` frontmatter key are skipped, not treated as an error.** Some real
historical briefs carry `focus` only in a `## Focus` body section, never frontmatter
(`validate_brief_text`'s own docstring notes this). Preview reports these as skipped with a
`"no focus: in frontmatter"` reason, mirroring `_scan_created_line`'s `None` return for a
missing `created:` line.

**Post-write validation checks round-trip fidelity, not `is_canonical_style()`.** After
splicing, re-read the file and confirm: (1) `validate_brief` still passes (parseable,
required fields intact), and (2) the re-parsed `focus` string equals the pre-rewrite parsed
value exactly (the splice changed only *style*, never the value). Do not additionally assert
`is_canonical_style()` post-write — a file with some other unrelated non-canonical field will
correctly still fail that whole-block check, and asserting it here would be a false failure
signal for a fix that did exactly what it was scoped to do. Roll back (rewrite the original
content) on either check failing, mirroring `backfill_created_quoting.py`'s rollback pattern.

## Risks / Trade-offs

- [Risk] `yaml.compose()`'s mark offsets could be off by one in some scalar-style edge case
  (e.g. a value node's `end_mark` including or excluding a trailing newline differently across
  styles), producing a malformed splice → [Mitigation] the post-write round-trip check
  (re-parsed `focus` value must equal the original parsed value, and `validate_brief` must
  still pass) catches this before the write is accepted, with automatic rollback; test coverage
  exercises every style PyYAML can currently produce for `focus:` across the real corpus
  (plain, single-quoted, double-quoted-folded, literal, folded) as splice-source fixtures.
- [Risk] Corpus scan touches ~1400 live files outside this repo (`$WORK_QUEUE_DIR`); a bug could
  corrupt real handoff briefs → [Mitigation] same preview/execute/rollback contract already
  proven safe by `backfill_created_quoting.py`'s corpus-wide run (PR #570): `execute` only acts
  on a re-passed preview (never a fresh scan at write time), requires explicit `--confirm`, and
  the work queue's own git-backed sync (`WORK_QUEUE_GIT_SYNC=1`, per-machine backup) gives a
  recovery path independent of this script.
- [Risk] A file changes (claimed, moved, edited) between `preview` and `execute` → [Mitigation]
  `execute` re-checks the file's current on-disk state (existence, current `focus:` span)
  immediately before writing and skips (does not clobber) anything that no longer matches what
  `preview` observed, same as `backfill_created_quoting.py`'s `execute_apply`.

## Migration Plan

1. Land the script + tests in this PR (no corpus writes yet — script default is `preview`,
   `execute` defaults to requiring an explicit flag).
2. As part of this change's implementation (not a separate follow-up), run
   `backfill-focus-style preview` against the real `$WORK_QUEUE_DIR`, inspect the proposal
   count and a sample of proposed diffs, then run `backfill-focus-style execute --confirm` with
   the preview piped in.
3. Rollback: none needed at the repo level (the script only touches files under
   `$WORK_QUEUE_DIR`, never repo-tracked content). If a bad execute run is discovered, the
   individual affected briefs can be restored from the work-queue's own git-backed history
   (`behindthedash/work-queue`), independent of this repo's git history.
