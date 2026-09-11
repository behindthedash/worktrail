#!/usr/bin/env python3
"""pr_ledger.py — durable, URL-keyed ledger of open pull requests plus the
periodic recovery sweep that files one `pr fix` brief per abandoned PR.

Why this exists: a PR opened by any path (the landing pipeline, a hook-approved
`gh pr create`, a resumed landing) can be left unwatched when the session that
opened it ends. Nothing then notices red CI, a BLOCKED merge state, or a PR
that simply stalls. The ledger records every opened PR once (keyed by URL, so
repeated registration is an upsert, never a duplicate), the opener heartbeats
while it is actively watching, and an external five-minute cron runs
`worktrail-pr-ledger sweep` to query live state and file exactly one recovery
brief for each non-terminal PR nobody is watching.

Storage: `$WORKTRAIL_PR_LEDGER` if set, else `<worktrail_home()>/pr-ledger.json`.
Every mutation is a flock-guarded read-modify-write followed by an atomic
`os.replace()` of a temp file, so concurrent registrations (two landings, a
sweep and a heartbeat) never tear the file. A ledger that fails to parse is
never overwritten in place: it is moved aside to `pr-ledger.json.malformed-<ts>`
and a fresh ledger is started, so no operator data is silently destroyed.

CLI (console script `worktrail-pr-ledger`, usable verbatim by the external
reconciliation job -- this repository never edits that host crontab):

    worktrail-pr-ledger register --url URL --repo PATH [--branch B]
                                 [--session-id S] [--run-id R] [--source SRC]
    worktrail-pr-ledger heartbeat --url URL [--pid PID]
    worktrail-pr-ledger unwatch   --url URL
    worktrail-pr-ledger session   --session-id S        # non-terminal PRs owned by S
    worktrail-pr-ledger list
    worktrail-pr-ledger sweep [--queue-base DIR] [--stale-after SECONDS]
                              [--heartbeat-window SECONDS]

All subcommands print JSON on stdout. `register`/`heartbeat`/`unwatch` exit 1
on a write failure so callers can surface it rather than claim durable
recovery they do not have.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import (
    read_frontmatter,
    serialize_frontmatter,
    validate_brief,
)
from ..shared.homedir import worktrail_home
from . import pr_labels

Runner = Callable[..., subprocess.CompletedProcess]

LEDGER_VERSION = 1
LEDGER_FILENAME = "pr-ledger.json"
LEDGER_ENV = "WORKTRAIL_PR_LEDGER"
BRIEF_SOURCE = "pr-ledger-sweep"
DEFAULT_HEARTBEAT_WINDOW_S = 1200
DEFAULT_STALE_AFTER_S = 1800

# Live-state classifications.
STATE_MERGED = "merged"
STATE_CLOSED = "closed"
STATE_GREEN_AUTO_MERGE = "green-auto-merge"
STATE_RED = "red"
STATE_BLOCKED = "blocked"
STATE_PENDING = "pending"
STATE_QUERY_FAILED = "query-failed"
TERMINAL_STATES = frozenset({STATE_MERGED, STATE_CLOSED})

_FAILED_CONCLUSIONS = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED"})
_PR_URL_RE = re.compile(r"https?://[^/]+/([^/]+)/([^/]+)/pull/(\d+)(?:[/?#].*)?$")


class LedgerError(Exception):
    """Raised when the ledger cannot be read or written durably."""


# --------------------------------------------------------------------------- #
# Time / path helpers
# --------------------------------------------------------------------------- #


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(ts: datetime.datetime) -> str:
    return ts.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def _parse_iso(raw: Any) -> datetime.datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def ledger_path() -> Path:
    override = os.environ.get(LEDGER_ENV)
    if override:
        return Path(override).expanduser()
    return worktrail_home() / LEDGER_FILENAME


def parse_pr_url(url: str) -> dict[str, Any] | None:
    """`{"slug": "owner/name", "number": int}` for a GitHub PR URL, else None."""
    match = _PR_URL_RE.match(url.strip())
    if not match:
        return None
    owner, name, number = match.groups()
    return {"slug": f"{owner}/{name}", "number": int(number)}


# --------------------------------------------------------------------------- #
# Ledger I/O (atomic, flock-guarded, malformed-preserving)
# --------------------------------------------------------------------------- #


def _empty_ledger() -> dict[str, Any]:
    return {"version": LEDGER_VERSION, "prs": {}}


def _preserve_malformed(path: Path) -> Path:
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    aside = path.with_name(f"{path.name}.malformed-{stamp}")
    suffix = 0
    while aside.exists():
        suffix += 1
        aside = path.with_name(f"{path.name}.malformed-{stamp}-{suffix}")
    os.replace(path, aside)
    return aside


def load_ledger(path: Path | None = None) -> dict[str, Any]:
    """Read the ledger. Missing -> empty. Malformed -> moved aside, empty
    returned (the malformed bytes are never overwritten in place)."""
    path = path or ledger_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_ledger()
    except OSError as exc:
        raise LedgerError(f"cannot read ledger {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("prs"), dict):
        aside = _preserve_malformed(path)
        print(f"pr_ledger: malformed ledger preserved at {aside}", file=sys.stderr)
        return _empty_ledger()
    data.setdefault("version", LEDGER_VERSION)
    return data


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise LedgerError(f"cannot write ledger {path}: {exc}") from exc


class _Locked:
    """Exclusive flock on `<ledger>.lock` for the read-modify-write window.
    Non-POSIX hosts (no fcntl) fall back to the atomic replace alone."""

    def __init__(self, path: Path) -> None:
        self._lock_path = path.with_name(path.name + ".lock")
        self._fh: Any = None

    def __enter__(self) -> None:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._lock_path, "a+", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)

    def __exit__(self, *_exc: object) -> None:
        if self._fh is None:
            return
        import fcntl

        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None


def _mutate(
    path: Path | None, fn: Callable[[dict[str, Any]], Any]
) -> tuple[dict[str, Any], Any]:
    """Locked read -> `fn(ledger)` -> atomic write. Returns (ledger, fn result)."""
    path = path or ledger_path()
    with _Locked(path):
        ledger = load_ledger(path)
        result = fn(ledger)
        _write_atomic(path, ledger)
    return ledger, result


# --------------------------------------------------------------------------- #
# Registration + watcher heartbeat
# --------------------------------------------------------------------------- #


def register(
    url: str,
    repo: str | Path,
    *,
    branch: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
    path: Path | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Upsert the ledger entry for `url`. A repeat registration keeps the
    original `opened_at` and only fills/overwrites the provided fields, so any
    number of opening paths registering the same PR yield exactly one entry."""
    url = url.strip()
    if not parse_pr_url(url):
        raise LedgerError(f"not a pull request URL: {url!r}")
    stamp = _iso(now or _now())

    def apply(ledger: dict[str, Any]) -> dict[str, Any]:
        entry = ledger["prs"].get(url)
        if not isinstance(entry, dict):
            entry = {
                "url": url,
                "opened_at": stamp,
                "watcher": None,
                "recovery_brief": None,
            }
        parsed = parse_pr_url(url) or {}
        entry["repo"] = str(repo)
        entry["slug"] = parsed.get("slug")
        entry["number"] = parsed.get("number")
        for key, value in (
            ("branch", branch),
            ("session_id", session_id),
            ("run_id", run_id),
            ("source", source),
        ):
            if value is not None:
                entry[key] = value
            else:
                entry.setdefault(key, None)
        entry["updated_at"] = stamp
        ledger["prs"][url] = entry
        return entry

    _, entry = _mutate(path, apply)
    return entry


