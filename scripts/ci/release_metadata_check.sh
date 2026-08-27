#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  release_metadata_check.sh --version-diff <path> \
    --plugin-manifest <path> --github-output <path>

Release intent is detected directly from the diff -- no label, no separate
event. If pyproject.toml's `version` line did not change, this PR is
ordinary and passes unconditionally. If it did change, this PR declares
release intent and its metadata is validated: the new version must be valid
semver, must be greater than the base branch's version, and
.codex-plugin/plugin.json's version must match it.

Inputs:
  --version-diff      Path to the `git diff <base>...<head> -- pyproject.toml`
                       output (empty file if pyproject.toml did not change).
  --plugin-manifest    Path to the current checkout's .codex-plugin/plugin.json.

Outputs (written to --github-output):
  is_release=true|false   whether pyproject.toml's version line changed
  pass=true|false
  reason=<text>
EOF
}

version_diff=""
plugin_manifest=""
github_output=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version-diff)
      version_diff="${2:-}"
      shift 2
      ;;
    --plugin-manifest)
      plugin_manifest="${2:-}"
      shift 2
      ;;
    --github-output)
      github_output="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$version_diff" ] || [ -z "$plugin_manifest" ] || [ -z "$github_output" ]; then
  echo "Missing required args." >&2
  usage >&2
  exit 2
fi

# True when v1 > v2, both "X.Y.Z".
version_gt() {
  local IFS=.
  local -a v1=($1) v2=($2)
  local i a b
  for i in 0 1 2; do
    a="${v1[i]:-0}"
    b="${v2[i]:-0}"
    if [ "$a" -gt "$b" ]; then return 0; fi
    if [ "$a" -lt "$b" ]; then return 1; fi
  done
  return 1
}

is_release="false"
if grep -q '^+version = ' "$version_diff"; then
  is_release="true"
fi

pass="false"
reason=""

if [ "$is_release" = "false" ]; then
  pass="true"
  reason="pyproject.toml version unchanged -- ordinary PR, no release validation required"
else
  old_version="$(grep '^-version = ' "$version_diff" | head -n 1 | sed -E 's/^-version = "([^"]*)".*/\1/')"
  new_version="$(grep '^+version = ' "$version_diff" | head -n 1 | sed -E 's/^\+version = "([^"]*)".*/\1/')"
  semver_re='^[0-9]+\.[0-9]+\.[0-9]+$'

  if ! [[ "$new_version" =~ $semver_re ]]; then
    reason="pyproject.toml version '$new_version' is not valid semver (expected X.Y.Z)"
  elif [ -z "$old_version" ]; then
    reason="could not determine the prior version from the pyproject.toml diff"
  elif ! version_gt "$new_version" "$old_version"; then
    reason="pyproject.toml version did not increase: '$old_version' -> '$new_version'"
  else
    plugin_version="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('version', ''))" "$plugin_manifest")"
    if [ "$plugin_version" != "$new_version" ]; then
      reason=".codex-plugin/plugin.json version ('$plugin_version') does not match pyproject.toml ('$new_version')"
    else
      pass="true"
      reason="release metadata valid: version bumped $old_version -> $new_version, .codex-plugin/plugin.json in sync"
    fi
  fi
fi

{
  echo "is_release=$is_release"
  echo "pass=$pass"
  echo "reason=$reason"
} >> "$github_output"
