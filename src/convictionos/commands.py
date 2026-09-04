from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from alembic import command
from alembic.config import Config

from convictionos.application.eligibility import EligibilityService
from convictionos.infrastructure.db import build_engine, build_session_factory
from convictionos.infrastructure.eligibility_ports import (
    AlpacaPaperAccountEligibilityPort,
    StaticCliEligibilityPort,
    StaticMarketDataEligibilityPort,
    StaticMcpEligibilityPort,
)
from convictionos.infrastructure.store import Store
from convictionos.settings import Settings
from convictionos.worker import run_worker


def sanitized_startup_summary(settings: Settings, *, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "broker_mode": settings.broker_mode,
        "agent_enabled": settings.agent_enabled,
        "paper_only": True,
        "live_trading_authorized": False,
        "workspace_id": settings.competition_workspace_id,
        "agent_id": settings.runtime_agent_id,
        "release_sha": settings.runtime_release_sha,
        "ai_provider": settings.ai_provider or None,
        "ai_model": settings.ai_model or None,
        "alpaca_cli_revision": settings.alpaca_cli_revision or None,
        "alpaca_mcp_version": settings.alpaca_mcp_version or None,
    }


def migrate() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def preflight() -> dict[str, Any]:
    settings = Settings()
    return sanitized_startup_summary(settings, role="preflight")


async def capture_eligibility_manifest(*, capture_baseline: bool) -> dict[str, Any]:
    settings = Settings()
    engine = build_engine(settings.database_url)
    account_port = AlpacaPaperAccountEligibilityPort(
        paper_base_url=settings.alpaca_paper_base_url,
        oauth_token=settings.alpaca_oauth_token,
        api_key_id=settings.alpaca_api_key_id,
        api_secret_key=settings.alpaca_api_secret_key,
    )
    try:
        service = EligibilityService(
            account_port=account_port,
            cli_port=StaticCliEligibilityPort(
                version="alpaca-cli",
                revision=settings.alpaca_cli_revision,
                digest=_file_sha256(settings.alpaca_cli_path),
            ),
            mcp_port=StaticMcpEligibilityPort(
                version=settings.alpaca_mcp_version,
                schema_hash=settings.alpaca_mcp_schema_hash,
            ),
            data_port=StaticMarketDataEligibilityPort(
                options_feed=settings.alpaca_option_data_feed,
            ),
            manifest_store=Store(build_session_factory(engine)),
            deployed_git_sha=settings.runtime_release_sha,
        )
        manifest = await service.verify(
            workspace_id=settings.competition_workspace_id,
            expected_account_id=settings.competition_account_id,
            capture_baseline=capture_baseline,
            now=datetime.now(UTC),
        )
        return {
            "outcome": manifest.outcome.value,
            "workspace_id": manifest.workspace_id,
            "paper_mode": True,
            "options_incorporated": manifest.options_level >= 3,
            "options_feed": manifest.option_feed.value,
            "market_data_tier": manifest.market_data_capability.execution_tier.value,
            "mandate_version": manifest.mandate_version,
            "deployed_sha": manifest.deployed_git_sha,
            "verified_at": manifest.verified_at.isoformat(),
            "public_reasons": manifest.public_reasons,
        }
    finally:
        await account_port.aclose()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="convictionos")
    subcommands = parser.add_subparsers(dest="role", required=True)
    api = subcommands.add_parser("api")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8000)
    subcommands.add_parser("worker")
    subcommands.add_parser("migrate")
    subcommands.add_parser("preflight")
    eligibility = subcommands.add_parser("eligibility-manifest")
    eligibility.add_argument("--capture-baseline", action="store_true")
    args = parser.parse_args(argv)

    if args.role == "api":
        from convictionos.api.main import build_application

        settings = Settings(require_agent_startup_invariants=False)
        uvicorn.run(
            build_application(settings),
            host=args.host,
            port=args.port,
        )
        return
    if args.role == "worker":
        asyncio.run(run_worker())
        return
    if args.role == "migrate":
        migrate()
        return
    if args.role == "preflight":
        print(sanitized_startup_summary(Settings(), role="preflight"))
        return
    if args.role == "eligibility-manifest":
        result = asyncio.run(
            capture_eligibility_manifest(capture_baseline=args.capture_baseline)
        )
        print(json.dumps(result, sort_keys=True))
        return
    raise ValueError(f"unsupported role: {args.role}")


def _file_sha256(path: str) -> str:
    candidate = Path(path)
    if not path.strip() or not candidate.is_file():
        return ""
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


if __name__ == "__main__":
    main()
