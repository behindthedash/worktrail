#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  automerge_eligibility.sh --github-output <path> --has-no-automerge-label <true|false>

Outputs (written to --github-output):
  eligible=true|false
  reason=<text>
EOF
}

github_output=""
has_no_automerge_label=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --github-output)
      github_output="${2:-}"
      shift 2
      ;;
    --has-no-automerge-label)
      has_no_automerge_label="${2:-}"
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

if [ -z "$github_output" ] || [ -z "$has_no_automerge_label" ]; then
  echo "Missing required args." >&2
  usage >&2
  exit 2
fi

if [ "$has_no_automerge_label" = "true" ]; then
  eligible="false"
  reason="go:no-automerge enforced"
  echo "PR has go:no-automerge label — skipping auto-merge arm."
else
  eligible="true"
  reason="no go:no-automerge label"
fi

{
  echo "eligible=$eligible"
  echo "reason=$reason"
} >> "$github_output"
