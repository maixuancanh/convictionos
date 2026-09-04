from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from convictionos.application.agent import (
    AgentMarketFrame,
    AgentRunState,
    AutonomousTradingAgent,
    DefinedRiskCallSpreadStrategy,
)
from convictionos.application.grounded_intelligence import GroundedIntelligenceService
from convictionos.application.market_truth import (
    MarketTruthOutcome,
    MarketTruthPacket,
    MarketTruthService,
)
from convictionos.application.model_gateway import IntelligenceRequest, ModelResult
from convictionos.application.portfolio_risk import PortfolioRiskService
from convictionos.application.strategy_router import StrategyRouter
from convictionos.domain.intelligence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceSourceType,
    GroundedThesis,
    ThesisDirection,
)
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.portfolio_risk import HorizonRiskBudget
from convictionos.domain.strategy import OptionMarketQuote
from convictionos.domain.trading import Horizon
from convictionos.infrastructure.alpaca_evidence import AlpacaEvidenceGateway
from convictionos.infrastructure.alpaca_market import PaperAccountState, PaperPosition
from convictionos.infrastructure.alpaca_mcp import McpObservation
from convictionos.infrastructure.brokers import FakeBroker
from convictionos.infrastructure.store import Store

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


class FakeEvidenceGateway:
    def __init__(self, item: EvidenceItem) -> None:
        self.item = item
        self.news_calls = 0

    async def news(
        self, underlying: str, *, observed_at: datetime, lookback_hours: int
    ) -> tuple[EvidenceItem, ...]:
        assert underlying == "SPY"
        assert observed_at == NOW
        assert lookback_hours == 48
        self.news_calls += 1
        return (self.item,)

    @staticmethod
    def build_bundle(
        frame: AgentMarketFrame, news: tuple[EvidenceItem, ...]
    ) -> EvidenceBundle:
        return AlpacaEvidenceGateway.build_bundle(frame, news)


class FakeGateway:
    provider = "test-provider"
    model = "test-model"

    def __init__(self, thesis: GroundedThesis) -> None:
        self.thesis = thesis
        self.calls = 0
        self.requests: list[IntelligenceRequest] = []

    async def analyze(self, request: IntelligenceRequest) -> ModelResult:
        self.calls += 1
        self.requests.append(request)
        return ModelResult(
            thesis=self.thesis,
            provider=self.provider,
            model=self.model,
        )


class FakeMarket:
    def __init__(self, frame: AgentMarketFrame, *, options_level: int = 3) -> None:
        self.frame = frame
        self.options_level = options_level
        self.observe_calls = 0
        self.account_calls = 0
        self.position_calls = 0

    async def observe(
        self, underlying: str, *, expiration_gte: date, expiration_lte: date
    ) -> AgentMarketFrame:
        assert underlying == "SPY"
        assert expiration_gte == date(2026, 9, 11)
        assert expiration_lte == date(2026, 10, 29)
        self.observe_calls += 1
        return self.frame

    async def get_account(self) -> PaperAccountState:
        self.account_calls += 1
        return PaperAccountState(
            account_id="paper-account-1",
            status="ACTIVE",
            equity=Decimal("10000"),
            buying_power=Decimal("5000"),
            options_trading_level=self.options_level,
        )

    async def get_positions(self) -> tuple[PaperPosition, ...]:
        self.position_calls += 1
        return ()


class ConflictingMarketTruth(MarketTruthService):
    def cross_check(self, *, frame, account, observations, observed_at):
        return MarketTruthPacket(
            outcome=MarketTruthOutcome.CONFLICTED,
            observed_at=observed_at,
            rest_source_hash=frame.snapshot_hash(),
            mcp_observation_hashes=(),
            reasons=("account_id_mismatch",),
            sources=("alpaca_rest", "alpaca_mcp"),
            truth_hash="pending",
        )


