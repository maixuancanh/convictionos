import asyncio
import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.application.agent import AutonomousTradingAgent, DefinedRiskCallSpreadStrategy
from convictionos.application.agent_scheduler import AgentScheduler
from convictionos.application.eligibility import EligibilityService
from convictionos.application.grounded_intelligence import GroundedIntelligenceService
from convictionos.application.position_lifecycle import PositionLifecycleService
from convictionos.application.runtime_coordinator import RuntimeCoordinator
from convictionos.application.strategy_router import StrategyRouter
from convictionos.infrastructure.alpaca import AlpacaRestOrderReader
from convictionos.infrastructure.alpaca_activities import AlpacaActivityGateway
from convictionos.infrastructure.alpaca_cli import AlpacaCliConfig, AlpacaCliOrderSubmitter
from convictionos.infrastructure.alpaca_evidence import AlpacaEvidenceGateway
from convictionos.infrastructure.alpaca_market import AlpacaPaperMarketGateway
from convictionos.infrastructure.brokers import BrokerPort, CompositeBroker, FakeBroker
from convictionos.infrastructure.db import build_engine, build_session_factory
from convictionos.infrastructure.eligibility_ports import (
    AlpacaPaperAccountEligibilityPort,
    StaticCliEligibilityPort,
    StaticMarketDataEligibilityPort,
    StaticMcpEligibilityPort,
)
from convictionos.infrastructure.model_claude import ClaudeModelGateway
from convictionos.infrastructure.model_gemini import GeminiModelGateway
from convictionos.infrastructure.model_openai import OpenAIModelGateway
from convictionos.infrastructure.model_openai_compatible import OpenAICompatibleModelGateway
from convictionos.infrastructure.runtime_store import RuntimeStore
from convictionos.infrastructure.store import Store
from convictionos.settings import Settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@dataclass(frozen=True)
class ApplicationContainer:
    settings: Settings
    engine: AsyncEngine
    broker: BrokerPort
    market: AlpacaPaperMarketGateway | None
    agent: AutonomousTradingAgent | None
    scheduler: AgentScheduler | None
    runtime_coordinator: RuntimeCoordinator | None


def validate_agent_configuration(settings: Settings) -> None:
    if not settings.agent_enabled or settings.broker_mode != "alpaca_paper":
        return
    if settings.ai_provider not in {"openai", "openrouter", "gemini", "claude"}:
        raise ValueError("AI_PROVIDER must be openai, openrouter, gemini, or claude")
    if not settings.ai_model:
        raise ValueError("AI_MODEL is required for the paper agent")
    if not settings.ai_api_key.get_secret_value():
        raise ValueError("AI_API_KEY is required for the paper agent")
    if not settings.agent_control_token:
        raise ValueError("AGENT_ENABLED requires AGENT_CONTROL_TOKEN")


def build_model_gateway(
    settings: Settings,
) -> OpenAIModelGateway | OpenAICompatibleModelGateway | GeminiModelGateway | ClaudeModelGateway:
    provider = settings.ai_provider
    if provider not in {"openai", "openrouter", "gemini", "claude"}:
        raise ValueError(f"unsupported AI provider: {provider}")
    client = httpx.AsyncClient(timeout=settings.ai_timeout_seconds)
    api_key = settings.ai_api_key.get_secret_value()
    if provider == "openai":
        if settings.ai_base_url and settings.ai_base_url.rstrip("/") != "https://api.openai.com/v1":
            return OpenAICompatibleModelGateway(
                model=settings.ai_model,
                api_key=api_key,
                base_url=settings.ai_base_url,
                max_output_tokens=settings.ai_max_output_tokens,
                http_client=client,
            )
        return OpenAIModelGateway(
            model=settings.ai_model, api_key=api_key, http_client=client
        )
    if provider == "openrouter":
        return OpenAICompatibleModelGateway(
            model=settings.ai_model,
            api_key=api_key,
            base_url=settings.ai_base_url or "https://openrouter.ai/api/v1",
            provider_name="openrouter",
            provider_controls={
                "require_parameters": True,
                "allow_fallbacks": False,
                "data_collection": "deny",
            },
            max_output_tokens=settings.ai_max_output_tokens,
            http_client=client,
        )
    if provider == "gemini":
        return GeminiModelGateway(
            model=settings.ai_model, api_key=api_key, http_client=client
        )
    return ClaudeModelGateway(
        model=settings.ai_model,
        api_key=api_key,
        http_client=client,
        max_output_tokens=settings.ai_max_output_tokens,
    )


def build_broker(settings: Settings) -> BrokerPort:
    if settings.broker_mode == "fake":
        return FakeBroker()
    if settings.broker_mode == "alpaca_paper":
        reader = AlpacaRestOrderReader(
            base_url=settings.alpaca_paper_base_url,
            oauth_token=settings.alpaca_oauth_token,
            api_key_id=settings.alpaca_api_key_id,
            api_secret_key=settings.alpaca_api_secret_key,
        )
        submitter = AlpacaCliOrderSubmitter(
            config=AlpacaCliConfig(
                executable=settings.alpaca_cli_path,
                revision=settings.alpaca_cli_revision,
                api_key_id=settings.alpaca_api_key_id,
                api_secret_key=settings.alpaca_api_secret_key,
            )
        )
        return CompositeBroker(submitter=submitter, reader=reader)
    raise ValueError(f"unsupported broker mode: {settings.broker_mode}")


