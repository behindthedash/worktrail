"""Deterministic repo inference from a handoff brief's focus text.

Three rules, tried in order, each returning only when it identifies exactly
one distinct known repo: (a) an explicit `Repo:`/`repo:` token, (b) a known
repo name mentioned as a whole word, (c) a unique path token (from
`router.brief_probes.extract_probes()`) that exists under exactly one known
checkout. A rule that matches two or more distinct repos stops -- inference
never guesses among ambiguous candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from worktrail.router.brief_probes import extract_probes

_REPO_TOKEN_RE = re.compile(r"[Rr]epo:\s*([^\s,;]+)")


@dataclass
class InferenceResult:
    repo: str | None
    rule: str | None
    candidates: list[str] = field(default_factory=list)


def _known_repos(repos_root: Path) -> dict[str, Path]:
    """Map repo basename -> absolute resolved checkout path.

    A known repo is a direct subdirectory of `repos_root` containing a
    `.git` entry (file or directory, so worktrees qualify too).
    """
    known: dict[str, Path] = {}
    try:
        entries = sorted(repos_root.iterdir())
    except OSError:
        return known
    for entry in entries:
        if not entry.is_dir():
            continue
        if not (entry / ".git").exists():
            continue
        known[entry.name] = entry.resolve()
    return known


def _rule_a(focus: str, known: dict[str, Path]) -> InferenceResult | None:
    matches = _REPO_TOKEN_RE.findall(focus)
    if not matches:
        return None
    candidates: list[str] = []
    for token in matches:
        token = token.rstrip(".,;:")
        basename = token.rsplit("/", 1)[-1]
        if basename in known and basename not in candidates:
            candidates.append(basename)
    if not candidates:
        return None
    if len(candidates) == 1:
        return InferenceResult(str(known[candidates[0]]), "a", [])
    return InferenceResult(None, "a", sorted(candidates))


def _rule_b(focus: str, known: dict[str, Path]) -> InferenceResult | None:
    candidates: list[str] = []
    for name in known:
        pattern = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
        if pattern.search(focus):
            candidates.append(name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return InferenceResult(str(known[candidates[0]]), "b", [])
    return InferenceResult(None, "b", sorted(candidates))


def _rule_c(focus: str, known: dict[str, Path]) -> InferenceResult | None:
    probes = extract_probes(focus)
    paths = probes.get("paths", [])
    candidates: list[str] = []
    for raw_path in paths:
        rel = raw_path.split(":", 1)[0]
        for name, checkout in known.items():
            if (checkout / rel).exists() and name not in candidates:
                candidates.append(name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return InferenceResult(str(known[candidates[0]]), "c", [])
    return InferenceResult(None, "c", sorted(candidates))


def infer_repo(focus: str, repos_root: str | Path | None = None) -> InferenceResult:
    """Infer the repo a brief's `focus` refers to, per design D1.

    Tries rule (a), then (b), then (c) in order; the first rule that
    identifies any candidate at all decides the outcome -- exactly one
    distinct repo resolves it, two or more returns `repo=None` with that
    rule's `candidates`, zero candidates falls through to the next rule.
    No rule matching anything returns `InferenceResult(None, None, [])`.
    """
    focus = focus or ""
    root = Path(repos_root).expanduser() if repos_root else Path.home() / "projects"
    known = _known_repos(root)

    for rule in (_rule_a, _rule_b, _rule_c):
        result = rule(focus, known)
        if result is not None:
            return result

    return InferenceResult(None, None, [])
