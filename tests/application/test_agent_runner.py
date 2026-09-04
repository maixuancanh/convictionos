from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.application.agent import (
    AgentMarketFrame,
    AgentRunState,
    AutonomousTradingAgent,
    DefinedRiskCallSpreadStrategy,
    OptionQuote,
)
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.infrastructure.alpaca_market import PaperAccountState, PaperPosition
from convictionos.infrastructure.brokers import BrokerOrder, BrokerOrderStatus, FakeBroker
from convictionos.infrastructure.store import Store

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


class FakeMarketGateway:
    def __init__(
        self, observed_at: datetime = NOW, positions: tuple[PaperPosition, ...] = ()
    ) -> None:
        self.observed_at = observed_at
        self.positions = positions
        self.observe_calls = 0

    async def observe(
        self, underlying: str, *, expiration_gte: date, expiration_lte: date
    ) -> AgentMarketFrame:
        del expiration_gte, expiration_lte
        self.observe_calls += 1
        return AgentMarketFrame(
            underlying=underlying,
            price=Decimal("505"),
            previous_close=Decimal("500"),
            observed_at=self.observed_at,
            source="test-market",
            market_data=build_market_data_capability(
                underlying_feed=UnderlyingFeed.IEX,
                options_feed=OptionsFeed.INDICATIVE,
                assessed_at=self.observed_at,
            ),
            calls=(
                OptionQuote(
                    symbol="SPY260918C00500000",
                    strike=Decimal("500"),
                    expiration=date(2026, 9, 18),
                    bid_price=Decimal("7.80"),
                    ask_price=Decimal("8.00"),
                    as_of=self.observed_at,
                ),
                OptionQuote(
                    symbol="SPY260918C00510000",
                    strike=Decimal("510"),
                    expiration=date(2026, 9, 18),
                    bid_price=Decimal("5.60"),
                    ask_price=Decimal("5.75"),
                    as_of=self.observed_at,
                ),
            ),
        )

    async def get_account(self) -> PaperAccountState:
        return PaperAccountState(
            account_id="paper-account-1",
            status="ACTIVE",
            equity=Decimal("100000"),
            buying_power=Decimal("200000"),
            options_trading_level=3,
        )

    async def get_positions(self) -> tuple[PaperPosition, ...]:
        return self.positions


class PendingThenFilledBroker:
    def __init__(self) -> None:
        self.submit_calls = 0

    async def submit(self, intent: object) -> BrokerOrder:
        self.submit_calls += 1
        idempotency_key = intent.idempotency_key
        return BrokerOrder(
            order_id="paper-order-1",
            client_order_id=idempotency_key,
            status=BrokerOrderStatus.NEW,
        )

    async def get_by_client_order_id(self, client_order_id: str) -> BrokerOrder:
        return BrokerOrder(
            order_id="paper-order-1",
            client_order_id=client_order_id,
            status=BrokerOrderStatus.FILLED,
        )


@pytest.mark.asyncio
async def test_run_once_reaches_filled_receipt_through_control_plane(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    broker = FakeBroker()
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=FakeMarketGateway(),
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.FILLED
    assert result.intent_id is not None
    assert result.receipt is not None
    assert result.receipt.evidence_snapshot_hash == result.decision.snapshot_hash
    assert result.receipt.policy_outcome.value == "allow"
    assert broker.submit_calls == 1
    assert result.market_data == result.receipt.market_data
    assert result.paper_accounting is None
    assert result.receipt.paper_accounting is None

    repeated = await agent.run_once(now=NOW)
    assert repeated.receipt == result.receipt
    assert repeated.market_data == result.receipt.market_data
    assert repeated.paper_accounting == result.receipt.paper_accounting
    assert broker.submit_calls == 1


@pytest.mark.asyncio
async def test_stale_observation_abstains_without_submitting(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    broker = FakeBroker()
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=FakeMarketGateway(NOW - timedelta(minutes=6)),
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.ABSTAINED
    assert result.intent_id is None
    assert result.receipt is None
    assert broker.submit_calls == 0


@pytest.mark.asyncio
async def test_repeated_run_reconciles_pending_order_without_resubmitting(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    broker = PendingThenFilledBroker()
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=FakeMarketGateway(),
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
    )

    first = await agent.run_once(now=NOW)
    second = await agent.run_once(now=NOW)

    assert first.state is AgentRunState.SUBMITTED
    assert second.state is AgentRunState.FILLED
    assert second.receipt is not None
    assert broker.submit_calls == 1


@pytest.mark.asyncio
async def test_existing_underlying_exposure_blocks_a_new_order(engine: AsyncEngine) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    broker = FakeBroker()
    existing = PaperPosition(
        symbol="SPY260918C00490000",
        quantity=Decimal("1"),
        market_value=Decimal("1000"),
        unrealized_pl=Decimal("10"),
    )
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=FakeMarketGateway(positions=(existing,)),
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.DENIED
    assert "existing underlying exposure" in result.decision.reasons[-1]
    assert broker.submit_calls == 0
