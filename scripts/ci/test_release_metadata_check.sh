#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECK="${ROOT}/scripts/ci/release_metadata_check.sh"

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
  local version_diff_content="$1"
  local plugin_version="$2"
  local expect_is_release="$3"
  local expect_pass="$4"

  local version_diff plugin_manifest out
  version_diff="$(mktemp)"
  plugin_manifest="$(mktemp)"
  out="$(mktemp)"
  printf '%s' "$version_diff_content" > "$version_diff"
  printf '{"version": "%s"}' "$plugin_version" > "$plugin_manifest"

  bash "$CHECK" \
    --version-diff "$version_diff" \
    --plugin-manifest "$plugin_manifest" \
    --github-output "$out" >/dev/null

  assert_kv "$out" "is_release" "$expect_is_release"
  assert_kv "$out" "pass" "$expect_pass"
  rm -f "$version_diff" "$plugin_manifest" "$out"
}

# No pyproject.toml version change: ordinary PR, passes without any bump.
run_case "" "0.8.2" false true

# Version bumped, semver increased, plugin.json in sync: passes.
run_case '-version = "0.8.2"
+version = "0.8.3"' "0.8.3" true true

# Version bumped but decreased: fails.
run_case '-version = "0.8.3"
+version = "0.8.2"' "0.8.2" true false

# Version bumped but plugin.json not updated to match: fails.
run_case '-version = "0.8.2"
+version = "0.8.3"' "0.8.2" true false

# Version bumped to a non-semver value: fails.
run_case '-version = "0.8.2"
+version = "0.8.3-rc1"' "0.8.3-rc1" true false

echo "release metadata check: OK"
