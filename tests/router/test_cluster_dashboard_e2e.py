#!/usr/bin/env python3
"""
End-to-end test for handoff-queue cluster detection (spec 018, TASK-005).

Drives the REAL entry points -- `work_queue.py list --json` followed by
`dashboard.py --json` -- exactly the way the `/go` conductor chains them,
against a realistic multi-brief work-queue fixture built on disk. No
mocking of `compute_clusters`/`dashboard.py` internals: every assertion
observes the actual subprocess JSON/text output.

This is the sole verification point (per TASK-005's frontmatter) for:
  - AC-010 [SEF]: every surfaced cluster/pair states its signal(s) and size.
  - AC-017 [SEF]: two runs against an unchanged queue are byte-identical.
  - AC-014 [EXT]'s closest automatable proxy: a realistic 3+-brief overlap
    scenario is called out on a single dashboard render.
  - Every [IMP] AC (AC-001-009, AC-011-013, AC-015, AC-016) at the
    full-workflow level, on top of TASK-001-004's own unit coverage.

Run with:
    python3 -m pytest plugins/developer-kit-project-management/skills/devkit-pm-go/scripts/test_cluster_dashboard_e2e.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple



# --------------------------------------------------------------------------- #
# Fixture construction
# --------------------------------------------------------------------------- #


def _write_brief(
    dirpath: Path,
    filename: str,
    *,
    repo: Optional[str] = None,
    target_spec: Optional[str] = None,
    related: Optional[List[str]] = None,
    blocked_by: Optional[List[str]] = None,
    focus: str = "",
) -> str:
    """Write a realistic queued-brief .md file; return its stem (brief id)."""
    lines = ["---", f"id: {filename[:-3]}", f'focus: "{focus}"']
    if repo is not None:
        lines.append(f"repo: {repo}")
    if target_spec is not None:
        lines.append(f'target-spec: "{target_spec}"')
    if related:
        lines.append("related:")
        lines.extend(f"  - {r}" for r in related)
    if blocked_by:
        lines.append("blocked-by:")
        lines.extend(f"  - {b}" for b in blocked_by)
    lines.append("status: queued")
    lines.append("---")
    lines.append("")
    lines.append("## Focus")
    lines.append("")
    lines.append(focus)
    lines.append("")
    path = dirpath / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path.stem


def _build_realistic_queue(queue_dir: Path) -> Dict[str, str]:
    """Materialize the realistic multi-brief fixture demanded by TASK-005's
    Test Instructions: (a) a 3-brief cluster via related + same-target-spec,
    (b) a literal duplicate-slug pair, (c) a near-identical focus-overlap
    pair, (d) an ordinary below-threshold overlap pair that must NOT
    surface, (e) a blocked-by-linked pair that would otherwise match and
    must NOT surface, (f) unrelated singleton briefs. Returns {label: stem}.
    """
    ids: Dict[str, str] = {}

    # (a) 3-brief cluster: a1-a2 via same-target-spec, a2-a3 via related-link
    # (only transitively connected -- no direct a1-a3 edge -- to exercise
    # connected-component assembly, not just pairwise matching).
    ids["a3"] = _write_brief(
        queue_dir,
        "20260701-100200-alpha-feature-work-part3.md",
        repo="repo-a",
        focus="Write end user documentation covering the new alpha workflow screens",
    )
    ids["a1"] = _write_brief(
        queue_dir,
        "20260701-100000-alpha-feature-work.md",
        repo="repo-a",
        target_spec="030-alpha",
        focus="Design the alpha module configuration schema and validation rules",
    )
    ids["a2"] = _write_brief(
        queue_dir,
        "20260701-100100-alpha-feature-work-part2.md",
        repo="repo-a",
        target_spec="030-alpha",
        related=[ids["a3"]],
        focus="Wire the alpha module settings panel into the existing account dashboard",
    )

    # (b) literal duplicate-slug pair -- same descriptive slug, different
    # timestamp prefixes (the 2026-07-14 retro's actual failure mode).
    ids["b1"] = _write_brief(
        queue_dir,
        "20260702-090000-fix-widget-alignment.md",
        repo="repo-b",
        focus="Grid alignment breaks on narrow viewport widths for the settings panel",
    )
    ids["b2"] = _write_brief(
        queue_dir,
        "20260702-091500-fix-widget-alignment.md",
        repo="repo-b",
        focus="Panel spacing looks wrong after the recent CSS refactor landed",
    )

    # (c) near-identical focus-overlap pair (overlap coefficient ~0.89, at/above
    # NEAR_IDENTICAL_THRESHOLD == 0.75) -- distinct slugs, so only focus-overlap
    # produces the match.
    ids["c1"] = _write_brief(
        queue_dir,
        "20260703-080000-queue-dup-warning-missing.md",
        repo="repo-c",
        focus="Queue dashboard shows duplicate briefs without any warning during render",
    )
    ids["c2"] = _write_brief(
        queue_dir,
        "20260703-081500-queue-dup-warning-absent.md",
        repo="repo-c",
        focus="Queue dashboard shows duplicate briefs without any warning today",
    )

    # (d) ordinary overlap pair (~0.4545, at/above OVERLAP_THRESHOLD == 0.45
    # but below NEAR_IDENTICAL_THRESHOLD == 0.50) with no third brief -- must
    # NOT surface. Retuned for the duplicate-brief-detection change's lowered
    # NEAR_IDENTICAL_THRESHOLD (was 0.75; this pair's original ~0.57 overlap
    # cleared the new 0.50 bar and started surfacing).
    ids["d1"] = _write_brief(
        queue_dir,
        "20260704-070000-network-retry-noise.md",
        repo="repo-d",
        focus=(
            "Render pipeline retries flaky network calls occasionally "
            "timeout latency spike retry"
        ),
    )
    ids["d2"] = _write_brief(
        queue_dir,
        "20260704-071000-response-latency-tuning.md",
        repo="repo-d",
        focus=(
            "Render pipeline retries occasionally timeout causing slow "
            "response jitter backend service"
        ),
    )

    # (e) blocked-by-linked pair that would otherwise match on same-target-spec
    # -- must NOT surface under any signal (REQ-009 overrides even an
    # unconditional match).
    ids["e1"] = _write_brief(
        queue_dir,
        "20260705-060000-blocked-feature-groundwork.md",
        repo="repo-e",
        target_spec="040-blocked-feature",
        focus="Prep foundation layer for blocked feature rollout work",
    )
    ids["e2"] = _write_brief(
        queue_dir,
        "20260705-061000-blocked-feature-rollout.md",
        repo="repo-e",
        target_spec="040-blocked-feature",
        blocked_by=[ids["e1"]],
        focus="Ship blocked feature rollout after foundation lands successfully",
    )

    # (f) unrelated singleton briefs -- must never appear in any cluster.
    ids["f1"] = _write_brief(
        queue_dir,
        "20260706-090000-improve-error-messages.md",
        repo="repo-f",
        focus="Improve error messages shown when API requests fail unexpectedly",
    )
    ids["f2"] = _write_brief(
        queue_dir,
        "20260706-091000-optimize-image-loading.md",
        repo="repo-g",
        focus="Optimize lazy image loading for the gallery page performance",
    )
    ids["f3"] = _write_brief(
        queue_dir,
        "20260706-092000-standalone-no-repo-note.md",
        repo=None,
        focus="Miscellaneous note captured without an associated repository context",
    )

    return ids


# Deliberately unterminated frontmatter (no closing `---`) -- both the
# loader-backed parser dashboard.py injects and work_queue.py's YAML parser
# must treat this as unparseable/empty without raising. Its slug
# ("alpha-feature-work") intentionally collides with group (a)'s a1 brief,
# so a successful parse WOULD have joined it to the 3-brief cluster --
# proving it is truly excluded, not just coincidentally unmatched.
_CORRUPT_BRIEF = """---
id: 20260707-050000-alpha-feature-work
focus: "Frontmatter deliberately left unterminated to simulate corruption"
repo: repo-a
target-spec: "030-alpha"