def build_agent(
    settings: Settings,
    engine: AsyncEngine,
    broker: BrokerPort,
    *,
    market: AlpacaPaperMarketGateway | None = None,
) -> AutonomousTradingAgent | None:
    if not settings.agent_enabled:
        return None
    if settings.broker_mode != "alpaca_paper":
        raise ValueError("AGENT_ENABLED requires BROKER_MODE=alpaca_paper")
    if not settings.agent_control_token:
        raise ValueError("AGENT_ENABLED requires AGENT_CONTROL_TOKEN")
    validate_agent_configuration(settings)
    store = Store(build_session_factory(engine))
    active_market = market or AlpacaPaperMarketGateway(
        paper_base_url=settings.alpaca_paper_base_url,
        data_base_url=settings.alpaca_data_base_url,
        option_data_feed=settings.alpaca_option_data_feed,
        oauth_token=settings.alpaca_oauth_token,
        api_key_id=settings.alpaca_api_key_id,
        api_secret_key=settings.alpaca_api_secret_key,
    )
    return AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=active_market,
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying=settings.agent_underlying,
        evidence=AlpacaEvidenceGateway(
            data_base_url=settings.alpaca_data_base_url,
            oauth_token=settings.alpaca_oauth_token,
            api_key_id=settings.alpaca_api_key_id,
            api_secret_key=settings.alpaca_api_secret_key,
        ),
        intelligence=GroundedIntelligenceService(store, build_model_gateway(settings)),
        router=StrategyRouter(),
        portfolio_risk=AutonomousTradingAgent._legacy_risk_service(),
    )


def build_container(
    settings: Settings, *, engine: AsyncEngine | None = None
) -> ApplicationContainer:
    engine = engine or build_engine(settings.database_url)
    broker = build_broker(settings)
    market = (
        AlpacaPaperMarketGateway(
            paper_base_url=settings.alpaca_paper_base_url,
            data_base_url=settings.alpaca_data_base_url,
            option_data_feed=settings.alpaca_option_data_feed,
            oauth_token=settings.alpaca_oauth_token,
            api_key_id=settings.alpaca_api_key_id,
            api_secret_key=settings.alpaca_api_secret_key,
        )
        if settings.agent_enabled and settings.broker_mode == "alpaca_paper"
        else None
    )
    agent = build_agent(settings, engine, broker, market=market)
    runtime_coordinator = (
        RuntimeCoordinator(
            RuntimeStore(build_session_factory(engine)),
            workspace_id=settings.competition_workspace_id,
            account_id=settings.competition_account_id,
            agent_id=settings.runtime_agent_id,
            owner_instance_id=settings.runtime_instance_id,
            release_sha=settings.runtime_release_sha,
        )
        if agent is not None and market is not None
        else None
    )
    scheduler = (
        AgentScheduler(
            agent,
            lifecycle_runner=PositionLifecycleService(
                Store(build_session_factory(engine)),
                agent._market,
                None,
                activities=AlpacaActivityGateway(
                    paper_base_url=settings.alpaca_paper_base_url,
                    oauth_token=settings.alpaca_oauth_token,
                    api_key_id=settings.alpaca_api_key_id,
                    api_secret_key=settings.alpaca_api_secret_key,
                ),
                orders=broker,
            ),
            market_clock=market,
            runtime_coordinator=runtime_coordinator,
        )
        if agent is not None and market is not None
        else None
    )
    return ApplicationContainer(
        settings=settings,
        engine=engine,
        broker=broker,
        market=market,
        agent=agent,
        scheduler=scheduler,
        runtime_coordinator=runtime_coordinator,
    )


def build_eligibility_capture(
    settings: Settings,
    engine: AsyncEngine,
):
    async def capture(workspace_id: str, capture_baseline: bool, now: datetime):
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
            return await service.verify(
                workspace_id=workspace_id,
                expected_account_id=settings.competition_account_id,
                capture_baseline=capture_baseline,
                now=now,
            )
        finally:
            await account_port.aclose()

    return capture


def _file_sha256(path: str) -> str:
    candidate = Path(path)
    if not path.strip() or not candidate.is_file():
        return ""
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def build_application(
    settings: Settings | None = None,
    *,
    scheduler_background: bool = False,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    container = build_container(settings or Settings(), engine=engine)
    return create_app(
        engine=container.engine,
        broker=container.broker,
        broker_mode=container.settings.broker_mode,
        agent=container.agent,
        agent_enabled=container.settings.agent_enabled,
        agent_control_token=container.settings.agent_control_token,
        scheduler=container.scheduler,
        runtime_coordinator=container.runtime_coordinator,
        scheduler_background=scheduler_background,
        scheduler_interval_seconds=container.settings.agent_interval_seconds,
        runtime_release_sha=container.settings.runtime_release_sha,
        cli_revision=container.settings.alpaca_cli_revision or None,
        mcp_version=container.settings.alpaca_mcp_version or None,
        eligibility_capture=build_eligibility_capture(
            container.settings,
            container.engine,
        ),
        competition_workspace_id=container.settings.competition_workspace_id,
        competition_account_id=container.settings.competition_account_id,
        include_demo_routes=False,
    )


def app() -> FastAPI:
    """Uvicorn factory; defer settings, database, and broker construction to startup."""
    return build_application()
