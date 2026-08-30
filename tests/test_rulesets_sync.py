"""Unit tests for Worktrail's rulesets drift guard."""

import importlib.util
import json
from pathlib import Path
from unittest import mock

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "ci"
    / "rulesets"
    / "rulesets_sync.py"
)
_spec = importlib.util.spec_from_file_location("rulesets_sync", _SCRIPT)
assert _spec is not None and _spec.loader is not None
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


def _write_local(tmp_path, name, required_checks):
    data = {
        "name": name,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [f"refs/heads/{name}"], "exclude": []}},
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                    "allowed_merge_methods": ["squash"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": c} for c in required_checks],
                },
            },
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data))
    return data


def _live_ruleset(local: dict, ruleset_id: int, live_checks) -> dict:
    live = json.loads(json.dumps(local))
    live["id"] = ruleset_id
    for rule in live["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [
                {"context": c} for c in live_checks
            ]
            # server always echoes these defaults even when unspecified locally
            rule["parameters"].setdefault("do_not_enforce_on_create", False)
    live["current_user_can_bypass"] = "never"
    return live


def test_check_detects_missing_required_status_check(tmp_path, capsys):
    local = _write_local(tmp_path, "protect-dev", ["api-unit", "stg-predeploy-gate"])
    live_list = [{"id": 1, "name": "protect-dev"}]
    live_full = _live_ruleset(local, 1, ["api-unit"])

    def fake_gh_api(path, token, method="GET", payload=None):
        if path.endswith("/rulesets"):
            return live_list
        if path.endswith("/rulesets/1"):
            assert method == "GET"
            return live_full
        raise AssertionError(f"unexpected call: {method} {path}")

    with (
        mock.patch.object(rs, "gh_api", side_effect=fake_gh_api),
        mock.patch.object(rs, "resolve_token", return_value="tok"),
        mock.patch.object(rs, "resolve_repo", return_value="owner/repo"),
    ):
        code = rs.main(["--check", "--dir", str(tmp_path)])

    assert code == 1
    err = capsys.readouterr().err
    assert "DRIFT: protect-dev" in err


def test_check_passes_when_identical(tmp_path, capsys):
    local = _write_local(tmp_path, "protect-prd", ["api-unit"])
    live_list = [{"id": 2, "name": "protect-prd"}]
    live_full = _live_ruleset(local, 2, ["api-unit"])

    def fake_gh_api(path, token, method="GET", payload=None):
        if path.endswith("/rulesets"):
            return live_list
        if path.endswith("/rulesets/2"):
            return live_full
        raise AssertionError(f"unexpected call: {method} {path}")

    with (
        mock.patch.object(rs, "gh_api", side_effect=fake_gh_api),
        mock.patch.object(rs, "resolve_token", return_value="tok"),
        mock.patch.object(rs, "resolve_repo", return_value="owner/repo"),
    ):
        code = rs.main(["--check", "--dir", str(tmp_path)])

    assert code == 0
    assert "in sync" in capsys.readouterr().out


def test_apply_puts_committed_json_when_drifted(tmp_path):
    local = _write_local(tmp_path, "protect-dev", ["api-unit", "stg-predeploy-gate"])
    live_list = [{"id": 1, "name": "protect-dev"}]
    live_full = _live_ruleset(local, 1, ["api-unit"])

    calls = []

    def fake_gh_api(path, token, method="GET", payload=None):
        calls.append((path, method, payload))
        if path.endswith("/rulesets") and method == "GET":
            return live_list
        if path.endswith("/rulesets/1") and method == "GET":
            return live_full
        if path.endswith("/rulesets/1") and method == "PUT":
            return {}
        raise AssertionError(f"unexpected call: {method} {path}")

    with (
        mock.patch.object(rs, "gh_api", side_effect=fake_gh_api),
        mock.patch.object(rs, "resolve_token", return_value="tok"),
        mock.patch.object(rs, "resolve_repo", return_value="owner/repo"),
    ):
        code = rs.main(["--apply", "--dir", str(tmp_path)])

    assert code == 0
    puts = [c for c in calls if c[1] == "PUT"]
    assert len(puts) == 1
    put_path, _, put_payload = puts[0]
    assert put_path == "/repos/owner/repo/rulesets/1"
    checks = put_payload["rules"][1]["parameters"]["required_status_checks"]
    assert {c["context"] for c in checks} == {"api-unit", "stg-predeploy-gate"}
    assert "id" not in put_payload


def test_missing_live_ruleset_reports_and_fails_check(tmp_path, capsys):
    _write_local(tmp_path, "protect-stg", ["api-unit"])

    def fake_gh_api(path, token, method="GET", payload=None):
        if path.endswith("/rulesets"):
            return []
        raise AssertionError(f"unexpected call: {method} {path}")

    with (
        mock.patch.object(rs, "gh_api", side_effect=fake_gh_api),
        mock.patch.object(rs, "resolve_token", return_value="tok"),
        mock.patch.object(rs, "resolve_repo", return_value="owner/repo"),
    ):
        code = rs.main(["--check", "--dir", str(tmp_path)])

    assert code == 1
    assert "missing live" in capsys.readouterr().err


def test_committed_rulesets_match_repo_rulesets_dir():
    """The real .github/rulesets/ files parse and canonicalize cleanly."""
    rulesets_dir = Path(__file__).resolve().parents[1] / ".github" / "rulesets"
    files = rs.load_local_files(rulesets_dir)
    assert {f.stem for f in files} == {"protect-main"}
    for f in files:
        canon = rs.canonicalize(json.loads(f.read_text()))
        assert canon["name"] == f.stem
        assert (
            canon["rules"]
            .get("required_status_checks", {})
            .get("required_status_checks")
        ), f"{f.name} has no required status checks"
