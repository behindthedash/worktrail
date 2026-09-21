"""Tests for the minimal PullHook client (claim / peek / ack)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from worktrail.workqueue.pullhook_client import (
    PullHookClient,
    PullHookError,
    PullHookTimeout,
)

TOKEN = "pk_consume_supersecret"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeOpener:
    """Records every request and replays a queued response or exception."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self._responses.pop(0) if self._responses else {}
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(json.dumps(outcome).encode("utf-8"))


def make_client(*responses, **kwargs):
    opener = FakeOpener(*responses)
    client = PullHookClient(
        "https://pullhook.example/api/",
        "worktrail-handoff",
        TOKEN,
        opener=opener,
        **kwargs,
    )
    return client, opener


ITEM = {
    "delivery_id": "d-1",
    "event_id": "evt-1",
    "payload": {"schema": "datalena.worktrail-handoff.v1"},
}


def test_claim_returns_items_and_sends_bearer_credential():
    client, opener = make_client({"items": [ITEM]})

    items = client.claim(max_items=3)

    assert [i.event_id for i in items] == ["evt-1"]
    assert items[0].delivery_id == "d-1"
    assert items[0].payload["schema"] == "datalena.worktrail-handoff.v1"

    request = opener.requests[0]
    assert request.method == "POST"
    assert (
        request.full_url
        == "https://pullhook.example/api/channels/worktrail-handoff/claim"
    )
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert json.loads(request.data) == {"max_items": 3}


def test_peek_does_not_claim():
    client, opener = make_client({"items": [dict(ITEM, delivery_id=None)]})

    items = client.peek(limit=2)

    request = opener.requests[0]
    assert request.method == "GET"
    assert request.full_url.startswith(
        "https://pullhook.example/api/channels/worktrail-handoff/peek?"
    )
    assert "limit=2" in request.full_url
    assert request.data is None
    assert items[0].delivery_id is None
    assert items[0].event_id == "evt-1"


def test_ack_posts_delivery_id():
    client, opener = make_client({"ok": True})

    client.ack("d-1")

    request = opener.requests[0]
    assert request.method == "POST"
    assert request.full_url.endswith("/channels/worktrail-handoff/ack")
    assert json.loads(request.data) == {"delivery_id": "d-1"}


def test_ack_rejects_empty_delivery_id():
    client, _ = make_client()
    with pytest.raises(ValueError):
        client.ack("")


def test_timeout_is_bounded_and_raises_pullhook_timeout():
    client, opener = make_client(
        urllib.error.URLError(TimeoutError("timed out")), timeout=2.5
    )

    with pytest.raises(PullHookTimeout) as excinfo:
        client.claim()

    assert opener.timeouts == [2.5]
    assert "timed out" in str(excinfo.value)


def test_bare_timeout_error_raises_pullhook_timeout():
    client, _ = make_client(TimeoutError("timed out"))
    with pytest.raises(PullHookTimeout):
        client.peek()


def test_default_timeout_is_applied():
    client, opener = make_client({"items": []})
    client.claim()
    assert opener.timeouts[0] == client.timeout
    assert client.timeout > 0


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(
            f"https://pullhook.example/api?token={TOKEN}",
            401,
            f"Unauthorized token={TOKEN}",
            {},
            None,
        ),
        urllib.error.URLError(f"connection refused ({TOKEN})"),
        OSError(f"socket blew up with {TOKEN}"),
        TimeoutError(f"timed out holding {TOKEN}"),
    ],
)
def test_credential_is_never_present_in_error_text(failure):
    client, _ = make_client(failure)

    with pytest.raises(PullHookError) as excinfo:
        client.claim()

    rendered = f"{excinfo.value}{excinfo.value.args!r}"
    assert TOKEN not in rendered
    # the raise chain is suppressed, so no __cause__ can leak the token either
    assert excinfo.value.__cause__ is None


def test_invalid_json_raises_pullhook_error():
    client = PullHookClient(
        "https://pullhook.example",
        "c",
        TOKEN,
        opener=lambda request, timeout=None: FakeResponse(b"not json"),
    )
    with pytest.raises(PullHookError):
        client.claim()


def test_item_without_event_id_is_rejected():
    client, _ = make_client({"items": [{"delivery_id": "d-1"}]})
    with pytest.raises(PullHookError):
        client.claim()


def test_bare_list_response_is_accepted():
    client, _ = make_client([ITEM])
    assert client.claim()[0].event_id == "evt-1"


def test_missing_credential_is_rejected():
    with pytest.raises(ValueError):
        PullHookClient("https://pullhook.example", "c", "")
