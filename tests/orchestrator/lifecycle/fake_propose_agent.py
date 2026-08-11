#!/usr/bin/env python3
"""A claude/codex/opencode stand-in for the propose-spawn lifecycle test.

Placed on PATH under all three binary names (`claude`, `codex`, `opencode`)
so `skill_dispatch.build_command`'s real argv is actually executed by
`subprocess.run`, not mocked -- the bug class this harness exists to catch
(PR #264: a spawn that exits 0 and authors nothing) lives in the gap between
"argv looks right" and "the child actually had permission to write", which a
mocked `subprocess.run` cannot observe.

Behavior: write `openspec/changes/$FAKE_PROPOSE_CHANGE_ID/{proposal,design,
tasks}.md` under the process's OWN cwd (proving `--cwd`/process-cwd targeting
reached the child) -- but ONLY if the per-agent flag that actually grants
write access is present in argv, exactly mirroring the real headless
permission gate each CLI enforces:

  - claude:   `--permission-mode bypassPermissions`
  - opencode: `--auto`
  - codex:    none required -- `skill_dispatch.build_command` always passes
              `-s danger-full-access` unconditionally for codex.

Missing the gate -> exit 0 and write nothing, which is the exact silent
no-op PR #264 verified live on 2026-08-09.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _write_allowed(agent: str, argv: list[str]) -> bool:
    if agent == "claude":
        return "--permission-mode" in argv and "bypassPermissions" in argv
    if agent == "opencode":
        return "--auto" in argv
    if agent == "codex":
        return "-s" in argv and "danger-full-access" in argv
    return False


def main(argv: list[str]) -> int:
    # The PATH shim execs this script under one shared interpreter path for
    # all three binary names, so `sys.argv[0]` is always this file's own
    # path, never "claude"/"codex"/"opencode" -- the shim passes the real
    # agent name as argv[0] instead.
    agent, argv = argv[0], argv[1:]
    change_id = os.environ.get("FAKE_PROPOSE_CHANGE_ID")
    if not change_id or not _write_allowed(agent, argv):
        return 0
    change_dir = Path.cwd() / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True, exist_ok=True)
    (change_dir / "proposal.md").write_text("## Why\n\nfake propose spawn.\n")
    (change_dir / "design.md").write_text("## Context\n\nfake.\n")
    (change_dir / "tasks.md").write_text("## 1. Setup\n\n- [ ] 1.1 fake task\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))  # argv[0] here is this file's own path; skip it
