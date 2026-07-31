#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  version_bump_check.sh --changed-files <path> --version-diff <path> \
    --has-exempt-label <true|false> --github-output <path>

Inputs:
  --changed-files   Path to a file with one changed path per line
                     (e.g. `git diff --name-only <base>...<head>`).
  --version-diff    Path to a file with the `git diff <base>...<head> --
                     pyproject.toml` output (empty file if pyproject.toml
                     did not change).
  --has-exempt-label  "true" if the PR carries the go:no-version-bump label.

Outputs (written to --github-output):
  required=true|false   whether src/worktrail/** changed
  bumped=true|false     whether pyproject.toml's version line changed
  exempt=true|false     echoes --has-exempt-label
  pass=true|false       required=false, OR bumped=true, OR exempt=true
  reason=<text>
EOF
}

changed_files=""
version_diff=""
has_exempt_label=""
github_output=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --changed-files)
      changed_files="${2:-}"
      shift 2
      ;;
    --version-diff)
      version_diff="${2:-}"
      shift 2
      ;;
    --has-exempt-label)
      has_exempt_label="${2:-}"
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

if [ -z "$changed_files" ] || [ -z "$version_diff" ] || [ -z "$has_exempt_label" ] || [ -z "$github_output" ]; then
  echo "Missing required args." >&2
  usage >&2
  exit 2
fi

required="false"
if grep -q '^src/worktrail/' "$changed_files"; then
  required="true"
fi

bumped="false"
if grep -q '^+version = ' "$version_diff"; then
  bumped="true"
fi

exempt="$has_exempt_label"

pass="false"
if [ "$required" = "false" ] || [ "$bumped" = "true" ] || [ "$exempt" = "true" ]; then
  pass="true"
fi

if [ "$required" = "false" ]; then
  reason="no src/worktrail/** changes -- no bump required"
elif [ "$bumped" = "true" ]; then
  reason="pyproject.toml version bumped"
elif [ "$exempt" = "true" ]; then
  reason="go:no-version-bump label present -- deferred to a future batch bump"
else
  reason="src/worktrail/** changed but pyproject.toml's version line did not, and no go:no-version-bump label is present"
fi

{
  echo "required=$required"
  echo "bumped=$bumped"
  echo "exempt=$exempt"
  echo "pass=$pass"
  echo "reason=$reason"
} >> "$github_output"