This body never receives a closing frontmatter fence, so parsers must treat
this file as unparseable (or empty-frontmatter) and skip it without raising.
"""


def _cluster_by_members(
    clusters: List[Dict],
) -> Dict[FrozenSet[str], Tuple[Tuple[str, ...], int]]:
    """Map each surfaced cluster's member set -> (sorted signals, size), so
    assertions are independent of the clusters list's own outer ordering."""
    return {frozenset(c["members"]): (tuple(sorted(c["signals"])), c["size"]) for c in clusters}


# --------------------------------------------------------------------------- #
# Test base: realistic fixture + real subprocess drivers
# --------------------------------------------------------------------------- #


class ClusterDashboardE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

        self.wq_base = self.tmp_path / "wq"
        self.queue_dir = self.wq_base / "queue"
        self.queue_dir.mkdir(parents=True)
        (self.wq_base / "picked").mkdir()

        # Empty specs root: --root must point at a real, deterministic,
        # empty directory so `scan()` never picks up this machine's actual
        # docs/specs and the render's "Active work"/backlog sections stay
        # empty regardless of where this test runs.
        self.specs_root = self.tmp_path / "specs"
        self.specs_root.mkdir()

        self.ids = _build_realistic_queue(self.queue_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, wq_base: Optional[Path] = None) -> Dict[str, str]:
        env = dict(os.environ)
        env["WORK_QUEUE_DIR"] = str(wq_base or self.wq_base)
        # Isolate cluster_telemetry from this machine's real worktrail-home cluster-log.jsonl
        # (item 6, cluster-precision dashboard surfacing): without this, dashboard.py
        # reads back real historical outcome counts, which is both a test-isolation
        # leak and a source of cross-run nondeterminism these e2e tests assert against.
        env["GO_CLUSTER_LOG"] = str(self.tmp_path / "cluster-log.jsonl")
        return env

    def _list_queue_json(self, env: Dict[str, str]) -> str:
        """Real `work_queue.py list --json` -- the same call `/go` makes
        before feeding the result into `dashboard.py --queue-json`."""
        r = subprocess.run(
            [sys.executable, "-m", "worktrail.workqueue.work_queue", "list", "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return r.stdout.strip()

    def _run_dashboard(
        self,
        env: Dict[str, str],
        *,
        root: Optional[Path] = None,
        queue_json: Optional[str] = None,
    ) -> Dict:
        args = [sys.executable, "-m", "worktrail.router.dashboard",
                "--root", str(root or self.specs_root), "--json"]
        if queue_json is not None:
            args += ["--queue-json", queue_json]
        r = subprocess.run(args, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(r.stdout)


# --------------------------------------------------------------------------- #
# Complete user journey (happy path)
# --------------------------------------------------------------------------- #


class HappyPath(ClusterDashboardE2E):
    def test_expected_clusters_surfaced_and_others_excluded(self):
        """AC-001-009, AC-014's scenario shape: the real dashboard.py --json
        clusters field contains exactly the 3-brief cluster, the
        duplicate-slug pair, and the near-identical overlap pair -- and
        excludes the ordinary overlap pair, the blocked-by pair, and every
        unrelated singleton."""
        env = self._env()
        qjson = self._list_queue_json(env)
        data = self._run_dashboard(env, queue_json=qjson)

        by_members = _cluster_by_members(data["clusters"])
        self.assertEqual(len(by_members), 3, msg=data["clusters"])

        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]
        b1, b2 = self.ids["b1"], self.ids["b2"]
        c1, c2 = self.ids["c1"], self.ids["c2"]

        self.assertIn(frozenset({a1, a2, a3}), by_members)
        self.assertIn(frozenset({b1, b2}), by_members)
        self.assertIn(frozenset({c1, c2}), by_members)

        excluded = [
            self.ids["d1"],
            self.ids["d2"],
            self.ids["e1"],
            self.ids["e2"],
            self.ids["f1"],
            self.ids["f2"],
            self.ids["f3"],
        ]
        all_surfaced_members = set().union(*by_members.keys())
        for stem in excluded:
            self.assertNotIn(stem, all_surfaced_members, msg=f"{stem} unexpectedly surfaced")

    def test_signals_and_size_correct_per_cluster(self):
        """AC-010: every surfaced cluster/pair states the correct signal(s)
        and member count."""
        env = self._env()
        qjson = self._list_queue_json(env)
        data = self._run_dashboard(env, queue_json=qjson)
        by_members = _cluster_by_members(data["clusters"])

        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]
        b1, b2 = self.ids["b1"], self.ids["b2"]
        c1, c2 = self.ids["c1"], self.ids["c2"]

        self.assertEqual(
            by_members[frozenset({a1, a2, a3})], (("related-link", "same-target-spec"), 3)
        )
        self.assertEqual(by_members[frozenset({b1, b2})], (("duplicate-slug",), 2))
        self.assertEqual(by_members[frozenset({c1, c2})], (("focus-overlap",), 2))

    def test_rendered_text_has_consolidatable_section_with_next_step(self):
        """AC-011, REQ-016, REQ-019: printed text includes the header, one
        line naming each cluster's members + signals, and exactly one
        trailing suggested next step."""
        env = self._env()
        qjson = self._list_queue_json(env)
        data = self._run_dashboard(env, queue_json=qjson)
        lines = data["rendered"].splitlines()

        self.assertIn("🔗 Consolidatable briefs (3)", lines)
        start = lines.index("🔗 Consolidatable briefs (3)")

        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]
        b1, b2 = self.ids["b1"], self.ids["b2"]
        c1, c2 = self.ids["c1"], self.ids["c2"]

        expected_rows = {
            f"    • {a1}, {a2}, {a3} — related-link, same-target-spec",
            f"    • {b1}, {b2} — duplicate-slug",
            f"    • {c1}, {c2} — focus-overlap",
        }
        actual_rows = set(lines[start + 1 : start + 4])
        self.assertEqual(actual_rows, expected_rows)

        # Exactly one trailing suggested-next-step line for the whole section.
        self.assertEqual(
            lines[start + 4], "    → Review for consolidation before dispatching separately."
        )
        # No second header (section appears once).
        self.assertEqual(lines.count("🔗 Consolidatable briefs (3)"), 1)


