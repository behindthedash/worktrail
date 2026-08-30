"""Suite-wide test isolation from the operator's real machine-wide state.

A real machine-wide config (routing.yaml, model-defaults.yaml, ...) under the
operator state dir (worktrail_home(): ~/.worktrail, legacy ~/.go) must never
change what a test asserts -- confirmed live 2026-08-03: two test_routing_e2e.py
tests broke the moment an operator actually populated the machine-wide
routing.yaml, because nothing pointed GO_ROUTING_FILE at an isolated path.
Every test in this suite gets a per-test WORKTRAIL_HOME so resolver-based
defaults (run records, telemetry logs, caches) can never touch the real
~/.worktrail or ~/.go either; a test that needs to exercise a real file does
so explicitly (writing to tmp_path and overriding the same env var itself),
which still wins since monkeypatch restores in LIFO order.

The isolated routing file is seeded with a minimal, current-schema
`routing.targets`/`routing.tiers`/`default_tier` baseline -- one target per
harness (`claude-sub`/`codex-sub`/`opencode-free`), all sharing the single
`t2-build` tier row -- so `live.py`'s default-model resolution
(`_default_model_for_agent()`, which every spawn-adjacent code path in
`live.py` falls back to when no explicit `model=`/routing override is given)
succeeds for whichever agent a test names, the same way the retired
`routing.agents` shape used to pre-seed all three agents here before the
target-selector routing schema replaced it (`policy._reject_legacy_routing_keys()`,
task 1.4). The LEGACY `agents:` shape must never be written here -- that key
is a hard `OperatorConfigError` as of task 1.4 -- but this new-schema seed is
not that shape and does not trip that rejection. A test that needs a
*different* target/tier/model, or genuinely no routing configured at all,
overrides GO_ROUTING_FILE itself (monkeypatch restores in LIFO order, so a
per-test override still wins).

WORKTRAIL_SKILL_DISPATCH_DEPTH is ambient *dispatch-session* state of the same
class: a suite run from inside a dispatched session (worktrail-go ->
worktrail-skill-dispatch -> agent shell -> pytest) inherits depth=1, and every
test calling skill_dispatch.main() on an internal skill then trips
blocked_internal_dispatch_recursion -- confirmed live 2026-08-25, 8 failures in
test_skill_dispatch.py inside dispatched sessions while the clean-env suite
passed (runs go-20260825-135050 / go-20260825-202107). The recursion guard
itself reads ambient depth by design and stays; tests that exercise depth
semantics set the variable explicitly.
"""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_go_machine_wide_config(tmp_path, monkeypatch):
    # Dispatch-chain marker: see module docstring -- never inherit it ambiently.
    monkeypatch.delenv("WORKTRAIL_SKILL_DISPATCH_DEPTH", raising=False)
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "worktrail-home"))
    # A dedicated tempdir OUTSIDE tmp_path's own tree, not a subdirectory of
    # it (worktrail-home/ included) -- several tests recursively glob tmp_path
    # itself for *.yaml run records (e.g. run_record.py's load_run_index(),
    # "**/*.yaml"); a routing.yaml anywhere under tmp_path got swept into
    # those scans and misread as a malformed run record (confirmed live:
    # RunRecordFormatError "unrecognized keys: claude-sub, ..., t2-build").
    with tempfile.TemporaryDirectory(prefix="worktrail-test-routing-") as routing_dir:
        routing_file = Path(routing_dir) / "routing.yaml"
        routing_file.write_text(
            "targets:\n"
            "  claude-sub:\n"
            "    harness: claude\n"
            "    pool: subscription\n"
            "  codex-sub:\n"
            "    harness: codex\n"
            "    pool: subscription\n"
            "  opencode-free:\n"
            "    harness: opencode\n"
            "    pool: free\n"
            "tiers:\n"
            "  t2-build:\n"
            "    claude-sub:\n"
            "      model: sonnet\n"
            "    codex-sub:\n"
            "      model: gpt-5.4-mini\n"
            "    opencode-free:\n"
            "      model: opencode/deepseek-v4-flash-free\n"
            "default_tier: t2-build\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GO_ROUTING_FILE", str(routing_file))
        monkeypatch.setenv(
            "GO_MODEL_DEFAULTS_FILE", str(tmp_path / "no-such-model-defaults.yaml")
        )
        monkeypatch.setenv(
            "GO_AGENT_CAPACITY_CACHE", str(tmp_path / "agent-capacity.json")
        )
        # The real work queue is machine-wide state too: any code path that falls
        # back to work_queue.base_dir() (e.g. drain's backlog seeding) must land in
        # a per-test directory, never the operator's $WORK_QUEUE_DIR/~/work-queue.
        monkeypatch.setenv("WORK_QUEUE_DIR", str(tmp_path / "work-queue"))
        yield
