"""Load the retro agent's worker notes for injection into worker prompts (design D3)."""

from __future__ import annotations

from pathlib import Path

from worktrail.learning.paths import retro_memory_path

SECTION_HEADING = "## Notes for workers"
MAX_BULLETS = 20
MAX_CHARS = 4000


def _bullets(section: list[str]) -> list[str]:
    bullets: list[list[str]] = []
    for line in section:
        if line.lstrip().startswith(("- ", "* ")):
            bullets.append([line.rstrip()])
        elif bullets and line.strip():
            bullets[-1].append(line.rstrip())
    return ["\n".join(b) for b in bullets]


def load_learned_notes(repo: Path) -> str | None:
    """The `## Notes for workers` bullets, capped to whole bullets; `None` if none."""
    try:
        text = retro_memory_path(repo).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == SECTION_HEADING)
    except StopIteration:
        return None
    section: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        section.append(line)
    kept: list[str] = []
    size = 0
    for bullet in _bullets(section)[:MAX_BULLETS]:
        added = len(bullet) + (1 if kept else 0)
        if size + added > MAX_CHARS:
            break
        kept.append(bullet)
        size += added
    return "\n".join(kept) or None
