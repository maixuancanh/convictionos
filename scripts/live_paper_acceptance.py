from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

READ_ONLY_ENDPOINTS = {
    "health": "/health/ready",
    "agent_status": "/v1/agent/status",
    "competition_readiness": "/v1/public/competition-readiness",
}


async def collect_read_only(base_url: str) -> dict[str, Any]:
    checks: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        for name, path in READ_ONLY_ENDPOINTS.items():
            try:
                response = await client.get(path)
                checks[name] = "ok" if response.status_code < 500 else "unavailable"
            except httpx.HTTPError:
                checks[name] = "unavailable"
    return {
        "phase": "read-only",
        "observed_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "order_mutations": 0,
    }


def collect_mutation(args: argparse.Namespace) -> dict[str, Any]:
    missing: list[str] = []
    if not args.authorize_paper_mutation:
        missing.append("authorize paper mutation")
    if not args.eligible_manifest_hash:
        missing.append("eligible manifest hash")
    if not args.authorized_intent_id:
        missing.append("authorized intent id")
    if not args.expected_account_fingerprint:
        missing.append("expected account fingerprint")
    if args.confirm != "PAPER_ONLY":
        missing.append("PAPER_ONLY confirmation")
    if missing:
        raise ValueError("Missing mutation guard: " + ", ".join(missing))
    return {
        "phase": "mutation",
        "observed_at": datetime.now(UTC).isoformat(),
        "eligible_manifest_hash": args.eligible_manifest_hash,
        "authorized_intent_id": args.authorized_intent_id,
        "account_fingerprint": args.expected_account_fingerprint,
        "order_mutations": 0,
        "outcome": "not_submitted_by_acceptance_script",
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "read-only":
        return await collect_read_only(args.base_url)
    return collect_mutation(args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("read-only", "mutation"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-paper/acceptance.json"))
    parser.add_argument("--authorize-paper-mutation", action="store_true")
    parser.add_argument("--eligible-manifest-hash")
    parser.add_argument("--authorized-intent-id")
    parser.add_argument("--expected-account-fingerprint")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except ValueError as error:
        raise SystemExit(str(error)) from error
    write_report(args.output, report)
    print(f"acceptance report written: {args.output}")


if __name__ == "__main__":
    main()
