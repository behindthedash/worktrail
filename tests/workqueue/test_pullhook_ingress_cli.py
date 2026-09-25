"""CLI contract for the PullHook handoff ingress."""

from __future__ import annotations

import json

import pytest

from worktrail.workqueue import pullhook_ingress_cli as cli
from worktrail.workqueue.pullhook_ingress import IngressResult


def _result() -> IngressResult:
    return IngressResult(status="created", event_id="evt-1", acked=True, created=True)


def test_credential_is_read_only_from_environment(monkeypatch, capsys):
    seen = {}

    class FakeClient:
        def __init__(self, base_url, channel, token, *, timeout):
            seen.update(
                base_url=base_url, channel=channel, token=token, timeout=timeout
            )

    monkeypatch.setenv("PULLHOOK_CONSUME_CREDENTIAL", "secret-value")
    monkeypatch.setattr(cli, "PullHookClient", FakeClient)
    monkeypatch.setattr(cli, "ingest", lambda *_args, **_kwargs: [_result()])

    code = cli.main(["--base-url", "https://pullhook.example", "--channel", "events"])

    assert code == 0
    assert seen["token"] == "secret-value"
    assert "secret-value" not in capsys.readouterr().out
    assert (
        cli.main(
            [
                "--base-url",
                "https://pullhook.example",
                "--channel",
                "events",
                "--token",
                "secret-value",
            ]
        )
        == 2
    )
    assert "secret-value" not in capsys.readouterr().err


def test_once_and_dry_run_limit_processing_to_one(monkeypatch, capsys):
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

    def fake_ingest(_client, **kwargs):
        calls.append(kwargs)
        return [_result()]

    monkeypatch.setenv("PULLHOOK_CONSUME_CREDENTIAL", "secret")
    monkeypatch.setattr(cli, "PullHookClient", FakeClient)
    monkeypatch.setattr(cli, "ingest", fake_ingest)

    assert (
        cli.main(
            [
                "--base-url",
                "https://pullhook.example",
                "--channel",
                "events",
                "--once",
                "--max-items",
                "20",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "--base-url",
                "https://pullhook.example",
                "--channel",
                "events",
                "--dry-run",
                "--max-items",
                "20",
            ]
        )
        == 0
    )

    assert [call["max_items"] for call in calls] == [1, 1]
    assert calls[1]["dry_run"] is True
    assert all(
        json.loads(line)["results"][0]["status"] == "created"
        for line in capsys.readouterr().out.splitlines()
    )


@pytest.mark.parametrize("value", ["0", "101"])
def test_batch_size_is_bounded(monkeypatch, value):
    monkeypatch.setenv("PULLHOOK_CONSUME_CREDENTIAL", "secret")
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--base-url",
                "https://pullhook.example",
                "--channel",
                "events",
                "--max-items",
                value,
            ]
        )


def test_missing_environment_credential_returns_json_error(monkeypatch, capsys):
    monkeypatch.delenv("PULLHOOK_CONSUME_CREDENTIAL", raising=False)

    code = cli.main(["--base-url", "https://pullhook.example", "--channel", "events"])

    assert code == 2
    assert json.loads(capsys.readouterr().err) == {
        "error": "environment variable PULLHOOK_CONSUME_CREDENTIAL is required"
    }