def heartbeat(
    url: str,
    *,
    pid: int | None = None,
    path: Path | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any] | None:
    """Mark `url` as actively watched right now. Returns the entry, or None if
    the PR is not registered (a heartbeat never creates an entry)."""
    stamp = _iso(now or _now())

    def apply(ledger: dict[str, Any]) -> dict[str, Any] | None:
        entry = ledger["prs"].get(url)
        if not isinstance(entry, dict):
            return None
        entry["watcher"] = {
            "pid": pid if pid is not None else os.getpid(),
            "heartbeat_at": stamp,
        }
        entry["updated_at"] = stamp
        return entry

    _, entry = _mutate(path, apply)
    return entry


def unwatch(
    url: str, *, path: Path | None = None, now: datetime.datetime | None = None
) -> dict[str, Any] | None:
    """Clear the watcher on `url` (the opener is done watching, outcome or not)."""
    stamp = _iso(now or _now())

    def apply(ledger: dict[str, Any]) -> dict[str, Any] | None:
        entry = ledger["prs"].get(url)
        if not isinstance(entry, dict):
            return None
        entry["watcher"] = None
        entry["updated_at"] = stamp
        return entry

    _, entry = _mutate(path, apply)
    return entry


def watcher_is_fresh(
    entry: dict[str, Any],
    *,
    now: datetime.datetime | None = None,
    window_s: float = DEFAULT_HEARTBEAT_WINDOW_S,
) -> bool:
    watcher = entry.get("watcher")
    if not isinstance(watcher, dict):
        return False
    beat = _parse_iso(watcher.get("heartbeat_at"))
    if beat is None:
        return False
    return ((now or _now()) - beat).total_seconds() <= window_s


