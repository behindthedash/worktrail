## 1. Guard narrowing

- [x] 1.1 Narrow `_forbidden_paths_touched()` in `src/worktrail/orchestrator/verify.py` to the
      branch's net scope against the base tip. After computing `touched` from
      `git diff --name-only <pre_sha>..<gb>` (unchanged, including the empty-`pre_sha` fail-open
      and the non-zero-exit early return), run a best-effort
      `git fetch -q <self.remote> <self.base>` followed by
      `git diff --name-only <self.remote>/<self.base>..<gb>`; when both succeed, intersect
      `touched` with that result so a path whose post-merge content equals the base tip's drops
      out, and when either fails, keep `touched` unnarrowed and `self.log(...)` that base-tip
      narrowing was unavailable. Apply the existing spec-root and declared-file tier filters —
      and the touched-not-declared plan-audit log — to the narrowed set. Update the docstring to
      state why scope is measured against the base tip while `_detect_self_merge` keeps the
      pre/post baseline, citing the `go-20260920-124658` false positive.
      Cover the new behavior in `tests/orchestrator/test_verify.py`. Teach the
      `ScopeCheckRun` fake to answer the base-tip `git diff` and `git fetch` separately from the
      pre/post `git diff` (defaulting the base diff to the same file list, so every existing
      `_forbidden_paths_touched` assertion keeps its current expectation), then add cases for: a
      denied path present in the pre/post diff but absent from the base diff returns no
      violation; a denied path present in both is still reported; a base-diff path absent from
      the pre/post diff is not reported; a failing fetch and a failing base diff each fall back
      to the unnarrowed set and log; empty `pre_sha` still returns `[]` with no diff call. Add
      one end-to-end case through `run_all` mirroring the live report — a `CONFLICTING` group
      whose resolve worker merges base in, bringing `.github/workflows/gitleaks.yml` and the
      spec root's `tasks.md` in unchanged — asserting the group lands in `merged` with
      `forbidden_path_violations` empty.
      (Requirements: The forbidden-path guard SHALL judge scope against the base tip; The guard
      SHALL refresh the base tip and fall back rather than disarm.)
      files: src/worktrail/orchestrator/verify.py, tests/orchestrator/test_verify.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q
      tests/orchestrator/test_verify.py`, then `PYTHONPATH=src pytest -q`, `PYTHONPATH=src
      python3 -m worktrail.orchestrator.orchestrate check`, `python3 scripts/ci/ruff_pinned.py
      check .`, `python3 scripts/ci/ruff_pinned.py format --check .` and `python3
      scripts/ci/check_shebang_exec_bits.py`. Then `openspec validate
      resolve-guard-diff-against-base-tip --strict` and `worktrail-compile
      openspec/changes/resolve-guard-diff-against-base-tip`.
