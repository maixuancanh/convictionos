import os

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

os.environ["AGENT_ENABLED"] = "false"

import convictionos.api.main as api_main
from convictionos.api.main import build_agent, build_broker, build_container
from convictionos.application.grounded_intelligence import GroundedIntelligenceService
from convictionos.application.portfolio_risk import PortfolioRiskService
from convictionos.application.strategy_router import StrategyRouter
from convictionos.domain.market_data import OptionsFeed
from convictionos.infrastructure.alpaca_evidence import AlpacaEvidenceGateway
from convictionos.infrastructure.alpaca_market import AlpacaPaperMarketGateway
from convictionos.infrastructure.brokers import CompositeBroker, FakeBroker
from convictionos.infrastructure.model_openai_compatible import OpenAICompatibleModelGateway
from convictionos.settings import Settings


def test_agent_is_off_unless_explicitly_enabled(engine: AsyncEngine) -> None:
    settings = Settings(agent_enabled=False, broker_mode="fake")

    assert build_agent(settings, engine, FakeBroker()) is None


def test_enabled_agent_requires_alpaca_paper_mode(engine: AsyncEngine) -> None:
    settings = Settings(
        agent_enabled=True,
        agent_control_token="control-secret",
        broker_mode="fake",
    )

    with pytest.raises(ValueError, match="alpaca_paper"):
        build_agent(settings, engine, FakeBroker())


def test_enabled_paper_agent_wires_grounded_router_pipeline(engine: AsyncEngine) -> None:
    settings = Settings(
        agent_enabled=True,
        agent_control_token="control-secret",
        broker_mode="alpaca_paper",
        competition_account_id="paper-account",
        alpaca_cli_revision="alpaca-cli-revision",
        alpaca_cli_path="tools/alpaca-cli",
        alpaca_mcp_version="alpaca-mcp-version",
        alpaca_mcp_command="uvx alpaca-mcp",
        alpaca_mcp_schema_hash="sha256:alpaca-mcp-schema",
        ai_provider="openrouter",
        ai_model="openrouter/auto",
        ai_api_key="openrouter-secret",
        alpaca_api_key_id="alpaca-key",
        alpaca_api_secret_key="alpaca-secret",
    )

    agent = build_agent(settings, engine, FakeBroker())

    assert agent is not None
    assert isinstance(agent._evidence, AlpacaEvidenceGateway)
    assert isinstance(agent._intelligence, GroundedIntelligenceService)
    assert isinstance(agent._intelligence._gateway, OpenAICompatibleModelGateway)
    assert agent._intelligence._gateway.provider == "openrouter"
    assert isinstance(agent._router, StrategyRouter)
    assert isinstance(agent._portfolio_risk, PortfolioRiskService)


def test_application_container_uses_alpaca_clock_not_local_weekday_guess() -> None:
    settings = Settings(
        agent_enabled=True,
        agent_control_token="control-secret",
        broker_mode="alpaca_paper",
        competition_account_id="paper-account",
        alpaca_cli_revision="alpaca-cli-revision",
        alpaca_cli_path="tools/alpaca-cli",
        alpaca_mcp_version="alpaca-mcp-version",
        alpaca_mcp_command="uvx alpaca-mcp",
        alpaca_mcp_schema_hash="sha256:alpaca-mcp-schema",
        ai_provider="openrouter",
        ai_model="openrouter/auto",
        ai_api_key="openrouter-secret",
        alpaca_api_key_id="alpaca-key",
        alpaca_api_secret_key="alpaca-secret",
    )

    container = build_container(settings)

    assert not hasattr(api_main, "_is_us_equity_market_open")
    assert container.scheduler is not None
    assert isinstance(container.market, AlpacaPaperMarketGateway)
    assert container.scheduler._market_clock is container.market


def test_settings_rejects_unknown_option_feed() -> None:
    with pytest.raises(ValueError, match="ALPACA_OPTION_DATA_FEED must be indicative or opra"):
        Settings(alpaca_option_data_feed="made-up")


