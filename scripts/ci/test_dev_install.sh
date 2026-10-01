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

# A fake bare `pip` on PATH so this test proves the installer uses the Python
# 3.14 module invocation instead of a PATH-selected pip executable.
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN"
PIP_CALLED_MARKER="$WORK/pip_called"
cat > "$FAKE_BIN/pip" <<EOF
#!/usr/bin/env bash
echo "\$@" > "$PIP_CALLED_MARKER"
EOF
chmod +x "$FAKE_BIN/pip"

# Stub Python 3.14 so this shell test remains hermetic while recording both
# the pip module install and post-install metadata verification invocations.
PYTHON314_CALLED_MARKER="$WORK/python314_called"
cat > "$FAKE_BIN/python3.14" <<EOF
#!/usr/bin/env bash
echo "\$@" >> "$PYTHON314_CALLED_MARKER"
EOF
chmod +x "$FAKE_BIN/python3.14"
export PATH="$FAKE_BIN:$PATH"

# Minimal repo standing in for the canonical checkout.
REPO="$WORK/repo"
mkdir -p "$REPO"
git -C "$REPO" init -q -b main
git -C "$REPO" -c user.email=test@example.com -c user.name=test commit -q --allow-empty -m init

# Canonical checkout: install and verification proceed through Python 3.14.
rm -f "$PIP_CALLED_MARKER"
rm -f "$PYTHON314_CALLED_MARKER"
( cd "$REPO" && bash "$SCRIPT" ) || fail "expected success from the canonical checkout"
[ ! -f "$PIP_CALLED_MARKER" ] || fail "bare pip must not be invoked from the canonical checkout"
[ -f "$PYTHON314_CALLED_MARKER" ] || fail "expected Python 3.14 to be invoked from the canonical checkout"
grep -Fqx -- "-m pip install -e .[dev]" "$PYTHON314_CALLED_MARKER" \
  || fail "expected the Python 3.14 pip module install invocation"
grep -q "scripts/check_packaging_metadata.py" "$PYTHON314_CALLED_MARKER" \
  || fail "expected the packaging metadata verifier script"

# Linked worktree: install refused, pip is never invoked.
WT="$WORK/repo-worktree"
git -C "$REPO" worktree add -q "$WT" -b task-branch main
rm -f "$PIP_CALLED_MARKER"
rm -f "$PYTHON314_CALLED_MARKER"
WORKTREE_STDERR="$WORK/worktree_stderr"
if ( cd "$WT" && bash "$SCRIPT" ) 2>"$WORKTREE_STDERR"; then
  fail "expected failure from a linked worktree"
fi
[ -f "$PIP_CALLED_MARKER" ] && fail "pip must not be invoked from a linked worktree"
[ -f "$PYTHON314_CALLED_MARKER" ] && fail "Python 3.14 must not be invoked from a linked worktree"
grep -q "refusing to 'pip install -e' from a linked git worktree" "$WORKTREE_STDERR" \
  || fail "expected the worktree-refusal message on stderr"

echo "dev-install worktree guard: OK"
