# Investigation: which writer produced non-canonical `focus:` frontmatter, and is the drift preventable?

**Triggered by:** work-queue brief `20260820-073119` (blocked by `20260820-073044`, delivered
via PR #570).

**Question:** brief `20260820-073119` reported `work-queue/picked/20260819-093300-new-datalena-when-i-browse.md`
as written by a non-canonical, unidentified writer — a double-quoted, line-folded `focus:`
scalar plus "mis-encoded unicode" — unlike `create_handoff.py`'s clean `|-` literal block. Which
code path wrote it, and can the corpus be protected against this drift regardless of the answer?

## Verified Observations

- The cited file is valid UTF-8 end-to-end (`data.decode('utf-8')` succeeds); the em dash in
  its body is `U+2014` (`hex(ord(...)) == '0x2014'`), the single correct code point for `—`.
  The brief's "mis-encoded unicode" claim does not hold up — `cat -A`'s `M-bM-^@M-^T` rendering
  of any non-ASCII UTF-8 byte sequence was misread as corruption; it is not.
- The file's `focus:` value **is** genuinely non-canonical: a double-quoted flow scalar folded
  at ~80 columns with `\<newline>  \ ` continuations, instead of `create_handoff.py`'s `|-`
  literal block style.
- The file's frontmatter key set and order (`id, created, focus, repo, remote, base-branch,
  status`, then only `recommended-route` as an optional trailer) matches
  `create_handoff.py`'s `create_handoff()` dict-building order exactly, including `repo: null`
  / `remote: null` / `base-branch: null` emitted as explicit `null`s (a shape only that
  function produces — `consolidate_cluster.py`'s writer omits those keys entirely rather than
  nulling them).
- `create_handoff()` has wrapped `focus` in the `_LiteralStr` block-scalar marker
  unconditionally since commit `63cec58` (2026-07-30, PR #49) — three weeks before this file's
  `created:` timestamp (2026-08-19T09:33). Verified directly against the commit that was `main`
  HEAD at that time (`git show 25e2f92:src/worktrail/workqueue/create_handoff.py`, the last
  commit before the file's creation timestamp): `_LiteralStr` was already present. The current
  `main` (`bdab558`) still has it. There is exactly one code path in this repo that builds this
  exact frontmatter shape, and it has been incapable of producing a double-quoted/folded
  `focus:` for three weeks prior to this file's creation.
- `consolidate_cluster.py` (`src/worktrail/router/consolidate_cluster.py`) is a second,
  previously-undocumented internal writer into `queue/`: it hand-rolls
  `yaml.safe_dump(focus, default_style='"', allow_unicode=True, width=10**9)` for its own
  `focus:` line instead of reusing `create_handoff.py`'s `_LiteralStr` marker. Its output is
  double-quoted by design (chosen in PR #369, 2026-08-13, specifically to make a focus
  containing a literal `"` round-trip safely) but not line-folded (the `width=10**9` fix, same
  PR, closes the folding defect this investigation's cited example exhibits). It also omits
  `repo`/`remote`/`base-branch` and emits an **unquoted** `created:` line
  (`f"created: {now.isoformat(...)}"`), which does not match the cited file's shape either.
- `spec_sync_sweep_brief.py` / `spec_sync_sweep_checkbox_brief.py` write findings-derived
  briefs into `queue/` via their own `yaml.safe_dump` calls, structurally distinct from both of
  the above (different field set entirely).
- No `~/projects/developer-kit` checkout exists on this machine to grep for a pre-extraction
  writer; the companion brief (`20260820-073044`, closed) had already grepped this repo's own
  `workqueue/*.py` for a second writer and found none at the time — `consolidate_cluster.py`
  lives under `router/`, outside that earlier grep's scope, which is why it was missed then.
- `shared/brief_frontmatter.py`'s `validate_brief`/`validate_brief_text` parse YAML values and
  check required-field non-emptiness only; there is no check anywhere in the repo that a
  written brief's serialized *style* (block vs. flow scalar, quoting) matches what the repo's
  own writers intend to produce. Any writer — this repo's own, or a genuinely external one this
  repo cannot change — can silently drift and nothing catches it.

## Unknowns / Missing Evidence

- The exact process that wrote the cited file. No session transcript, cron log, or run record
  for that specific write is available from this workstation. `~/.claude/plugins/cache/worktrail/worktrail/`
  holds 140+ SHA-keyed historical plugin snapshots (pre-dating the "console-script only, no
  script-path resolution" design this repo's own `AGENTS.md` now mandates); a session holding
  stale, pre-refactor skill prose that once resolved a script path directly into one of those
  snapshots — rather than calling `worktrail-handoff` via `PATH`, which always resolves to the
  current editable install — would reproduce pre-`_LiteralStr` output even weeks after `main`
  was fixed, but no artifact on this machine confirms that actually happened for this file.
- Whether any other machine, CI runner, or Codex/OpenCode session used a pinned/cached
  `worktrail` wheel older than `63cec58`. Stale wheels for `0.6.0`/`0.7.0`/`0.8.5` exist in
  `~/.cache/pip/wheels` but are inert unless something explicitly installs from them; no
  evidence found that anything currently does.

## Hypotheses

- **Hypothesis (unconfirmed):** the cited file was produced by a session executing an old,
  pre-`63cec58` snapshot of `create_handoff.py`'s logic directly (bypassing the `worktrail-handoff`
  console script and its always-current `PATH` resolution), most plausibly via a stale
  `$CLAUDE_PLUGIN_ROOT`-relative script invocation carried over from before this repo adopted
  its current console-script-only design. Not proven; no direct evidence of the specific
  process ties it to this file.

## Confirmed defect (independent of the above hypothesis)

Regardless of which process wrote the one cited example, the corpus has **no enforcement** that
any writer — present, future, internal, or external — produces canonical frontmatter style, and
a second internal writer (`consolidate_cluster.py`) already diverges from `create_handoff.py`'s
own style (double-quoted vs. literal-block `focus:`). This is confirmed by direct code
inspection, not inference.

## Recommended next route

**Route F** (defect repair), scoped to the confirmed gap: extract a shared frontmatter
serializer both internal writers use, route `consolidate_cluster.py` through it, and extend
`shared/brief_frontmatter.py` to assert canonical style so a future writer that skips the
shared serializer is caught rather than silently accepted. This closes the gap for every
writer this repo owns and gives external tools a validation backstop, without depending on
identifying the unconfirmed historical culprit. Continuing in the same run (recorded below).

**Deferred, explicitly out of scope for this fix:** backfilling the ~1400 existing corpus
files' `focus:` style to canonical. Unlike the narrow one-line `created:` requoting PR #570
already ran corpus-wide, a `focus:` style rewrite touches free-text content across the whole
corpus and deserves its own isolated review — filed as a separate handoff, not bundled here.