def session_entries(
    session_id: str, *, path: Path | None = None
) -> list[dict[str, Any]]:
    """Ledger entries owned by `session_id` whose last known state is
    non-terminal. Read-only (no live query) so a Stop hook stays fast."""
    ledger = load_ledger(path)
    out = []
    for entry in ledger["prs"].values():
        if not isinstance(entry, dict) or entry.get("session_id") != session_id:
            continue
        if entry.get("last_state") in TERMINAL_STATES:
            continue
        out.append(entry)
    return sorted(out, key=lambda e: e.get("opened_at") or "")


# --------------------------------------------------------------------------- #
# Live state query + classification
# --------------------------------------------------------------------------- #

_VIEW_FIELDS = (
    "state,mergedAt,autoMergeRequest,mergeStateStatus,statusCheckRollup,isDraft"
)


def query_pr_state(
    url: str, repo: str | Path | None, runner: Runner | None = None
) -> dict[str, Any] | None:
    """`gh pr view <url> --json ...` parsed, or None when the query fails."""
    cmd = ["gh", "pr", "view", url, "--json", _VIEW_FIELDS]
    try:
        result = pr_labels._run_gh_cmd(cmd, str(repo) if repo else None, runner)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _rollup_has_failure(rollup: Any) -> bool:
    for check in rollup or []:
        if not isinstance(check, dict):
            continue
        conclusion = (check.get("conclusion") or check.get("state") or "").upper()
        if conclusion in _FAILED_CONCLUSIONS:
            return True
    return False


def classify_state(payload: dict[str, Any] | None) -> str:
    """Collapse a `gh pr view` payload to one ledger state."""
    if payload is None:
        return STATE_QUERY_FAILED
    state = (payload.get("state") or "").upper()
    if state == "MERGED" or payload.get("mergedAt"):
        return STATE_MERGED
    if state == "CLOSED":
        return STATE_CLOSED
    if _rollup_has_failure(payload.get("statusCheckRollup")):
        return STATE_RED
    # Auto-merge wins over BLOCKED: GitHub reports mergeStateStatus BLOCKED while
    # required checks are still running, and a single reading is not definitive
    # (see land_pr._merge_state_guard). With auto-merge armed and nothing red,
    # GitHub will land it once the checks finish -- do not recover it.
    if payload.get("autoMergeRequest"):
        return STATE_GREEN_AUTO_MERGE
    if (payload.get("mergeStateStatus") or "").upper() == "BLOCKED":
        return STATE_BLOCKED
    return STATE_PENDING


# --------------------------------------------------------------------------- #
# Recovery brief (idempotent per PR URL)
# --------------------------------------------------------------------------- #


def _queue_base() -> Path:
    return Path(os.environ.get("WORK_QUEUE_DIR", "~/work-queue")).expanduser()


def _slug_for_id(url: str) -> str:
    parsed = parse_pr_url(url) or {}
    slug = str(parsed.get("slug") or "pr").lower().replace("/", "-")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "pr"
    return f"{slug}-{parsed.get('number', 0)}"


def existing_recovery_brief(url: str, queue_base: Path) -> Path | None:
    """The queued or picked brief already filed for `url`, if any."""
    for folder in ("queue", "picked"):
        root = queue_base / folder
        if not root.is_dir():
            continue
        for md in sorted(root.rglob("*.md")):
            fm = read_frontmatter(md)
            if fm.get("pr-url") == url and fm.get("brief-source") == BRIEF_SOURCE:
                return md
    return None