class CorroboratingMarketTruthObserver:
    def __init__(self) -> None:
        self.calls = 0

    async def observe_market_truth(self, frame, account, *, observed_at):
        self.calls += 1
        assert frame.underlying == "SPY"
        assert account.account_id == "paper-account-1"
        assert observed_at == NOW
        return (
            mcp_observation(
                "get_account",
                {
                    "id": "paper-account-1",
                    "status": "ACTIVE",
                    "cash": "5000",
                    "buying_power": "5000",
                },
            ),
            mcp_observation(
                "get_clock",
                {
                    "timestamp": NOW.isoformat(),
                    "is_open": True,
                    "session_open": True,
                },
            ),
            mcp_observation(
                "get_option_chain",
                {
                    "symbol": "SPY261030P00510000",
                    "expiration_date": "2026-10-30",
                    "strike_price": "510",
                    "type": "put",
                    "latest_quote_timestamp": NOW.isoformat(),
                },
            ),
            mcp_observation(
                "get_news",
                {"id": "news-1", "published_at": "2026-08-30T14:55:00+00:00"},
            ),
        )


def mcp_observation(tool_name: str, facts: dict[str, object]) -> McpObservation:
    return McpObservation(
        provider_version="alpaca-mcp-server==2.3.1",
        tool_name=tool_name,
        request_hash=f"request-{tool_name}",
        observed_at=NOW,
        facts=facts,
        limitations=("mcp_read_model",),
        content_hash=f"content-{tool_name}",
    )


def news_item() -> EvidenceItem:
    return EvidenceItem(
        source_id="news-1",
        provider="alpaca_news",
        source_type=EvidenceSourceType.NEWS,
        published_at=NOW,
        observed_at=NOW,
        headline="A bounded bearish catalyst",
        summary="A point-in-time source for the bearish thesis.",
        symbols=("SPY",),
    )


def thesis(direction: ThesisDirection) -> GroundedThesis:
    return GroundedThesis(
        direction=direction,
        confidence=Decimal("0.85"),
        horizon=Horizon.SWING,
        catalyst="earnings guidance catalyst",
        causal_mechanism="forward earnings expectations deteriorate",
        beneficiaries=("SPY",) if direction is ThesisDirection.BULLISH else (),
        adversely_affected=("SPY",) if direction is ThesisDirection.BEARISH else (),
        invalidation="guidance is restored",
        material_risks=("macro reversal",),
        source_ids=("alpaca-market:SPY:2026-08-30T15:00:00+00:00", "news-1"),
        contradicting_source_ids=(),
    )


def quote(
    symbol: str,
    strike: str,
    delta: str,
    *,
    bid: str,
    ask: str,
) -> OptionMarketQuote:
    return OptionMarketQuote(
        symbol=symbol,
        option_type="put",
        strike=Decimal(strike),
        expiration=date(2026, 10, 30),
        bid=Decimal(bid),
        ask=Decimal(ask),
        delta=-Decimal(delta),
        implied_volatility=Decimal("0.25"),
        open_interest=1200,
        volume=300,
        as_of=NOW,
        tradable=True,
    )


def frame(*, previous_close: str = "500") -> AgentMarketFrame:
    return AgentMarketFrame(
        underlying="SPY",
        price=Decimal("505"),
        previous_close=Decimal(previous_close),
        observed_at=NOW,
        source="alpaca-indicative",
        market_data=build_market_data_capability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.INDICATIVE,
            assessed_at=NOW,
        ),
        calls=(),
        option_quotes=(
            quote("SPY261030P00510000", "510", "0.55", bid="3.80", ask="4.00"),
            quote("SPY261030P00470000", "470", "0.40", bid="1.90", ask="2.10"),
        ),
    )