# --------------------------------------------------------------------------- #
# Error handling and edge cases
# --------------------------------------------------------------------------- #


class ErrorHandling(ClusterDashboardE2E):
    def test_corrupted_brief_excluded_without_aborting_render(self):
        """AC-015: a corrupted brief (unterminated frontmatter, colliding
        slug with a1) is silently excluded; every other cluster is
        unaffected and the render still completes (subprocess exit 0)."""
        env = self._env()
        baseline_json = self._list_queue_json(env)
        baseline = self._run_dashboard(env, queue_json=baseline_json)
        baseline_clusters = _cluster_by_members(baseline["clusters"])

        (self.queue_dir / "20260707-050000-alpha-feature-work.md").write_text(
            _CORRUPT_BRIEF, encoding="utf-8"
        )

        env2 = self._env()
        qjson2 = self._list_queue_json(env2)  # itself must not crash either
        data = self._run_dashboard(env2, queue_json=qjson2)  # exit 0 asserted inside
        clusters = _cluster_by_members(data["clusters"])

        self.assertEqual(clusters, baseline_clusters)
        all_members = set().union(*clusters.keys()) if clusters else set()
        self.assertNotIn("20260707-050000-alpha-feature-work", all_members)

    def test_empty_queue_dir_no_clusters_and_rest_byte_identical(self):
        """AC-012, REQ-017: an empty (and separately, nonexistent) queue
        directory yields clusters: [], no cluster section, and the rest of
        the rendered text is byte-for-byte identical to the populated run
        with the cluster block excised."""
        env_full = self._env()
        qjson = self._list_queue_json(env_full)
        full = self._run_dashboard(env_full, queue_json=qjson)
        self.assertTrue(full["clusters"])
        full_lines = full["rendered"].splitlines()
        self.assertIn("🔗 Consolidatable briefs (3)", full_lines)

        # (1) Empty queue dir: exists, zero .md files.
        empty_base = self.tmp_path / "wq-empty"
        (empty_base / "queue").mkdir(parents=True)
        (empty_base / "picked").mkdir()
        env_empty = self._env(wq_base=empty_base)
        empty = self._run_dashboard(env_empty, queue_json=qjson)
        self.assertEqual(empty["clusters"], [])
        self.assertNotIn("🔗", empty["rendered"])

        # (2) Nonexistent queue dir: base exists, "queue" subdir absent.
        missing_base = self.tmp_path / "wq-missing"
        missing_base.mkdir()
        (missing_base / "picked").mkdir()
        env_missing = self._env(wq_base=missing_base)
        missing = self._run_dashboard(env_missing, queue_json=qjson)
        self.assertEqual(missing["clusters"], [])
        self.assertNotIn("🔗", missing["rendered"])

        # Byte-for-byte: strip the cluster block (header + 3 rows + 1
        # suggestion line) out of the populated render and compare.
        start = full_lines.index("🔗 Consolidatable briefs (3)")
        block_len = 1 + 3 + 1
        stripped = full_lines[:start] + full_lines[start + block_len :]
        self.assertEqual("\n".join(stripped), empty["rendered"])
        self.assertEqual(empty["rendered"], missing["rendered"])


