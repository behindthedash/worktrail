#!/usr/bin/env python3
"""
Cluster signal extraction and pairwise Signal Match computation for the go
skill's Consume-time cluster detection (spec 018).

Extracts a Cluster Signal (repo, target-spec, related-ID list, blocked-by-ID
list, descriptive slug, focus-text tokens) from each queued brief and computes
pairwise Signal Matches between briefs across four signal types:
duplicate-slug, same-target-spec, related-link, focus-overlap. A `blocked-by`
relationship between a pair excludes it from every signal (including
duplicate-slug, which is otherwise repo-independent); same-target-spec,
related-link, and focus-overlap additionally require both briefs to share a
non-null `repo`.

Cluster assembly (connected components over the Signal Match edges) and the
reporting-threshold filter that decides which components are surfaced (size
>= 3 always; size == 2 only when near-identical) are implemented here too.
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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .policy import VALID_AGENT_CLIS, load_policy

# Leading YYYYMMDD-HHMMSS- timestamp prefix used by queued-brief filenames.
_SLUG_PREFIX_RE = re.compile(r"^\d{8}-\d{6}-")

# Overlap-coefficient floor for a focus-overlap Signal Match — mirrors
# score_candidates.py's BATCH_MIN (Consume-time companion floor).
OVERLAP_THRESHOLD = 0.45

# Stricter overlap-coefficient floor a size-2 component's focus-overlap edge
# must clear to be treated as near-identical (and thus surfaced) rather than
# an ordinary match (REQ-013). Lowered from 0.75 per duplicate-brief-detection
# design.md decision D2: a meaningful drop without collapsing onto
# OVERLAP_THRESHOLD (0.45), which would eliminate the size-2 extra-scrutiny
# concept entirely rather than loosening it.
NEAR_IDENTICAL_THRESHOLD = 0.50

# Lower overlap-coefficient floor, below NEAR_IDENTICAL_THRESHOLD, at/above
# which a size-2, null-vs-null focus-overlap candidate becomes eligible for
# LLM verification (see duplicate-brief-detection design.md decision D3).
# Chosen to include the motivating PR #93 pair (0.44) with headroom, without
# inviting near-zero-overlap pairs into an LLM call.
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


def _normalize_repo(val: Any) -> Optional[str]:
    """Return None for absent/null repos so they never match each other."""
    if val is None or val in ("null", "~", ""):
        return None
    return str(val)


def _coerce_id_list(val: Any) -> List[str]:
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
    path: Path, parse_frontmatter: Callable[[str], Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Extract a Cluster Signal from a queued brief.

    Returns a dict with `repo`, `target_spec`, `related`, `blocked_by`,
    `slug`, and `focus_tokens` (plus `path`/`stem` for pair-matching), or None
    if the brief is unreadable or its frontmatter can't be parsed/is empty.
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
        "related": _coerce_id_list(fm.get("related")),
        "blocked_by": _coerce_id_list(fm.get("blocked-by")),
        "slug": _slug(path.stem),
        "focus_text": str(fm.get("focus") or ""),
        "focus_tokens": _tokenize(str(fm.get("focus") or "")),
    }


def _is_blocked_by_pair(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> bool:
    """True if either signal's blocked-by list points at the other's stem."""
    for dep in sig_a["blocked_by"]:
        if _id_matches(dep, sig_b["stem"]):
            return True
    for dep in sig_b["blocked_by"]:
        if _id_matches(dep, sig_a["stem"]):
            return True
    return False


def _signal_matches(
    sig_a: Dict[str, Any], sig_b: Dict[str, Any]
) -> List[Tuple[str, Optional[float]]]:
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

    matches: List[Tuple[str, Optional[float]]] = []

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

    if sig_a["target_spec"] is not None and sig_a["target_spec"] == sig_b["target_spec"]:
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


# LLM verification gate (duplicate-brief-detection design.md D3/D4/D5): asks
# a headless agent CLI whether two briefs' focus text describes the same
# underlying work. Per-agent invocation mirrors spawnlib.py's `build_cmd()`
# binary/subcommand choice, but as a plain single-turn text prompt rather
# than that function's worktree-oriented stream-json/permission-bypass
# flags (D4: "non-worktree, non-git invocation" — this call reads no files
# and writes nothing, so codex additionally gets `-s read-only`).
_AGENT_VERIFY_CMD: Dict[str, Callable[[str], List[str]]] = {
    "claude": lambda prompt: ["claude", "-p", prompt],
    "codex": lambda prompt: ["codex", "exec", "-s", "read-only", prompt],
    "opencode": lambda prompt: ["opencode", "run", prompt],
}

_VERDICT_RE = re.compile(r"^\s*(yes|no)\b", re.IGNORECASE)


def _resolve_verification_agent_cli(repo_root: Optional[Path]) -> Optional[str]:
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


def _parse_verification_verdict(output: str) -> Optional[bool]:
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
    repo_root: Optional[Path] = None,
    agent_cli: Optional[str] = None,
) -> Optional[bool]:
    """LLM verification gate: ask the configured headless agent CLI whether
    two briefs' focus text describes the same underlying work.

    `agent_cli` overrides the policy-resolved default
    (`_resolve_verification_agent_cli`) — primarily for tests. Returns
    True/False for a parsed verdict, or None when no agent CLI is
    configured/recognized, the call fails, or its output can't be parsed as
    a verdict — callers must treat None as "not verified" (fail-open, D5).
    Never raises.
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
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_verification_verdict(proc.stdout)