def risk_service(*, per_trade_max_loss: str = "250") -> PortfolioRiskService:
    return PortfolioRiskService(
        budgets={
            Horizon.SWING: HorizonRiskBudget(
                minimum_dte=30,
                maximum_dte=90,
                per_trade_max_loss=Decimal(per_trade_max_loss),
                aggregate_capacity=Decimal("500"),
                max_positions=2,
                confidence_threshold=Decimal("0.70"),
                cooldown_seconds=0,
            )
        },
        aggregate_max_loss=Decimal("600"),
        max_directional_exposure=Decimal("500"),
        minimum_options_level=3,
    )


def build_agent(
    engine: AsyncEngine,
    *,
    model_thesis: GroundedThesis,
    options_level: int = 3,
    per_trade_max_loss: str = "250",
) -> tuple[AutonomousTradingAgent, FakeGateway, FakeEvidenceGateway, FakeBroker]:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    gateway = FakeGateway(model_thesis)
    evidence = FakeEvidenceGateway(news_item())
    broker = FakeBroker()
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=FakeMarket(
            frame(
                previous_close=(
                    "500" if model_thesis.direction is ThesisDirection.NEUTRAL else "510"
                )
            ),
            options_level=options_level,
        ),
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
        evidence=evidence,
        intelligence=GroundedIntelligenceService(store, gateway),
        router=StrategyRouter(max_debit=Decimal("2.50")),
        portfolio_risk=risk_service(per_trade_max_loss=per_trade_max_loss),
    )
    return agent, gateway, evidence, broker


