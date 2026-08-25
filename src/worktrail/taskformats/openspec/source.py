"""`TaskSource` backed by an OpenSpec change directory.

Layout (OpenSpec 1.6.0, `spec-driven` schema):

    openspec/changes/<change-id>/
        proposal.md
        design.md
        specs/**/*.md
        tasks.md          <- the whole task list, one file

The single `tasks.md` is workable because run status does not live in the
artifact during a run. Task branches carry no `openspec/**` diff at all; the
orchestrator records status in its run journal and writes the artifact exactly
once per group at integrate time (`integrate._write_group_task_status`, PR #7).
Without that, N parallel branches would each rewrite the same file and collide
on every task -- which is what made file-per-task look mandatory.

Consequently this adapter needs **no OpenSpec custom schema**: the stock
`tasks` artifact is sufficient, and `FIELD_SCHEMA`-equivalent frontmatter
validation is not needed because no per-task frontmatter exists to validate.
See `docs/design/conductor-lanes.md` §2 and §4.5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from worktrail.orchestrator.coordinator import TAIL_KINDS
from worktrail.taskformats.openspec import schema as os_schema

DEFAULT_SPEC_ROOT = "openspec/changes"


class OpenSpecTaskSource:
    """`TaskSource` over `openspec/changes/<change-id>/`.

    Deliberately does not synthesise `files` (file scope). `runnable_frontier()`
    treats an empty file set as "collides with nothing", so inventing a scope
    here would be worse than declaring none: it would let the coordinator run
    two tasks concurrently against a guess. File scope is the compiled
    RunPlan's job (design P3); until it exists, the conservative `deps` below
    are what keep a run correct.
    """

    def __init__(self, repo_root: Path, spec_root: str = DEFAULT_SPEC_ROOT):
        self.repo_root = Path(repo_root)
        self._spec_root = spec_root

    # -- reads ---------------------------------------------------------------

    def load(self, spec_ref: str) -> Tuple[str, List[Dict[str, Any]]]:
        """Return `(change_id, tasks)` for one OpenSpec change.

        Dependency defaults, until the RunPlan supplies real edges:

        - **within a group**: strictly sequential (`1.2` depends on `1.1`).
        - **across groups**: independent.

        This mirrors the authored intent OpenSpec's own schema instructs -- "Group
        related tasks under ## numbered headings" and "Order tasks by dependency
        (what must be done first?)" -- and is the safe direction to be wrong in.
        Assuming independence instead would fan out tasks that share files, with
        no file scope available to stop them.

        An impl task's own "strictly sequential" predecessor is its nearest
        NON-tail sibling, not literally the immediately-preceding task -- a
        tail-kind (e2e/cleanup) task never runs during the main fan-out
        (`coordinator.runnable_frontier()` skips it unconditionally; it only
        dispatches later, in the strictly-serialized tail phase, once every
        non-tail task is already done). If a tail task happens to be first in
        its group, anchoring the next impl task's baseline dep on it would make
        that dep permanently unsatisfiable during the fan-out -- the impl task
        waits on the tail task, which itself waits on every non-tail task
        (including that impl task) to finish first.
        """
        tasks_md = self.task_file_path("", spec_ref)
        if not tasks_md.is_file():
            raise FileNotFoundError(f"no tasks.md for change {spec_ref!r} at {tasks_md}")

        parsed = os_schema.parse_tasks_md(tasks_md.read_text())
        rel = str(tasks_md.relative_to(self.repo_root))

        out: List[Dict[str, Any]] = []
        prev_in_group: Dict[str, str] = {}
        prev_non_tail_in_group: Dict[str, str] = {}
        non_tail_ids: List[str] = []
        for t in parsed.tasks:
            if t.kind in TAIL_KINDS:
                # A tail task verifies or tidies up after the implementation. Its
                # section reads as independent like any other, so without this it
                # would be fanned out ALONGSIDE the work it exists to check.
                # Depending on every preceding non-tail task is what
                # `coordinator.tail_held_out_task_ids()` needs to hold it back.
                deps = [prev_in_group[t.group]] if t.group in prev_in_group else []
                deps = sorted(set(deps) | set(non_tail_ids))
            else:
                deps = (
                    [prev_non_tail_in_group[t.group]]
                    if t.group in prev_non_tail_in_group
                    else []
                )
                non_tail_ids.append(t.id)
                prev_non_tail_in_group[t.group] = t.id
            prev_in_group[t.group] = t.id
            out.append(
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "deps": deps,
                    "external_deps": [],
                    "files": [],
                    "kind": t.kind,
                    "tags": list(t.tags),
                    "group": t.group,
                    "group_title": t.group_title,
                    "path": rel,
                    "frontmatter_warnings": list(parsed.warnings),
                }
            )
        return spec_ref, out

    def validate_dependencies(self, spec_id: str, tasks: List[Dict[str, Any]]) -> List[str]:
        """Report `deps` entries that name no task id in this change.

        Same-spec `deps` behaves identically to the devkit adapter: every
        `deps` entry is checked against the loaded task-id set, and each miss
        becomes one "unresolved same-spec dependency" diagnostic, worded
        `"{task_id} — ..."` so a caller can print it as `f"WARN: {diagnostic}"`
        (matching the existing external-dependency WARN format in
        `orchestrator/live.py`'s `precheck()`).

        `decision-refs:` is a devkit-frontmatter field; `tasks.md` carries no
        per-task metadata section to read it from. If a task dict carries one
        anyway -- the format exposing it some other way is a possibility this
        method stays open to, not something it assumes -- its decision-log
        coverage is not validated (there is nowhere to read a decision log
        from either); instead each such task gets one "decision-refs
        unsupported for OpenSpec-format tasks" diagnostic.
        """
        task_ids = {t["id"] for t in tasks}
        diagnostics: List[str] = []
        for t in tasks:
            for dep in t.get("deps") or []:
                if dep not in task_ids:
                    diagnostics.append(
                        f"{t['id']} — unresolved same-spec dependency '{dep}' "
                        f"(no task with that id in this change)"
                    )
            if t.get("decision-refs"):
                diagnostics.append(
                    f"{t['id']} — decision-refs unsupported for OpenSpec-format tasks "
                    f"(no per-task decision-log coverage check is available for this format)"
                )
        return diagnostics

    def resolve_external_dependency(self, dep_ref: str) -> Dict[str, Any]:
        """Resolve a `<change-id>/<task-id>` cross-change reference."""
        unresolved = {
            "ref": dep_ref,
            "resolved": False,
            "satisfied": False,
            "status": None,
            "reason": "malformed",
        }
        change_id, sep, task_id = dep_ref.partition("/")
        if not sep or not change_id or not task_id:
            return unresolved

        root = self.repo_root.resolve()
        tasks_md = (root / self._spec_root / change_id / "tasks.md").resolve()
        try:
            tasks_md.relative_to(root)
        except ValueError:
            return {**unresolved, "reason": "change folder not found"}
        if not tasks_md.is_file():
            return {**unresolved, "reason": "change folder not found"}

        task = os_schema.parse_tasks_md(tasks_md.read_text()).by_id(task_id)
        if task is None:
            return {**unresolved, "reason": "task not found"}
        return {
            "ref": dep_ref,
            "resolved": True,
            "satisfied": task.status == os_schema.STATUS_COMPLETED,
            "status": task.status,
            "reason": None,
        }

    # -- writes --------------------------------------------------------------

    def mark_status(self, task_id: str, status: str, *, spec_ref: Optional[str] = None) -> bool:
        """Tick the task's checkbox. Returns True if the file changed.

        Only `completed` is representable -- a checkbox is a boolean, and
        OpenSpec's tracker reads nothing else. Any other status is a no-op here
        rather than an error: intermediate run states (claimed, reviewing,
        failed) live in the run journal by design, and this method is called at
        integrate time, when the only transition that reaches the artifact is
        the terminal one.
        """
        if spec_ref is None:
            raise ValueError("OpenSpecTaskSource.mark_status requires spec_ref")
        if status != "completed":
            return False
        return os_schema.set_task_checked(self.task_file_path(task_id, spec_ref), task_id, True)

    # -- paths ---------------------------------------------------------------

    def task_brief_ref(self, task_id: str, spec_ref: str) -> tuple:
        """One `tasks.md` holds every task, so the worker needs the item anchor.

        Without it the orchestrator hands a worker a path to a file containing
        the whole change's checklist and no way to tell which line is its job.
        """
        return f"{self._spec_root}/{spec_ref}/tasks.md", task_id

    def task_file_path(self, task_id: str, spec_ref: str) -> Path:
        """Every task lives in the change's single `tasks.md`.

        `task_id` is accepted and ignored: the `TaskSource` contract is "where
        is this task's definition", and for OpenSpec the answer is the same file
        for every task. Addressability within it is by the `N.M` id, which is
        what `mark_status` and the worker prompt use.
        """
        return self.spec_root(spec_ref) / "tasks.md"

    def spec_root(self, spec_ref: str) -> Path:
        return self.repo_root / self._spec_root / spec_ref

    def spec_root_prefix(self) -> str:
        # Guards the whole openspec tree, not just changes/: a worker must not
        # edit archived changes or the merged specs under openspec/specs/ either.
        return f"{self._spec_root.split('/')[0]}/"

    def file_sections(self, text: str) -> tuple:
        # OpenSpec has no per-task create/modify sections. File scope is supplied
        # by the compiled run plan, not inferred from tasks.md prose.
        return [], []
