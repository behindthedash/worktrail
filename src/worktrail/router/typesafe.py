#!/usr/bin/env python3
"""One client for the judgment service the router's two judgment backends share.

`risk_judgment.py` (what does this change do?) and `relatedness_judgment.py`
(do these two briefs describe the same work?) ask completely different
questions, but the transport, the credential, the timeout and the
"what counts as unavailable" rule are the same, and a second copy would be one
more place for the failure-mode contract to drift. This module owns exactly
that much and knows nothing about either question.

**Unavailable is a first-class answer.** `JUDGMENT_ERRORS` is the tuple both
callers catch to mean "fall back to the offline path": no credential, HTTP
error, transport error, timeout, unparseable body. Neither backend is allowed
to raise at its caller -- a router that crashes because a third party is down
would be worse than the heuristic it replaced.

Stdlib-only, and no key value is ever read outside `post()`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"

# Seconds for one request. Measured cost is ~930 tokens and ~0.55s per item;
# this is a ceiling for a slow response, not an expected duration. It is
# deliberately short: an answer that arrives after the operator has given up is
# worth less than the offline result that arrives immediately.
TIMEOUT_S = 20

# Everything that means "no usable answer". `ValueError` covers a caller's own
# shape check on the parsed body, so a response missing a field is the same
# event as a response that never arrived.
JUDGMENT_ERRORS = (
    urllib.error.URLError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
)


def is_configured() -> bool:
    """True when a credential is present. No network call; the value is not read."""
    return bool(os.environ.get(API_KEY_ENV, "").strip())


def post(state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    """Ask every question about `state` in one request. Raises on any failure.

    One request per item, not one per question: the questions share the same
    state, and the whole set costs about what a single question would.
    """
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(
            {"state": state, "model": MODEL, "questions": questions}
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ[API_KEY_ENV].strip()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read())


def noul(answers: Any, key: str) -> float:
    """The numeric `noul` for `key`, raising `ValueError` on any other shape."""
    answer = answers.get(key) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or not isinstance(answer.get("noul"), (int, float)):
        raise ValueError(f"response has no numeric noul for {key}")  # noqa: TRY004
    return float(answer["noul"])
