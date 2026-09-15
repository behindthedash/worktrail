## 1. Capture-source stamp in `create_handoff` and the seeder (`handoff-brief-capture-provenance`)

- [x] 1.1 In `src/worktrail/workqueue/create_handoff.py`:
      - add `captured_by: str | None = None` to `create_handoff()`;
      - validate it against `^[a-z0-9][a-z0-9-]*(:[A-Za-z0-9._/-]+)?$`, raising `ValueError` alongside the existing argument checks (before `queue.mkdir`);
      - stamp `captured-by` right after `created`, defaulting to `unknown`;
      - add `--captured-by` to `main()` with default `worktrail-handoff` and pass it through.
      In `src/worktrail/workqueue/seed_backlog.py`, pass `captured_by="seed-backlog"` to `create_handoff()`.
      Write the failing tests first in `tests/workqueue/test_create_handoff.py`. Cover:
      - an explicit value, with key order after `created`;
      - the omitted-kwarg `unknown` default;
      - a malformed value raising with no file left in `queue/`;
      - the CLI with and without the flag;
      - the created brief still passing `validate_brief` and `is_canonical_style`;
      - a hand-written brief without `captured-by` still validating and classifying by `seeded-from`.
      In `tests/workqueue/test_seed_backlog.py`, cover a seeded brief carrying `captured-by: seed-backlog` and still carrying its `seeded-from` key.
      files: src/worktrail/workqueue/create_handoff.py, tests/workqueue/test_create_handoff.py, src/worktrail/workqueue/seed_backlog.py, tests/workqueue/test_seed_backlog.py
      Covers: Minted Briefs Carry A Validated Capture Source; The Handoff CLI Accepts A Capture Source; Briefs Without A Capture Source Remain Valid; In-Repo Minting Callers Stamp Their Own Source

## 2. Cluster consolidation (`handoff-brief-capture-provenance`)

- [ ] 2.1 In `src/worktrail/router/consolidate_cluster.py`, add `"captured-by": "consolidate-cluster"` right after `created` in the consolidated brief's frontmatter dict. Write the failing test first in `tests/router/test_consolidate_cluster.py`: the written brief carries `captured-by: consolidate-cluster` and stays canonical-style.
      files: src/worktrail/router/consolidate_cluster.py, tests/router/test_consolidate_cluster.py
      Covers: In-Repo Minting Callers Stamp Their Own Source

## 3. Skill documentation (`handoff-brief-capture-provenance`)

- [x] 3.1 In `skills/worktrail-handoff/SKILL.md` Step 2, add `[--captured-by "$SOURCE"]` to the `worktrail-handoff` invocation. Add one sentence telling hooks, detectors, and other automated callers to pass their own kebab-case source name. The default is `worktrail-handoff`.
      files: skills/worktrail-handoff/SKILL.md
      depends: 1.1

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `openspec validate handoff-brief-capture-provenance --strict`.
      Then, against a temporary `--queue-dir`, run `worktrail-handoff --focus "provenance smoke" --captured-by detector:smoke --json`. Confirm the created brief's frontmatter shows `captured-by: detector:smoke` directly after `created`.
      depends: 1.1, 2.1, 3.1
