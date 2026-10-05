## Why

`worktrail-run-record active-conflicts` returns a clean all-clear when the records root it was
pointed at does not exist: `_active_conflicts()` guards its whole scan with
`if repo_dir.is_dir():` (`src/worktrail/router/run_record.py:1406`), so a nonexistent
`<--dir>/<repo.name>` silently iterates zero records, and `cmd_active_conflicts()` prints the
empty partitions and unconditionally `return 0` (`run_record.py:1497-1498`). Verified today on
this checkout:

```
$ worktrail-run-record active-conflicts --dir /tmp/definitely-not-there-xyz --repo "$PWD" --specification some-spec
{"live": [], "stale": [], "warnings": []}
$ echo $?
0
```

A safety scan that reads "no conflicts" from a path it never read is a false all-clear for the
`#active-conflicts-scan` hard stops and the same-repo-run-concurrency launch detection. The
same false all-clear reaches every in-process consumer of the same function — the pre-fan-out
same-repo detection (`orchestrator/live.py:4610`, whose width-halving silently no-ops), the
quarantine-sweep conflict check (`drain/drain.py:1883`), and `claim`'s exclusivity scan
(`run_record.py:2161`) — because none of them can distinguish "scanned, nothing found" from
"never scanned". The trigger is a typo or a wrong path level: pointing `--dir` at the per-repo
subdir (`$HOME/.worktrail/runs/worktrail`) instead of the runs root
(`$HOME/.worktrail/runs`) scans nothing while the root correctly finds the live run.

## What Changes

- **`_active_conflicts()` reports an unresolved records root instead of returning silently.**
  When `<dir>/<repo.name>` does not exist, both partitions stay empty and the `warnings` list
  gains an entry naming that exact path, so every in-process consumer sees the unresolved-root
  condition through the return contract it already reads.
- **`cmd_active_conflicts` fails loud on the same condition.** It still prints the JSON
  (warning included, so machine parsers see the diagnostic) and then exits nonzero instead of
  `return 0`.
- **When the records root exists, nothing changes** — partitions, entries, and exit status are
  exactly as today. An existing-but-empty root remains a legitimate clean scan.
- **No control-flow change in any caller.** `live.py`, `drain.py`, and `claim` keep deciding on
  the `live`/`stale` partitions; the warning is surfaced, not acted on.
- **Regression tests first**: tests that fail on the current base for the original reason
  (missing root → empty partitions, no warning, rc 0), then pass after the fix. Two existing
  tests that codified the old silent behavior are updated with it.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `active-conflicts-staleness-reconciliation`: the scan's records-root resolution — an
  unresolved `<dir>/<repo.name>` becomes a reported warning (naming the path) plus a nonzero
  CLI exit, instead of a silent empty all-clear.

## Impact

- `src/worktrail/router/run_record.py` — `_active_conflicts()` (warning on unresolved root)
  and `cmd_active_conflicts()` (nonzero exit after printing on that condition). `_is_stale()`,
  the `live`/`stale` semantics, and every caller's control flow are untouched.
- In-process consumers gain visibility with no code change: `orchestrator/live.py`
  (`_same_repo_live_runs`), `drain/drain.py` (quarantine-sweep conflict check),
  `run_record.cmd_claim` (already surfaces `warnings` in its conflict output).
- `tests/router/test_run_record.py` — `test_missing_run_record_directory_returns_empty_list`
  and `test_missing_run_record_directory_returns_empty_partitions` assert the old silent
  behavior and are updated; new tests cover the warning, the nonzero exit, and the unchanged
  existing-root cases.
- `_file_overlap_conflicts()` shares the `repo_dir.is_dir()` guard shape but is a different
  code path with a different caller contract (`claim --expected-files`); it is an explicit
  non-goal here.
- No skill-prose change: the documented `#active-conflicts-scan` derives `--dir` from a run
  record the pipeline already created, so the root exists; a caller without one now gets
  exactly the fail-loud signal this change adds.
