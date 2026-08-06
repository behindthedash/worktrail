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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional


def cache_path() -> Path:
    return Path(os.environ.get("GO_AGENT_CAPACITY_CACHE", "~/.go/agent-capacity.json")).expanduser()
DEFAULT_COOLDOWNS = {
    "startup": 60,
    "sandbox": 60,
    "transport": 30,
    "auth": 3600,
    "billing": 3600,
}


class ProviderUnavailable(RuntimeError):
    """Raised when a provider's persisted cooldown has not expired."""

    def __init__(self, provider_key: str, state: Dict):
        self.provider_key = provider_key
        self.state = state
        until = state.get("retry_after") or state.get("reset_at") or "unknown"
        super().__init__(f"provider {provider_key} unavailable until {until}")


class AllProvidersUnavailable(ProviderUnavailable):
    """Raised when the primary and configured fallback providers are all gated."""

    def __init__(self, providers: Iterable[str], states: Dict[str, Dict]):
        self.providers = tuple(providers)
        self.states = states
        # Keep provider_key aligned with the final fallback check for callers
        # that already report the failing provider; the full ordered set remains
        # available in ``providers``/``states`` for the new gate record.
        last = self.providers[-1] if self.providers else "unknown"
        super().__init__(last, states.get(last, {}))
        self.args = ("all configured providers unavailable: " + ", ".join(self.providers),)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def provider_key(agent: str, model: str) -> str:
    return f"{agent}:{model}"


def _safe_identifier(value: str) -> str:
    """Keep operator-facing provider labels free of credentials/control text."""
    value = str(value)
    cleaned = re.sub(r"[^A-Za-z0-9_.:/-]", "_", value)
    return cleaned[:120] or "unknown"


def load(path: Optional[Path] = None) -> Dict:
    path = path or cache_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"version": 1, "providers": {}}
    if not isinstance(value, dict) or not isinstance(value.get("providers"), dict):
        return {"version": 1, "providers": {}}
    return value


def save(value: Dict, path: Optional[Path] = None) -> None:
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


def configure(providers: Iterable[tuple[str, str]], path: Optional[Path] = None) -> None:
    """Remember the provider/model-safe set used by the current dispatch."""
    path = path or cache_path()
    data = load(path)
    data["configured_providers"] = sorted(
        {_safe_identifier(provider_key(agent, model)) for agent, model in providers}
    )
    save(data, path)


def gate_snapshot(path: Optional[Path] = None, now: Optional[datetime] = None) -> Dict:
    """Return sanitized active-gate data for the run record/dashboard."""
    now = now or _now()
    data = load(path)
    configured = [
        _safe_identifier(value)
        for value in data.get("configured_providers", [])
        if isinstance(value, str)
    ]
    if not configured:
        configured = sorted(
            _safe_identifier(value)
            for value in data.get("providers", {})
            if isinstance(value, str)
        )
    gated = []
    for key in configured:
        state = data.get("providers", {}).get(key)
        if not isinstance(state, dict) or state.get("status") != "unavailable":
            continue
        retry_at = _parse_time(state.get("retry_after")) or _parse_time(state.get("reset_at"))
        if retry_at and retry_at > now:
            gated.append({
                "provider": key,
                "failure_class": _safe_identifier(state.get("failure_class") or "unknown"),
                "retry_after": retry_at.isoformat(),
            })
    return {
        "configured": configured,
        "gated": gated,
        "all_gated": bool(configured) and len(gated) == len(configured),
        "retry_after": min((item["retry_after"] for item in gated), default=None),
    }


def check(agent: str, model: str, path: Optional[Path] = None, now: Optional[datetime] = None) -> None:
    now = now or _now()
    key = provider_key(agent, model)
    state = load(path).get("providers", {}).get(key)
    if not isinstance(state, dict):
        return
    retry_at = _parse_time(state.get("retry_after")) or _parse_time(state.get("reset_at"))
    if retry_at and retry_at > now:
        raise ProviderUnavailable(key, state)


