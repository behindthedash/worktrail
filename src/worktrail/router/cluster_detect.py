#!/usr/bin/env python3
"""
Cluster signal extraction and pairwise Signal Match computation for the go
skill's Consume-time cluster detection (see the `duplicate-brief-detection`
change/spec).

Extracts a Cluster Signal (repo, target-spec, target-task, related-ID list,
blocked-by-ID list, descriptive slug, focus-text tokens) from each queued
brief and computes pairwise Signal Matches between briefs across four signal
types: duplicate-slug, same-target-spec, related-link, focus-overlap. A
`blocked-by` relationship between a pair excludes it from every signal
(including duplicate-slug, which is otherwise repo-independent);
same-target-spec, related-link, and focus-overlap additionally require both
briefs to share a non-null `repo`.

A fifth signal, `target-task-match`, connects a single brief to a synthetic
task node (rather than to another brief) when the brief carries both `repo`
and `target-spec` and its focus text overlaps an open, unchecked task in
that target change at/above `OVERLAP_THRESHOLD` (see `_target_task_edges`).
Two briefs that independently match the same task are thereby folded into
one connected component via that shared task node, with no special-case
"two briefs, one task" logic needed — the existing connected-component
assembly below does it for free. Computing this signal requires a caller-
injected `task_candidates_fn` (see `compute_clusters`); omitting it computes
none of these edges, preserving prior behavior exactly.

Cluster assembly (connected components over the Signal Match edges) is
implemented here too; every assembled component is surfaced, size 2
included — 2-member components pass under the same per-signal edge
thresholds as larger ones (full-recall decision, 2026-08-13; no
near-identical bar).
The public `compute_clusters()` entry point wires queued briefs end-to-end
through extraction/matching/assembly/filtering, skipping any unreadable
brief and degrading to `[]` on any unexpected failure — the whole module
performs no filesystem writes.

`_verify_same_work()` is the one exception to "no network calls": the
duplicate-brief-detection change's LLM verification gate, which shells out to
whichever headless agent CLI `router/policy.py` resolves (`claude`, `codex`,
`opencode`) with a single-turn, read-only, non-worktree prompt asking whether
two briefs' focus text describes the same underlying work. It never raises —
any resolution/invocation/parsing failure is a `None` ("not verified")
verdict, so the rest of the module's fail-open posture is unaffected.

`parse_frontmatter` is injected by the caller (dashboard.py's already-resolved
loader-backed `_parse_fm`) rather than reimplemented here, so this module
contains no frontmatter-parsing logic and no cross-skill import into
`handoff/scripts/`.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .policy import VALID_AGENT_CLIS, load_policy

# Leading YYYYMMDD-HHMMSS- timestamp prefix used by queued-brief filenames.
_SLUG_PREFIX_RE = re.compile(r"^\d{8}-\d{6}-")

# Overlap-coefficient floor for a focus-overlap Signal Match — mirrors
# score_candidates.py's BATCH_MIN (Consume-time companion floor).
OVERLAP_THRESHOLD = 0.45

# Lower overlap-coefficient floor, below OVERLAP_THRESHOLD, at/above which a
# size-2, null-vs-null focus-overlap candidate becomes eligible for LLM
# verification (see duplicate-brief-detection design.md decision D3). Chosen
# to include the motivating PR #93 pair (0.44) with headroom, without
# inviting near-zero-overlap pairs into an LLM call. (The band's upper bound
# was NEAR_IDENTICAL_THRESHOLD (0.50) until the 2026-08-13 full-recall
# decision removed the size-2 near-identical bar; pairs at/above
# OVERLAP_THRESHOLD now form an ordinary edge and surface directly.)
LLM_GATE_FLOOR = 0.35


def _slug(filename: str) -> str:
    """Strip the leading YYYYMMDD-HHMMSS- timestamp prefix, if present.

    A filename without a timestamp-shaped prefix is returned unchanged.
    """
    return _SLUG_PREFIX_RE.sub("", filename, count=1)


def _tokenize(text: str) -> set:
    """Return lowercase alphanumeric tokens of length >= 3 from text."""
    if not text:
        return set()
    return {t.lower() for t in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", text)}


def _overlap_coefficient(a: set, b: set) -> float:
    """Overlap coefficient: |A ∩ B| / min(|A|, |B|). 0.0 if either set is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _normalize_repo(val: Any) -> str | None:
    """Return None for absent/null repos so they never match each other."""
    if val is None or val in ("null", "~", ""):
        return None
    return str(val)


