#!/usr/bin/env python3
"""Single resolver for the operator state directory (`~/.worktrail`).

Everything machine-local that worktrail persists outside a project repo --
run records, the agent-capacity cache, routing/model-defaults config, triage
output, telemetry logs -- lives under one operator home directory. This
module is the only place that decides where that directory is; call sites
compose paths from it (`worktrail_home() / "runs"`) instead of hardcoding.

Historically this directory was `~/.go` (the "/go" front door era). Machines
that haven't migrated yet keep working via the legacy fallback below; the
operator migration itself (mv + symlink) is a manual step, never performed
by this code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORKTRAIL_HOME_ENV = "WORKTRAIL_HOME"

_DEFAULT_DIRNAME = ".worktrail"
_LEGACY_DIRNAME = ".go"

# Module-level so the legacy-fallback note prints at most once per process,
# not once per resolver call (the resolver is called on every default-path
# lookup).
_legacy_warned = False


def worktrail_home() -> Path:
    """The operator state directory, resolved fresh on every call.

    Resolution order:
    1. `$WORKTRAIL_HOME` (expanduser'd) if set -- always wins, even when the
       directory doesn't exist yet.
    2. `~/.worktrail` if it exists.
    3. Legacy `~/.go` if IT exists (not-yet-migrated machines; a one-line
       deprecation note goes to stderr at most once per process).
    4. `~/.worktrail` (fresh default). Never created here -- callers keep
       their existing lazy-mkdir behavior at each write site.
    """
    global _legacy_warned
    override = os.environ.get(WORKTRAIL_HOME_ENV)
    if override:
        return Path(override).expanduser()
    preferred = Path.home() / _DEFAULT_DIRNAME
    if preferred.is_dir():
        return preferred
    legacy = Path.home() / _LEGACY_DIRNAME
    if legacy.is_dir():
        if not _legacy_warned:
            _legacy_warned = True
            print(
                f"worktrail: using legacy state dir {legacy} -- rename it to "
                f"{preferred} (or set ${WORKTRAIL_HOME_ENV}) to silence this note",
                file=sys.stderr,
            )
        return legacy
    return preferred
