#!/usr/bin/env bash
set -euo pipefail

# `pip install -e` records an absolute path to *this* checkout in the
# environment's site-packages. If that checkout is a linked git worktree
# (created for one task, per this workspace's worktree-per-task convention)
# rather than the canonical checkout, deleting the worktree after merge --
# the standard teardown step -- silently kills every worktrail-* console
# script (ModuleNotFoundError: No module named 'worktrail') with no warning
# until a command is next run. Refuse to install from a worktree; point at
# the canonical checkout instead.

REPO_ROOT="$(git rev-parse --show-toplevel)"
GIT_DIR="$(git rev-parse --git-dir)"
GIT_COMMON_DIR="$(git rev-parse --git-common-dir)"

# A linked worktree's --git-dir (its private per-worktree metadata dir under
# the main repo's .git/worktrees/<name>) differs from --git-common-dir (the
# shared .git shared by every worktree). They're equal only in the canonical
# checkout (or any non-worktree clone).
if [[ "$(cd "$GIT_DIR" && pwd)" != "$(cd "$GIT_COMMON_DIR" && pwd)" ]]; then
  cat >&2 <<EOF
error: refusing to 'pip install -e' from a linked git worktree:
  $REPO_ROOT

This directory is a task worktree, not the canonical worktrail checkout.
An editable install made here breaks (ModuleNotFoundError: No module named
'worktrail') the moment this worktree is deleted -- the standard
worktree-teardown-after-merge step.

Run this script from the canonical checkout instead, e.g.:
  cd "$(dirname "$GIT_COMMON_DIR")" && ./scripts/dev-install.sh
EOF
  exit 1
fi

cd "$REPO_ROOT"

# Plain install first (works in a venv or any environment without PEP 668
# guarding the system Python). Only fall back to --break-system-packages when
# pip actually reports an externally-managed environment, rather than always
# passing a flag that pip itself rejects inside a venv.
install_log="$(mktemp)"
trap 'rm -f "$install_log"' EXIT
if pip install -e ".[dev]" >"$install_log" 2>&1; then
  cat "$install_log"
elif grep -q "externally-managed-environment" "$install_log"; then
  echo "note: externally-managed environment detected, retrying with --break-system-packages --user" >&2
  pip install -e ".[dev]" --break-system-packages --user
else
  cat "$install_log" >&2
  exit 1
fi

# Fail immediately if pip left stale entry-point metadata behind, or if the
# package and Codex plugin versions were not bumped together. Source tests read
# pyproject.toml directly; this post-install check owns installed-state freshness.
python3 scripts/check_packaging_metadata.py --repo "$REPO_ROOT"
