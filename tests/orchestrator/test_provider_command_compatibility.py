from __future__ import annotations

import subprocess

from worktrail.orchestrator import provider_command_compatibility as compat


def test_probe_covers_every_provider_and_command_surface(monkeypatch):
    seen: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        seen.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="provider help", stderr="")

    monkeypatch.setattr(compat.subprocess, "run", fake_run)

    results = compat.probe_all()

    assert len(results) == 12
    assert {(result.provider, result.surface) for result in results} == {
        (provider, surface)
        for provider in ("claude", "codex", "opencode")
        for surface in ("cluster-detect", "drain", "skill-dispatch", "spawnlib")
    }
    assert all(result.ok for result in results)
    assert all(argv[-1] == "--help" for argv in seen)


def test_probe_fails_closed_when_installed_parser_rejects_generated_argv(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[0] == "codex" and "-a" in argv:
            return subprocess.CompletedProcess(
                argv, 2, stdout="", stderr="error: unexpected argument '-a'"
            )
        return subprocess.CompletedProcess(argv, 0, stdout="provider help", stderr="")

    monkeypatch.setattr(compat.subprocess, "run", fake_run)
    monkeypatch.setattr(
        compat,
        "command_matrix",
        lambda: {("codex", "spawnlib"): ["codex", "exec", "-a", "on-request", "probe"]},
    )

    result = compat.probe_all()[0]

    assert not result.ok
    assert result.returncode == 2
    assert "unexpected argument '-a'" in result.detail


def test_probe_fails_closed_when_provider_is_missing(monkeypatch):
    def missing(_argv, **_kwargs):
        raise FileNotFoundError("provider not found")

    monkeypatch.setattr(compat.subprocess, "run", missing)
    monkeypatch.setattr(
        compat,
        "command_matrix",
        lambda: {("claude", "drain"): ["claude", "-p", "probe"]},
    )

    result = compat.probe_all()[0]

    assert not result.ok
    assert result.returncode is None
    assert result.detail == "provider not found"


def test_main_reports_each_surface_and_returns_nonzero_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        compat,
        "probe_all",
        lambda timeout=compat.DEFAULT_TIMEOUT: [
            compat.ProbeResult("claude", "drain", True, 0, "provider help"),
            compat.ProbeResult("codex", "spawnlib", False, 2, "obsolete flag"),
        ],
    )

    assert compat.main([]) == 1
    output = capsys.readouterr().out
    assert "PASS claude/drain" in output
    assert "FAIL codex/spawnlib: obsolete flag" in output
