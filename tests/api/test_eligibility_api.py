from datetime import UTC, datetime
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.domain.competition import COMPETITION_MANDATE_VERSION
from convictionos.domain.eligibility import (
    EligibilityManifest,
    evaluate_manifest_outcome,
)
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.infrastructure.brokers import FakeBroker
from convictionos.infrastructure.db import build_session_factory
from convictionos.infrastructure.store import Store


def manifest() -> EligibilityManifest:
    capability = build_market_data_capability(
        UnderlyingFeed.IEX,
        OptionsFeed.INDICATIVE,
        datetime(2026, 9, 4, tzinfo=UTC),
    )
    outcome, checks = evaluate_manifest_outcome(
        expected_account_id="paper-account-secret",
        observed_account_id="paper-account-secret",
        environment="paper",
        account_status="ACTIVE",
        starting_equity=Decimal("100000.00"),
        starting_cash=Decimal("100000.00"),
        no_positions_at_baseline=True,
        no_orders_at_baseline=True,
        options_level=3,
        trading_api_read_ok=True,
        cli_revision="cli-revision",
        cli_digest="cli-digest",
        mcp_schema_hash="schema-hash",
        market_data_capability=capability,
        mandate_version=COMPETITION_MANDATE_VERSION,
        baseline_manifest_id=None,
    )
    return EligibilityManifest(
        manifest_id="manifest-1",
        workspace_id="workspace-1",
        expected_account_id="paper-account-secret",
        observed_account_id="paper-account-secret",
        environment="paper",
        account_status="ACTIVE",
        starting_equity=Decimal("100000.00"),
        starting_cash=Decimal("100000.00"),
        no_positions_at_baseline=True,
        no_orders_at_baseline=True,
        options_level=3,
        trading_api_read_ok=True,
        cli_version="1.0",
        cli_revision="cli-revision",
        cli_digest="cli-digest",
        mcp_version="mcp-1.0",
        mcp_schema_hash="schema-hash",
        option_feed=OptionsFeed.INDICATIVE,
        market_data_capability=capability,
        mandate_version=COMPETITION_MANDATE_VERSION,
        deployed_git_sha="release-sha",
        verified_at=datetime(2026, 9, 4, tzinfo=UTC),
        check_results=checks,
        outcome=outcome,
    )


async def test_public_readiness_redacts_account_and_private_identifiers(
    engine: AsyncEngine,
) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        eligibility_manifest=manifest(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/public/competition-readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "eligible"
    assert body["paper_mode"] is True
    assert body["options_incorporated"] is True
    assert body["cli_revision"] == "cli-revision"
    assert body["mcp_version"] == "mcp-1.0"
    assert body["deployed_sha"] == "release-sha"
    assert "paper-account-secret" not in response.text
    assert "manifest-1" not in response.text


async def test_public_readiness_loads_latest_manifest_from_store(
    engine: AsyncEngine,
) -> None:
    await Store(build_session_factory(engine)).create_or_load_eligibility_manifest(
        manifest()
    )
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        competition_workspace_id="workspace-1",
        competition_account_id="paper-account-secret",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/public/competition-readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "eligible"
    assert body["options_incorporated"] is True
    assert body["cli_revision"] == "cli-revision"
    assert body["mcp_version"] == "mcp-1.0"
    assert body["deployed_sha"] == "release-sha"
    assert "paper-account-secret" not in response.text
    assert "manifest-1" not in response.text


async def test_workspace_eligibility_requires_control_token_and_workspace_match(
    engine: AsyncEngine,
) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        agent_control_token="control-secret",
        eligibility_manifest=manifest(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/v1/workspaces/workspace-1/eligibility")
        wrong = await client.get(
            "/v1/workspaces/workspace-2/eligibility",
            headers={"X-Agent-Control-Token": "control-secret"},
        )
        accepted = await client.get(
            "/v1/workspaces/workspace-1/eligibility",
            headers={"X-Agent-Control-Token": "control-secret"},
        )

    assert missing.status_code == 403
    assert wrong.status_code == 404
    assert accepted.status_code == 200
    assert accepted.json()["observed_account_id"] == "paper-account-secret"


async def test_capture_baseline_endpoint_requires_token_and_returns_public_summary(
    engine: AsyncEngine,
) -> None:
    captured: list[tuple[str, bool]] = []

    async def capture(
        workspace_id: str, capture_baseline: bool, now: datetime
    ) -> EligibilityManifest:
        assert now.tzinfo is not None
        captured.append((workspace_id, capture_baseline))
        return manifest()

    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        agent_control_token="control-secret",
        eligibility_capture=capture,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post(
            "/v1/workspaces/workspace-1/eligibility/capture-baseline"
        )
        accepted = await client.post(
            "/v1/workspaces/workspace-1/eligibility/capture-baseline",
            headers={"X-Agent-Control-Token": "control-secret"},
        )

    assert missing.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["outcome"] == "eligible"
    assert captured == [("workspace-1", True)]
    assert "paper-account-secret" not in accepted.text
    assert "manifest-1" not in accepted.text