# --------------------------------------------------------------------------- #
# Data persistence / determinism / integration with existing sections
# --------------------------------------------------------------------------- #


class DataIntegrityAndDeterminism(ClusterDashboardE2E):
    def test_cluster_detection_never_modifies_brief_files(self):
        """REQ-NR002: no brief .md file's contents or mtime change as a
        result of running cluster detection, even across repeated renders."""
        files = sorted(self.queue_dir.glob("*.md"))

        def _fingerprint() -> Dict[str, Tuple[int, str]]:
            return {
                f.name: (f.stat().st_mtime_ns, hashlib.sha256(f.read_bytes()).hexdigest())
                for f in sorted(self.queue_dir.glob("*.md"))
            }

        before = _fingerprint()
        self.assertEqual(len(before), len(files))

        env = self._env()
        qjson = self._list_queue_json(env)
        self._run_dashboard(env, queue_json=qjson)
        self._run_dashboard(env, queue_json=qjson)

        after = _fingerprint()
        self.assertEqual(before, after)

    def test_json_clusters_deterministic_across_two_runs(self):
        """AC-017: running dashboard.py --json twice against an unchanged
        fixture produces byte-identical clusters output both times."""
        env = self._env()
        qjson = self._list_queue_json(env)
        d1 = self._run_dashboard(env, queue_json=qjson)
        d2 = self._run_dashboard(env, queue_json=qjson)
        self.assertEqual(
            json.dumps(d1["clusters"], sort_keys=True), json.dumps(d2["clusters"], sort_keys=True)
        )
        self.assertEqual(d1["rendered"], d2["rendered"])

    def test_existing_sections_present_and_structurally_unchanged(self):
        """AC-013: the existing 📥 Queued handoffs section and category_actions
        remain present and byte-identical whether or not clusters are surfaced.

        `category_items` was also asserted byte-identical here under spec 018,
        when cluster detection was informational-only (decision-log.md
        DEC-001). The 2026-07-14 consolidate-cluster-action delta intentionally
        supersedes that: `category_items['workqueue']` now surfaces a
        `type: "cluster"` item ranked ahead of individual queue items when
        clusters are present (REQ-CHG-006/007), so it is expected to differ
        between the clustered `full` fixture and the cluster-free `empty` one.
        The still-valid "no clusters -> no behavior change" half of that
        invariant is covered by TASK-CHG-004's dedicated regression test."""
        env_full = self._env()
        qjson = self._list_queue_json(env_full)
        full = self._run_dashboard(env_full, queue_json=qjson)

        empty_base = self.tmp_path / "wq-empty2"
        (empty_base / "queue").mkdir(parents=True)
        (empty_base / "picked").mkdir()
        env_empty = self._env(wq_base=empty_base)
        empty = self._run_dashboard(env_empty, queue_json=qjson)

        self.assertEqual(full["category_actions"], empty["category_actions"])

        total_queue_briefs = len(json.loads(qjson)["briefs"])
        self.assertEqual(total_queue_briefs, len(self.ids))
        self.assertIn(f"📥 Queued handoffs ({total_queue_briefs})", full["rendered"])
        self.assertIn("clusters", full)
        self.assertIsInstance(full["category_actions"], list)
        self.assertIsInstance(full["category_items"], dict)


