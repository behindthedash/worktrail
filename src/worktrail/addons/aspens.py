"""The `aspens` add-on: keeps skill-doc sync in step with each task's own commit.

Wraps the third-party `aspens` CLI (npm-installed skill-doc generator/sync tool)
behind the `AddOn` protocol (`worktrail.addons.base`), so an opted-in repo gets
it installed, configured, and synced end-to-end without a repo owner ever
hand-running the CLI or its own post-commit hook.
"""

from __future__ import annotations

import shutil
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
CONFIGURE_TIMEOUT = 120
RUN_TIMEOUT = 120


class AspensAddOn:
    """Installs, configures, and syncs the `aspens` skill-doc CLI."""

    name = "aspens"

    def install(self, ctx: Any) -> None:
        """Ensure the `aspens` CLI is present, re-checking at most once per CHECK_INTERVAL_SECONDS.

        Gated by a machine-local marker (not per-repo/per-worktree) so a fresh
        check doesn't add a network round trip to every task's pre-PR flow.

        Only installs when `aspens` is absent from PATH. An unconditional
        `npm install -g aspens` replaced an operator's `npm link`ed fork with
        the registry build (live, 2026-09-01), reintroducing the destructive
        doc-sync rewrite the fork had already fixed.
        """
        if self._marker_is_fresh():
            return
        if shutil.which("aspens"):
            self._touch_marker()
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
        """Run `aspens doc init` once, leaving an existing `.aspens.json` untouched.

        Never passes `--install-hook`: aspens' own post-commit hook is the
        detached, non-committing mechanism this add-on exists to replace, so
        the shared runner's own stage-and-commit step must stay the only
        thing that ever commits aspens' output.
        """
        worktree = Path(ctx.worktree)
        if (worktree / ".aspens.json").exists():
            return
        config = getattr(ctx, "config", None) or {}
        cmd = ["aspens", "doc", "init"]
        if config.get("target"):
            cmd += ["--target", str(config["target"])]
        if config.get("backend"):
            cmd += ["--backend", str(config["backend"])]
        try:
            subprocess.run(
                cmd,
                cwd=str(worktree),
                capture_output=True,
                text=True,
                timeout=getattr(ctx, "timeout", CONFIGURE_TIMEOUT),
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    def run(self, ctx: Any) -> AddOnResult:
        """Run `aspens doc sync` (or `--refresh`) and report changed paths to commit.

        Unlike `install`/`configure`, subprocess failures (`TimeoutExpired`,
        `OSError`) and a non-zero exit are deliberately left to propagate
        rather than swallowed: this is the one call the shared runner's
        `required` gate (design D4) needs to be able to catch and fail on,
        while `install`/`configure` stay best-effort priming that never blocks.
        """
        worktree = Path(ctx.worktree)
        config = getattr(ctx, "config", None) or {}
        cmd = ["aspens", "doc", "sync"]
        if config.get("refresh"):
            cmd.append("--refresh")
        result = subprocess.run(
            cmd,
            check=False,
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=getattr(ctx, "timeout", RUN_TIMEOUT),
        )
        if result.returncode != 0:
            tail = ((result.stderr or "") + (result.stdout or "")).strip()[-300:]
            raise RuntimeError(
                f"aspens doc sync failed (exit {result.returncode}): {tail}"
            )
        paths = self._changed_paths(worktree)
        if not paths:
            return AddOnResult(changed=False, detail="no changes", paths=[])
        return AddOnResult(
            changed=True, detail=f"synced {len(paths)} path(s)", paths=paths
        )

    def _changed_paths(self, worktree: Path) -> list[Path]:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
        )
        paths: list[Path] = []
        for line in status.stdout.splitlines():
            if not line.strip():
                continue
            entry = line[3:]
            if " -> " in entry:  # renames: "R  old -> new"
                entry = entry.split(" -> ", 1)[1]
            paths.append(Path(entry))
        return paths

    def _marker_is_fresh(self) -> bool:
        try:
            last_check = float(LAST_CHECK_MARKER.read_text().strip())
        except (OSError, ValueError):
            return False
        return (time.time() - last_check) < CHECK_INTERVAL_SECONDS

    def _touch_marker(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        LAST_CHECK_MARKER.write_text(str(time.time()))
