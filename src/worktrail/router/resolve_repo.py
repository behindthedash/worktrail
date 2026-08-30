#!/usr/bin/env python3
"""
`sdd-workflow` conductor -- target-repo resolver.

`go` is often launched from a *multi-repo parent directory* (the user starts
Claude once at `~/projects` and works on any repo underneath). The conductor
must answer "which repo are we GO'ing?" BEFORE it writes any artifact or pulls
anything. This module is the deterministic, unit-testable core of that decision;
`SKILL.md` turns the result into either a silent pick or an `AskUserQuestion`.

Pure file inspection -- no git invocation, no network -- so it is safe to run
anywhere and testable against a temp directory tree.

Resolution order (mirrors SKILL.md Step 0):
  1. start (or an ancestor) is itself a git repo        -> mode "in-repo"
  2. not in a repo, a HINT uniquely matches one sibling -> mode "derived"
  3. not in a repo, exactly one sibling repo, no hint   -> mode "single-candidate"
  4. not in a repo, several siblings (hint ambiguous)   -> mode "ambiguous"  (-> prompt)
  5. no candidate repos found                           -> mode "none"

A "candidate" is an immediate child directory of `start` that is a git repo.
We do NOT recurse: a monorepo of repos is one level deep by convention, and a
deep walk would surface vendored/nested `.git` dirs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_TOKEN = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- #
# Repo detection (pure)
# --------------------------------------------------------------------------- #
def is_git_repo(path: Path) -> bool:
    """A worktree or a normal checkout: `.git` may be a dir OR a file (worktree)."""
    return (Path(path) / ".git").exists()


def is_workspace_root(path: Path) -> bool:
    """A workspace root carries a project index plus a sibling `projects/` container."""
    path = Path(path)
    return (path / "REPOS.md").is_file() and (path / "projects").is_dir()


def _canonical_repo_from_worktree(dot_git_file: Path) -> Path | None:
    """A worktree's `.git` file contains `gitdir: <canonical>/.git/worktrees/<name>`.
    Follow that pointer to the canonical repo root."""
    try:
        first_line = dot_git_file.read_text().splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first_line.startswith("gitdir:"):
        return None
    gitdir = Path(first_line.split(":", 1)[1].strip()).resolve()
    # gitdir is <canonical>/.git/worktrees/<name> — walk up to <canonical>
    # by finding the ancestor that is an actual .git directory (not a file).
    for ancestor in gitdir.parents:
        if ancestor.name == ".git" and ancestor.is_dir():
            return ancestor.parent
    return None


def find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` to the first ancestor that is a git repo.

    When a worktree `.git` file is found, resolves to the canonical repo root
    (the main checkout) rather than the ephemeral worktree path.
    """
    start = Path(start).resolve()
    for d in (start, *start.parents):
        if not is_git_repo(d):
            continue
        dot_git = d / ".git"
        if dot_git.is_file():
            # Worktree — follow the gitdir pointer to the canonical repo
            canonical = _canonical_repo_from_worktree(dot_git)
            if canonical is not None:
                return canonical
        return d
    return None


def list_candidate_repos(parent: Path) -> list[Path]:
    """Immediate child directories of `parent` that are git repos, name-sorted."""
    parent = Path(parent).resolve()
    if not parent.is_dir():
        return []
    return sorted(
        (d for d in parent.iterdir() if d.is_dir() and is_git_repo(d)),
        key=lambda p: p.name.lower(),
    )


# --------------------------------------------------------------------------- #
# Hint matching (pure)
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.split(text.lower()) if t]


def initials(name: str) -> str:
    """`gracefully-giving-back` -> `ggb` (acronym of hyphen/underscore parts)."""
    return "".join(p[0] for p in _TOKEN.split(name.lower()) if p)


# Common request words that must never derive a repo via substring matching —
# "fix the upload timeout" was silently deriving `behindthedash` because "the"
# is a substring of the name. Exact name/acronym/name-token matches still win.
_HINT_STOPWORDS = {
    "the",
    "and",
    "for",
    "fix",
    "add",
    "bug",
    "new",
    "with",
    "from",
    "into",
    "this",
    "that",
    "when",
    "then",
    "them",
    "they",
    "have",
    "will",
    "make",
    "spec",
    "specs",
    "task",
    "tasks",
    "repo",
    "route",
    "handoff",
    "brief",
    "queue",
    "work",
    "code",
    "test",
    "tests",
    "implement",
    "continue",
    "update",
    "create",
    "delete",
    "remove",
    "change",
}


