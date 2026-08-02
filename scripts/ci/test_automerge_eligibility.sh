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
  local has_no_automerge="$1"
  local has_risk_label="$2"
  local expect_eligible="$3"

  local out
  out="$(mktemp)"
  bash "$ELIG" \
    --github-output "$out" \
    --has-no-automerge-label "$has_no_automerge" \
    --has-risk-label "$has_risk_label" >/dev/null

  assert_kv "$out" "eligible" "$expect_eligible"
  rm -f "$out"
}

# has_no_automerge  has_risk_label  expect_eligible
run_case false true  true   # normal case: labeled, no block -> eligible
run_case true  true  false  # go:no-automerge still wins even with a risk label
run_case false false false  # NEW: no risk label at all -> fails closed regardless
run_case true  false false  # neither present -> still blocked

echo "automerge eligibility: OK"
