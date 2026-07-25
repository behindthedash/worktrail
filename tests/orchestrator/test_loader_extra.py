#!/usr/bin/env python3
"""Extra coverage for loader.py: error paths and edge cases."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator
from worktrail.taskformats.devkit import source as loader


class ParseFrontmatterTests(unittest.TestCase):
    def test_no_frontmatter_returns_empty(self):
        self.assertEqual(loader.parse_frontmatter("no dashes here\nbody"), {})

    def test_comment_line_skipped(self):
        text = "---\n# comment\nid: T1\n---\nbody"
        fm = loader.parse_frontmatter(text)
        self.assertEqual(fm["id"], "T1")
        self.assertNotIn("# comment", fm)

    def test_line_without_colon_skipped(self):
        text = "---\nid: T1\njust-a-value\n---\nbody"
        fm = loader.parse_frontmatter(text)
        self.assertEqual(fm["id"], "T1")
        self.assertNotIn("just-a-value", fm)

    def test_empty_inner_list(self):
        text = "---\ndeps: []\n---\nbody"
        fm = loader.parse_frontmatter(text)
        self.assertEqual(fm["deps"], [])

    def test_inline_list_parsed(self):
        text = "---\nfiles: [a.ts, b.ts]\n---\nbody"
        fm = loader.parse_frontmatter(text)
        self.assertEqual(fm["files"], ["a.ts", "b.ts"])

    def test_multiline_list_parsed(self):
        text = "---\nid: T1\nfiles:\n  - src/lib/foo.ts\n  - src/lib/bar.ts\nkind: impl\n---\nbody"
        fm = loader.parse_frontmatter(text)
        self.assertEqual(fm["files"], ["src/lib/foo.ts", "src/lib/bar.ts"])
        self.assertEqual(fm["id"], "T1")
        self.assertEqual(fm["kind"], "impl")

    def test_multiline_list_inline_mixed(self):
        text = "---\ndeps: [TASK-001]\nfiles:\n  - src/a.ts\n  - src/b.ts\ntimeout: 900\n---\nbody"
        fm = loader.parse_frontmatter(text)
        self.assertEqual(fm["deps"], ["TASK-001"])
        self.assertEqual(fm["files"], ["src/a.ts", "src/b.ts"])
        self.assertEqual(fm["timeout"], "900")

    def test_multiline_list_key_with_no_items(self):
        text = "---\nid: T1\nfiles:\nkind: e2e\n---\nbody"
        fm = loader.parse_frontmatter(text)
        self.assertNotIn("files", fm)
        self.assertEqual(fm["kind"], "e2e")

    def test_quoted_value_stripped(self):
        text = '---\ntitle: "My Task"\n---\nbody'
        fm = loader.parse_frontmatter(text)
        self.assertEqual(fm["title"], "My Task")


class FindTasksDirTests(unittest.TestCase):
    def test_main_tasks_dir_preferred(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "TASK-001.md").write_text("---\nid: TASK-001\n---\n")
            result = loader._find_tasks_dir(spec)
            self.assertEqual(result, tasks)

    def test_change_dir_passed_directly_resolves_its_own_tasks(self):
        """The real, documented contract: callers pass $CHANGE_DIR itself for
        change-spec work (pipeline-details.md modify-pipeline step 4), so
        _find_tasks_dir just needs to resolve THAT folder's own tasks/ --
        no changes/<name>/ auto-discovery involved."""
        with tempfile.TemporaryDirectory() as t:
            change_dir = Path(t) / "001-spec" / "changes" / "20260101-fix"
            tasks = change_dir / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "TASK-CHG-001.md").write_text("---\nid: TASK-CHG-001\n---\n")
            result = loader._find_tasks_dir(change_dir)
            self.assertEqual(result, tasks)

    def test_no_tasks_returns_main_path(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            spec.mkdir()
            result = loader._find_tasks_dir(spec)
            self.assertEqual(result, spec / "tasks")


class LoadSpecTests(unittest.TestCase):
    def _write_task(self, tasks_dir: Path, name: str, body: str) -> None:
        (tasks_dir / name).write_text(body)

    def test_no_tasks_dir_raises(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "missing-spec"
            spec.mkdir()
            with self.assertRaises(FileNotFoundError):
                loader.load_spec(str(spec))

    def test_aux_files_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\nfiles: [a.ts]\nkind: impl\n---\n",
            )
            self._write_task(tasks, "TASK-001--review.md", "---\nid: TASK-001--review\n---\n")
            _, loaded = loader.load_spec(str(spec))
            ids = [t["id"] for t in loaded]
            self.assertIn("TASK-001", ids)
            self.assertNotIn("TASK-001--review", ids)

    def test_malformed_timeout_normalised_to_none(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\ntimeout: abc\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertIsNone(loaded[0]["timeout"])

    def test_nonpositive_timeout_normalised_to_none(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\ntimeout: 0\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertIsNone(loaded[0]["timeout"])

    def test_valid_timeout_parsed(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\ntimeout: 1800\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["timeout"], 1800)

    def test_depends_on_accepted_as_dependencies_alias(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndepends_on: []\n"
                "files: [a.ts]\nkind: impl\n---\n",
            )
            self._write_task(
                tasks,
                "TASK-002.md",
                "---\nid: TASK-002\nstatus: pending\ndepends_on: [TASK-001]\n"
                "files: [b.ts]\nkind: impl\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            by_id = {t["id"]: t for t in loaded}
            self.assertEqual(by_id["TASK-002"]["deps"], ["TASK-001"])

    def test_dependencies_takes_precedence_over_depends_on(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: [TASK-000]\n"
                "depends_on: [TASK-999]\nfiles: [a.ts]\nkind: impl\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["deps"], ["TASK-000"])

    def test_spec_id_is_folder_name(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "042-my-feature"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\n---\n",
            )
            spec_id, _ = loader.load_spec(str(spec))
            self.assertEqual(spec_id, "042-my-feature")

    def test_complexity_and_domain_projected_verbatim(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\ncomplexity: hard\n"
                "domain: backend\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["complexity"], "hard")
            self.assertEqual(loaded[0]["domain"], "backend")

    def test_missing_complexity_and_domain_default(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["complexity"], "standard")
            self.assertEqual(loaded[0]["domain"], "")

    def test_unknown_complexity_normalised_to_standard(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\ncomplexity: bogus\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["complexity"], "standard")

    def test_previously_projected_fields_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: [TASK-000]\n"
                "files: [a.ts]\nkind: impl\nreqs: [REQ-001]\ntimeout: 900\n"
                "title: Sample\nreview: skip\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            task = loaded[0]
            self.assertEqual(task["id"], "TASK-001")
            self.assertEqual(task["deps"], ["TASK-000"])
            self.assertEqual(task["files"], ["a.ts"])
            self.assertEqual(task["kind"], "impl")
            self.assertEqual(task["reqs"], ["REQ-001"])
            self.assertEqual(task["status"], "pending")
            self.assertEqual(task["timeout"], 900)
            self.assertEqual(task["title"], "Sample")
            self.assertEqual(task["review"], "skip")


class ExternalDependenciesTests(unittest.TestCase):
    """spec 025-cross-spec-task-dependencies TASK-001: `external-dependencies:`
    is a separate, additive field alongside `dependencies:`/`depends_on:` --
    that field's parsing/aliasing/resolution must not change (AC-002)."""

    def _write_task(self, tasks_dir: Path, name: str, body: str) -> None:
        (tasks_dir / name).write_text(body)

    def test_external_deps_entry_parsed(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\n"
                "external-dependencies: [098-x/TASK-036]\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["external_deps"], ["098-x/TASK-036"])

    def test_external_deps_absent_defaults_to_empty_list(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["external_deps"], [])

    def test_external_deps_inline_list_two_entries_in_order(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\n"
                "external-dependencies: [098-x/TASK-036, 099-y/TASK-002]\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(
                loaded[0]["external_deps"],
                ["098-x/TASK-036", "099-y/TASK-002"],
            )

    def test_missing_separator_produces_one_warning(self):
        warnings = loader.validate_external_dependencies(["TASK-036"], label="TASK-001.md")
        self.assertEqual(len(warnings), 1)
        self.assertIn("TASK-001.md", warnings[0])
        self.assertIn("TASK-036", warnings[0])

    def test_empty_spec_id_segment_produces_one_warning(self):
        warnings = loader.validate_external_dependencies(["/TASK-036"], label="TASK-001.md")
        self.assertEqual(len(warnings), 1)

    def test_empty_task_id_segment_produces_one_warning(self):
        warnings = loader.validate_external_dependencies(["098-x/"], label="TASK-001.md")
        self.assertEqual(len(warnings), 1)

    def test_well_formed_entry_produces_no_warnings(self):
        warnings = loader.validate_external_dependencies(["098-x/TASK-036"], label="TASK-001.md")
        self.assertEqual(warnings, [])

    def test_malformed_entry_retained_in_external_deps(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\n"
                "external-dependencies: [TASK-036]\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["external_deps"], ["TASK-036"])
            self.assertEqual(len(loaded[0]["external_deps_warnings"]), 1)

    def test_dependencies_and_external_dependencies_independent_no_cross_contamination(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks = spec / "tasks"
            tasks.mkdir(parents=True)
            self._write_task(
                tasks,
                "TASK-001.md",
                "---\nid: TASK-001\nstatus: pending\ndependencies: [TASK-000]\n"
                "files: [a.ts]\nkind: impl\n"
                "external-dependencies: [098-x/TASK-036]\n---\n",
            )
            _, loaded = loader.load_spec(str(spec))
            task = loaded[0]
            self.assertEqual(task["deps"], ["TASK-000"])
            self.assertEqual(task["external_deps"], ["098-x/TASK-036"])


class FrontmatterKeyValidationTests(unittest.TestCase):
    """route:J loader-frontmatter-schema-validation: an unrecognized
    frontmatter key that looks like a typo of a known field must warn loudly
    instead of being silently dropped -- the failure class root-caused twice
    independently (incidents 20260723-191922 and 20260713-153320, both
    `depends_on` vs `dependencies`)."""

    def test_known_keys_produce_no_warnings(self):
        fm = {
            "id": "TASK-001",
            "title": "x",
            "spec": "docs/specs/001/x.md",
            "lang": "python",
            "status": "pending",
            "dependencies": [],
            "files": [],
            "kind": "impl",
            "reqs": [],
            "timeout": "900",
            "review": "",
            "complexity": "standard",
            "domain": "backend",
            "ac-mapping": [],
            "imp-requirements": [],
            "success-criteria": "",
        }
        self.assertEqual(loader.validate_frontmatter_keys(fm), [])

    def test_depends_on_alias_is_not_a_typo(self):
        """The real incident's exact key -- already an accepted alias, not a
        warn-worthy typo."""
        fm = {"id": "TASK-CHG-001", "depends_on": ["TASK-CHG-000"]}
        self.assertEqual(loader.validate_frontmatter_keys(fm), [])

    def test_close_typo_of_review_warns_with_suggestion(self):
        fm = {"id": "TASK-001", "revie": "skip"}
        warnings = loader.validate_frontmatter_keys(fm)
        self.assertEqual(len(warnings), 1)
        self.assertIn("'revie'", warnings[0])
        self.assertIn("review", warnings[0])

    def test_close_typo_of_dependencies_warns(self):
        fm = {"id": "TASK-001", "dependancies": []}
        warnings = loader.validate_frontmatter_keys(fm)
        self.assertEqual(len(warnings), 1)
        self.assertIn("dependancies", warnings[0])

    def test_unrelated_unknown_key_stays_silent(self):
        """No close match -> silent by design (avoids flagging legitimate
        future field additions as typos)."""
        fm = {"id": "TASK-001", "owner": "alice"}
        self.assertEqual(loader.validate_frontmatter_keys(fm), [])

    def test_label_prefixes_the_warning(self):
        fm = {"id": "TASK-001", "kinf": "impl"}
        warnings = loader.validate_frontmatter_keys(fm, label="TASK-001.md")
        self.assertTrue(warnings[0].startswith("TASK-001.md: "))

    def test_load_spec_attaches_frontmatter_warnings_to_task_dict(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "TASK-001.md").write_text(
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\nrevie: skip\n---\nbody\n"
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(len(loaded[0]["frontmatter_warnings"]), 1)
            self.assertIn("revie", loaded[0]["frontmatter_warnings"][0])

    def test_load_spec_clean_task_has_empty_warnings_list(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec"
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "TASK-001.md").write_text(
                "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
                "files: [a.ts]\nkind: impl\n---\nbody\n"
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["frontmatter_warnings"], [])

    def test_load_spec_real_incident_shape_depends_on_no_warning(self):
        """Seeded with this session's exact incident shape (TASK-CHG-004.md,
        095-synthetic-auth-health-monitoring): depends_on is a recognized
        alias, so load_spec must attach zero warnings for it."""
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec" / "changes" / "2026-07-23--postdeploy"
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "TASK-CHG-004.md").write_text(
                "---\nid: TASK-CHG-004\nstatus: pending\ndepends_on: [TASK-CHG-001]\n"
                "files: [a.ts]\nkind: impl\n---\nbody\n"
            )
            _, loaded = loader.load_spec(str(spec))
            self.assertEqual(loaded[0]["deps"], ["TASK-CHG-001"])
            self.assertEqual(loaded[0]["frontmatter_warnings"], [])


class DependsOnFrontierRegressionTests(unittest.TestCase):
    """End-to-end regression for the false dependency-blind fan-out defect.

    Reproduces the exact incident (datalena spec 095, change
    2026-07-23--postdeploy-config-integrity-smoke): a change-spec task declared
    `depends_on: [TASK-CHG-001]` instead of the canonical `dependencies:` key.
    `loader.load_spec()` silently dropped it to `deps: []`, so
    `coordinator.runnable_frontier()` treated the dependent task as immediately
    runnable -- the pipeline fanned both tasks out in the same tick even though
    one declared a real dependency on the other.
    """

    def _write_task(self, tasks_dir: Path, name: str, body: str) -> None:
        (tasks_dir / name).write_text(body)

    def test_depends_on_task_deferred_to_second_tick(self):
        with tempfile.TemporaryDirectory() as t:
            spec = Path(t) / "001-spec" / "changes" / "2026-01-01--fix"
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir(parents=True)
            self._write_task(
                tasks_dir,
                "TASK-CHG-001.md",
                "---\nid: TASK-CHG-001\nstatus: pending\ndepends_on: []\n"
                "files: [a.ts]\nkind: impl\n---\n",
            )
            self._write_task(
                tasks_dir,
                "TASK-CHG-002.md",
                "---\nid: TASK-CHG-002\nstatus: pending\n"
                "depends_on: [TASK-CHG-001]\nfiles: [b.ts]\nkind: impl\n---\n",
            )
            # spec_rel points at the change folder itself (matching how route F/G
            # actually invokes the orchestrator on a change-spec).
            _, loaded = loader.load_spec(str(spec))

            tick1 = coordinator.runnable_frontier(loaded, max_workers=4)
            self.assertEqual(
                [t["id"] for t in tick1],
                ["TASK-CHG-001"],
                "dependent task must NOT fan out in the same tick as "
                "its undeclared-to-the-old-parser dependency",
            )

            by_id = {t["id"]: t for t in loaded}
            by_id["TASK-CHG-001"]["status"] = "done"
            tick2 = coordinator.runnable_frontier(loaded, max_workers=4)
            self.assertEqual([t["id"] for t in tick2], ["TASK-CHG-002"])


class ResolveExternalDependencyTests(unittest.TestCase):
    """spec 025-cross-spec-task-dependencies TASK-002:
    loader.resolve_external_dependency() -- the single cross-spec resolution
    function every downstream consumer (precheck, frontier gating, worker
    prompt, worktree stacking) calls into."""

    def _write_task(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)

    def test_malformed_ref_no_slash(self):
        # repo_root deliberately does not exist -- a malformed ref must
        # short-circuit before any filesystem access.
        repo_root = Path("/nonexistent/does-not-exist")
        result = loader.resolve_external_dependency(repo_root, "TASK-036")
        self.assertEqual(
            result,
            {
                "ref": "TASK-036",
                "resolved": False,
                "satisfied": False,
                "status": None,
                "reason": "malformed",
            },
        )

    def test_empty_spec_id_segment_is_malformed(self):
        result = loader.resolve_external_dependency(Path("/nonexistent"), "/TASK-036")
        self.assertEqual(result["reason"], "malformed")
        self.assertFalse(result["resolved"])

    def test_empty_task_id_segment_is_malformed(self):
        result = loader.resolve_external_dependency(Path("/nonexistent"), "098-x/")
        self.assertEqual(result["reason"], "malformed")
        self.assertFalse(result["resolved"])

    def test_spec_id_folder_not_found(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            (repo_root / "docs" / "specs").mkdir(parents=True)
            result = loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertFalse(result["resolved"])
            self.assertFalse(result["satisfied"])
            self.assertIsNone(result["status"])
            self.assertEqual(result["reason"], "spec-id folder not found")

    def test_task_file_not_found(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            (repo_root / "docs" / "specs" / "098-x" / "tasks").mkdir(parents=True)
            result = loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertFalse(result["resolved"])
            self.assertEqual(result["reason"], "task file not found")

    def test_status_done_is_resolved_and_satisfied(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            self._write_task(
                repo_root / "docs/specs/098-x/tasks/TASK-036.md",
                "---\nid: TASK-036\nstatus: done\n---\n",
            )
            result = loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertEqual(
                result,
                {
                    "ref": "098-x/TASK-036",
                    "resolved": True,
                    "satisfied": True,
                    "status": "done",
                    "reason": None,
                },
            )

    def test_status_completed_is_satisfied(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            self._write_task(
                repo_root / "docs/specs/098-x/tasks/TASK-036.md",
                "---\nid: TASK-036\nstatus: completed\n---\n",
            )
            result = loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertTrue(result["resolved"])
            self.assertTrue(result["satisfied"])
            self.assertEqual(result["status"], "completed")

    def test_status_pending_is_resolved_but_unsatisfied(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            self._write_task(
                repo_root / "docs/specs/098-x/tasks/TASK-036.md",
                "---\nid: TASK-036\nstatus: pending\n---\n",
            )
            result = loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertTrue(result["resolved"])
            self.assertFalse(result["satisfied"])
            self.assertEqual(result["status"], "pending")

    def test_status_in_progress_is_unsatisfied(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            self._write_task(
                repo_root / "docs/specs/098-x/tasks/TASK-036.md",
                "---\nid: TASK-036\nstatus: in_progress\n---\n",
            )
            result = loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertTrue(result["resolved"])
            self.assertFalse(result["satisfied"])

    def test_status_absent_is_resolved_but_unsatisfied(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            self._write_task(
                repo_root / "docs/specs/098-x/tasks/TASK-036.md",
                "---\nid: TASK-036\n---\n",
            )
            result = loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertTrue(result["resolved"])
            self.assertFalse(result["satisfied"])
            self.assertIsNone(result["status"])

    def test_path_traversal_task_id_never_escapes_repo_root(self):
        """A task-id segment crafted with enough `../` to land outside
        repo_root must never resolve, even when a same-named file with a
        satisfied status genuinely exists at the escaped location."""
        with tempfile.TemporaryDirectory() as outer_dir:
            outer = Path(outer_dir)
            repo_root = outer / "repo"
            (repo_root / "docs/specs/098-x/tasks").mkdir(parents=True)
            # Planted OUTSIDE repo_root at the exact spot a naive `../` join
            # from docs/specs/098-x/tasks/ would land on.
            self._write_task(outer / "TASK-001.md", "---\nid: TASK-001\nstatus: done\n---\n")

            result = loader.resolve_external_dependency(repo_root, "098-x/../../../../../TASK-001")
            self.assertFalse(result["resolved"])
            self.assertFalse(result["satisfied"])

    def test_bare_parent_spec_id_stays_within_repo_root_and_unresolved(self):
        # `spec_id` is a single ref.partition("/") segment, so it can never
        # itself contain another "/" -- a lone ".." is the most it can do,
        # which lands one level up (still inside repo_root) and then fails
        # the task-file lookup rather than escaping anywhere.
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            (repo_root / "docs" / "specs").mkdir(parents=True)
            result = loader.resolve_external_dependency(repo_root, "../TASK-001")
            self.assertFalse(result["resolved"])
            self.assertEqual(result["reason"], "task file not found")

    def test_never_mutates_sibling_task_file(self):
        with tempfile.TemporaryDirectory() as t:
            repo_root = Path(t)
            task_path = repo_root / "docs/specs/098-x/tasks/TASK-036.md"
            body = "---\nid: TASK-036\nstatus: done\n---\n"
            self._write_task(task_path, body)
            loader.resolve_external_dependency(repo_root, "098-x/TASK-036")
            self.assertEqual(task_path.read_text(), body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
