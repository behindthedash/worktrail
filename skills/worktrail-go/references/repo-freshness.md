# Repo-Resolution Staleness Guard

`resolve_repo.py` decides *which* checkout on disk `$REPO` is, by pure file inspection —
it has no way to know that checkout is missing commits already on the remote. A stale
local checkout doesn't just look "old"; it can be missing files entirely.
`policy.py` reads `docs/specs/go-policy.yaml` off disk and reports "no policy configured"
when the file doesn't exist locally — indistinguishable from "this repo genuinely has no
policy file" versus "the policy file was added upstream and this checkout hasn't pulled it
yet." Incident: `kudera-consulting`'s local checkout was one commit behind `origin/main`,
that commit added `go-policy.yaml`, and `policy.py` silently fell back to defaults instead
of surfacing "a policy exists upstream, pull first." Across a 16+ repo workspace, a stale
checkout is a systemic way for `/go` to file briefs against phantom gaps.

Run right after Phase 3 resolves `$REPO`:

```bash
FRESHNESS=$(worktrail-check-repo-freshness --repo "$REPO" --json 2>/dev/null)
STALE=$(echo "$FRESHNESS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('stale', False))" 2>/dev/null)
if [ "$STALE" = "True" ]; then
  WARNING=$(echo "$FRESHNESS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('warning') or '')")
  echo "⚠️  $REPO: $WARNING" >&2
fi
```

Surface the warning to the user alongside Phase 4's policy warnings — a stale repo and an
empty policy together mean "pull before trusting this," not "this repo has no policy."

**What the check does.** Fetches `$REPO`'s checked-out branch from `origin` (best-effort,
`--no-fetch` to skip the network round-trip and compare against whatever remote-tracking
ref is already cached) and compares `HEAD` against `origin/<branch>` via
`git rev-list --left-right --count`. `behind > 0` is stale; `ahead > 0` alone (unpushed
local commits) is not.

**Best-effort, never a hard dependency.** `checked: false` (not a git repo, detached HEAD,
offline, unreachable remote, unknown branch) means "no signal" — treat it as silently
unknown, not as fresh. Never block dispatch on this check; it's a nudge to pull, not a
gate.
