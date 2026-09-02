"""Small, machine-local capacity cache for headless agent providers.

The cache is deliberately outside project repositories.  It is advisory state:
malformed or stale data is ignored, and a provider is never treated as
unavailable without an explicit future timestamp.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..shared.homedir import env_setting, worktrail_home


def cache_path() -> Path:
    override = env_setting("WORKTRAIL_AGENT_CAPACITY_CACHE")
    if override:
        return Path(override).expanduser()
    return worktrail_home() / "agent-capacity.json"


DEFAULT_COOLDOWNS = {
    "startup": 60,
    "sandbox": 60,
    "transport": 30,
    # auth never self-heals (needs `codex login` / a fresh key): a 1h gate just
    # re-burned spawn_agent's retry budget every hour of a multi-hour run
    # (brief 20260901-175101). Operators clear it explicitly once fixed.
    "auth": 86400,
    "billing": 3600,
    "model_unavailable": 86400,
}


class ProviderUnavailable(RuntimeError):
    """Raised when a provider's persisted cooldown has not expired."""

    def __init__(self, provider_key: str, state: dict):
        self.provider_key = provider_key
        self.state = state
        until = state.get("retry_after") or state.get("reset_at") or "unknown"
        super().__init__(f"provider {provider_key} unavailable until {until}")


class AllProvidersUnavailable(ProviderUnavailable):
    """Raised when the primary and configured fallback providers are all gated."""

    def __init__(self, providers: Iterable[str], states: dict[str, dict]):
        self.providers = tuple(providers)
        self.states = states
        # Keep provider_key aligned with the final fallback check for callers
        # that already report the failing provider; the full ordered set remains
        # available in ``providers``/``states`` for the new gate record.
        last = self.providers[-1] if self.providers else "unknown"
        super().__init__(last, states.get(last, {}))
        self.args = (
            "all configured providers unavailable: " + ", ".join(self.providers),
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def provider_key(target: str, model: str) -> str:
    return f"{target}:{model}"


def _safe_identifier(value: str) -> str:
    """Keep operator-facing provider labels free of credentials/control text."""
    value = str(value)
    cleaned = re.sub(r"[^A-Za-z0-9_.:/-]", "_", value)
    return cleaned[:120] or "unknown"


def load(path: Path | None = None) -> dict:
    path = path or cache_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"version": 1, "providers": {}}
    if not isinstance(value, dict) or not isinstance(value.get("providers"), dict):
        return {"version": 1, "providers": {}}
    return value


def save(value: dict, path: Path | None = None) -> None:
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


@contextmanager
def write_lock(path: Path) -> Iterator[None]:
    """Serialize a load -> mutate -> save sequence against concurrent writers.

    Every writer in this module (``record``/``cmd_clear``) holds
    a blocking exclusive ``flock`` on a sidecar ``<cache>.lock`` for the whole
    read-modify-write; without it, two workers finishing close together both
    load the same snapshot and the second ``os.replace`` silently discards the
    first worker's provider state. The kernel drops the lock when the holder
    dies, so a crash never wedges the cache. Readers (``check``,
    ``gate_snapshot``, ``cmd_status``, bare ``load``) stay lock-free on
    purpose: ``save`` publishes via ``os.replace``, so a concurrent ``load``
    always sees a complete old or new file, never a torn one. Best-effort: if
    ``flock`` is unavailable (non-POSIX), degrade to the unlocked behavior
    rather than failing the write, matching ``live.RunLock``.
    """
    try:
        import fcntl
    except ImportError:  # non-POSIX: degrade to no-op
        yield
        return
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def gate_snapshot(
    providers: Iterable[str], path: Path | None = None, now: datetime | None = None
) -> dict:
    """Return sanitized active-gate data for the run record/dashboard.

    ``providers`` is the caller-supplied provider-key set to evaluate (e.g. the
    routing-derived set); a stale ``configured_providers`` key left in an
    existing cache file is never read.
    """
    now = now or _now()
    data = load(path)
    configured = sorted(
        {_safe_identifier(value) for value in providers if isinstance(value, str)}
    )
    gated = []
    for key in configured:
        state = data.get("providers", {}).get(key)
        if not isinstance(state, dict) or state.get("status") != "unavailable":
            continue
        retry_at = _parse_time(state.get("retry_after")) or _parse_time(
            state.get("reset_at")
        )
        if retry_at and retry_at > now:
            gated.append(
                {
                    "provider": key,
                    "failure_class": _safe_identifier(
                        state.get("failure_class") or "unknown"
                    ),
                    "retry_after": retry_at.isoformat(),
                }
            )
    return {
        "configured": configured,
        "gated": gated,
        "all_gated": bool(configured) and len(gated) == len(configured),
        "retry_after": min((item["retry_after"] for item in gated), default=None),
    }


