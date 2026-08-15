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
  local changed_files_content="${4-}"

  local pyproject_diff changed_files out
  pyproject_diff="$(mktemp)"
  changed_files="$(mktemp)"
  out="$(mktemp)"
  printf '%s' "$pyproject_diff_content" > "$pyproject_diff"
  printf '%s' "$changed_files_content" > "$changed_files"

  bash "$CHECK" \
    --paths-filter-code "$paths_filter_code" \
    --pyproject-diff "$pyproject_diff" \
    --changed-files "$changed_files" \
    --github-output "$out" >/dev/null

  assert_kv "$out" "bookkeeping" "$expect_bookkeeping"
  rm -f "$pyproject_diff" "$changed_files" "$out"
}

# docs-only: code filter false, pyproject untouched -> bookkeeping.
run_case false "" true "docs/foo.md"

# docs+openspec: code filter false, pyproject untouched -> bookkeeping.
run_case false "" true "openspec/changes/x/proposal.md"

# src-alongside-docs: code changed, pyproject untouched -> not bookkeeping.
run_case true "" false "src/worktrail/orchestrator/live.py"

# pyproject-version-only, pyproject.toml the only changed path ->
# bookkeeping.
run_case true \
  '-version = "0.8.2"
+version = "0.8.3"' true "pyproject.toml"

# pyproject-version-only plus the paired plugin manifest bump, still no
# other changed path -> bookkeeping.
run_case true \
  '-version = "0.8.2"
+version = "0.8.3"' true \
  "pyproject.toml
.codex-plugin/plugin.json"

# pyproject-with-other-line: version bump plus another changed line in
# pyproject.toml itself -> not bookkeeping.
run_case true \
  '-version = "0.8.2"
+version = "0.8.3"
-description = "old"
+description = "new"' false "pyproject.toml"

# REGRESSION (the shape that broke PRs #426 and #438): pyproject.toml's own
# diff is version-only, but a real code file changed in the same diff ->
# must NOT bypass the test suite.
run_case true \
  '-version = "0.8.2"
+version = "0.8.3"' false \
  "pyproject.toml
src/worktrail/orchestrator/live.py"

# paths-filter-code=true with pyproject untouched -> not bookkeeping.
run_case true "" false "src/worktrail/router/policy.py"

echo "bookkeeping gate check: OK"
