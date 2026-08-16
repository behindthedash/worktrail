#!/usr/bin/env python3
"""Resolve the Worktrail invocation context: provider identity and native-Skill
capability, as two independent values, plus the dispatch mode they imply.

`Skill(...)` is a host capability, not a shell command, and it is independent of
which provider CLI was resolved: an embedded Codex host resolves to the `codex`
provider and exposes no `Skill(...)` at all, while a Claude Code session
resolves to `claude` and does. Branching on provider identity therefore picks
the wrong dispatch path for exactly the hosts that need the adapter.

The capability signal is a boolean, supplied by an explicit caller parameter
(the parent agent observing its own tool surface) or by the
`WORKTRAIL_NATIVE_SKILL` operator/harness override. An absent or unparseable
signal resolves to False, never True: assuming the capability is present is the
speculative `Skill(...)` call that a caller cannot retry out of, whereas the
adapter path is correct whenever a provider CLI is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, Mapping, Optional

SUPPORTED_AGENTS = ("claude", "codex", "opencode")

CAPABILITY_ENV = "WORKTRAIL_NATIVE_SKILL"
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})

# Dispatch modes, in the order the decision tree considers them. The tuple is
# the canonical enumeration: skills/worktrail-go/SKILL.md's dispatch table is
# pinned to it by tests/test_plugin_surface.py, so adding or renaming a mode
# without updating the skill doc fails the build instead of drifting silently.
IN_SESSION_RESUME = "in-session-resume"
NATIVE_SKILL = "native-skill"
ADAPTER = "adapter"
BLOCKED = "blocked"
DISPATCH_MODES = (IN_SESSION_RESUME, NATIVE_SKILL, ADAPTER, BLOCKED)


@dataclass(frozen=True)
class InvocationContext:
    agent_cli: str
    native_skill_available: bool
    dispatch_mode: str
    capability_source: str
    dispatch_id: str
    blocked_reason: Optional[str] = None
    warning: Optional[str] = None


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _generate_dispatch_id() -> str:
    """A fresh, unguessable identity for one /go invocation.

    Threaded as `--by` through every `worktrail-work-queue claim` call this
    dispatch makes (the front door's own claim, then sdd-workflow's
    handoff-seed claim) so `claim()`'s `same_owner` result can tell "this
    dispatch already owns this brief" apart from "a different dispatch does" --
    see docs/specs/research/concurrent-go-dispatch-brief-claim-race.md. Must
    be stable across both calls of one dispatch and distinct across two
    concurrent dispatches; a fresh uuid4 per top-level `resolve()` call
    satisfies both without any shared external state.
    """
    return f"go-{uuid.uuid4().hex[:16]}"


def _resolve_agent(
    agent: Optional[str],
    policy_agent: Optional[str],
    environ: Mapping[str, str],
) -> str:
    """Precedence: explicit invocation > repository policy > machine-wide env >
    detected host > claude. Unchanged from the pre-existing contract; only the
    capability axis is new."""
    resolved = (
        agent
        or policy_agent
        or environ.get("GO_AGENT_CLI")
        or environ.get("ORCH_AGENT")
        or ("opencode" if environ.get("OPENCODE_PARENT") else None)
        or ("codex" if environ.get("CODEX_CI") or environ.get("CODEX_THREAD_ID") else None)
        or "claude"
    )
    if resolved not in SUPPORTED_AGENTS:
        raise ValueError(
            f"unsupported agent: {resolved!r}; allowed: {', '.join(SUPPORTED_AGENTS)}"
        )
    return resolved


def _resolve_capability(
    native_skill: Optional[bool],
    environ: Mapping[str, str],
) -> tuple[bool, str, Optional[str]]:
    if native_skill is not None:
        return native_skill, "explicit", None
    if CAPABILITY_ENV not in environ:
        return False, "unset", None
    raw = environ[CAPABILITY_ENV].strip().lower()
    if raw in _TRUE:
        return True, "environment", None
    if raw in _FALSE:
        return False, "environment", None
    return False, "unset", (
        f"{CAPABILITY_ENV}={environ[CAPABILITY_ENV]!r} is not a recognized boolean; "
        "treating native Skill as unavailable"
    )


def resolve(
    *,
    agent: Optional[str] = None,
    policy_agent: Optional[str] = None,
    native_skill: Optional[bool] = None,
    active_resume: bool = False,
    dispatch_id: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
) -> InvocationContext:
    """Resolve both axes once and apply the deterministic decision tree."""
    env = os.environ if environ is None else environ
    locate = _which if which is None else which

    agent_cli = _resolve_agent(agent, policy_agent, env)
    available, source, warning = _resolve_capability(native_skill, env)
    resolved_dispatch_id = dispatch_id or _generate_dispatch_id()

    # An already-active resume is owned by the parent that holds its run record
    # and worktree; spawning any child re-enters a run already in flight.
    if active_resume:
        mode, blocked_reason = IN_SESSION_RESUME, None
    elif available:
        mode, blocked_reason = NATIVE_SKILL, None
    elif locate(agent_cli) is not None:
        mode, blocked_reason = ADAPTER, None
    else:
        mode = BLOCKED
        blocked_reason = (
            f"no dispatch capability: the host exposes no native Skill and the "
            f"resolved provider CLI '{agent_cli}' is not on PATH. Install "
            f"'{agent_cli}', pass an --agent whose CLI is installed, or set "
            f"{CAPABILITY_ENV}=1 if this host does expose Skill."
        )

    return InvocationContext(
        agent_cli=agent_cli,
        native_skill_available=available,
        dispatch_mode=mode,
        capability_source=source,
        dispatch_id=resolved_dispatch_id,
        blocked_reason=blocked_reason,
        warning=warning,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", help="explicit provider override")
    parser.add_argument("--policy-agent", help="agent_cli resolved from repository policy")
    capability = parser.add_mutually_exclusive_group()
    capability.add_argument(
        "--native-skill",
        dest="native_skill",
        action="store_true",
        default=None,
        help="the calling host exposes a native Skill tool",
    )
    capability.add_argument(
        "--no-native-skill",
        dest="native_skill",
        action="store_false",
        help="the calling host exposes no native Skill tool",
    )
    parser.add_argument(
        "--active-resume",
        action="store_true",
        help="this dispatch continues a run whose record and worktree already exist",
    )
    parser.add_argument(
        "--dispatch-id",
        default=None,
        help="override the generated dispatch identity (tests/replay only)",
    )
    parser.add_argument("--json", action="store_true")
    parsed = parser.parse_args(argv)

    try:
        context = resolve(
            agent=parsed.agent,
            policy_agent=parsed.policy_agent,
            native_skill=parsed.native_skill,
            active_resume=parsed.active_resume,
            dispatch_id=parsed.dispatch_id,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if context.warning:
        print(f"warning: {context.warning}", file=sys.stderr)

    if parsed.json:
        print(json.dumps(asdict(context), sort_keys=True))
    else:
        print(f"agent_cli: {context.agent_cli}")
        print(f"native_skill_available: {context.native_skill_available}")
        print(f"dispatch_mode: {context.dispatch_mode}")
        print(f"dispatch_id: {context.dispatch_id}")

    if context.dispatch_mode == BLOCKED:
        print(
            f"blocked_missing_dispatch_capability: {context.blocked_reason}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