# --------------------------------------------------------------------------- #
# Consolidate-cluster full pipeline (spec 018 change 2026-07-14, TASK-CHG-004):
# work_queue.py list --json -> dashboard.py --json -> consolidate_cluster.py
# preview/execute, chained exactly as the /go conductor does, driven entirely
# through subprocess calls -- no mocking of any of the three scripts'
# internals. Reuses ClusterDashboardE2E's realistic multi-brief fixture: the
# 3-member cluster "a" (a1/a2/a3, related-link + same-target-spec) and the
# two 2-member clusters "b" (duplicate-slug) and "c" (focus-overlap).
# --------------------------------------------------------------------------- #


class ConsolidateClusterE2E(ClusterDashboardE2E):
    def _picked_dir(self) -> Path:
        return self.wq_base / "picked"

    def _cluster_item(self, data: Dict, members: FrozenSet[str]) -> Dict:
        """The `type: "cluster"` picker item whose members == `members`, from
        a dashboard.py --json run's category_items['workqueue']."""
        for item in data["category_items"]["workqueue"]:
            if item.get("type") == "cluster" and frozenset(item.get("members", [])) == members:
                return item
        self.fail(f"no cluster item for {sorted(members)} in {data['category_items']['workqueue']}")

    def _preview(self, member_ids: List[str], env: Dict[str, str]) -> Dict:
        """Real `consolidate_cluster.py preview` subprocess call."""
        r = subprocess.run(
            [sys.executable, "-m", "worktrail.router.consolidate_cluster", "preview", *member_ids, "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(r.stdout)

    def _execute(
        self,
        member_ids: List[str],
        draft: Optional[Dict],
        env: Dict[str, str],
        *,
        confirm: bool,
    ) -> Dict:
        """Real `consolidate_cluster.py execute` subprocess call -- the
        confirm/decline boundary is exercised as a CLI flag, exactly the
        non-interactive boundary the change spec's Testing Strategy calls
        for (no AskUserQuestion is driven here)."""
        args = [
            sys.executable,
            "-m", "worktrail.router.consolidate_cluster",
            "execute",
            *member_ids,
            "--draft",
            json.dumps(draft or {}),
            "--confirm" if confirm else "--decline",
            "--json",
        ]
        r = subprocess.run(args, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(r.stdout)

    def _claim(self, identifier: str, env: Dict[str, str]) -> Dict:
        """Real `work_queue.py claim` subprocess call -- simulates a second
        session claiming a cluster member out-of-band between the dashboard
        snapshot and preview/execute."""
        r = subprocess.run(
            [sys.executable, "-m", "worktrail.workqueue.work_queue", "claim", identifier, "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(r.stdout)

    def _consolidate_cluster_a(self, env: Dict[str, str]) -> Dict[str, Any]:
        """Full happy-path pipeline for the 3-member cluster "a"
        (a1/a2/a3): snapshot -> preview -> execute --confirm. Returns the
        dashboard snapshot, cluster item, preview, and execute result, for
        reuse by both the happy-path and data-persistence assertions."""
        qjson = self._list_queue_json(env)
        dash = self._run_dashboard(env, queue_json=qjson)
        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]
        cluster_item = self._cluster_item(dash, frozenset({a1, a2, a3}))
        preview = self._preview(cluster_item["members"], env)
        self.assertFalse(preview["withdrawn"])
        self.assertIsNotNone(preview["draft"])
        execute = self._execute(preview["resolvable_members"], preview["draft"], env, confirm=True)
        self.assertEqual(execute["status"], "written")
        return {
            "dash": dash,
            "cluster_item": cluster_item,
            "preview": preview,
            "execute": execute,
            "new_brief_id": Path(execute["new_brief_path"]).stem,
        }


class HappyPathConsolidation(ConsolidateClusterE2E):
    def test_happy_path_full_pipeline(self):
        """AC-001, AC-003, AC-006, AC-007: the real dashboard cluster item
        for the 3-member cluster is ranked ahead of individual queue items
        and none of its members double as individual items; preview drafts a
        non-null, non-withdrawn consolidation; execute --confirm leaves
        exactly one new brief in queue/ and moves every member to picked/
        with status: done and a ## Superseded note; a follow-up list --json
        shows only the consolidated brief, not the superseded originals."""
        env = self._env()
        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]

        before_queue = {f.name for f in self.queue_dir.glob("*.md")}

        qjson = self._list_queue_json(env)
        dash = self._run_dashboard(env, queue_json=qjson)
        cluster_item = self._cluster_item(dash, frozenset({a1, a2, a3}))
        self.assertEqual(cluster_item["action"], "consolidate-cluster")
        self.assertEqual(sorted(cluster_item["members"]), sorted([a1, a2, a3]))

        workqueue = dash["category_items"]["workqueue"]
        self.assertEqual(workqueue[0]["type"], "cluster")
        queue_item_ids = {i["id"] for i in workqueue if i.get("type") == "queue"}
        self.assertFalse(queue_item_ids & {a1, a2, a3})

        preview = self._preview(cluster_item["members"], env)
        self.assertFalse(preview["withdrawn"])
        self.assertIsNotNone(preview["draft"])

        execute = self._execute(preview["resolvable_members"], preview["draft"], env, confirm=True)
        self.assertEqual(execute["status"], "written")
        self.assertEqual(sorted(execute["members_completed"]), sorted([a1, a2, a3]))
        self.assertEqual(execute["members_skipped"], [])

        after_queue = {f.name for f in self.queue_dir.glob("*.md")}
        new_files = after_queue - before_queue
        self.assertEqual(len(new_files), 1, msg=new_files)
        new_brief_name = next(iter(new_files))
        self.assertEqual(str(self.queue_dir / new_brief_name), execute["new_brief_path"])
        new_brief_id = new_brief_name[:-3]

        picked_dir = self._picked_dir()
        for member_id in (a1, a2, a3):
            picked_path = picked_dir / f"{member_id}.md"
            self.assertTrue(picked_path.exists(), msg=f"{member_id} missing from picked/")
            body = picked_path.read_text(encoding="utf-8")
            self.assertIn("status: done", body)
            self.assertIn("## Superseded", body)
            self.assertIn(new_brief_id, body)

        after_list = json.loads(self._list_queue_json(env))
        after_filenames = {b["filename"] for b in after_list["briefs"]}
        self.assertIn(new_brief_name, after_filenames)
        self.assertNotIn(f"{a1}.md", after_filenames)
        self.assertNotIn(f"{a2}.md", after_filenames)
        self.assertNotIn(f"{a3}.md", after_filenames)


class DeclineConsolidation(ConsolidateClusterE2E):
    def test_decline_performs_zero_writes(self):
        """AC-002, AC-004: consolidate_cluster.py execute --decline performs
        no writes -- a work_queue.py list --json snapshot taken immediately
        before and after is byte-for-byte identical, and no file under
        queue/ or picked/ was added, moved, or modified."""
        env = self._env()
        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]

        qjson = self._list_queue_json(env)
        dash = self._run_dashboard(env, queue_json=qjson)
        cluster_item = self._cluster_item(dash, frozenset({a1, a2, a3}))
        preview = self._preview(cluster_item["members"], env)
        self.assertFalse(preview["withdrawn"])

        picked_dir = self._picked_dir()
        before_list = self._list_queue_json(env)
        before_queue = {f.name: f.read_bytes() for f in self.queue_dir.glob("*.md")}
        before_picked = {f.name: f.read_bytes() for f in picked_dir.glob("*.md")}

        execute = self._execute(preview["resolvable_members"], preview["draft"], env, confirm=False)
        self.assertEqual(execute["status"], "declined")
        self.assertIsNone(execute["new_brief_path"])

        after_list = self._list_queue_json(env)
        self.assertEqual(before_list, after_list)

        after_queue = {f.name: f.read_bytes() for f in self.queue_dir.glob("*.md")}
        after_picked = {f.name: f.read_bytes() for f in picked_dir.glob("*.md")}
        self.assertEqual(before_queue, after_queue)
        self.assertEqual(before_picked, after_picked)


class RaceStalenessConsolidation(ConsolidateClusterE2E):
    def test_two_member_cluster_withdrawn_when_claim_drops_below_two(self):
        """AC-005: claiming one member of the 2-member cluster "b"
        (duplicate-slug: b1/b2) out-of-band between the dashboard snapshot
        and preview excludes it from resolvable_members and, since that
        drops resolvable membership below 2, withdraws the whole cluster
        (no draft)."""
        env = self._env()
        b1, b2 = self.ids["b1"], self.ids["b2"]

        qjson = self._list_queue_json(env)
        dash = self._run_dashboard(env, queue_json=qjson)
        cluster_item = self._cluster_item(dash, frozenset({b1, b2}))

        claim = self._claim(b2, env)
        self.assertEqual(claim["status"], "claimed")

        preview = self._preview(cluster_item["members"], env)
        self.assertNotIn(b2, preview["resolvable_members"])
        self.assertIn(b2, preview["excluded_members"])
        self.assertTrue(preview["withdrawn"])
        self.assertIsNone(preview["draft"])

    def test_three_member_cluster_not_withdrawn_execute_still_succeeds(self):
        """AC-005: claiming one member of the 3-member cluster "a" out-of-
        band before preview leaves 2 resolvable -- the cluster is NOT
        withdrawn, and execute --confirm on the 2 remaining members still
        succeeds normally, writing a consolidated brief from just those 2."""
        env = self._env()
        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]

        qjson = self._list_queue_json(env)
        dash = self._run_dashboard(env, queue_json=qjson)
        cluster_item = self._cluster_item(dash, frozenset({a1, a2, a3}))

        claim = self._claim(a2, env)
        self.assertEqual(claim["status"], "claimed")

        preview = self._preview(cluster_item["members"], env)
        self.assertNotIn(a2, preview["resolvable_members"])
        self.assertIn(a2, preview["excluded_members"])
        self.assertFalse(preview["withdrawn"])
        self.assertIsNotNone(preview["draft"])
        self.assertEqual(sorted(preview["resolvable_members"]), sorted([a1, a3]))

        execute = self._execute(preview["resolvable_members"], preview["draft"], env, confirm=True)
        self.assertEqual(execute["status"], "written")
        self.assertEqual(sorted(execute["members_completed"]), sorted([a1, a3]))
        self.assertEqual(execute["members_skipped"], [])

        picked_dir = self._picked_dir()
        for member_id in (a1, a3):
            self.assertTrue((picked_dir / f"{member_id}.md").exists())
        # a2 was claimed out-of-band (not by the consolidation), so it's in
        # picked/ too, but NOT via the consolidation's own claim/done path --
        # it must not carry a Superseded note or status: done from this run.
        a2_body = (picked_dir / f"{a2}.md").read_text(encoding="utf-8")
        self.assertNotIn("## Superseded", a2_body)


class DataPersistenceAfterConsolidation(ConsolidateClusterE2E):
    def test_consolidated_brief_replaces_members_and_is_not_reclustered(self):
        """Data persistence and retrieval end-to-end: after the happy-path
        run, a fresh dashboard.py --json call against the now-mutated
        fixture shows the superseded members in no cluster's members list
        and in no `type: "queue"` item; the new consolidated brief (which
        has no cluster-mates of its own, since its `related:` targets are
        now in picked/, not queue/) is never a member of any surfaced
        cluster, and appears as an ordinary `type: "queue"` item wherever it
        is visible."""
        env = self._env()
        a1, a2, a3 = self.ids["a1"], self.ids["a2"], self.ids["a3"]
        result = self._consolidate_cluster_a(env)
        new_id = result["new_brief_id"]

        qjson2 = self._list_queue_json(env)
        dash2 = self._run_dashboard(env, queue_json=qjson2)

        all_cluster_members: set = set()
        for cluster in dash2["clusters"]:
            all_cluster_members.update(cluster.get("members", []))
        self.assertFalse(all_cluster_members & {a1, a2, a3})
        self.assertNotIn(new_id, all_cluster_members)

        for item in dash2["category_items"]["workqueue"]:
            if item.get("type") == "cluster":
                self.assertFalse(set(item.get("members", [])) & {a1, a2, a3})
                self.assertNotIn(new_id, item.get("members", []))
            elif item.get("type") == "queue":
                self.assertNotIn(item.get("id"), {a1, a2, a3})
                if item.get("id") == new_id:
                    self.assertEqual(item["type"], "queue")


# --------------------------------------------------------------------------- #
# Supplemental (TASK-CHG-004): restores the still-valid half of spec 018's
# original AC-013 `category_items` invariant. TASK-CHG-001 narrowed
# DataIntegrityAndDeterminism.test_existing_sections_present_and_structurally_
# unchanged to `category_actions` only, since `category_items` now
# legitimately differs between a clustered fixture and a cluster-free one.
# The narrowing does not apply when there are no clusters at all -- this
# restores that no-clusters coverage under the new contract.
# --------------------------------------------------------------------------- #


class NoClusterCategoryItemsInvariant(ClusterDashboardE2E):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

        self.wq_base = self.tmp_path / "wq"
        self.queue_dir = self.wq_base / "queue"
        self.queue_dir.mkdir(parents=True)
        (self.wq_base / "picked").mkdir()

        self.specs_root = self.tmp_path / "specs"
        self.specs_root.mkdir()

        # Unrelated singleton briefs only (mirrors group (f) of
        # _build_realistic_queue) -- no related:/target-spec/focus-overlap
        # signal between any pair, so compute_clusters() returns [] here.
        self.ids = {
            "f1": _write_brief(
                self.queue_dir,
                "20260706-090000-improve-error-messages.md",
                repo="repo-f",
                focus="Improve error messages shown when API requests fail unexpectedly",
            ),
            "f2": _write_brief(
                self.queue_dir,
                "20260706-091000-optimize-image-loading.md",
                repo="repo-g",
                focus="Optimize lazy image loading for the gallery page performance",
            ),
            "f3": _write_brief(
                self.queue_dir,
                "20260706-092000-standalone-no-repo-note.md",
                repo=None,
                focus="Miscellaneous note captured without an associated repository context",
            ),
        }

    def test_category_items_byte_identical_full_vs_empty_when_no_clusters(self):
        """No-clusters half of the original AC-013 invariant: with an empty
        `clusters` output on both sides, `category_items` is byte-identical
        between a populated (cluster-free) queue and a truly empty one --
        exactly the pre-this-change behavior, restored under the new
        cluster-aware `build_category_items` contract."""
        env_full = self._env()
        qjson = self._list_queue_json(env_full)
        full = self._run_dashboard(env_full, queue_json=qjson)
        self.assertEqual(full["clusters"], [])

        empty_base = self.tmp_path / "wq-empty"
        (empty_base / "queue").mkdir(parents=True)
        (empty_base / "picked").mkdir()
        env_empty = self._env(wq_base=empty_base)
        empty = self._run_dashboard(env_empty, queue_json=qjson)
        self.assertEqual(empty["clusters"], [])

        self.assertEqual(
            json.dumps(full["category_items"], sort_keys=True),
            json.dumps(empty["category_items"], sort_keys=True),
        )


# --------------------------------------------------------------------------- #
# Supplemental (nice-to-have, not blocking): informal timing observation
# --------------------------------------------------------------------------- #


class SupplementalPerformance(unittest.TestCase):
    def test_tens_of_briefs_completes_quickly(self):
        """Informal timing spot-check only -- the spec sets no formal
        performance criterion. A generous bound avoids CI flakiness while
        still catching an accidental quadratic blowup."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            wq_base = tmp_path / "wq"
            queue_dir = wq_base / "queue"
            queue_dir.mkdir(parents=True)
            (wq_base / "picked").mkdir()
            specs_root = tmp_path / "specs"
            specs_root.mkdir()

            for i in range(40):
                _write_brief(
                    queue_dir,
                    f"202607{10 + i // 10:02d}-{i:06d}-synthetic-brief-{i}.md",
                    repo=f"repo-{i % 5}",
                    target_spec=f"0{i % 7}-spec" if i % 3 == 0 else None,
                    focus=f"Synthetic filler text number {i} covering unrelated topic area",
                )

            env = dict(os.environ)
            env["WORK_QUEUE_DIR"] = str(wq_base)
            started = time.monotonic()
            r = subprocess.run(
                [sys.executable, "-m", "worktrail.router.dashboard", "--root", str(specs_root), "--json"],
                capture_output=True,
                text=True,
                env=env,
            )
            elapsed = time.monotonic() - started
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            self.assertLess(
                elapsed, 10.0, msg=f"compute_clusters took {elapsed:.2f}s for 40 briefs"
            )


if __name__ == "__main__":
    unittest.main()
