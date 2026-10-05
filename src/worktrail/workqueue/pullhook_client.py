"""
Minimal PullHook client -- claim, peek, and ack over PullHook's HTTP API.

Only the three verbs the handoff ingress needs are implemented:

- `claim(max_items=N)` takes items off the channel and returns them with a
  delivery id each, so they are invisible to other consumers until acked.
- `peek(limit=N)` reads items *without* claiming them, for dry-run inspection.
- `ack(delivery_id)` permanently removes a claimed item.

Every request carries the consume credential as a bearer token, and every
request is bounded by `timeout` seconds -- an unreachable relay fails the
ingress run rather than hanging it.

The credential never appears in an exception message or log line: all error
text is built by `_redact()`, which replaces any occurrence of the token with
`***`. Raise `PullHookError` (or a subclass) and callers can log it verbatim.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_TIMEOUT = 15.0

__all__ = [
    "DEFAULT_TIMEOUT",
    "PullHookClient",
    "PullHookError",
    "PullHookItem",
    "PullHookTimeout",
]


class PullHookError(RuntimeError):
    """A PullHook request failed. The message never contains the credential."""


class PullHookTimeout(PullHookError):
    """A PullHook request exceeded the configured timeout."""


@dataclass(frozen=True)
class PullHookItem:
    """One delivery returned by `claim()` or `peek()`."""

    delivery_id: str | None
    event_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Any) -> PullHookItem:
        if not isinstance(raw, dict):
            raise PullHookError("PullHook returned a non-object item")
        delivery_id = raw.get("id")
        if not isinstance(delivery_id, str) or not delivery_id:
            raise PullHookError("PullHook item is missing an id")
        body = raw.get("body")
        if not isinstance(body, str):
            raise PullHookError("PullHook item body is not a string")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise PullHookError("PullHook item body is not valid JSON") from None
        if not isinstance(payload, dict):
            raise PullHookError("PullHook item body is not a JSON object")
        event_id = payload.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise PullHookError("PullHook item body is missing an event id")
        return cls(delivery_id=delivery_id, event_id=event_id, payload=payload)


class PullHookClient:
    """Bearer-authenticated client for one PullHook channel."""

    def __init__(
        self,
        base_url: str,
        channel: str,
        token: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Any | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not channel:
            raise ValueError("channel is required")
        if not token:
            raise ValueError("a consume credential is required")
        self.base_url = base_url.rstrip("/")
        self.channel = channel
        self.timeout = timeout
        self._token = token
        self._opener = opener or urllib.request.urlopen

    # -- verbs ---------------------------------------------------------

    def claim(self, max_items: int = 1) -> list[PullHookItem]:
        """Claim one item, making it invisible to other consumers until acked."""
        if max_items != 1:
            raise ValueError("PullHook claim returns one item per request")
        body = self._request(
            "POST",
            f"/api/hooks/{urllib.parse.quote(self.channel)}/claim",
        )
        return self._items(body)

    def peek(self, limit: int = 1) -> list[PullHookItem]:
        """Read one item without claiming it."""
        if limit != 1:
            raise ValueError("PullHook peek returns one item per request")
        body = self._request(
            "GET",
            f"/api/hooks/{urllib.parse.quote(self.channel)}/peek",
        )
        return self._items(body)

    def ack(self, delivery_id: str) -> None:
        """Permanently remove a claimed item."""
        if not delivery_id:
            raise ValueError("delivery_id is required")
        self._request(
            "DELETE",
            f"/api/hooks/{urllib.parse.quote(self.channel)}/items/"
            f"{urllib.parse.quote(delivery_id, safe='')}",
        )

    # -- internals -----------------------------------------------------

    @staticmethod
    def _items(body: Any) -> list[PullHookItem]:
        if isinstance(body, dict):
            if body.get("ok") is False:
                raise PullHookError("PullHook rejected the request")
            raw_item = body.get("webhook")
            if raw_item is None:
                return []
            return [PullHookItem.from_json(raw_item)]
        raise PullHookError("PullHook returned an invalid response")

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise PullHookError(
                self._redact(f"PullHook {method} {path} failed: HTTP {exc.code}")
            ) from None
        except TimeoutError as exc:
            raise PullHookTimeout(
                self._redact(
                    f"PullHook {method} {path} timed out after {self.timeout}s: {exc}"
                )
            ) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                raise PullHookTimeout(
                    self._redact(
                        f"PullHook {method} {path} timed out after {self.timeout}s: {reason}"
                    )
                ) from None
            raise PullHookError(
                self._redact(f"PullHook {method} {path} failed: {reason}")
            ) from None
        except OSError as exc:
            raise PullHookError(
                self._redact(f"PullHook {method} {path} failed: {exc}")
            ) from None

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PullHookError(
                self._redact(f"PullHook {method} {path} returned invalid JSON: {exc}")
            ) from None