@pytest.mark.asyncio
async def test_research_only_frame_abstains_before_evidence_model_and_broker(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    gateway = FakeGateway(thesis(ThesisDirection.BEARISH))
    evidence = FakeEvidenceGateway(news_item())
    broker = FakeBroker()
    research_frame = frame().model_copy(
        update={
            "market_data": build_market_data_capability(
                underlying_feed=UnderlyingFeed.IEX,
                options_feed=OptionsFeed.UNKNOWN,
                assessed_at=NOW,
            )
        }
    )
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=FakeMarket(research_frame),
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
        evidence=evidence,
        intelligence=GroundedIntelligenceService(store, gateway),
        router=StrategyRouter(max_debit=Decimal("2.50")),
        portfolio_risk=risk_service(),
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.ABSTAINED
    assert result.market_data == research_frame.market_data
    assert result.decision.reasons == ("market_data_research_only",)
    assert evidence.news_calls == 0
    assert gateway.calls == 0
    assert broker.submit_calls == 0


@pytest.mark.asyncio
async def test_bearish_grounded_thesis_reaches_bear_put_and_reconciles_idempotently(
    engine: AsyncEngine,
) -> None:
    agent, gateway, evidence, broker = build_agent(
        engine, model_thesis=thesis(ThesisDirection.BEARISH)
    )

    first = await agent.run_once(now=NOW)
    repeated = await agent.run_once(now=NOW)

    assert first.state is AgentRunState.FILLED
    assert first.selected_candidate is not None
    assert first.selected_candidate.direction == "bearish"
    assert first.selected_candidate.long_symbol == "SPY261030P00510000"
    assert first.selected_candidate.short_symbol == "SPY261030P00470000"
    assert first.quant_signal is not None
    assert first.quant_signal.direction is ThesisDirection.BEARISH
    assert first.risk_decision is not None
    assert first.risk_decision.outcome.value == "allow"
    assert first.receipt is not None
    assert first.market_data is not None
    persisted_intent = await agent._store.load_intent(first.intent_id)
    assert persisted_intent.legs[0].quantity == Decimal("1")
    assert repeated.receipt == first.receipt
    assert gateway.calls == 1
    assert evidence.news_calls == 2
    assert broker.submit_calls == 1
    assert gateway.requests[0].evidence.items[-1].provider == "alpaca_news"


@pytest.mark.asyncio
async def test_corroborated_market_truth_is_bound_to_grounded_v2_evidence(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    gateway = FakeGateway(thesis(ThesisDirection.BEARISH))
    evidence = FakeEvidenceGateway(news_item())
    broker = FakeBroker()
    market = FakeMarket(frame(previous_close="510"))
    observer = CorroboratingMarketTruthObserver()
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=market,
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
        evidence=evidence,
        intelligence=GroundedIntelligenceService(store, gateway),
        router=StrategyRouter(max_debit=Decimal("2.50")),
        portfolio_risk=risk_service(),
        market_truth=MarketTruthService(required_mcp=True),
        market_truth_observer=observer,
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.FILLED
    assert observer.calls == 1
    assert market.account_calls == 1
    assert market.position_calls == 1
    assert gateway.calls == 1
    model_evidence = gateway.requests[0].evidence
    assert model_evidence.version == "grounded-options-router-v2"
    assert model_evidence.market_truth_hash is not None
    assert model_evidence.mcp_observation_hashes == (
        "content-get_account",
        "content-get_clock",
        "content-get_option_chain",
        "content-get_news",
    )


@pytest.mark.asyncio
async def test_market_truth_conflict_abstains_before_model_router_risk_and_broker(
    engine: AsyncEngine,
) -> None:
    store = Store(async_sessionmaker(engine, expire_on_commit=False))
    gateway = FakeGateway(thesis(ThesisDirection.BEARISH))
    evidence = FakeEvidenceGateway(news_item())
    broker = FakeBroker()
    agent = AutonomousTradingAgent(
        store=store,
        broker=broker,
        market=FakeMarket(frame(previous_close="510")),
        strategy=DefinedRiskCallSpreadStrategy(),
        underlying="SPY",
        evidence=evidence,
        intelligence=GroundedIntelligenceService(store, gateway),
        router=StrategyRouter(max_debit=Decimal("2.50")),
        portfolio_risk=risk_service(),
        market_truth=ConflictingMarketTruth(required_mcp=True),
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.ABSTAINED
    assert result.decision.reasons == ("market_truth_conflicted", "account_id_mismatch")
    assert evidence.news_calls == 0
    assert gateway.calls == 0
    assert broker.submit_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_direction, expected_reason",
    [
        (ThesisDirection.NEUTRAL, "neutral direction"),
        (ThesisDirection.BULLISH, "AI and quant direction conflict"),
    ],
)
async def test_neutral_or_conflicting_direction_abstains_before_broker_submit(
    engine: AsyncEngine,
    model_direction: ThesisDirection,
    expected_reason: str,
) -> None:
    agent, _, _, broker = build_agent(engine, model_thesis=thesis(model_direction))

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.ABSTAINED
    assert result.intent_id is None
    assert result.receipt is None
    assert any(expected_reason in reason for reason in result.decision.reasons)
    assert broker.submit_calls == 0


@pytest.mark.asyncio
async def test_risk_denial_does_not_create_or_submit_intent(engine: AsyncEngine) -> None:
    agent, _, _, broker = build_agent(
        engine,
        model_thesis=thesis(ThesisDirection.BEARISH),
        per_trade_max_loss="100",
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.DENIED
    assert result.selected_candidate is not None
    assert result.risk_decision is not None
    assert result.risk_decision.outcome.value == "deny"
    assert "per_trade_max_loss_exceeded" in result.risk_decision.reasons
    assert result.intent_id is None
    assert result.receipt is None
    assert broker.submit_calls == 0


@pytest.mark.asyncio
async def test_insufficient_options_level_is_risk_denial_not_legacy_symbol_block(
    engine: AsyncEngine,
) -> None:
    agent, _, _, broker = build_agent(
        engine,
        model_thesis=thesis(ThesisDirection.BEARISH),
        options_level=2,
    )

    result = await agent.run_once(now=NOW)

    assert result.state is AgentRunState.DENIED
    assert result.risk_decision is not None
    assert "options_level_insufficient" in result.risk_decision.reasons
    assert not any("startswith" in reason for reason in result.decision.reasons)
    assert broker.submit_calls == 0
