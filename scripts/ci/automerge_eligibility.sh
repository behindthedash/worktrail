#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  automerge_eligibility.sh --github-output <path> \
    --has-no-automerge-label <true|false> \
    --has-risk-label <true|false>

Outputs (written to --github-output):
  eligible=true|false
  reason=<text>

Notes:
  --has-risk-label is a POSITIVE requirement (go:risk-low or go:risk-medium
  present): a PR with no risk label at all -- e.g. one created directly via
  `gh pr create` outside the classifier pipeline -- fails closed and is
  never eligible, regardless of --has-no-automerge-label. Verified live
  2026-08-01: every hand-created PR that day merged with zero labels because
  the prior check only looked for the ABSENCE of go:no-automerge, so
  max_risk policy provided no actual protection for PRs the classifier
  never touched.
EOF
}

github_output=""
has_no_automerge_label=""
has_risk_label=""

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
    --has-risk-label)
      has_risk_label="${2:-}"
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

if [ -z "$github_output" ] || [ -z "$has_no_automerge_label" ] || [ -z "$has_risk_label" ]; then
  echo "Missing required args." >&2
  usage >&2
  exit 2
fi

if [ "$has_risk_label" != "true" ]; then
  eligible="false"
  reason="no go:risk-low/medium label present"
  echo "PR has no go:risk-low/medium label — skipping auto-merge arm (fail closed on unlabeled PRs)."
elif [ "$has_no_automerge_label" = "true" ]; then
  eligible="false"
  reason="go:no-automerge enforced"
  echo "PR has go:no-automerge label — skipping auto-merge arm."
else
  eligible="true"
  reason="risk label present, no go:no-automerge"
fi

{
  echo "eligible=$eligible"
  echo "reason=$reason"
} >> "$github_output"