def record(
    agent: str,
    model: str,
    *,
    outcome: str,
    failure_class: Optional[str] = None,
    retry_after: Optional[datetime] = None,
    source: str = "spawn",
    confidence: str = "high",
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict:
    now = now or _now()
    data = load(path)
    key = provider_key(agent, model)
    state = {
        "status": outcome,
        "agent": _safe_identifier(agent),
        "model": _safe_identifier(model),
        "checked_at": now.isoformat(),
        "failure_class": failure_class,
        "source": source,
        "confidence": confidence,
    }
    if retry_after:
        state["retry_after"] = retry_after.isoformat()
    data.setdefault("version", 1)
    data.setdefault("providers", {})[key] = state
    save(data, path)
    return state


def classify_failure(returncode: int, stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".lower()
    if any(token in text for token in ("authentication", "unauthorized", "invalid api key")):
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
    if any(token in text for token in
           ("billing", "payment", "quota exceeded", "usage limit", "session limit",
            "weekly limit")):
        return "billing"
    if any(token in text for token in ("sandbox", "permission denied", "not permitted")):
        return "sandbox"
    if returncode != 0 and any(token in text for token in ("not found", "command", "startup")):
        return "startup"
    return "transport"


# Matches Codex's usage-cap wording: "... try again at Aug 8th, 2026 2:17 AM."
# Month name may be abbreviated or full; day may carry an ordinal suffix.
_EXPLICIT_RESET_RE = re.compile(
    r"try again at\s+([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(\d{4})\s+(\d{1,2}:\d{2}\s*[AaPp][Mm])",
)


def parse_explicit_reset(text: str) -> Optional[datetime]:
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
            parsed = datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        return parsed.astimezone(timezone.utc)
    return None


def retry_time(failure_class: str, now: Optional[datetime] = None) -> datetime:
    now = now or _now()
    seconds = int(os.environ.get(f"GO_AGENT_{failure_class.upper()}_COOLDOWN", DEFAULT_COOLDOWNS.get(failure_class, 30)))
    return now + timedelta(seconds=max(1, seconds))


MAX_REASON_LENGTH = 500
MAX_AUDIT_ENTRIES = 100


def _add_audit(data: Dict, action: str, scope: str, providers: list[str], reason: str, at: Optional[datetime] = None) -> None:
    at = at or _now()
    data.setdefault("audit", [])
    data["audit"].append({
        "action": action,
        "scope": scope,
        "providers": sorted(providers),
        "reason": reason[:MAX_REASON_LENGTH],
        "at": at.isoformat(),
    })
    if len(data["audit"]) > MAX_AUDIT_ENTRIES:
        data["audit"] = data["audit"][-MAX_AUDIT_ENTRIES:]


def cmd_status(path: Optional[Path] = None, now: Optional[datetime] = None) -> int:
    now = now or _now()
    p = path or cache_path()
    raw = load(p)
    providers = raw.get("providers", {})
    configured = raw.get("configured_providers", [])

    print(f"cache: {p}")
    print(f"configured: {len(configured)}")
    if configured:
        print()
        for key in sorted(configured):
            state = providers.get(key)
            if not isinstance(state, dict):
                print(f"  {key}  (no record)")
                continue
            status_label = state.get("status", "unknown")
            retry_at = _parse_time(state.get("retry_after")) or _parse_time(state.get("reset_at"))
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
            print(f"  {entry.get('at','')}  {entry.get('action','')}  scope={entry.get('scope','')}  reason={entry.get('reason','')}")
    return 0


def cmd_clear(scope: str, reason: str, path: Optional[Path] = None, now: Optional[datetime] = None) -> int:
    now = now or _now()
    p = path or cache_path()
    reason_trimmed = reason.strip()
    if not reason_trimmed:
        print("error: --reason is required and must be non-empty", file=sys.stderr)
        return 1
    if len(reason_trimmed) > MAX_REASON_LENGTH:
        print(f"error: --reason must be at most {MAX_REASON_LENGTH} characters", file=sys.stderr)
        return 1

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


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="capacity-cache operator")
    parser.add_argument("--cache", type=str, default=None, help="override cache path")
    sub = parser.add_subparsers(dest="command")

    status_parser = sub.add_parser("status", help="show capacity cache status")

    clear_parser = sub.add_parser("clear", help="clear capacity gate(s)")
    clear_parser.add_argument("scope", nargs="?", default=None, help="provider key or --all")
    clear_parser.add_argument("--all", dest="all_flag", action="store_true", default=False, help="clear all gates")
    clear_parser.add_argument("--reason", type=str, default="", help="required reason for clearing")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_usage()
        print("error: specify 'status' or 'clear'", file=sys.stderr)
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

    return 1


if __name__ == "__main__":
    sys.exit(main())
