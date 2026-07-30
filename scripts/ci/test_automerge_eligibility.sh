#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ELIG="${ROOT}/scripts/ci/automerge_eligibility.sh"

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
  local has_label="$1"
  local expect_eligible="$2"

  local out
  out="$(mktemp)"
  bash "$ELIG" --github-output "$out" --has-no-automerge-label "$has_label" >/dev/null

  assert_kv "$out" "eligible" "$expect_eligible"
  rm -f "$out"
}

run_case false true
run_case true false

echo "automerge eligibility: OK"
