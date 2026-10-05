"""Consume Datalena handoff events from PullHook into the local work queue."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from worktrail.workqueue.pullhook_client import PullHookClient, PullHookError
from worktrail.workqueue.pullhook_ingress import IngressResult, exit_code, ingest

_MAX_BATCH = 100
_TOKEN_ENV = "PULLHOOK_CONSUME_CREDENTIAL"
_CREDENTIAL_FLAGS = {
    "--token",
    "--consume-token",
    "--credential",
    "--consume-credential",
    "--pullhook-credential",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="PullHook service base URL")
    parser.add_argument("--channel", required=True, help="PullHook channel name")
    parser.add_argument(
        "--queue-dir", type=Path, help="work-queue root (defaults to WORK_QUEUE_DIR)"
    )
    parser.add_argument(
        "--once", action="store_true", help="process at most one event and exit"
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=10,
        help=f"maximum events to process in this run (1-{_MAX_BATCH}; default 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="peek at one event and validate it without creating or acknowledging it",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="PullHook request timeout in seconds (1-60; default 15)",
    )
    return parser


def _result_payload(results: list[IngressResult]) -> dict[str, object]:
    return {
        "results": [result.to_dict() for result in results],
        "exit_code": exit_code(results),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if any(arg.split("=", 1)[0] in _CREDENTIAL_FLAGS for arg in raw_args):
        print(
            json.dumps(
                {
                    "error": (
                        "credential flags are not accepted; set "
                        f"the {_TOKEN_ENV} environment variable"
                    )
                }
            ),
            file=sys.stderr,
        )
        return 2
    args = parser.parse_args(raw_args)
    if not 1 <= args.max_items <= _MAX_BATCH:
        parser.error(f"--max-items must be between 1 and {_MAX_BATCH}")
    if not 1 <= args.timeout <= 60:
        parser.error("--timeout must be between 1 and 60 seconds")

    token = os.environ.get(_TOKEN_ENV, "")
    if not token:
        print(
            json.dumps({"error": f"environment variable {_TOKEN_ENV} is required"}),
            file=sys.stderr,
        )
        return 2

    try:
        client = PullHookClient(
            args.base_url, args.channel, token, timeout=args.timeout
        )
        results = ingest(
            client,
            max_items=1 if args.once or args.dry_run else args.max_items,
            queue_base=args.queue_dir,
            dry_run=args.dry_run,
        )
    except PullHookError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    payload = _result_payload(results)
    print(json.dumps(payload, sort_keys=True))
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
