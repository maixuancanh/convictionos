"""Fail-closed competition readiness smoke checks."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("offline", "read-only-paper", "authorized-paper"),
        default="offline",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--authorize-paper-order", action="store_true")
    parser.add_argument("--intent-id")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


async def collect(phase: str, base_url: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=10) as client:
        for name, path in (
            ("health", "/health"),
            ("agent_status", "/v1/agent/status"),
            ("public_readiness", "/v1/public/competition-readiness"),
        ):
            try:
                response = await client.get(path)
                checks[name] = {"status_code": response.status_code, "ok": response.is_success}
                if response.is_success:
                    checks[name]["body"] = response.json()
            except httpx.HTTPError as error:
                checks[name] = {"ok": False, "reason": type(error).__name__}
    return {
        "phase": phase,
        "checked_at": datetime.now(UTC).isoformat(),
        "order_mutations": 0,
        "checks": checks,
    }


async def main() -> int:
    args = parse_args()
    if args.phase == "authorized-paper":
        if not args.authorize_paper_order:
            raise SystemExit("authorized-paper requires --authorize-paper-order")
        if not args.intent_id:
            raise SystemExit("authorized-paper requires an already persisted --intent-id")
        raise SystemExit(
            "authorized-paper is unavailable until a persisted eligible intent is wired"
        )
    result = await collect(args.phase, args.base_url)
    output = json.dumps(result, indent=2, sort_keys=True, default=str)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    return 0 if args.phase == "offline" else int(
        not all(item["ok"] for item in result["checks"].values())
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
