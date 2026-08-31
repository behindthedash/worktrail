#!/usr/bin/env python3
"""Stand-in for the headless openspec-propose spawn (`#openspec-propose`).

Simulates the real child's incremental artifact authoring: writes
`proposal.md` immediately, signals readiness, then blocks -- exactly the
shape of a spawn that gets killed (OOM, host disconnect) after `proposal.md`
lands on disk but before `design.md`/`tasks.md` are ever written.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    change_dir = Path(argv[0])
    ready = Path(argv[1])
    change_dir.mkdir(parents=True, exist_ok=True)
    (change_dir / "proposal.md").write_text(
        "# Proposal\n\nDraft in progress.\n", encoding="utf-8"
    )
    ready.write_text("ready\n", encoding="utf-8")
    # Still "authoring" design.md/tasks.md when the kill arrives.
    while True:
        time.sleep(0.05)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
