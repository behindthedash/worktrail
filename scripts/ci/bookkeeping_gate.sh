#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bookkeeping_gate.sh --paths-filter-code <true|false> \
    --pyproject-diff <path> --changed-files <path> --github-output <path>

Inputs:
  --paths-filter-code  The dorny/paths-filter `code` output: "true" means a
                        non-doc/openspec/md path changed.
  --pyproject-diff     Path to a `git diff -- pyproject.toml` capture
                        (empty file if pyproject.toml did not change).
  --changed-files       Path to a `git diff --name-only` capture listing every
                        path changed in the diff, one per line (empty file if
                        nothing changed). Used to confirm that pyproject.toml's
                        version-only diff isn't riding alongside an unrelated
                        code change.
  --github-output      Path to append outputs to.

Outputs (written to --github-output):
  bookkeeping=true|false   true if the diff is docs/openspec/md-only, or is
                            code-only because pyproject.toml (and/or
                            .codex-plugin/plugin.json) is the ONLY changed
                            path outside docs/openspec/md and its version
                            line was bumped with nothing else changed in it.
EOF
}

paths_filter_code=""
pyproject_diff=""
changed_files=""
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
    --changed-files)
      changed_files="${2:-}"
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

if [ -z "$paths_filter_code" ] || [ -z "$pyproject_diff" ] || [ -z "$changed_files" ] || [ -z "$github_output" ]; then
  echo "Missing required args." >&2
  usage >&2
  exit 2
fi

# True if any changed path is neither a doc/openspec/markdown path nor one of
# the two files this repo's version-bump convention touches together
# (pyproject.toml, .codex-plugin/plugin.json). Any such path disqualifies the
# version-bump bookkeeping bypass below, regardless of what pyproject.toml's
# own diff looks like.
other_code_changed="false"
if [ -s "$changed_files" ]; then
  # `|| [ -n "$f" ]` also processes a final line with no trailing newline —
  # `git diff --name-only` always terminates its last line, but the plain
  # `while read` idiom drops an unterminated final line silently otherwise.
  while IFS= read -r f || [ -n "$f" ]; do
    [ -n "$f" ] || continue
    case "$f" in
      openspec/*|docs/*|*.md|pyproject.toml|.codex-plugin/plugin.json) ;;
      *) other_code_changed="true" ;;
    esac
  done < "$changed_files"
fi

bookkeeping="false"

if [ "$paths_filter_code" = "false" ]; then
  bookkeeping="true"
elif [ "$other_code_changed" = "false" ] && [ -s "$pyproject_diff" ]; then
  changed_lines="$(grep -E '^[+-]' "$pyproject_diff" | grep -vE '^(\+\+\+|---) ' || true)"
  if [ -n "$changed_lines" ] && ! printf '%s\n' "$changed_lines" | grep -qvE '^[+-]version = '; then
    bookkeeping="true"
  fi
fi

{
  echo "bookkeeping=$bookkeeping"
} >> "$github_output"