def _coerce_id_list(val: Any) -> list[str]:
    """Normalize a frontmatter list field that may come back as a list or a
    single bare string, depending on the injected parser's handling."""
    if isinstance(val, list):
        return [str(x) for x in val if x]
    if isinstance(val, str) and val:
        return [val]
    return []


def _id_matches(identifier: str, stem: str) -> bool:
    """True if identifier resolves to this brief stem (mirrors resolve() forms)."""
    if not identifier:
        return False
    if identifier in (stem + ".md", stem):
        return True
    if stem.startswith(identifier):
        return True
    if stem.endswith(identifier):
        return True
    return False


def _extract_signal(
    path: Path, parse_frontmatter: Callable[[str], dict[str, Any]]
) -> dict[str, Any] | None:
    """Extract a Cluster Signal from a queued brief.

    Returns a dict with `repo`, `target_spec`, `target_task`, `related`,
    `blocked_by`, `slug`, and `focus_tokens` (plus `path`/`stem` for
    pair-matching), or None if the brief is unreadable or its frontmatter
    can't be parsed/is empty.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        fm = parse_frontmatter(text)
    except Exception:
        return None
    if not fm:
        return None
    return {
        "path": path,
        "stem": path.stem,
        "repo": _normalize_repo(fm.get("repo")),
        "target_spec": _normalize_repo(fm.get("target-spec")),
        "target_task": _normalize_repo(fm.get("target-task")),
        "related": _coerce_id_list(fm.get("related")),
        "blocked_by": _coerce_id_list(fm.get("blocked-by")),
        "slug": _slug(path.stem),
        "focus_text": str(fm.get("focus") or ""),
        "focus_tokens": _tokenize(str(fm.get("focus") or "")),
    }


def _is_blocked_by_pair(sig_a: dict[str, Any], sig_b: dict[str, Any]) -> bool:
    """True if either signal's blocked-by list points at the other's stem."""
    for dep in sig_a["blocked_by"]:
        if _id_matches(dep, sig_b["stem"]):
            return True
    for dep in sig_b["blocked_by"]:
        if _id_matches(dep, sig_a["stem"]):
            return True
    return False


def _signal_matches(
    sig_a: dict[str, Any], sig_b: dict[str, Any]
) -> list[tuple[str, float | None]]:
    """Compute Signal Matches between two Cluster Signals.

    Returns a list of (signal_type, score) tuples: score is None for
    non-scored signals (duplicate-slug, same-target-spec, related-link) and
    the overlap coefficient (float) for focus-overlap. Empty if the pair is
    excluded by a blocked-by relationship (checked first, before any other
    signal, so it overrides even the repo-independent duplicate-slug match).

    same-target-spec, related-link, and focus-overlap are repo-scoped: they
    require both briefs to carry the same repo, where a pair of null repos
    counts as the same repo (a mismatch is one null and one non-null, or two
    different non-null values). duplicate-slug is unconditional on repo.
    """
    if _is_blocked_by_pair(sig_a, sig_b):
        return []

    matches: list[tuple[str, float | None]] = []

    if sig_a["slug"] and sig_a["slug"] == sig_b["slug"]:
        matches.append(("duplicate-slug", None))

    repo_a = sig_a["repo"]
    repo_b = sig_b["repo"]
    # A pair where both repos are null is treated as same-repo here too (per
    # duplicate-brief-detection design.md), since a null repo means "not yet
    # linked to a target repo" rather than "known to differ" — only an
    # actual repo mismatch (one null, one set, or two different non-null
    # values) excludes the pair.
    same_repo = repo_a == repo_b
    if not same_repo:
        return matches

    if (
        sig_a["target_spec"] is not None
        and sig_a["target_spec"] == sig_b["target_spec"]
    ):
        matches.append(("same-target-spec", None))

    related = any(_id_matches(rid, sig_b["stem"]) for rid in sig_a["related"]) or any(
        _id_matches(rid, sig_a["stem"]) for rid in sig_b["related"]
    )
    if related:
        matches.append(("related-link", None))

    overlap = _overlap_coefficient(sig_a["focus_tokens"], sig_b["focus_tokens"])
    if overlap >= OVERLAP_THRESHOLD:
        matches.append(("focus-overlap", overlap))

    return matches


_Edge = tuple[str, str, list[tuple[str, float | None]]]


def _target_task_edges(
    signals: list[dict[str, Any]],
    task_candidates_fn: Callable[[str, str], list[dict[str, Any]]] | None,
) -> list[_Edge]:
    """Compute brief-vs-task `target-task-match` edges (Dashboard Advisory
    Surfaces Brief-vs-Task Matches).

    For every Cluster Signal carrying both a non-null `repo` and
    `target_spec`, looks up that target change's open, unchecked task
    candidates via the caller-injected `task_candidates_fn(repo,
    target_spec)` — the 1.1 per-task enumeration
    (`overlap_check.task_candidates`), resolved to a concrete repo/specs-root
    by the caller the same way `parse_frontmatter` is caller-injected, so
    this module does no cross-repo filesystem lookup or format detection of
    its own. Results are cached per `(repo, target_spec)` pair since several
    briefs commonly share a target change.

    Adds one edge per task whose `task_text` overlaps the brief's focus text
    at/above `OVERLAP_THRESHOLD`, connecting the brief's stem to a synthetic,
    repo-scoped task node id (`task::<repo>::<target_spec>::<task_id>` —
    scoped so two different repos' changes never collide on a shared
    target-spec/task-id pair, mirroring the repo-scoping every other
    cross-repo signal in this module already enforces). A brief whose own
    `target_task` already names the matched task is skipped for that task —
    the link is already explicit, no advisory needed.

    Candidate entries without a `task_id` (the whole-spec/whole-change
    fallback shape `task_candidates` returns for a non-OpenSpec or
    unresolved target) are ignored, since only real per-task entries
    participate in this signal. `task_candidates_fn` is optional; omitting
    it (the default) returns no edges, preserving prior behavior exactly.
    Never raises: a lookup failure for one `(repo, target_spec)` pair
    degrades to no task edges for that pair, matching this module's
    fail-open posture elsewhere.
    """
    if task_candidates_fn is None:
        return []

    edges: list[_Edge] = []
    cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sig in signals:
        repo = sig["repo"]
        target = sig["target_spec"]
        if repo is None or target is None:
            continue
        key = (repo, target)
        if key not in cache:
            try:
                cache[key] = task_candidates_fn(repo, target)
            except Exception:  # noqa: BLE001 — best-effort, never break clustering
                cache[key] = []
        for entry in cache[key]:
            task_id = entry.get("task_id")
            if not task_id:
                continue  # whole-spec/whole-change fallback candidate, not a task
            if sig["target_task"] is not None and sig["target_task"] == task_id:
                continue  # brief already explicitly names this task
            overlap = _overlap_coefficient(
                sig["focus_tokens"], _tokenize(str(entry.get("task_text") or ""))
            )
            if overlap < OVERLAP_THRESHOLD:
                continue
            node_id = f"task::{repo}::{target}::{task_id}"
            edges.append((sig["stem"], node_id, [("target-task-match", overlap)]))
    return edges


# LLM verification gate (duplicate-brief-detection design.md D3/D4/D5): asks
# a headless agent CLI whether two briefs' focus text describes the same
# underlying work. Per-agent invocation mirrors spawnlib.py's `build_cmd()`
# binary/subcommand choice, but as a plain single-turn text prompt rather
# than that function's worktree-oriented stream-json/permission-bypass
# flags (D4: "non-worktree, non-git invocation" — this call reads no files
# and writes nothing, so codex additionally gets `-s read-only`).

# Wall-clock budget for the verification subprocess call (D3). A hang here
# must never stall compute_clusters()'s dashboard-render path, so a timeout
# is treated the same as every other fail-open outcome below: a None verdict.
_VERIFY_TIMEOUT_SECONDS = 10
_AGENT_VERIFY_CMD: dict[str, Callable[[str], list[str]]] = {
    "claude": lambda prompt: ["claude", "-p", prompt],
    "codex": lambda prompt: ["codex", "exec", "-s", "read-only", prompt],
    "opencode": lambda prompt: ["opencode", "run", prompt],
}

_VERDICT_RE = re.compile(r"^\s*(yes|no)\b", re.IGNORECASE)


def _resolve_verification_agent_cli(repo_root: Path | None) -> str | None:
    """Resolve the headless agent CLI for the LLM verification gate.

    Mirrors dashboard.py's `_planned_agent_for_item()`: `router/policy.py`'s
    `load_policy(repo_root)["agent_cli"]`, which defaults to `None` when
    unconfigured (policy.py's own default — not a "claude" fallback), since
    an unset `agent_cli` is a legitimate "no verification available" outcome
    (D5), not an error. `repo_root` is caller-injected the same way
    `parse_frontmatter` is; `None` (no repo context available) also resolves
    to unconfigured. Any policy-load failure is swallowed, matching
    dashboard.py's own `_load_dashboard_policy()`.
    """
    if repo_root is None:
        return None
    try:
        agent_cli = load_policy(Path(repo_root)).get("agent_cli")
    except Exception:  # noqa: BLE001 — unconfigured, not a crash
        return None
    return agent_cli if agent_cli in VALID_AGENT_CLIS else None


def _verification_prompt(focus_a: str, focus_b: str) -> str:
    """Build the LLM verification gate's single-turn prompt (D4)."""
    return (
        "Two queued work briefs matched on a weak text-overlap signal. Based "
        "only on the focus text below, do they describe the SAME underlying "
        "work (a likely duplicate)? Reply with exactly one line: the single "
        "word YES or NO, optionally followed by a colon and a one-sentence "
        "reason. Do not use any tools; do not write more than one line.\n\n"
        f"Brief A focus:\n{focus_a}\n\nBrief B focus:\n{focus_b}\n"
    )


def _parse_verification_verdict(output: str) -> bool | None:
    """Parse a yes/no verdict from the verification call's stdout.

    Returns True/False for the first non-empty line starting with YES/NO
    (case-insensitive); None if the output is empty or that line doesn't
    start with either word.
    """
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = _VERDICT_RE.match(line)
        return match.group(1).lower() == "yes" if match else None
    return None


def _verify_same_work(
    focus_a: str,
    focus_b: str,
    *,
    repo_root: Path | None = None,
    agent_cli: str | None = None,
) -> bool | None:
    """LLM verification gate: ask the configured headless agent CLI whether
    two briefs' focus text describes the same underlying work.

    `agent_cli` overrides the policy-resolved default
    (`_resolve_verification_agent_cli`) — primarily for tests. Returns
    True/False for a parsed verdict, or None when no agent CLI is
    configured/recognized, the call times out (`_VERIFY_TIMEOUT_SECONDS`),
    exits non-zero, or its output can't be parsed as a verdict — callers
    must treat None as "not verified" (fail-open, D5). Never raises, so one
    candidate pair's verification failure can't suppress unrelated clusters
    found elsewhere in the same `compute_clusters()` scan.
    """
    resolved = agent_cli or _resolve_verification_agent_cli(repo_root)
    build_cmd = _AGENT_VERIFY_CMD.get(resolved) if resolved else None
    if build_cmd is None:
        return None
    try:
        proc = subprocess.run(
            build_cmd(_verification_prompt(focus_a, focus_b)),
            capture_output=True,
            text=True,
            timeout=_VERIFY_TIMEOUT_SECONDS,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _parse_verification_verdict(proc.stdout)


def _llm_gate_score(sig_a: dict[str, Any], sig_b: dict[str, Any]) -> float | None:
    """Return the focus-overlap coefficient if this pair falls in the LLM
    verification gate's band (design.md D3): both `repo` null, not
    blocked-by-excluded, and overlap in `[LLM_GATE_FLOOR,
    OVERLAP_THRESHOLD)`. `None` if any of those don't hold. The band sits
    entirely below `OVERLAP_THRESHOLD`, so it covers exactly the pairs
    `_signal_matches` does not connect with a focus-overlap edge (the
    motivating PR #93 case, at 0.44); pairs at/above the threshold form an
    ordinary edge and surface without LLM involvement.
    """
    if sig_a["repo"] is not None or sig_b["repo"] is not None:
        return None
    if _is_blocked_by_pair(sig_a, sig_b):
        return None
    overlap = _overlap_coefficient(sig_a["focus_tokens"], sig_b["focus_tokens"])
    if LLM_GATE_FLOOR <= overlap < OVERLAP_THRESHOLD:
        return overlap
    return None


def _llm_gate_clusters(
    signals: list[dict[str, Any]],
    components: list[dict[str, Any]],
    *,
    repo_root: Path | None,
    agent_cli: str | None,
) -> list[dict[str, Any]]:
    """Run the LLM verification gate over every size-2, null-vs-null
    candidate pair in the gate band and return surfaced-shape cluster dicts
    for the ones the LLM confirms describe the same underlying work
    (design.md D3).

    `components` is `_connected_components`'s output, used only to check
    that neither brief of a candidate pair already belongs to an assembled
    component: every component is surfaced outright (no size-2 bar), so a
    member of one never needs — and must never get — LLM promotion into a
    second, overlapping cluster. The gate is thereby scoped to
    component-free pairs whose only connection is sub-threshold focus
    overlap.
    """
    clustered_members = {member for comp in components for member in comp["members"]}
    gate_clusters: list[dict[str, Any]] = []
    for i in range(len(signals)):
        for j in range(i + 1, len(signals)):
            sig_a, sig_b = signals[i], signals[j]
            if _llm_gate_score(sig_a, sig_b) is None:
                continue
            stem_a, stem_b = sig_a["stem"], sig_b["stem"]
            if stem_a in clustered_members or stem_b in clustered_members:
                continue  # already surfaced via a component, or belongs elsewhere

            verdict = _verify_same_work(
                sig_a["focus_text"],
                sig_b["focus_text"],
                repo_root=repo_root,
                agent_cli=agent_cli,
            )
            if not verdict:
                continue
            gate_clusters.append(
                {
                    "members": sorted((stem_a, stem_b)),
                    "signals": ["focus-overlap"],
                    "size": 2,
                }
            )
    return gate_clusters


def _connected_components(edges: list[_Edge]) -> list[dict[str, Any]]:
    """Assemble Signal Match edges into connected components via union-find.

    Any edge with a non-empty matches list connects its two ids into the same
    component, regardless of signal type or score — connectivity does not
    distinguish signal types (REQ-011). Edges with an empty matches list (no
    Signal Match) are ignored. Every component is surfaced, size 2 included
    (full-recall decision, 2026-08-13): a component always has >= 2 members
    (it requires at least one edge), and no near-identical bar applies.

    Returns one surfaced-shape dict per component with `members` (sorted
    ids), `signals` (sorted, de-duplicated labels of every edge within the
    component), and `size`.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    active_edges = [e for e in edges if e[2]]
    for id_a, id_b, _matches in active_edges:
        union(id_a, id_b)

    members_by_root: dict[str, set] = {}
    signals_by_root: dict[str, set] = {}
    for id_a, id_b, matches in active_edges:
        root = find(id_a)
        members_by_root.setdefault(root, set()).update((id_a, id_b))
        signals_by_root.setdefault(root, set()).update(label for label, _ in matches)

    components = [
        {
            "members": sorted(members),
            "signals": sorted(signals_by_root[root]),
            "size": len(members),
        }
        for root, members in members_by_root.items()
    ]
    components.sort(key=lambda c: c["members"])
    return components


def _assemble_clusters(
    edges: list[_Edge],
    *,
    signals: list[dict[str, Any]] | None = None,
    repo_root: Path | None = None,
    agent_cli: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble Signal Match edges into surfaced clusters via
    connected-component assembly (`_connected_components`); every assembled
    component is surfaced. An empty `edges` list yields an empty cluster
    list.

    `signals` (the original Cluster Signals the edges were computed from) is
    optional and, when supplied, additionally runs the LLM verification gate
    (`_llm_gate_clusters`, design.md D3) over the same-named candidates and
    merges any it confirms into the surfaced list, deduplicated by member
    set. Omitting `signals` (the default) preserves the pre-gate behavior
    exactly — no LLM calls, gate-only candidates never surfaced.
    """
    components = _connected_components(edges)
    reportable = list(components)
    if signals:
        existing = {tuple(c["members"]) for c in reportable}
        for gated in _llm_gate_clusters(
            signals, components, repo_root=repo_root, agent_cli=agent_cli
        ):
            key = tuple(gated["members"])
            if key not in existing:
                reportable.append(gated)
                existing.add(key)
        reportable.sort(key=lambda c: c["members"])
    return reportable


def _compute_clusters_inner(
    queue_dir: Path,
    parse_frontmatter: Callable[[str], dict[str, Any]],
    *,
    repo_root: Path | None,
    agent_cli: str | None,
    task_candidates_fn: Callable[[str, str], list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    """Unguarded body of `compute_clusters` (see there for the public
    contract). Split out so the broad exception handler wraps the whole
    computation, not just this function's own logic."""
    queue_dir = Path(queue_dir)
    if not queue_dir.is_dir():
        return []

    signals = []
    for path in sorted(queue_dir.glob("*.md")):
        sig = _extract_signal(path, parse_frontmatter)
        if sig is not None:
            signals.append(sig)

    edges: list[_Edge] = []
    for i in range(len(signals)):
        for j in range(i + 1, len(signals)):
            sig_a, sig_b = signals[i], signals[j]
            matches = _signal_matches(sig_a, sig_b)
            edges.append((sig_a["stem"], sig_b["stem"], matches))
    edges.extend(_target_task_edges(signals, task_candidates_fn))

    return _assemble_clusters(
        edges, signals=signals, repo_root=repo_root, agent_cli=agent_cli
    )


def compute_clusters(
    queue_dir: Path,
    parse_frontmatter: Callable[[str], dict[str, Any]],
    *,
    repo_root: Path | None = None,
    agent_cli: str | None = None,
    task_candidates_fn: Callable[[str, str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Public entry point: scan `queue_dir` for queued briefs, extract Cluster
    Signals (skipping any brief whose frontmatter can't be read or parsed),
    compute pairwise Signal Matches, and assemble/filter them into the
    surfaced cluster list.

    Returns `[]` if `queue_dir` doesn't exist or contains no `.md` files.
    The whole computation is wrapped in a broad exception handler: this
    module is a read-only, defense-in-depth "never crash the dashboard
    render" boundary (mirrors dashboard.py's `_safe_detect_stage`), so any
    unexpected failure anywhere in extraction/matching/assembly degrades to
    an empty cluster list rather than propagating.

    `repo_root` and `agent_cli` are optional and feed the LLM verification
    gate (design.md D3/D4): `repo_root` lets it resolve the configured
    headless agent CLI via `router/policy.py` the same way `parse_frontmatter`
    is caller-injected; `agent_cli` overrides that resolution outright
    (primarily for tests). Neither is required — omitting both simply means
    the gate never has a CLI to call, so gate-band candidates fail open to
    "not verified" (not surfaced), same as any other resolution failure.

    `task_candidates_fn` is optional and feeds the `target-task-match` signal
    (Dashboard Advisory Surfaces Brief-vs-Task Matches): a `(repo,
    target_spec) -> [{task_id, task_text, checked}, ...]` callable — a
    caller-supplied adapter over `overlap_check.task_candidates`, resolving a
    brief's `repo` frontmatter to a concrete specs root the same way
    `parse_frontmatter` is caller-injected — used to match a queued brief's
    focus text against open, unchecked tasks in its `target-spec` change.
    Omitting it (the default) computes no task edges, preserving prior
    behavior exactly. See `_target_task_edges` for the matching rule.
    """
    try:
        return _compute_clusters_inner(
            queue_dir,
            parse_frontmatter,
            repo_root=repo_root,
            agent_cli=agent_cli,
            task_candidates_fn=task_candidates_fn,
        )
    except Exception:  # noqa: BLE001 — degrade, never crash the dashboard render
        return []