def test_settings_accepts_dashboard_paper_endpoint_with_v2_suffix() -> None:
    settings = Settings(alpaca_paper_base_url="https://paper-api.alpaca.markets/v2")

    assert settings.alpaca_paper_base_url == "https://paper-api.alpaca.markets"


def test_enabled_agent_wires_configured_opra_feed(engine: AsyncEngine) -> None:
    settings = Settings(
        agent_enabled=True,
        agent_control_token="control-secret",
        broker_mode="alpaca_paper",
        competition_account_id="paper-account",
        alpaca_cli_revision="alpaca-cli-revision",
        alpaca_cli_path="tools/alpaca-cli",
        alpaca_mcp_version="alpaca-mcp-version",
        alpaca_mcp_command="uvx alpaca-mcp",
        alpaca_mcp_schema_hash="sha256:alpaca-mcp-schema",
        ai_provider="openrouter",
        ai_model="openrouter/auto",
        ai_api_key="openrouter-secret",
        alpaca_api_key_id="alpaca-key",
        alpaca_api_secret_key="alpaca-secret",
        alpaca_option_data_feed="opra",
    )

    agent = build_agent(settings, engine, FakeBroker())

    assert agent is not None
    assert agent._market._option_data_feed is OptionsFeed.OPRA


def test_alpaca_paper_broker_wiring_uses_cli_submitter_and_rest_reader() -> None:
    settings = Settings(
        broker_mode="alpaca_paper",
        alpaca_api_key_id="alpaca-key",
        alpaca_api_secret_key="alpaca-secret",
        alpaca_cli_revision="53606273aa230a40c64b783425dcb3f4423ede30",
        alpaca_cli_path="alpaca",
    )

    broker = build_broker(settings)

    assert isinstance(broker, CompositeBroker)
    assert not hasattr(broker._reader, "submit")


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"competition_account_id": ""}, "COMPETITION_ACCOUNT_ID"),
        (
            {
                "competition_account_id": "paper-account",
                "competition_baseline_equity": "99999.99",
            },
            "baseline",
        ),
        (
            {"competition_account_id": "paper-account", "alpaca_live_trade": True},
            "ALPACA_LIVE_TRADE",
        ),
        (
            {"competition_account_id": "paper-account", "competition_mandate_version": "other"},
            "mandate",
        ),
        (
            {"competition_account_id": "paper-account", "alpaca_cli_revision": ""},
            "ALPACA_CLI_REVISION",
        ),
        ({"competition_account_id": "paper-account", "alpaca_cli_path": ""}, "ALPACA_CLI_PATH"),
        (
            {"competition_account_id": "paper-account", "alpaca_mcp_version": ""},
            "ALPACA_MCP_VERSION",
        ),
        (
            {"competition_account_id": "paper-account", "alpaca_mcp_command": ""},
            "ALPACA_MCP_COMMAND",
        ),
        (
            {"competition_account_id": "paper-account", "alpaca_mcp_schema_hash": ""},
            "ALPACA_MCP_SCHEMA_HASH",
        ),
    ],
)
def test_agent_enabled_paper_startup_rejects_invalid_competition_invariants(
    overrides: dict[str, object], match: str
) -> None:
    values = {
        "agent_enabled": True,
        "agent_control_token": "control-secret",
        "broker_mode": "alpaca_paper",
        "ai_provider": "openrouter",
        "ai_model": "openrouter/auto",
        "ai_api_key": "openrouter-secret",
        "alpaca_api_key_id": "alpaca-key",
        "alpaca_api_secret_key": "alpaca-secret",
        "competition_account_id": "paper-account",
        "alpaca_cli_revision": "alpaca-cli-revision",
        "alpaca_cli_path": "tools/alpaca-cli",
        "alpaca_mcp_version": "alpaca-mcp-version",
        "alpaca_mcp_command": "uvx alpaca-mcp",
        "alpaca_mcp_schema_hash": "sha256:alpaca-mcp-schema",
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=match):
        Settings(**values)
