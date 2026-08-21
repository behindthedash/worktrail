## 1. Backfill script

- [ ] 1.1 Create `src/worktrail/workqueue/backfill_focus_style.py`: `build_preview(queue_base)`
      scans `queue/*.md` + `picked/*.md`, uses `yaml.compose()` on each file's frontmatter block
      to locate the `focus:` value node's exact character span, and proposes any file whose
      current span-rendering differs from `serialize_frontmatter({"focus": value})`'s rendering
      of the same parsed value. Files with no frontmatter block, unparseable YAML, or no
      `focus:` key are reported in `skipped` with a reason, not proposed.
- [ ] 1.2 Implement the span-splice rewrite: given a proposal, re-locate the `focus:` value
      node's span in the file's *current* on-disk content (not a cached preview span — the file
      may have changed since preview), replace only that span with
      `serialize_frontmatter({"focus": value})`'s value portion, and leave every other
      character — other frontmatter lines, the closing fence, and the body — untouched.
- [ ] 1.3 Implement `execute_apply(preview, queue_base, confirm)` mirroring
      `backfill_created_quoting.execute_apply`'s contract: `confirm=False` performs zero writes;
      `confirm=True` re-checks each file still exists and its `focus:` span still matches what
      preview observed (skip, don't clobber, if not), performs the splice, then re-validates via
      `validate_brief` **and** confirms the re-parsed `focus` string equals the pre-rewrite
      parsed value exactly — rolling back the write (restoring the pre-write content) if either
      check fails.
- [ ] 1.4 Implement `main()`/CLI with `preview` and `execute` subcommands, `--queue-dir`
      override, and `execute`'s `--preview`/stdin + `--confirm`/`--decline` flags, matching
      `backfill_created_quoting.py`'s argument shape exactly.
- [ ] 1.5 Register the console script entry point in `pyproject.toml`:
      `worktrail-backfill-focus-style = "worktrail.workqueue.backfill_focus_style:main"`,
      alongside the existing `worktrail-backfill-created-quoting` entry.

## 2. Tests

- [ ] 2.1 `tests/workqueue/test_backfill_focus_style.py` — `build_preview`: proposes a
      double-quoted-folded `focus:` (the corpus's known real defect shape), proposes a plain
      unquoted `focus:` needing quoting, skips a `focus:` already in canonical `|-` style, skips
      a brief with no `focus:` frontmatter key (body-only `## Focus`) with the expected skip
      reason, and scans both `queue/` and `picked/`.
- [ ] 2.2 `tests/workqueue/test_backfill_focus_style.py` — span-splice/execute: for each
      pre-#582 style PyYAML can produce (plain scalar, single-quoted, double-quoted-and-folded,
      folded `>`, and an already-literal `|-` that differs only in trailing-whitespace
      stripping), confirm the rewritten file's `focus:` is canonical, the re-parsed `focus`
      string is byte-identical to the original parsed value, and every other line (including a
      deliberately-still-non-canonical `created:` in one fixture) plus the full body is
      byte-for-byte unchanged from the original file.
- [ ] 2.3 `tests/workqueue/test_backfill_focus_style.py` — safety contract: `decline` writes
      nothing; a second `execute --confirm` run against an already-fixed preview is a no-op
      (idempotent); a file removed or whose `focus:` changed since preview is skipped, not
      clobbered; a forced post-write validation failure triggers rollback to the original
      content.
- [ ] 2.4 `tests/workqueue/test_backfill_focus_style.py` — CLI: `execute` reads the preview JSON
      from stdin when `--preview` is omitted, matching
      `MainCliTestCase.test_execute_reads_preview_from_stdin_when_flag_omitted`.

## 3. Corpus backfill

- [x] 3.1 [e2e] Run `worktrail-backfill-focus-style preview` against the real
      `$WORK_QUEUE_DIR` corpus; inspect the proposal count and a sample of proposed diffs before
      proceeding.
- [ ] 3.2 [e2e] Run `worktrail-backfill-focus-style execute --confirm` against the real corpus
      with the preview piped in; confirm the `stamped` count matches the preview's proposal
      count and `skipped` is empty (or has only expected reasons).
- [ ] 3.3 [e2e] Re-run `worktrail-backfill-focus-style preview` against the real corpus and
      confirm it now proposes zero files, verifying the backfill is complete and idempotent.

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`; confirm both are
      green.