def _render_brief(entry: dict[str, Any], state: str, reason: str, brief_id: str) -> str:
    url = entry["url"]
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    focus = f"pr fix {url} -- {reason}"
    # Routed through `serialize_frontmatter` so the brief matches the canonical
    # style every other `queue/` writer produces (`is_canonical_style`), instead
    # of being flagged as a style-mismatch by the corpus scanner on every sweep.
    frontmatter = serialize_frontmatter(
        {
            "id": brief_id,
            "created": created,
            "focus": focus,
            "repo": entry.get("repo"),
            "remote": None,
            "status": "queued",
            "intent": "pr fix",
            "pr-url": url,
            "pr-state": state,
            "brief-source": BRIEF_SOURCE,
        }
    )
    lines = [
        "---",
        frontmatter.rstrip("\n"),
        "---",
        "",
        "## Focus",
        "",
        focus,
        "",
        "## Context",
        "",
        f"- PR: {url}",
        f"- Branch: `{entry.get('branch') or 'unknown'}`",
        f"- Live state at sweep: `{state}` ({reason})",
        f"- Opened at: {entry.get('opened_at')}",
        f"- Opening session: {entry.get('session_id') or 'unknown'} "
        + f"(run: {entry.get('run_id') or 'unknown'})",
        "",
        "## Next step",
        "",
        f"Run `pr fix` against {url} from `{entry.get('repo')}`: diagnose the red or",
        "blocked state, push the fix, and let the landing pipeline re-watch the PR.",
        "",
    ]
    return "\n".join(lines)


def file_recovery_brief(
    entry: dict[str, Any], state: str, reason: str, queue_base: Path
) -> Path:
    queue_dir = queue_base / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005
    brief_id = f"{stamp}-pr-fix-{_slug_for_id(entry['url'])}"
    path = queue_dir / f"{brief_id}.md"
    path.write_text(_render_brief(entry, state, reason, brief_id), encoding="utf-8")
    ok, why = validate_brief(path, required=("id", "status", "focus"))
    if not ok:
        # Never leave an invalid brief behind for a consumer to claim.
        try:
            path.unlink()
        except OSError:
            pass
        raise ValueError(f"written recovery brief failed validation: {why}")
    return path


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #


def _decide(
    entry: dict[str, Any],
    state: str,
    *,
    now: datetime.datetime,
    stale_after_s: float,
    heartbeat_window_s: float,
) -> tuple[str, str]:
    """(action, reason) with action in {remove, retain, recover}."""
    if state in TERMINAL_STATES:
        return "remove", f"PR is {state}"
    if state == STATE_QUERY_FAILED:
        return "retain", "live query failed; retained for the next sweep"
    if watcher_is_fresh(entry, now=now, window_s=heartbeat_window_s):
        return "retain", "an opener is actively watching (fresh heartbeat)"
    if state == STATE_RED:
        return "recover", "CI is red and nobody is watching"
    if state == STATE_BLOCKED:
        return "recover", "merge state is BLOCKED and nobody is watching"
    if state == STATE_GREEN_AUTO_MERGE:
        return "retain", "green with auto-merge armed; GitHub will land it"
    last = _parse_iso(entry.get("updated_at")) or _parse_iso(entry.get("opened_at"))
    age = (now - last).total_seconds() if last else float("inf")
    if age > stale_after_s:
        return (
            "recover",
            f"unwatched and pending for {int(age)}s (> {int(stale_after_s)}s)",
        )
    return "retain", "pending; within the stale window"


