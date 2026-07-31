#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECK="${ROOT}/scripts/ci/version_bump_check.sh"

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
  local changed_files_content="$1"
  local version_diff_content="$2"
  local has_exempt_label="$3"
  local expect_pass="$4"

  local changed_files version_diff out
  changed_files="$(mktemp)"
  version_diff="$(mktemp)"
  out="$(mktemp)"
  printf '%s' "$changed_files_content" > "$changed_files"
  printf '%s' "$version_diff_content" > "$version_diff"

  bash "$CHECK" \
    --changed-files "$changed_files" \
    --version-diff "$version_diff" \
    --has-exempt-label "$has_exempt_label" \
    --github-output "$out" >/dev/null

  assert_kv "$out" "pass" "$expect_pass"
  rm -f "$changed_files" "$version_diff" "$out"
}

# Docs/test-only PR, no src/ change: no bump required, passes.
run_case "tests/orchestrator/test_dispatch.py
docs/design/history/go-v2-design.md" "" false true

# src/ changed, version bumped: passes.
run_case "src/worktrail/orchestrator/dispatch.py" \
  '-version = "0.8.2"
+version = "0.8.3"' false true

# src/ changed, version NOT bumped, no exemption: fails.
run_case "src/worktrail/orchestrator/dispatch.py" "" false false

# src/ changed, version NOT bumped, exemption label present: passes.
run_case "src/worktrail/orchestrator/dispatch.py" "" true true

echo "version bump check: OK"
