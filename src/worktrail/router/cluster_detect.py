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
performs no filesystem writes and issues no network calls.

`parse_frontmatter` is injected by the caller (dashboard.py's already-resolved
loader-backed `_parse_fm`) rather than reimplemented here, so this module
contains no frontmatter-parsing logic and no cross-skill import into
`handoff/scripts/`.

Stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Leading YYYYMMDD-HHMMSS- timestamp prefix used by queued-brief filenames.
_SLUG_PREFIX_RE = re.compile(r"^\d{8}-\d{6}-")

# Overlap-coefficient floor for a focus-overlap Signal Match — mirrors
# score_candidates.py's BATCH_MIN (Consume-time companion floor).
OVERLAP_THRESHOLD = 0.45

# Stricter overlap-coefficient floor a size-2 component's focus-overlap edge
# must clear to be treated as near-identical (and thus surfaced) rather than
# an ordinary match (REQ-013). Deliberately well above OVERLAP_THRESHOLD.
NEAR_IDENTICAL_THRESHOLD = 0.75


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
    require both briefs to carry the same non-null repo. duplicate-slug is
    unconditional on repo.
    """
    if _is_blocked_by_pair(sig_a, sig_b):
        return []

    matches: List[Tuple[str, Optional[float]]] = []

    if sig_a["slug"] and sig_a["slug"] == sig_b["slug"]:
        matches.append(("duplicate-slug", None))

    repo_a = sig_a["repo"]
    repo_b = sig_b["repo"]
    same_repo = repo_a is not None and repo_b is not None and repo_a == repo_b
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


def _assemble_clusters(edges: List[_Edge]) -> List[Dict[str, Any]]:
    """Assemble Signal Match edges into surfaced clusters: connected-component
    assembly (`_connected_components`) followed by the reporting-threshold
    filter (`_filter_reportable`). An empty `edges` list yields an empty
    cluster list."""
    return _filter_reportable(_connected_components(edges))


def _compute_clusters_inner(
    queue_dir: Path, parse_frontmatter: Callable[[str], Dict[str, Any]]
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

    return _assemble_clusters(edges)


def compute_clusters(
    queue_dir: Path, parse_frontmatter: Callable[[str], Dict[str, Any]]
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
    """
    try:
        return _compute_clusters_inner(queue_dir, parse_frontmatter)
    except Exception:  # noqa: BLE001 — degrade, never crash the dashboard render
        return []
