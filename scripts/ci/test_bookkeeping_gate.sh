#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECK="${ROOT}/scripts/ci/bookkeeping_gate.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_kv() {
  local file="$1"
  local key="$2"
  local expected="$3"
  local actual
  actual="$(grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
  [ "$actual" = "$expected" ] || fail "Expected ${key}=${expected}, got ${key}=${actual}"
}

run_case() {
  local paths_filter_code="$1"
  local pyproject_diff_content="$2"
  local expect_bookkeeping="$3"

  local pyproject_diff out
  pyproject_diff="$(mktemp)"
  out="$(mktemp)"
  printf '%s' "$pyproject_diff_content" > "$pyproject_diff"

  bash "$CHECK" \
    --paths-filter-code "$paths_filter_code" \
    --pyproject-diff "$pyproject_diff" \
    --github-output "$out" >/dev/null

  assert_kv "$out" "bookkeeping" "$expect_bookkeeping"
  rm -f "$pyproject_diff" "$out"
}

# docs-only: code filter false, pyproject untouched -> bookkeeping.
run_case false "" true

# docs+openspec: code filter false, pyproject untouched -> bookkeeping.
run_case false "" true

# src-alongside-docs: code changed, pyproject untouched -> not bookkeeping.
run_case true "" false

# pyproject-version-only: code changed, but pyproject diff is only the
# version line -> bookkeeping.
run_case true \
  '-version = "0.8.2"
+version = "0.8.3"' true

# pyproject-with-other-line: version bump plus another changed line ->
# not bookkeeping.
run_case true \
  '-version = "0.8.2"
+version = "0.8.3"
-description = "old"
+description = "new"' false

# paths-filter-code=true with pyproject untouched -> not bookkeeping.
run_case true "" false

echo "bookkeeping gate check: OK"
