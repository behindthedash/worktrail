## Why

`premise_check` reports prose as a broken premise. Its path needles come straight from
`router.brief_probes.extract_probes()`, whose `_is_path_token`
(`src/worktrail/router/brief_probes.py`) rejects only `#`, `()<>`, absolute/`~` paths, and a
five-word prose denylist (`e.g`/`i.e`/`etc`/`vs`/`a.k.a`). Confirmed by inspection 2026-09-19:
there is no pytest node-id handling and no requirement that a `/`-bearing token actually name a
file, so any slash-joined prose passes as a path needle and `_check_path`
(`src/worktrail/workqueue/premise_check.py:235`) reports it `path does not exist`.

This group's own premise-check output reproduces all three cases verbatim (brief
`20260918-214416-premise-check-path-extractor-false`):

- `claude/codex/opencode` -- a prose list of harness names -- reported `path does not exist`.
- `stub/disable` -- a prose either/or -- reported `path does not exist`.
- a pytest node-id (`tests/...py::test_name`) -- whose *file half is real* -- reported
  `path does not exist`, because `_check_path` only strips a trailing `:LINE` and leaves
  `::name` attached.

Each one is an UNCONFIRMED line in the evaluation prompt that an agent then has to disprove by
hand, which is exactly the re-derivation the premise check exists to remove. The absence-claim
branch is worse than noisy: a prose token in an absence window would be *confirmed* ("absence
confirmed: path does not exist"), manufacturing evidence for a claim nobody made.

No purely textual rule can separate `claude/codex/opencode` from `src/worktrail/router` -- both
are plain lowercase segments joined by slashes. The repo itself is the discriminator, and
`premise_check` already has it: a token that neither exists in the checkout nor carries a file
extension is not a path claim and must produce no verdict at all.

## What Changes

- `extract_probes` strips a pytest node-id suffix (`::test_name`, `::TestClass::test_name`) from
  a token before path classification, so the real file half becomes the probe. All three
  consumers of the extractor (`premise_check`, `repo_inference`, `check_deferred_work_handoff`)
  get the corrected token.
- `premise_check`'s path check gains a shape gate: a presence-or-absence path needle that does
  **not** exist in the repo and is **not** filename-shaped (no extension on its last segment, no
  trailing `/`) yields no result row at all -- it is dropped, not reported unconfirmed and not
  reported as a confirmed absence.
- Every other outcome is unchanged: an existing path still confirms (`:LINE` refinement
  included), a *filename-shaped* missing path still refutes (`docs/typo.md` stays a real
  premise failure), and quoted/command needles are untouched.

## Capabilities

### New Capabilities
- `premise-check-path-needle-shape`: a path needle only produces a verdict when it is actually a
  path claim.

### Modified Capabilities

## Impact

- `src/worktrail/router/brief_probes.py` (node-id stripping).
- `src/worktrail/workqueue/premise_check.py` (shape gate in `_check_path` / `run_premise_check`).
- `tests/router/test_brief_probes.py` (new), `tests/workqueue/test_premise_check.py`.
- Fewer premise-check rows, never a changed verdict for a token that is a real path.