def sweep(
    *,
    path: Path | None = None,
    queue_base: Path | None = None,
    runner: Runner | None = None,
    now: datetime.datetime | None = None,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    heartbeat_window_s: float = DEFAULT_HEARTBEAT_WINDOW_S,
) -> dict[str, Any]:
    """Query every ledgered PR live, drop merged/closed ones, and file at most
    one recovery brief per unwatched non-terminal PR. Safe to run every five
    minutes: a PR that already has a queued/picked recovery brief is skipped,
    and a failed live query retains the entry untouched.

    The live `gh pr view` queries run *outside* the ledger lock (they can take
    tens of seconds each), so `register`/`heartbeat` on the landing pipeline's
    critical path never block behind a sweep. The lock is taken only for the
    read-modify-write that applies the decisions, re-reading each entry so a
    heartbeat that arrived during the query phase is honoured.

    One brief that fails to write never aborts the pass: the failure is
    recorded per entry (`brief_failed`) and every other update still lands.
    """
    now = now or _now()
    queue_base = queue_base or _queue_base()
    path = path or ledger_path()
    report: dict[str, Any] = {
        "removed": [],
        "retained": [],
        "recovered": [],
        "already_briefed": [],
        "query_failed": [],
        "brief_failed": [],
    }

    # Phase 1 (unlocked): snapshot the URLs and query live state.
    snapshot = load_ledger(path)
    states: dict[str, str] = {}
    for url, entry in snapshot["prs"].items():
        if not isinstance(entry, dict):
            continue
        states[url] = classify_state(query_pr_state(url, entry.get("repo"), runner))

    # Phase 2 (locked): apply decisions against the current ledger contents.
    with _Locked(path):
        ledger = load_ledger(path)
        for url in list(ledger["prs"].keys()):
            entry = ledger["prs"][url]
            if not isinstance(entry, dict):
                del ledger["prs"][url]
                report["removed"].append({"url": url, "reason": "malformed entry"})
                continue
            if url not in states:
                # Registered after the snapshot; the next sweep will query it.
                continue
            entry.setdefault("url", url)
            state = states[url]
            action, reason = _decide(
                entry,
                state,
                now=now,
                stale_after_s=stale_after_s,
                heartbeat_window_s=heartbeat_window_s,
            )
            if state == STATE_QUERY_FAILED:
                entry["last_query_failed_at"] = _iso(now)
                report["query_failed"].append({"url": url, "reason": reason})
                continue
            entry["last_state"] = state
            entry["last_swept_at"] = _iso(now)
            if action == "remove":
                del ledger["prs"][url]
                report["removed"].append({"url": url, "reason": reason})
                continue
            if action == "retain":
                report["retained"].append(
                    {"url": url, "state": state, "reason": reason}
                )
                continue
            existing = existing_recovery_brief(url, queue_base)
            if existing is not None:
                entry["recovery_brief"] = str(existing)
                report["already_briefed"].append({"url": url, "brief": str(existing)})
                continue
            try:
                brief = file_recovery_brief(entry, state, reason, queue_base)
            except (OSError, ValueError) as exc:
                entry["last_brief_error"] = f"{_iso(now)}: {exc}"
                report["brief_failed"].append({"url": url, "error": str(exc)})
                print(
                    f"pr_ledger: recovery brief for {url} failed: {exc}",
                    file=sys.stderr,
                )
                continue
            entry["recovery_brief"] = str(brief)
            entry["recovery_brief_at"] = _iso(now)
            report["recovered"].append(
                {"url": url, "state": state, "reason": reason, "brief": str(brief)}
            )
        _write_atomic(path, ledger)
    report["remaining"] = len(ledger["prs"])
    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worktrail-pr-ledger",
        description="Durable URL-keyed PR ledger, watcher heartbeat, and recovery sweep.",
    )
    parser.add_argument(
        "--ledger", type=Path, default=None, help="ledger path override"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("register", help="upsert a PR entry")
    p_reg.add_argument("--url", required=True)
    p_reg.add_argument("--repo", required=True)
    p_reg.add_argument("--branch")
    p_reg.add_argument("--session-id")
    p_reg.add_argument("--run-id")
    p_reg.add_argument("--source")

    p_hb = sub.add_parser("heartbeat", help="mark a PR as actively watched")
    p_hb.add_argument("--url", required=True)
    p_hb.add_argument("--pid", type=int)

    p_un = sub.add_parser("unwatch", help="clear a PR's watcher")
    p_un.add_argument("--url", required=True)

    p_sess = sub.add_parser("session", help="non-terminal PRs owned by a session")
    p_sess.add_argument("--session-id", required=True)

    sub.add_parser("list", help="dump the ledger")

    p_sw = sub.add_parser("sweep", help="query live state and file recovery briefs")
    p_sw.add_argument("--queue-base", type=Path, default=None)
    p_sw.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER_S)
    p_sw.add_argument(
        "--heartbeat-window", type=float, default=DEFAULT_HEARTBEAT_WINDOW_S
    )

    args = parser.parse_args(argv)
    path = args.ledger

    try:
        if args.command == "register":
            _emit(
                register(
                    args.url,
                    args.repo,
                    branch=args.branch,
                    session_id=args.session_id,
                    run_id=args.run_id,
                    source=args.source,
                    path=path,
                )
            )
            return 0
        if args.command == "heartbeat":
            entry = heartbeat(args.url, pid=args.pid, path=path)
            _emit(entry)
            return 0 if entry is not None else 1
        if args.command == "unwatch":
            entry = unwatch(args.url, path=path)
            _emit(entry)
            return 0 if entry is not None else 1
        if args.command == "session":
            _emit(session_entries(args.session_id, path=path))
            return 0
        if args.command == "list":
            _emit(load_ledger(path))
            return 0
        if args.command == "sweep":
            _emit(
                sweep(
                    path=path,
                    queue_base=args.queue_base,
                    stale_after_s=args.stale_after,
                    heartbeat_window_s=args.heartbeat_window,
                )
            )
            return 0
    except (LedgerError, ValueError) as exc:
        print(f"worktrail-pr-ledger: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
