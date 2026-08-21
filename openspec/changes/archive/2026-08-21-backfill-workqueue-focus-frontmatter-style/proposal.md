## Why

PR #582 made `focus:` frontmatter style canonical (`|-` literal block, via the new
`serialize_frontmatter()`/`is_canonical_style()` in `shared/brief_frontmatter.py`) for every
writer this repo owns, but only going forward — it deliberately left the ~1400 existing
`queue/`/`picked/` corpus files untouched
(`docs/specs/research/queue-frontmatter-cross-writer-drift.md`, "Deferred, explicitly out of
scope for this fix"). Those legacy files still carry whatever scalar style (double-quoted,
plain, folded) their original writer produced, so `is_canonical_style()` returns `False` for
most of the corpus today and any future style-consistency check or tooling that assumes
canonical `focus:` formatting has to special-case the pre-#582 backlog. This backfills the
existing corpus to the canonical style PR #582 already established, closing that gap the same
way PR #570 closed the narrower `created:` quoting gap.

## What Changes

- Add `src/worktrail/workqueue/backfill_focus_style.py`, a preview/execute script mirroring
  `backfill_created_quoting.py`'s shape: `preview` scans `queue/*.md` + `picked/*.md` for briefs
  whose frontmatter is parseable but not `is_canonical_style()`-canonical and proposes rewriting
  them; `execute` takes a re-passed preview JSON plus an explicit `--confirm`/`--decline` and
  rewrites each proposed file's frontmatter block by re-parsing it and re-emitting it via
  `serialize_frontmatter()`.
- Each rewrite replaces only the `---`-fenced frontmatter block. The rewrite is verified
  byte-for-byte equivalent everywhere else (body content, and any part of the frontmatter block
  not affected by scalar-style differences) before being accepted, and is skipped (not
  clobbered) if the file changed between preview and execute or fails post-write validation via
  `validate_brief`.
- Run the script's `execute --confirm` against the real `$WORK_QUEUE_DIR` corpus as part of this
  change's implementation, so the ~1400-file backlog is actually canonicalized, not just
  scriptable.

## Capabilities

No specs under `openspec/specs/` declare requirements for work-queue brief frontmatter
serialization or style — PR #582 that established the canonical style itself shipped outside
the spec-driven flow (an investigation-triggered defect repair), and PR #570's structurally
identical `created:`-quoting backfill was likewise not spec-tracked. This change adds a
one-time corpus-migration script with no new or changed spec-level behavior beyond what #582
already defined; `skip_specs: true` is set in `.openspec.yaml`.

## Impact

- New file: `src/worktrail/workqueue/backfill_focus_style.py` (+ `tests/workqueue/test_backfill_focus_style.py`).
- Reads/writes: `$WORK_QUEUE_DIR/queue/*.md` and `$WORK_QUEUE_DIR/picked/*.md` — the live
  work-queue corpus, not repo-tracked files. No changes to `shared/brief_frontmatter.py`,
  `create_handoff.py`, or `consolidate_cluster.py` (already canonical writers per #582).
  No API or schema changes; existing readers (`work_queue.py`, `validate_brief`) are agnostic to
  scalar style and are unaffected.