def derive_from_hint(candidates: list[Path], hint: str) -> list[Path]:
    """Return candidates whose name plausibly matches the user's hint.

    Matches, in order of intent, any of:
      - a hint token equals the repo name or its acronym  ("GGB" -> ggb)
      - a hint token equals one of the repo name's tokens ("giving" -> gracefully-giving-back)
      - a non-stopword hint token (len>=4) is a substring of the name, or vice-versa
    Deduped, preserving candidate order. Empty hint -> no matches.
    """
    htoks = _tokens(hint)
    if not htoks:
        return []
    matched: list[Path] = []
    for c in candidates:
        name = c.name.lower()
        ntoks = _tokens(c.name)
        acro = initials(c.name)
        hit = (
            any(t == name or t == acro for t in htoks)
            or any(t in ntoks for t in htoks)
            or any(
                len(t) >= 4 and t not in _HINT_STOPWORDS and (t in name or name in t)
                for t in htoks
            )
        )
        if hit:
            matched.append(c)
    return matched


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #
def resolve(start: Path, hint: str = "") -> dict:
    """Decide the target repo. See module docstring for the mode contract."""
    start = Path(start).resolve()

    # in-repo detection must win when start is inside a project repo; the
    # workspace walk below would otherwise shadow it on any machine with the
    # standard layout (~/REPOS.md + ~/projects/), because every repo under
    # ~/projects has the workspace root as an ancestor. Workspace mode applies
    # only when the enclosing repo IS the workspace root itself (e.g. a
    # dotfiles-tracked home dir) or start is outside any repo.
    repo_root = find_repo_root(start)

    for d in (start, *start.parents):
        if is_workspace_root(d):
            if repo_root is not None and Path(repo_root) != d:
                break  # inside a real project repo under the workspace -> in-repo
            candidates = list_candidate_repos(d / "projects")
            cand_strs = [str(c) for c in candidates]
            if not candidates:
                return {"mode": "none", "repo": None, "candidates": []}
            if hint:
                matches = derive_from_hint(candidates, hint)
                if len(matches) == 1:
                    return {
                        "mode": "derived",
                        "repo": str(matches[0]),
                        "candidates": cand_strs,
                    }
                if len(matches) > 1:
                    return {
                        "mode": "ambiguous",
                        "repo": None,
                        "candidates": [str(m) for m in matches],
                    }
                return {"mode": "ambiguous", "repo": None, "candidates": cand_strs}
            if len(candidates) == 1:
                return {
                    "mode": "single-candidate",
                    "repo": cand_strs[0],
                    "candidates": cand_strs,
                }
            return {"mode": "ambiguous", "repo": None, "candidates": cand_strs}

    if repo_root is not None:
        return {"mode": "in-repo", "repo": str(repo_root), "candidates": []}

    candidates = list_candidate_repos(start)
    cand_strs = [str(c) for c in candidates]

    if not candidates:
        return {"mode": "none", "repo": None, "candidates": []}

    if hint:
        matches = derive_from_hint(candidates, hint)
        if len(matches) == 1:
            return {"mode": "derived", "repo": str(matches[0]), "candidates": cand_strs}
        if len(matches) > 1:
            return {
                "mode": "ambiguous",
                "repo": None,
                "candidates": [str(m) for m in matches],
            }
        # hint matched nothing -> fall back to the full list for a prompt
        return {"mode": "ambiguous", "repo": None, "candidates": cand_strs}

    if len(candidates) == 1:
        return {
            "mode": "single-candidate",
            "repo": cand_strs[0],
            "candidates": cand_strs,
        }
    return {"mode": "ambiguous", "repo": None, "candidates": cand_strs}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="sdd-workflow conductor target-repo resolver"
    )
    p.add_argument(
        "--start", default=".", help="directory Claude was launched from (cwd)"
    )
    p.add_argument(
        "--hint", default="", help="free-text user input to derive the repo from"
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of a summary")
    args = p.parse_args(argv)

    res = resolve(Path(args.start), args.hint)
    if args.json:
        print(json.dumps(res))
        return 0

    mode = res["mode"]
    if mode in ("in-repo", "derived", "single-candidate"):
        print(f"{mode}: {res['repo']}")
    elif mode == "ambiguous":
        print("ambiguous -- choose one:")
        for c in res["candidates"]:
            print(f"  {c}")
    else:
        print("none: no git repos found under the start directory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
