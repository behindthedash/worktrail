"""The `aspens` add-on: keeps skill-doc sync in step with each task's own commit.

Wraps the third-party `aspens` CLI (npm-installed skill-doc generator/sync tool)
behind the `AddOn` protocol (`worktrail.addons.base`), so an opted-in repo gets
it installed, configured, and synced end-to-end without a repo owner ever
hand-running the CLI or its own post-commit hook.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from worktrail.addons.base import AddOnResult

CACHE_DIR = Path.home() / ".cache" / "worktrail" / "addons" / "aspens"
LAST_CHECK_MARKER = CACHE_DIR / "last-check"

# Default interval between aspens CLI presence/update checks, per design D7.
CHECK_INTERVAL_SECONDS = 24 * 60 * 60

INSTALL_TIMEOUT = 120


class AspensAddOn:
    """Installs, configures, and syncs the `aspens` skill-doc CLI."""

    name = "aspens"

    def install(self, ctx: Any) -> None:
        """Ensure the `aspens` CLI is present, re-checking at most once per CHECK_INTERVAL_SECONDS.

        Gated by a machine-local marker (not per-repo/per-worktree) so a fresh
        check doesn't add a network round trip to every task's pre-PR flow.
        """
        if self._marker_is_fresh():
            return
        try:
            subprocess.run(
                ["npm", "install", "-g", "aspens"],
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        self._touch_marker()

    def configure(self, ctx: Any) -> None:
        raise NotImplementedError

    def run(self, ctx: Any) -> AddOnResult:
        raise NotImplementedError

    def _marker_is_fresh(self) -> bool:
        try:
            last_check = float(LAST_CHECK_MARKER.read_text().strip())
        except (OSError, ValueError):
            return False
        return (time.time() - last_check) < CHECK_INTERVAL_SECONDS

    def _touch_marker(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        LAST_CHECK_MARKER.write_text(str(time.time()))
