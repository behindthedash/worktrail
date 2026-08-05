#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${ROOT}/scripts/dev-install.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# A fake `pip` on PATH so this test never touches the real environment; it
# just records whether it was invoked.
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN"
PIP_CALLED_MARKER="$WORK/pip_called"
cat > "$FAKE_BIN/pip" <<EOF
#!/usr/bin/env bash
echo "\$@" > "$PIP_CALLED_MARKER"
EOF
chmod +x "$FAKE_BIN/pip"
export PATH="$FAKE_BIN:$PATH"

# Minimal repo standing in for the canonical checkout.
REPO="$WORK/repo"
mkdir -p "$REPO"
git -C "$REPO" init -q -b main
git -C "$REPO" -c user.email=test@example.com -c user.name=test commit -q --allow-empty -m init

# Canonical checkout: install proceeds, pip is invoked.
rm -f "$PIP_CALLED_MARKER"
( cd "$REPO" && bash "$SCRIPT" ) || fail "expected success from the canonical checkout"
[ -f "$PIP_CALLED_MARKER" ] || fail "expected pip to be invoked from the canonical checkout"

# Linked worktree: install refused, pip is never invoked.
WT="$WORK/repo-worktree"
git -C "$REPO" worktree add -q "$WT" -b task-branch main
rm -f "$PIP_CALLED_MARKER"
WORKTREE_STDERR="$WORK/worktree_stderr"
if ( cd "$WT" && bash "$SCRIPT" ) 2>"$WORKTREE_STDERR"; then
  fail "expected failure from a linked worktree"
fi
[ -f "$PIP_CALLED_MARKER" ] && fail "pip must not be invoked from a linked worktree"
grep -q "refusing to 'pip install -e' from a linked git worktree" "$WORKTREE_STDERR" \
  || fail "expected the worktree-refusal message on stderr"

echo "dev-install worktree guard: OK"
