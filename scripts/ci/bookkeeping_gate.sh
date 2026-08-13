#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bookkeeping_gate.sh --paths-filter-code <true|false> \
    --pyproject-diff <path> --github-output <path>

Inputs:
  --paths-filter-code  The dorny/paths-filter `code` output: "true" means a
                        non-doc/openspec/md path changed.
  --pyproject-diff     Path to a `git diff -- pyproject.toml` capture
                        (empty file if pyproject.toml did not change).
  --github-output      Path to append outputs to.

Outputs (written to --github-output):
  bookkeeping=true|false   true if the diff is docs/openspec/md-only, or is
                            code-only because pyproject.toml's version line
                            was bumped and nothing else in it changed.
EOF
}

paths_filter_code=""
pyproject_diff=""
github_output=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --paths-filter-code)
      paths_filter_code="${2:-}"
      shift 2
      ;;
    --pyproject-diff)
      pyproject_diff="${2:-}"
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

if [ -z "$paths_filter_code" ] || [ -z "$pyproject_diff" ] || [ -z "$github_output" ]; then
  echo "Missing required args." >&2
  usage >&2
  exit 2
fi

bookkeeping="false"

if [ "$paths_filter_code" = "false" ]; then
  bookkeeping="true"
elif [ -s "$pyproject_diff" ]; then
  changed_lines="$(grep -E '^[+-]' "$pyproject_diff" | grep -vE '^(\+\+\+|---) ' || true)"
  if [ -n "$changed_lines" ] && ! printf '%s\n' "$changed_lines" | grep -qvE '^[+-]version = '; then
    bookkeeping="true"
  fi
fi

{
  echo "bookkeeping=$bookkeeping"
} >> "$github_output"
