"""Where a cold worker is told to read its brief, per task format.

This seam exists because the orchestrator used to build `tasks/<id>.md` by hand
in five places. That is devkit's layout; OpenSpec keeps every task in one
`tasks.md`, so a live OpenSpec run died in `_require_task_file` before spawning
a single worker -- "worktree has no task file at
openspec/changes/080-.../tasks/1.1.md" -- for a file that was never supposed to
exist. Found by running spec 080 for real; no unit test could see it, because
every test built its own devkit-shaped ctx.
"""

from __future__ import annotations

import types

from worktrail.orchestrator import dispatch, live
from worktrail.taskformats import resolve


class TestAdapters:
    def test_devkit_brief_is_the_task_file_with_no_anchor(self):
        path, anchor = resolve.task_brief_ref_for("/repo/docs/specs/080-x", "TASK-001")
        assert path == "docs/specs/080-x/tasks/TASK-001.md"
        assert anchor == ""

    def test_openspec_brief_is_the_shared_file_plus_an_anchor(self):
        """Without the anchor the worker opens the whole change's checklist with
        nothing marking which line is its job."""
        path, anchor = resolve.task_brief_ref_for("/repo/openspec/changes/080-x", "1.1")
        assert path == "openspec/changes/080-x/tasks.md"
        assert anchor == "1.1"

    def test_the_path_is_repo_relative_not_absolute(self):
        """It goes into a prompt for a worker running in a worktree, so an
        absolute path from the orchestrator's own checkout would be wrong."""
        for spec in ("/a/b/c/docs/specs/x", "/a/b/c/openspec/changes/x"):
            assert not resolve.task_brief_ref_for(spec, "t")[0].startswith("/")


class TestPromptRendering:
    def test_devkit_rendering_is_unchanged(self):
        path, note = dispatch._task_brief(
            {"task_brief": {"path_fmt": "docs/specs/x/tasks/{task_id}.md", "anchor_fmt": ""}},
            "TASK-001",
        )
        assert path == "docs/specs/x/tasks/TASK-001.md"
        assert note == ""

    def test_openspec_rendering_names_the_item_and_warns_off_the_rest(self):
        path, note = dispatch._task_brief(
            {"task_brief": {"path_fmt": "openspec/changes/x/tasks.md", "anchor_fmt": "{task_id}"}},
            "1.1",
        )
        assert path == "openspec/changes/x/tasks.md"
        assert "`1.1` item ONLY" in note

    def test_a_ctx_predating_the_seam_still_renders_devkit(self):
        """Cassettes and callers written before this seam pass no `task_brief`."""
        path, note = dispatch._task_brief({"spec_folder": "docs/specs/x/"}, "TASK-002")
        assert path == "docs/specs/x/tasks/TASK-002.md"
        assert note == ""


class TestLiveWiring:
    def _ctx_for(self, rel):
        return live.LiveSpawn._task_brief_ctx(types.SimpleNamespace(spec_folder_rel=rel))

    def test_templates_resolve_per_format(self):
        assert self._ctx_for("docs/specs/080-x") == {
            "path_fmt": "docs/specs/080-x/tasks/{task_id}.md",
            "anchor_fmt": "",
        }
        assert self._ctx_for("openspec/changes/080-x") == {
            "path_fmt": "openspec/changes/080-x/tasks.md",
            "anchor_fmt": "{task_id}",
        }

    def test_the_probe_sentinel_cannot_leak_into_a_rendered_path(self):
        """`_task_brief_ctx` substitutes a sentinel id to build a template. If an
        adapter ever mangled it, the sentinel would reach a worker's prompt."""
        for rel in ("docs/specs/x", "openspec/changes/x"):
            ctx = self._ctx_for(rel)
            path, note = dispatch._task_brief({"task_brief": ctx}, "1.1")
            assert "\x00" not in path and "\x00" not in note


class TestEndToEndPrompt:
    def _prompt(self, rel, tid, role=dispatch.ROLE_IMPLEMENT):
        ctx = {
            "spec_id": "080-x",
            "spec_folder": rel + "/",
            "worktree_path": "/wt",
            "branch": "b",
            "spec_root_prefix": resolve.spec_root_prefix_for(rel),
            "task_brief": live.LiveSpawn._task_brief_ctx(
                types.SimpleNamespace(spec_folder_rel=rel)
            ),
        }
        task = {"id": tid, "title": "Blast-radius path detection", "files": ["ci/a.py"]}
        return dispatch.build_worker_prompt(role, task, ctx)

    def test_an_openspec_worker_is_never_sent_to_a_per_task_file(self):
        """The exact failure the live 080 run hit."""
        p = self._prompt("openspec/changes/080-x", "1.1")
        assert "openspec/changes/080-x/tasks/1.1.md" not in p
        assert "openspec/changes/080-x/tasks.md" in p

    def test_a_devkit_worker_is_still_sent_to_its_own_file(self):
        p = self._prompt("docs/specs/080-x", "TASK-001")
        assert "docs/specs/080-x/tasks/TASK-001.md" in p

    def test_the_title_reaches_the_worker(self):
        """In OpenSpec the one-line task IS the brief, so a worker that cannot
        find its item would otherwise know only its id."""
        for rel, tid in (("openspec/changes/080-x", "1.1"), ("docs/specs/080-x", "TASK-001")):
            assert "Task title: Blast-radius path detection" in self._prompt(rel, tid)

    def test_every_role_addresses_the_brief_correctly(self):
        for role in dispatch.ROLES:
            p = self._prompt("openspec/changes/080-x", "1.1", role)
            assert "/tasks/1.1.md" not in p, role