def _llm_gate_score(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> Optional[float]:
    """Return the focus-overlap coefficient if this pair falls in the LLM
    verification gate's band (design.md D3): both `repo` null, not
    blocked-by-excluded, and overlap in `[LLM_GATE_FLOOR,
    NEAR_IDENTICAL_THRESHOLD)`. `None` if any of those don't hold. This band
    starts below `OVERLAP_THRESHOLD`, so it deliberately overlaps pairs that
    `_signal_matches` does not itself connect with a focus-overlap edge
    (the motivating PR #93 case, at 0.44) as well as ones it does
    (0.45 to <0.50).
    """
    if sig_a["repo"] is not None or sig_b["repo"] is not None:
        return None
    if _is_blocked_by_pair(sig_a, sig_b):
        return None
    overlap = _overlap_coefficient(sig_a["focus_tokens"], sig_b["focus_tokens"])
    if LLM_GATE_FLOOR <= overlap < NEAR_IDENTICAL_THRESHOLD:
        return overlap
    return None


def _llm_gate_clusters(
    signals: List[Dict[str, Any]],
    components: List[Dict[str, Any]],
    *,
    repo_root: Optional[Path],
    agent_cli: Optional[str],
) -> List[Dict[str, Any]]:
    """Run the LLM verification gate over every size-2, null-vs-null
    candidate pair in the gate band and return surfaced-shape cluster dicts
    for the ones the LLM confirms describe the same underlying work
    (design.md D3).

    `components` is `_connected_components`'s raw (pre-filter) output, used
    only to check that a candidate pair isn't already folded into a larger
    or already-qualifying structure: a pair is only gated when neither brief
    already belongs to a component with a third member, and (for a pair that
    already forms an ordinary focus-overlap edge in the 0.45-0.50 sub-band)
    that component isn't already near-identical by some other edge. This
    keeps the gate scoped to genuine size-2 candidates, never promoting a
    member of an already-decided cluster.
    """
    member_component: Dict[str, int] = {
        member: idx for idx, comp in enumerate(components) for member in comp["members"]
    }
    gate_clusters: List[Dict[str, Any]] = []
    for i in range(len(signals)):
        for j in range(i + 1, len(signals)):
            sig_a, sig_b = signals[i], signals[j]
            if _llm_gate_score(sig_a, sig_b) is None:
                continue
            stem_a, stem_b = sig_a["stem"], sig_b["stem"]
            comp_a = member_component.get(stem_a)
            comp_b = member_component.get(stem_b)
            if comp_a != comp_b:
                continue  # one of the pair already belongs elsewhere

            labels = ["focus-overlap"]
            if comp_a is not None:
                comp = components[comp_a]
                if comp["size"] != 2:
                    continue
                if any(_is_near_identical(matches) for (_, _, matches) in comp["_edges"]):
                    continue  # already surfaced without the LLM gate
                labels = comp["signals"]

            verdict = _verify_same_work(
                sig_a["focus_text"], sig_b["focus_text"], repo_root=repo_root, agent_cli=agent_cli
            )
            if not verdict:
                continue
            gate_clusters.append(
                {"members": sorted((stem_a, stem_b)), "signals": labels, "size": 2}
            )
    return gate_clusters


_Edge = Tuple[str, str, List[Tuple[str, Optional[float]]]]


def _is_near_identical(matches: List[Tuple[str, Optional[float]]]) -> bool:
    """True if a pair's Signal Matches qualify it as near-identical: a
    duplicate-slug match, or a focus-overlap score at/above
    NEAR_IDENTICAL_THRESHOLD (REQ-013)."""
    for label, score in matches:
        if label == "duplicate-slug":
            return True
        if label == "focus-overlap" and score is not None and score >= NEAR_IDENTICAL_THRESHOLD:
            return True
    return False


def _connected_components(edges: List[_Edge]) -> List[Dict[str, Any]]:
    """Assemble Signal Match edges into connected components via union-find.

    Any edge with a non-empty matches list connects its two ids into the same
    component, regardless of signal type or score — connectivity does not
    distinguish qualifying from ordinary matches (REQ-011); that distinction
    is applied later by `_filter_reportable`. Edges with an empty matches list
    (no Signal Match) are ignored.

    Returns one dict per component with `members` (sorted ids), `signals`
    (sorted, de-duplicated labels of every edge within the component), `size`,
    and `_edges` (the raw contributing edges — internal, consumed by
    `_filter_reportable` to test near-identical qualification and stripped
    from the final surfaced output).
    """
    parent: Dict[str, str] = {}

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

    members_by_root: Dict[str, set] = {}
    edges_by_root: Dict[str, List[_Edge]] = {}
    for id_a, id_b, matches in active_edges:
        root = find(id_a)
        members_by_root.setdefault(root, set()).update((id_a, id_b))
        edges_by_root.setdefault(root, []).append((id_a, id_b, matches))

    components = []
    for root, members in members_by_root.items():
        comp_edges = edges_by_root[root]
        signals = sorted({label for (_, _, matches) in comp_edges for label, _ in matches})
        components.append(
            {
                "members": sorted(members),
                "signals": signals,
                "size": len(members),
                "_edges": comp_edges,
            }
        )
    components.sort(key=lambda c: c["members"])
    return components


def _filter_reportable(components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply the reporting-threshold filter to connected components.

    Every component of size >= 3 is surfaced unconditionally (REQ-012).
    A size == 2 component is surfaced only if it qualifies as near-identical
    (REQ-013); otherwise it is dropped (REQ-014). Components of size <= 1
    never occur here (a component requires at least one edge) but would be
    dropped too. Strips the internal `_edges` key from the returned dicts.
    """
    reportable = []
    for comp in components:
        size = comp["size"]
        if size >= 3:
            qualifies = True
        elif size == 2:
            qualifies = any(_is_near_identical(matches) for (_, _, matches) in comp["_edges"])
        else:
            qualifies = False
        if not qualifies:
            continue
        reportable.append({"members": comp["members"], "signals": comp["signals"], "size": size})
    return reportable


def _assemble_clusters(
    edges: List[_Edge],
    *,
    signals: Optional[List[Dict[str, Any]]] = None,
    repo_root: Optional[Path] = None,
    agent_cli: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Assemble Signal Match edges into surfaced clusters: connected-component
    assembly (`_connected_components`) followed by the reporting-threshold
    filter (`_filter_reportable`). An empty `edges` list yields an empty
    cluster list.

    `signals` (the original Cluster Signals the edges were computed from) is
    optional and, when supplied, additionally runs the LLM verification gate
    (`_llm_gate_clusters`, design.md D3) over the same-named candidates and
    merges any it confirms into the surfaced list, deduplicated by member
    set. Omitting `signals` (the default) preserves the pre-gate behavior
    exactly — no LLM calls, gate-only candidates never surfaced.
    """
    components = _connected_components(edges)
    reportable = _filter_reportable(components)
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
    parse_frontmatter: Callable[[str], Dict[str, Any]],
    *,
    repo_root: Optional[Path],
    agent_cli: Optional[str],
) -> List[Dict[str, Any]]:
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

    edges: List[_Edge] = []
    for i in range(len(signals)):
        for j in range(i + 1, len(signals)):
            sig_a, sig_b = signals[i], signals[j]
            matches = _signal_matches(sig_a, sig_b)
            edges.append((sig_a["stem"], sig_b["stem"], matches))

    return _assemble_clusters(edges, signals=signals, repo_root=repo_root, agent_cli=agent_cli)


def compute_clusters(
    queue_dir: Path,
    parse_frontmatter: Callable[[str], Dict[str, Any]],
    *,
    repo_root: Optional[Path] = None,
    agent_cli: Optional[str] = None,
) -> List[Dict[str, Any]]:
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
    """
    try:
        return _compute_clusters_inner(
            queue_dir, parse_frontmatter, repo_root=repo_root, agent_cli=agent_cli
        )
    except Exception:  # noqa: BLE001 — degrade, never crash the dashboard render
        return []
