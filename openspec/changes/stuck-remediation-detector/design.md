## Context

`drain.py`'s `REMEDIATION_TABLE` (`drain.py:1222`) pairs each stage with a
`finder(repos_root, go_repo)` and an `action(finding, agent, timeout, spawner,
log)`. `sweep_remediations` (`drain.py:1243`) iterates the table once per
sweep; `drain()` calls it up to twice per invocation — once before the
queue-drain loop (unconditionally, when `slot == 0`, `repos_root` is set, and
not `--dry-run`) and once after (only when the loop actually ran at least one
iteration). An action that raises is caught per-finding and only logged
(`"{label} error: ..."`); an action that returns normally lands in
`results[key]` (returned to the caller as `resumed[key]`) with no signal
distinguishing "this genuinely fixed the finding" from "this returned
successfully but the finding is still there." See proposal.md for the
motivating incident (PR #515, `sync_pending`'s no-op action).

`drain()` is invoked once per nightly cron run (`worktrail-drain-nightly.sh`
in the devops repo); there is currently no state carried between one
invocation and the next except the capacity cache and the run-records
directory, neither of which record remediation-table findings. All
machine-local, non-project state already lives under `worktrail_home()`
(`~/.worktrail`), and `agent_capacity.py` (`src/worktrail/orchestrator/
agent_capacity.py`) already establishes the pattern for that kind of
state: a JSON file, atomic write via `tempfile.mkstemp` + `os.replace`, and a
`flock`-based `write_lock(path)` context manager guarding read-modify-write.
`write_lock` takes a plain `Path` and has no dependency on the rest of
`agent_capacity`'s schema, so it is directly reusable rather than
reimplemented.

## Goals / Non-Goals

**Goals**
- Detect, generically across all five `REMEDIATION_TABLE` rows, a finding
  whose action keeps reporting apparent success while the finder keeps
  finding it again on the next sweep.
- Make the detection additive to `sweep_remediations`/`drain()` — no finder
  or action function's signature or behavior changes.
- Keep the persisted state small, bounded, and consistent with the existing
  `agent_capacity.py` machine-local-state pattern (same directory, same
  atomic-write/lock discipline).

**Non-Goals**
- Do not change `worktrail-drain-digest.py` (devops repo) to render the new
  `stuck_remediations` summary key. That is a separate repo and a separate
  PR (see proposal.md "Impact"); this change only needs to make the data
  available in the JSON summary the digest already reads.
- Do not attempt to diagnose *why* a remediation is stuck (e.g. distinguish
  "action is a no-op" from "action succeeds but something else undoes it").
  The detector's job is to surface the recurrence for a human to
  investigate, not to root-cause it.
- Do not track findings whose action raised an exception as part of the
  streak. That failure mode is already visible via the existing per-finding
  error log line; this detector's value is specifically in the case an
  operator currently cannot see (apparent success, no visible signal).
- Do not add a CLI-configurable retention window. A fixed, generous default
  is enough to keep the history file bounded; there is no current operator
  need to tune it, and adding the knob would be speculative.

## Decisions

### D1 — Identity and "apparent success" input come straight from `sweep_remediations`'s existing return shape

Every one of the five actions already returns a result dict shaped
`{"repo": <repo_name str>, "spec_id": <spec_id str>, ...}` on success, and
`sweep_remediations` already collects exactly those (only the non-raising
ones) into `results[key]` — this is precisely "found again this sweep, and
the action reported apparent success" with zero new plumbing. The detector
reads `resumed` (the pre+post merged dict `drain()` already builds at
`drain.py:1716-1717`), not the finders directly, so it never needs to
re-invoke a finder or duplicate `sweep_remediations`'s per-finding
try/except.

Identity = `f"{remediation_key}::{repo_name}::{spec_id}"` (a single string
key, not a tuple), matching `record_capacity_gate`'s bare-string-key style
and keeping the persisted JSON's `identities` object directly
`json.dumps`-able without a custom encoder.

Alternative considered: track identities at the finder level (call each
finder a second time, independent of `sweep_remediations`) to also see
findings whose action raised. Rejected — it would double the sweep's cost
per drain run, and the exception case already has a visible log line, so it
adds detection surface without adding *new* visibility.

### D2 — Streak state is "last recorded streak count", not a rolling window of dated records

The simplest model that satisfies "N consecutive sweeps" without needing to
reason about calendar dates or missed nights: each identity's persisted
record is just `{"streak": <int>, "last_seen": <iso8601>}`. Every sweep:

```python
def record_and_detect(
    history: Dict[str, Any],
    resumed: Dict[str, List[Dict[str, Any]]],
    now: datetime,
    threshold: int,
    retention: timedelta,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    prior = history.get("identities", {}) if isinstance(history, dict) else {}
    updated: Dict[str, Any] = {}
    stuck: List[Dict[str, Any]] = []
    for key, findings in resumed.items():
        for finding in findings:
            repo_name, spec_id = finding.get("repo"), finding.get("spec_id")
            if not repo_name or not spec_id:
                continue
            ident = f"{key}::{repo_name}::{spec_id}"
            streak = prior.get(ident, {}).get("streak", 0) + 1
            updated[ident] = {"streak": streak, "last_seen": now.isoformat()}
            if streak >= threshold:
                stuck.append({"key": key, "repo_name": repo_name,
                              "spec_id": spec_id, "streak": streak})
    # Anything not re-affirmed this sweep resets: either the finder stopped
    # returning it (genuinely fixed) or the action raised this time (already
    # logged separately) -- either way its streak does not carry forward.
    # A record that IS still within the retention window but wasn't found
    # this sweep is simply dropped (streak reset to 0, equivalent to not
    # persisting a zero-streak entry). Only genuinely stale entries beyond
    # `retention` would ever need an explicit prune path; in practice the
    # "not found this sweep -> drop" rule already prunes everything except
    # entries from a sweep that crashed before reaching this point.
    return {"version": 1, "identities": updated}, stuck
```

This one rule ("only identities re-affirmed this sweep survive into the new
history") gives the required behavior for all three spec scenarios for free:
recurrence extends the streak, a cleared finding's streak resets (it is
simply absent from `updated`), and an action-raised sweep's streak resets
(the finding is absent from `resumed[key]` on that sweep because
`sweep_remediations` only collects non-raising results). Retention pruning
(D4) exists only as a defensive backstop for a persisted file whose
identities never even matter given this replace-not-merge design — see D4.

Alternative considered: persist a list of per-sweep records (list of
`{"date": ..., "ok": bool}`) and compute the streak by scanning the tail.
Rejected — strictly more state for no behavioral difference, since a single
missed/failed sweep already needs to reset the streak to 0 under the spec
(no "grace" for a skipped night), which the simpler last-count model gives
directly.

### D3 — Threshold default of 3, exposed as `--stuck-threshold`

3 matches the exact number of consecutive nights a human needed to notice
the PR #515 pattern by hand (proposal.md). Exposed as a `DrainConfig.
stuck_threshold: int = 3` field and a `--stuck-threshold` CLI flag, mirroring
the existing `--consecutive-failures` circuit-breaker flag's shape
(`args.consecutive_failures`, `DrainConfig.failure_threshold`).

### D4 — New module `src/worktrail/drain/stuck_remediation.py`; reuses `agent_capacity.write_lock`

```python
from ..orchestrator.agent_capacity import write_lock  # reuse the flock helper

def history_path() -> Path:
    override = env_setting("WORKTRAIL_STUCK_REMEDIATION_HISTORY")
    if override:
        return Path(override).expanduser()
    return worktrail_home() / "remediation-history.json"

def load(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"version": 1, "identities": {}}
    if not isinstance(value, dict) or not isinstance(value.get("identities"), dict):
        return {"version": 1, "identities": {}}
    return value

def save(value: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass

def sweep_and_record(
    resumed: Dict[str, List[Dict[str, Any]]],
    path: Path,
    threshold: int = 3,
    retention: timedelta = DEFAULT_RETENTION,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    with write_lock(path):
        history = load(path)
        new_history, stuck = record_and_detect(history, resumed, now, threshold, retention)
        save(new_history, path)
    return stuck
```

`load`/`save` intentionally duplicate `agent_capacity.load`/`save`'s ~15
lines rather than generalizing them into a shared helper parametrized by
schema — the two schemas (`{"providers": {...}}` vs `{"identities": {...}}`)
are different enough, and the duplication small enough, that extracting a
shared "small atomic JSON cache" helper is not justified by one additional
caller (equal-score tie-breaker: prefer the simpler, more locally-readable
module over a new shared abstraction for its own sake).

`write_lock` IS reused directly (not duplicated) — it is already a
zero-schema-dependency, path-parametrized `flock` helper, so importing it
carries no coupling cost the two modules don't already share (`drain.py`
already imports `agent_capacity` for the capacity cache).

### D5 — Wiring point in `drain()`: once per invocation, right where `resumed` is finalized

`drain()` merges the pre-loop and (conditional) post-loop sweep results into
one `resumed` dict at `drain.py:1716-1717`, immediately before building
`summary`. The stuck-detection call goes immediately after that merge, under
the same guard the sweeps themselves already use
(`slot == 0 and config.repos_root is not None and not config.dry_run`) — so
a dry run or a non-slot-0 worker never touches the persisted history file,
and the call naturally only fires when `resumed` could be non-empty:

```python
stuck: List[Dict[str, Any]] = []
if slot == 0 and config.repos_root is not None and not config.dry_run:
    stuck = stuck_remediation.sweep_and_record(
        resumed, config.stuck_history_path, threshold=config.stuck_threshold)
    for item in stuck:
        log(f"stuck remediation: {item['key']} {item['repo_name']} "
            f"{item['spec_id']} recurred {item['streak']} consecutive sweeps "
            "despite apparent success -- the action is not actually "
            "resolving the finding, investigate directly")
summary["stuck_remediations"] = stuck
```

This runs once per `drain()` invocation (once per night under the cron
wrapper) even though `sweep_remediations` itself may run twice within that
invocation — the pre/post merge already collapses same-night duplicates
before the streak logic ever sees them, so a night with both a pre-loop and
a post-loop hit for the same identity still only advances that identity's
streak by 1, not 2.

## Risks / Trade-offs

- [Risk] A finding whose finder returns it exactly once every *other* sweep
  (e.g. genuinely intermittent) never reaches a streak of `threshold` and is
  never flagged, even though it's arguably still "stuck." → Mitigation:
  out of scope for this change (see proposal.md's scope: mirrors the PR #515
  pattern, which was strictly consecutive); a future change can generalize
  to "N of the last M sweeps" if that pattern is observed in practice.
- [Risk] `drain()` invoked manually (not via the nightly cron) advances the
  same persisted streak as a real nightly run, so an operator running
  `worktrail-drain` by hand several times in one day could reach the
  threshold faster than "N nights" implies. → Mitigation: documented as a
  known approximation in this design; the persisted state already assumes
  one `drain()` invocation ≈ one sweep occasion, matching how the rest of
  `drain.py`'s existing capacity-cache/lock-file state already treats a
  drain invocation as the unit of state advancement.
- [Risk] The history file grows if many distinct identities cycle through
  without ever resolving or aging out. → Mitigation: D2's "only re-affirmed
  identities survive" rule already means the file's size is bounded by the
  number of *currently, this-sweep* recurring findings, not by all history
  ever seen — there is no unbounded-accumulation path to mitigate beyond
  that.

## Migration Plan

Purely additive: new module, new `DrainConfig` fields with defaults, new
summary-dict key. No existing finder/action/table row changes. First run
after deploy starts with an empty history file (or none — `load()` degrades
to `{"version": 1, "identities": {}}` on a missing/corrupt file, matching
`agent_capacity.load`'s same degrade-on-corruption behavior), so the
earliest a flag can fire is `stuck_threshold` sweeps after deploy. No
rollback concerns beyond deleting the new history file, which is advisory
state like the capacity cache.