def check(
    target: str, model: str, path: Path | None = None, now: datetime | None = None
) -> None:
    now = now or _now()
    key = provider_key(target, model)
    state = load(path).get("providers", {}).get(key)
    if not isinstance(state, dict):
        return
    retry_at = _parse_time(state.get("retry_after")) or _parse_time(
        state.get("reset_at")
    )
    if retry_at and retry_at > now:
        raise ProviderUnavailable(key, state)


def gate_for_agent(
    routing: dict,
    agent: str,
    *,
    tier: str | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Pre-launch, single-provider capacity check for a dispatch that has
    already committed to one resolved agent (the go front door's adapter
    boundary) rather than selecting across a tier row (`select_cell`).

    Finds the routing target(s) whose ``harness`` matches ``agent`` in the
    resolved tier row (``tier`` or ``routing["default_tier"]``) and checks
    each with `check()`. Returns ``None`` when nothing is gated -- including
    when routing has no target for this agent at all, which degrades to
    "nothing to check, proceed" rather than blocking a repo with no routing
    configured. Returns the first gate's state dict (plus its target name)
    when every matching target is gated. Never substitutes a different
    provider -- this only answers "is the one resolved agent currently
    unavailable," the fail-fast alternative to `select_cell`'s fallback.
    """
    resolved_tier = tier or routing.get("default_tier")
    tier_row = (routing.get("tiers") or {}).get(resolved_tier) or {}
    targets = routing.get("targets") or {}
    matching = [
        name
        for name, cfg in targets.items()
        if isinstance(cfg, dict) and cfg.get("harness") == agent and name in tier_row
    ]
    if not matching:
        return None
    last_gate: dict | None = None
    for target in matching:
        model = (tier_row.get(target) or {}).get("model")
        if not model:
            return None
        try:
            check(target, model, path=path, now=now)
        except ProviderUnavailable as exc:
            last_gate = {"target": target, "model": model, **exc.state}
            continue
        return None
    return last_gate


def record(
    target: str,
    model: str,
    *,
    outcome: str,
    failure_class: str | None = None,
    retry_after: datetime | None = None,
    source: str = "spawn",
    confidence: str = "high",
    path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or _now()
    path = path or cache_path()
    key = provider_key(target, model)
    state = {
        "status": outcome,
        "target": _safe_identifier(target),
        "model": _safe_identifier(model),
        "checked_at": now.isoformat(),
        "failure_class": failure_class,
        "source": source,
        "confidence": confidence,
    }
    if retry_after:
        state["retry_after"] = retry_after.isoformat()
    with write_lock(path):
        data = load(path)
        data.setdefault("version", 1)
        data.setdefault("providers", {})[key] = state
        save(data, path)
    return state


def classify_failure(returncode: int, stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if any(
        token in text
        for token in (
            "model not found",
            "unknown model",
            "invalid model",
            "unsupported model",
            "model does not exist",
            "no such model",
        )
    ):
        return "model_unavailable"
    # "refresh token" / "log out and sign in" cover codex's own consumed-refresh-
    # token wording (confirmed live 2026-09-01: "Your access token could not be
    # refreshed because your refresh token was already used. Please log out and
    # sign in again.") -- the accompanying "401 Unauthorized" line is not always
    # present in the captured stream.
    if any(
        token in text
        for token in (
            "authentication",
            "unauthorized",
            "invalid api key",
            "refresh token",
            "log out and sign in",
        )
    ):
        return "auth"
    # "usage limit"/"session limit" cover Codex's and Claude's own wording for a
    # provider-side usage cap (confirmed live 2026-08-02: codex's "You've hit
    # your usage limit ... try again at Aug 8th, 2026 2:17 AM." previously fell
    # through to "transport", giving it a 30s cooldown instead of the real
    # multi-day reset — see parse_explicit_reset for extracting that timestamp.
    # "weekly limit" covers Claude's own weekly-cap wording (confirmed live
    # 2026-08-05: "You've hit your weekly limit · resets 2pm (America/Los_Angeles)"
    # previously fell through to "transport" too, causing two consecutive
    # weekly-cap hits to trip worktrail-drain's circuit breaker as plain
    # failures instead of a capacity gate).
    if any(
        token in text
        for token in (
            "billing",
            "payment",
            "quota exceeded",
            "usage limit",
            "session limit",
            "weekly limit",
        )
    ):
        return "billing"
    if any(
        token in text for token in ("sandbox", "permission denied", "not permitted")
    ):
        return "sandbox"
    if returncode != 0 and any(
        token in text for token in ("not found", "command", "startup")
    ):
        return "startup"
    return "transport"


# Matches Codex's usage-cap wording: "... try again at Aug 8th, 2026 2:17 AM."
# Month name may be abbreviated or full; day may carry an ordinal suffix.
_EXPLICIT_RESET_RE = re.compile(
    r"try again at\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(\d{4})\s+(\d{1,2}:\d{2}\s*[AaPp][Mm])",
)


def parse_explicit_reset(text: str) -> datetime | None:
    """Extract an explicit reset timestamp from a usage-cap notice, e.g. Codex's
    "try again at Aug 8th, 2026 2:17 AM." Returns None when no such timestamp
    is present, so callers fall back to the generic per-failure-class cooldown
    (`retry_time`) instead of guessing.

    The CLI reports the reset time in local wall-clock time (same convention as
    Claude's own session-limit notice), so the naive parsed value is treated as
    local time and converted to UTC-aware for storage/comparison consistency
    with every other timestamp in this cache.
    """
    m = _EXPLICIT_RESET_RE.search(text or "")
    if not m:
        return None
    month, day, year, clock = m.groups()
    candidate = f"{month} {day} {year} {clock.replace(' ', '').upper()}"
    for fmt in ("%b %d %Y %I:%M%p", "%B %d %Y %I:%M%p"):
        try:
            parsed = datetime.strptime(candidate, fmt)  # noqa: DTZ007
        except ValueError:
            continue
        return parsed.astimezone(timezone.utc)
    return None


def retry_time(failure_class: str, now: datetime | None = None) -> datetime:
    now = now or _now()
    seconds = int(
        os.environ.get(
            f"GO_AGENT_{failure_class.upper()}_COOLDOWN",
            DEFAULT_COOLDOWNS.get(failure_class, 30),
        )
    )
    return now + timedelta(seconds=max(1, seconds))


MAX_REASON_LENGTH = 500
MAX_AUDIT_ENTRIES = 100


def _add_audit(
    data: dict,
    action: str,
    scope: str,
    providers: list[str],
    reason: str,
    at: datetime | None = None,
) -> None:
    at = at or _now()
    data.setdefault("audit", [])
    data["audit"].append(
        {
            "action": action,
            "scope": scope,
            "providers": sorted(providers),
            "reason": reason[:MAX_REASON_LENGTH],
            "at": at.isoformat(),
        }
    )
    if len(data["audit"]) > MAX_AUDIT_ENTRIES:
        data["audit"] = data["audit"][-MAX_AUDIT_ENTRIES:]


def cmd_status(path: Path | None = None, now: datetime | None = None) -> int:
    """Print every provider this cache has a recorded status for.

    Pre-7.2, this listed only the ``configured_providers`` subset written by
    ``configure()``. That function is gone (task 7.2 -- nothing populates
    ``configured_providers`` anymore, and ``gate_snapshot`` has ignored it
    since task 7.1), so this now lists every key under ``providers`` directly
    -- the same set ``record()``/``cmd_clear()`` actually maintain.
    """
    now = now or _now()
    p = path or cache_path()
    raw = load(p)
    providers = raw.get("providers", {})

    print(f"cache: {p}")
    print(f"providers: {len(providers)}")
    if providers:
        print()
        for key in sorted(providers):
            state = providers[key]
            status_label = state.get("status", "unknown")
            retry_at = _parse_time(state.get("retry_after")) or _parse_time(
                state.get("reset_at")
            )
            active = "  (active)" if retry_at and retry_at > now else ""
            fc = state.get("failure_class", "")
            checked = state.get("checked_at", "")
            parts = [f"  {key}  {status_label}{active}"]
            if fc:
                parts.append(f"         failure: {fc}")
            if checked:
                parts.append(f"         checked: {checked}")
            if retry_at:
                parts.append(f"         retry:   {retry_at.isoformat()}")
            print("\n".join(parts))

    audit = raw.get("audit", [])
    if audit:
        print(f"\naudit: {len(audit)} entries (last 5)")
        for entry in audit[-5:]:
            print(
                f"  {entry.get('at', '')}  {entry.get('action', '')}  scope={entry.get('scope', '')}  reason={entry.get('reason', '')}"
            )
    return 0


def cmd_clear(
    scope: str, reason: str, path: Path | None = None, now: datetime | None = None
) -> int:
    now = now or _now()
    p = path or cache_path()
    reason_trimmed = reason.strip()
    if not reason_trimmed:
        print("error: --reason is required and must be non-empty", file=sys.stderr)
        return 1
    if len(reason_trimmed) > MAX_REASON_LENGTH:
        print(
            f"error: --reason must be at most {MAX_REASON_LENGTH} characters",
            file=sys.stderr,
        )
        return 1

    with write_lock(p):
        raw = load(p)
        providers = raw.get("providers", {})

        if scope == "--all":
            if not providers:
                return 0
            cleared = sorted(providers.keys())
            raw["providers"] = {}
            _add_audit(raw, "clear", "all", cleared, reason_trimmed, now)
            save(raw, p)
            for key in cleared:
                print(f"cleared: {key}")
            return 0

        key = _safe_identifier(scope)
        if key not in providers:
            print(f"error: unknown provider key '{scope}'", file=sys.stderr)
            return 1

        del raw["providers"][key]
        _add_audit(raw, "clear", "provider", [key], reason_trimmed, now)
        save(raw, p)
        print(f"cleared: {key}")
        return 0


def cmd_check_agent(
    agent: str,
    routing_json: str,
    tier: str | None,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Fail-fast pre-launch check for the go front door's adapter dispatch
    boundary: is the one already-resolved ``agent`` currently capacity-gated?
    Never selects a different provider -- see `gate_for_agent`'s docstring."""
    try:
        routing = json.loads(routing_json)
    except json.JSONDecodeError as exc:
        print(f"error: --routing must be valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(routing, dict):
        print("error: --routing must be a JSON object", file=sys.stderr)
        return 2
    gate = gate_for_agent(routing, agent, tier=tier, path=path, now=now)
    if gate is None:
        print(json.dumps({"gated": False}))
        return 0
    retry_at = _parse_time(gate.get("retry_after")) or _parse_time(gate.get("reset_at"))
    print(
        json.dumps(
            {
                "gated": True,
                "target": gate.get("target"),
                "model": gate.get("model"),
                "failure_class": gate.get("failure_class"),
                "retry_after": retry_at.isoformat() if retry_at else None,
            }
        )
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="capacity-cache operator")
    parser.add_argument("--cache", type=str, default=None, help="override cache path")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="show capacity cache status")

    clear_parser = sub.add_parser("clear", help="clear capacity gate(s)")
    clear_parser.add_argument(
        "scope", nargs="?", default=None, help="provider key or --all"
    )
    clear_parser.add_argument(
        "--all",
        dest="all_flag",
        action="store_true",
        default=False,
        help="clear all gates",
    )
    clear_parser.add_argument(
        "--reason", type=str, default="", help="required reason for clearing"
    )

    check_agent_parser = sub.add_parser(
        "check-agent",
        help=(
            "fail-fast pre-launch check: is this one resolved agent gated? "
            "(never selects a different provider)"
        ),
    )
    check_agent_parser.add_argument(
        "--agent", required=True, help="resolved harness, e.g. claude/codex/opencode"
    )
    check_agent_parser.add_argument(
        "--routing",
        required=True,
        help="JSON object from resolve_routing() (targets/tiers/default_tier)",
    )
    check_agent_parser.add_argument(
        "--tier", default=None, help="tier row to check (default: routing.default_tier)"
    )

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage()
        print("error: specify 'status', 'clear', or 'check-agent'", file=sys.stderr)
        return 1

    cache = Path(args.cache).expanduser() if args.cache else None

    if args.command == "status":
        return cmd_status(path=cache)

    if args.command == "clear":
        scope = None
        if args.all_flag:
            scope = "--all"
        elif args.scope:
            scope = args.scope
        else:
            print("error: specify a provider key or --all", file=sys.stderr)
            return 1
        return cmd_clear(scope, args.reason, path=cache)

    if args.command == "check-agent":
        return cmd_check_agent(args.agent, args.routing, args.tier, path=cache)

    return 1


if __name__ == "__main__":
    sys.exit(main())
