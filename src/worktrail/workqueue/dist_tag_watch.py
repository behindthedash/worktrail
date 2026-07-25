#!/usr/bin/env python3
"""
Dist-tag watcher -- scans every brief in the work-queue for a `watch:`
frontmatter list, queries each watched package's latest published version
from npm or PyPI, and, when the observed version differs from the recorded
one, clears the brief's `next-check-after` field and advances the recorded
version in place (one atomic write per brief).

`watch:` grammar (see ../../../../../docs/specs/015-universal-go-front-door/
changes/2026-07-14--dist-tag-watcher/contracts/watch-field.event.md):

    watch:
      - npm:left-pad@1.0.0
      - pypi:requests@2.31.0

Each entry is independent: a malformed entry or a failed registry lookup is
reported per-entry and never blocks any other entry, on the same brief or a
different one. A brief with zero changed entries gets zero writes -- not
even a no-op rewrite -- so mtime/content are byte-identical across a no-op
run (this is what AC-002/AC-005 verify).

CLI:

    dist_tag_watch.py [--queue-dir DIR] [--json] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..shared.brief_frontmatter import read_frontmatter

_FM_RE = re.compile(r"^---\r?\n(.*?)\n---\r?\n", re.DOTALL)

_ENTRY_RE = re.compile(r"^(?P<registry>[^:]+):(?P<package>[^@]+)@(?P<version>.+)$")

_FETCH_TIMEOUT = 10  # seconds -- bounds one hung registry call (see registry-lookup.md)

_REGISTRY_URLS = {
    "npm": "https://registry.npmjs.org/{package}/latest",
    "pypi": "https://pypi.org/pypi/{package}/json",
}


# --------------------------------------------------------------------------- #
# Location resolution (mirrors work_queue.py's base_dir()/queue_dir())
# --------------------------------------------------------------------------- #


def base_dir(queue_dir_arg: Optional[str] = None) -> Path:
    """Resolve the work-queue root: --queue-dir, else $WORK_QUEUE_DIR, else ~/work-queue."""
    if queue_dir_arg:
        return Path(queue_dir_arg).expanduser()
    return Path(os.environ.get("WORK_QUEUE_DIR", "~/work-queue")).expanduser()


def queue_dir(queue_dir_arg: Optional[str] = None) -> Path:
    return base_dir(queue_dir_arg) / "queue"


# --------------------------------------------------------------------------- #
# Frontmatter mutation (regex-scoped block replace, same technique as
# work_queue.py's _remove_fm_field / _set_fm_list_field)
# --------------------------------------------------------------------------- #


def _remove_fm_field(content: str, key: str) -> str:
    """Remove a scalar frontmatter field (and nothing else) if present."""
    m = _FM_RE.match(content)
    if not m:
        return content
    block = m.group(1)
    key_re = re.compile(r"^" + re.escape(key) + r"\s*:")
    lines = [line for line in block.split("\n") if not key_re.match(line)]
    new_block = "\n".join(lines)
    if new_block == block:
        return content
    return content[: m.start(1)] + new_block + content[m.end(1) :]


def _set_fm_list_field(content: str, key: str, values: List[str]) -> str:
    """Set/replace a list field inside the YAML frontmatter block.

    Renders the field as a YAML block sequence, preserving key order and every
    other field/the body, matching work_queue.py's _set_fm_list_field.
    """
    m = _FM_RE.match(content)
    if not m:
        return content

    block = m.group(1)
    lines = block.split("\n")
    new_lines: List[str] = []
    skip_continuations = False
    key_found = False
    key_re = re.compile(r"^" + re.escape(key) + r"\s*:")

    for line in lines:
        if skip_continuations:
            if line.startswith("  ") or line.startswith("\t"):
                continue
            skip_continuations = False
        if key_re.match(line):
            key_found = True
            skip_continuations = True
            if values:
                new_lines.append(f"{key}:")
                new_lines.extend(f"  - {v}" for v in values)
            continue
        new_lines.append(line)

    if not key_found and values:
        new_lines.append(f"{key}:")
        new_lines.extend(f"  - {v}" for v in values)

    new_block = "\n".join(new_lines)
    return content[: m.start(1)] + new_block + content[m.end(1) :]


# --------------------------------------------------------------------------- #
# `watch:` entry parsing
# --------------------------------------------------------------------------- #


def parse_watch_entry(raw: str) -> Tuple[Optional[Tuple[str, str, str]], Optional[str]]:
    """Parse one `watch:` list item.

    Returns ``((registry, package, version), None)`` on success, or
    ``(None, error_message)`` when the entry is unparsable (wrong shape or an
    unsupported registry) -- never raises (REQ-CHG-003, AC-004).
    """
    m = _ENTRY_RE.match(raw.strip())
    if not m:
        return None, f"unparsable watch entry: {raw!r}"
    registry = m.group("registry")
    package = m.group("package")
    version = m.group("version")
    if registry not in _REGISTRY_URLS:
        return None, f"unsupported registry {registry!r} in entry: {raw!r}"
    if not package or not version:
        return None, f"unparsable watch entry: {raw!r}"
    return (registry, package, version), None


# --------------------------------------------------------------------------- #
# Registry lookups (injectable so tests never touch the network)
# --------------------------------------------------------------------------- #


def _default_fetch(url: str) -> str:
    """Real network fetch -- GET `url`, return the response body as text."""
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310 -- read-only, no-auth registry GET
        return resp.read().decode("utf-8")


def query_latest_version(
    registry: str, package: str, fetch: Any = _default_fetch
) -> Tuple[Optional[str], Optional[str]]:
    """Query a registry for `package`'s latest published version.

    Returns ``(version, None)`` on success, or ``(None, error_message)`` on any
    failure (network error, non-200, non-JSON body, missing field) -- never
    raises (REQ-CHG-008, AC-006).
    """
    url_template = _REGISTRY_URLS.get(registry)
    if url_template is None:
        return None, f"unsupported registry {registry!r}"
    url = url_template.format(package=package)
    try:
        body = fetch(url)
    except Exception as exc:  # noqa: BLE001 -- any fetch failure is a per-entry error, never fatal
        return None, f"fetch failed for {url}: {exc}"

    try:
        data = json.loads(body)
    except (TypeError, ValueError) as exc:
        return None, f"malformed JSON from {url}: {exc}"

    if registry == "npm":
        version = data.get("version") if isinstance(data, dict) else None
        if not version:
            return None, f"missing 'version' field in npm response for {package!r}"
        return str(version), None

    # pypi
    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    if not version:
        return None, f"missing 'info.version' field in PyPI response for {package!r}"
    return str(version), None


# --------------------------------------------------------------------------- #
# Per-brief check
# --------------------------------------------------------------------------- #


def check_brief(path: Path, fetch: Any = _default_fetch, dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """Check one brief's `watch:` entries; returns ``None`` if it has none.

    Queries every entry independently, rewrites `next-check-after`/`watch:` in
    one atomic write when at least one entry changed (skipped entirely in
    `--dry-run`), and returns the per-brief report dict otherwise
    (REQ-CHG-006, REQ-CHG-007, AC-001, AC-002, AC-005).
    """
    fm = read_frontmatter(path)
    raw_entries = fm.get("watch")
    if not raw_entries:
        return None
    if isinstance(raw_entries, str):
        raw_entries = [raw_entries]
    if not isinstance(raw_entries, list):
        return None

    entries: List[Dict[str, Any]] = []
    new_watch_values: List[str] = []
    any_changed = False

    for raw in raw_entries:
        raw_str = str(raw)
        parsed, parse_err = parse_watch_entry(raw_str)
        if parsed is None:
            entries.append(
                {
                    "raw": raw_str,
                    "registry": None,
                    "package": None,
                    "recorded_version": None,
                    "latest_version": None,
                    "changed": False,
                    "error": parse_err,
                }
            )
            new_watch_values.append(raw_str)
            continue

        registry, package, recorded_version = parsed
        latest_version, fetch_err = query_latest_version(registry, package, fetch=fetch)
        if fetch_err is not None:
            entries.append(
                {
                    "raw": raw_str,
                    "registry": registry,
                    "package": package,
                    "recorded_version": recorded_version,
                    "latest_version": None,
                    "changed": False,
                    "error": fetch_err,
                }
            )
            new_watch_values.append(raw_str)
            continue

        changed = latest_version != recorded_version
        entries.append(
            {
                "raw": raw_str,
                "registry": registry,
                "package": package,
                "recorded_version": recorded_version,
                "latest_version": latest_version,
                "changed": changed,
                "error": None,
            }
        )
        if changed:
            any_changed = True
            new_watch_values.append(f"{registry}:{package}@{latest_version}")
        else:
            new_watch_values.append(raw_str)

    written = False
    if any_changed and not dry_run:
        content = path.read_text(encoding="utf-8")
        content = _remove_fm_field(content, "next-check-after")
        content = _set_fm_list_field(content, "watch", new_watch_values)
        path.write_text(content, encoding="utf-8")
        written = True

    return {
        "filename": path.name,
        "entries": entries,
        "next_check_after_cleared": any_changed,
        "written": written,
    }


# --------------------------------------------------------------------------- #
# Run over the queue
# --------------------------------------------------------------------------- #


def _md_files(d: Path) -> List[Path]:
    if not d.is_dir():
        return []
    return sorted(f for f in d.iterdir() if f.is_file() and f.suffix == ".md")


def run_check(
    queue_dir_arg: Optional[str] = None, fetch: Any = _default_fetch, dry_run: bool = False
) -> Dict[str, Any]:
    """Check every brief in the queue dir; returns the `--json` report shape."""
    briefs: List[Dict[str, Any]] = []
    changed = 0
    errors = 0
    for path in _md_files(queue_dir(queue_dir_arg)):
        result = check_brief(path, fetch=fetch, dry_run=dry_run)
        if result is None:
            continue
        briefs.append(result)
        if result["next_check_after_cleared"]:
            changed += 1
        errors += sum(1 for e in result["entries"] if e["error"] is not None)

    return {
        "checked": len(briefs),
        "changed": changed,
        "errors": errors,
        "dry_run": dry_run,
        "briefs": briefs,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_human(result: Dict[str, Any]) -> None:
    print(
        f"checked {result['checked']} brief(s), {result['changed']} changed, "
        f"{result['errors']} error(s)" + (" [dry-run]" if result["dry_run"] else "")
    )
    for brief in result["briefs"]:
        for entry in brief["entries"]:
            if entry["error"]:
                print(f"  {brief['filename']}: {entry['raw']} -- error: {entry['error']}")
            elif entry["changed"]:
                print(
                    f"  {brief['filename']}: {entry['raw']} -> "
                    f"{entry['registry']}:{entry['package']}@{entry['latest_version']}"
                )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="dist-tag watcher for handoff briefs")
    p.add_argument("--queue-dir", default=None, help="work-queue root's queue/ parent")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--dry-run", action="store_true", help="report verdicts without writing")
    args = p.parse_args(argv)

    result = run_check(queue_dir_arg=args.queue_dir, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result))
    else:
        _print_human(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
