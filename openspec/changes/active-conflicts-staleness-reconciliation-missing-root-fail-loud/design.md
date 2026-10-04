## Context

See `proposal.md` — Why, for the defect and the verified reproduction. What matters for the
design: `_active_conflicts()` (`src/worktrail/router/run_record.py:1378`) returns
`{"live": [...], "stale": [...], "warnings": [...]}`, and its whole scan sits behind
`if repo_dir.is_dir():` (line 1406); `cmd_active_conflicts()` (line 1483) computes
`repo_dir = Path(args.dir).expanduser() / repo.name`, prints the JSON, and `return 0`. Three
in-process callers consume the same function: `orchestrator/live.py:4610` (`live` only, for the
width-halving note), `drain/drain.py:1883` (quarantine-sweep conflict check, `live` only), and
`run_record.cmd_claim` (line 2161, `live` to block, and `warnings` forwarded into its conflict
output). `main()` returns the subcommand's value and the console script is
`sys.exit(main())`, so a command's `return` is the process exit code.

Constraints that shape the approach: the `warnings` field is part of the established return
contract (malformed records already land there, several tests assert dict equality on the
return value, and tests assert on `str(path)` substrings inside warning strings), and the
required behavior explicitly forbids changing `_is_stale()`, the `live`/`stale` meanings, or
any caller's control flow.

## Goals / Non-Goals

**Goals:**

- An unresolved records root is visible to every consumer of `_active_conflicts()` through the
  channel they already read, and is impossible to mistake for a clean scan at the CLI.
- Zero behavior change whenever `<dir>/<repo.name>` exists.

**Non-Goals:**

- `_file_overlap_conflicts()` (line 1435) shares the guard shape but is a different code path
  with a different caller contract (`claim --expected-files`); its missing-root silence is not
  part of this defect's required behavior and is deliberately not addressed here.
- Any change to malformed-record handling, warning entries for those, or their (zero) effect
  on the CLI exit status.
- Any skill/prose edit: the documented `#active-conflicts-scan` derives `--dir` from a run
  record the pipeline already created, so its root always exists; the new nonzero exit only
  fires for a root that was never a valid target.

## Decisions

### D1: The unresolved root is a `warnings` entry, not a new return field and not an exception

`_active_conflicts()` appends one entry to `warnings` naming the exact `<dir>/<repo.name>`
path, and returns the normal dict with both partitions empty.

- *Why warnings*: it is the existing "the scan could not read something" channel — malformed
  records already surface there, `cmd_claim` already forwards it, and every caller's `live`
  handling keeps working untouched. It is the narrowest contract extension that satisfies
  "visible to every in-process consumer".
- *Alternative rejected — a new key (e.g. `"unresolved_root": true`)*: widens a return shape
  that in-process consumers and the existing test surface assert by dict equality, and buys
  nothing over a warning entry naming the path.
- *Alternative rejected — raise*: changes every caller's control flow (explicitly forbidden)
  and turns a detectable diagnostic into a crash for readers that only want the partitions.
- *Wording*: matches `_load_lenient()`'s tone (`<path> <lowercase prose sentence>`), e.g.
  `<repo_dir> does not exist — no run records were scanned for this repo`. The path must be
  the full `<dir>/<repo.name>` the scan resolved, so the diagnostic identifies which level of
  `--dir` was wrong; tests assert the path is present, mirroring the malformed-record tests.

Contract for in-process callers: `warnings` is the visibility channel; no caller control flow
changes. `_same_repo_live_runs` still returns `live` (empty), the drain sweep still reads only
`live`, and `claim` still blocks only on `live` — each now has the condition in the dict it
already receives. `claim`'s own root always exists by construction (the record being claimed
lives under it), so in practice it sees no new entries.

### D2: CLI exit code is 1

`cmd_active_conflicts` prints the JSON and returns `1` when the records root does not exist;
`0` otherwise (as today).

- *Why 1*: it is this module's established failure code — `cmd_assert_terminal` returns 1 for
  a non-terminal run, `cmd_claim` returns 1 for `already-claimed`/`conflict`, and `main()`'s
  `RunRecordFormatError` handler returns 1. There is no `return 2` anywhere in
  `run_record.py`; 2 in this codebase is argparse's usage-error code, and a `--dir` that names
  a directory that does not exist is not an argument-shape error — the argument parsed fine.
- *Why nonzero at all*: the command cannot fulfil its contract (a scan of the records root it
  was pointed at), and reporting success is the defect. The print still happens first, so
  machine consumers capture the diagnostic; under shell `set -e` the assignment's nonzero
  status stops the caller — the fail-loud semantics the hard-stop flow wants.

### D3: The exit status is derived by re-evaluating `repo_dir.is_dir()`, not by matching `warnings`

`cmd_active_conflicts` returns `0 if repo_dir.is_dir() else 1` after the scan and print.

- *Why*: `warnings` also carries malformed-record entries, which must NOT change the exit
  status (an existing root with a malformed sibling still exits 0, exactly as today). Deriving
  from the same predicate `_active_conflicts()` uses keeps the two in lockstep without
  string-matching warning prose or leaking a sentinel into the return contract.
- *Alternatives rejected*: matching the warning string in `warnings` (couples the CLI to
  prose); threading a second return value out of `_active_conflicts()` (same contract widening
  as D1's rejected option).
- *Race*: the directory can appear or vanish between the scan and the re-check. Both outcomes
  are conservative — worst cases are a stale warning with exit 0, or a clean scan with exit 1
  — for a read-only diagnostic, so no locking.

## Risks / Trade-offs

- [A caller legitimately runs `active-conflicts` before any run record exists for the repo now
  gets exit 1] → intended: an unresolved root is indistinguishable from a typo'd `--dir`, and
  that is exactly the condition being made loud. The pipeline's own scan always follows
  `start` for the same run, so its root exists.
- [Two existing tests codify the silent behavior and flip meaning] → they are updated in the
  same change; they are the regression tests the defect repair starts from (see `tasks.md`).
- [Warning wear on repo-wide consumers for a fresh machine's missing root] → acceptable; the
  alternative is the false all-clear this change exists to remove.
- [Scan/re-check race] → conservative both ways (D3); no consequence for a read-only scan.

## Migration Plan

None. No data migration, no interface versioning; the behavior change ships as an ordinary
fix, with the semver bump handled by the repo's separate release-metadata commit.
