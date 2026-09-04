from datetime import UTC, datetime
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from convictionos.api.app import create_app
from convictionos.application.agent import (
    AgentAction,
    AgentDecision,
    AgentRunResult,
    AgentRunState,
    QuantSignalSnapshot,
)
from convictionos.application.portfolio_risk import RiskDecision, RiskOutcome
from convictionos.domain.intelligence import ThesisDirection
from convictionos.domain.mandates import PolicyOutcome
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.paper_accounting import PaperExecutionAccounting
from convictionos.domain.receipts import DecisionReceipt
from convictionos.domain.strategy import StrategyCandidate
from convictionos.domain.trading import Horizon
from convictionos.infrastructure.brokers import FakeBroker

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


class StubAgent:
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result

    async def run_once(self, *, now: datetime) -> AgentRunResult:
        del now
        return self.result


def decision(*reasons: str) -> AgentDecision:
    return AgentDecision(
        decision_id="decision-safe-projection",
        action=AgentAction.ABSTAIN,
        underlying="SPY",
        thesis="A grounded thesis is available for review.",
        invalidation="The thesis is invalidated by new evidence.",
        reasons=reasons,
        snapshot_hash="snapshot-safe",
    )


def candidate() -> StrategyCandidate:
    return StrategyCandidate(
        strategy_id="candidate-safe",
        direction="bearish",
        horizon=Horizon.SWING,
        long_symbol="SPY261030P00510000",
        short_symbol="SPY261030P00470000",
        limit_debit=Decimal("2.10"),
        max_loss=Decimal("210"),
        width=Decimal("40"),
        dte=61,
        long_delta=Decimal("-0.55"),
        liquidity_score=Decimal("0.88"),
        slippage_estimate=Decimal("0.10"),
        utility_score=Decimal("0.79"),
        rejection_reasons=(),
    )


def quant_snapshot() -> QuantSignalSnapshot:
    return QuantSignalSnapshot(
        direction=ThesisDirection.BEARISH,
        momentum=Decimal("-0.01"),
        realized_volatility=Decimal("0.20"),
        as_of=NOW,
        version="quant-v1",
    )


def selected_result() -> AgentRunResult:
    return AgentRunResult(
        state=AgentRunState.DENIED,
        decision=decision("portfolio risk budget exceeded"),
        intent_id="must-not-leak-intent",
        broker_order_id="must-not-leak-order",
        selected_candidate=candidate(),
        quant_snapshot=quant_snapshot(),
        risk_decision=RiskDecision(
            outcome=RiskOutcome.DENY,
            reasons=("per_trade_max_loss_exceeded",),
            horizon=Horizon.SWING,
            projected_max_loss=Decimal("210"),
        ),
    )


async def test_readiness_is_typed_and_unavailable_before_any_run(
    engine: AsyncEngine,
) -> None:
    app = create_app(engine=engine, broker=FakeBroker())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/strategy/readiness")
        control_plane = await client.get("/v1/control-plane")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "unavailable",
        "provider_thesis_status": "unavailable",
        "direction": None,
        "horizon": None,
        "ai_quant_agreement": "unavailable",
        "candidate_outcome": "unavailable",
        "selected_candidate": None,
        "rejected_candidates": [],
        "max_loss": None,
        "expiry": None,
        "expiry_dte": None,
        "liquidity": None,
        "portfolio_risk_budget": None,
        "evidence_freshness": "unavailable",
        "abstention_reason": "no agent run result available",
        "paper_only": True,
        "live_trading_authorized": False,
        "underlying_feed": None,
        "options_feed": None,
        "market_data_tier": "unavailable",
        "max_spread_units": None,
        "performance_eligible": False,
        "market_data_limitations": [],
        "accounting_state": "unavailable",
        "authorized_cost_ceiling": None,
        "broker_reported_entry_cost": None,
        "conservative_realized_pnl": None,
            "lifecycle_summary": {
            "open": 0,
            "close_pending": 0,
            "review": 0,
            "closed": 0,
            "positions": [],
        },
    }
    assert control_plane.json()["agent_readiness"] == body


async def test_readiness_projects_run_result_without_execution_internals(
    engine: AsyncEngine,
) -> None:
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        agent=StubAgent(selected_result()),
        agent_enabled=True,
        agent_control_token="control-secret",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        run = await client.post(
            "/v1/agent/run-once", headers={"X-Agent-Control-Token": "control-secret"}
        )
        response = await client.get("/v1/agent/readiness")
        dashboard = await client.get("/dashboard")

    assert run.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "denied"
    assert body["provider_thesis_status"] == "validated"
    assert body["direction"] == "bearish"
    assert body["horizon"] == "swing"
    assert body["ai_quant_agreement"] == "confirmed"
    assert body["candidate_outcome"] == "selected"
    assert body["selected_candidate"] == {
        "strategy_id": "candidate-safe",
        "direction": "bearish",
        "horizon": "swing",
        "long_symbol": "SPY261030P00510000",
        "short_symbol": "SPY261030P00470000",
        "max_loss": "210",
        "expiry_dte": 61,
        "liquidity": "0.88",
    }
    assert body["expiry"] == "2026-10-30"
    assert body["max_loss"] == "210"
    assert body["liquidity"] == "0.88"
    assert body["portfolio_risk_budget"] == {
        "outcome": "deny",
        "horizon": "swing",
        "projected_max_loss": "210",
        "reasons": ["per_trade_max_loss_exceeded"],
    }
    assert body["abstention_reason"] == "portfolio risk budget exceeded"
    assert "must-not-leak-intent" not in response.text
    assert "must-not-leak-order" not in response.text
    assert "agent_control_token" not in response.text
    assert "Mission Control" in dashboard.text
    assert "AI THESIS" in dashboard.text
    assert "QUANT CONFIRMATION" in dashboard.text
    assert "PAPER-SAFE" in dashboard.text
    assert "no live capital authorization" in dashboard.text


async def test_readiness_projects_indicative_capability_and_open_fill_accounting(
    engine: AsyncEngine,
) -> None:
    capability = build_market_data_capability(UnderlyingFeed.IEX, OptionsFeed.INDICATIVE, NOW)
    accounting = PaperExecutionAccounting.from_open_fill(
        capability=capability,
        authorized_limit_price=Decimal("1.25"),
        spread_units=1,
        broker_reported_fill_price=Decimal("1.20"),
        broker_reported_filled_qty=Decimal("1"),
    )
    result = selected_result().model_copy(
        update={"market_data": capability, "paper_accounting": accounting}
    )
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        agent=StubAgent(result),
        agent_enabled=True,
        agent_control_token="control-secret",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agent/run-once", headers={"X-Agent-Control-Token": "control-secret"})
        response = await client.get("/v1/strategy/readiness")
        dashboard = await client.get("/dashboard")

    body = response.json()
    assert body["underlying_feed"] == "iex"
    assert body["options_feed"] == "indicative"
    assert body["market_data_tier"] == "paper_executable"
    assert body["max_spread_units"] == 1
    assert body["performance_eligible"] is False
    assert body["market_data_limitations"] == [
        "indicative_options_feed",
        "paper_simulation_not_live_equivalent",
    ]
    assert body["accounting_state"] == "open_fill_only"
    assert body["authorized_cost_ceiling"] == "125.00"
    assert body["broker_reported_entry_cost"] == "120.00"
    assert body["conservative_realized_pnl"] is None
    assert "INDICATIVE DATA" in dashboard.text
    assert "Paper fills do not establish live execution quality" in dashboard.text
    assert "OPEN FILL ONLY" in dashboard.text
    assert "Realized P&amp;L unavailable until reconciled close." in dashboard.text


async def test_readiness_honors_receipt_capability_and_accounting_facts(
    engine: AsyncEngine,
) -> None:
    capability = build_market_data_capability(UnderlyingFeed.SIP, OptionsFeed.OPRA, NOW)
    accounting = PaperExecutionAccounting.from_open_fill(
        capability=capability,
        authorized_limit_price=Decimal("1.25"),
        spread_units=1,
        broker_reported_fill_price=Decimal("1.20"),
        broker_reported_filled_qty=Decimal("1"),
    )
    result = selected_result().model_copy(
        update={
            "receipt": DecisionReceipt(
                receipt_id="receipt-safe",
                intent_id="intent-safe",
                operation_hash="operation-safe",
                evidence_snapshot_hash="evidence-safe",
                mandate_id="mandate-safe",
                mandate_version=1,
                policy_outcome=PolicyOutcome.DENY,
                policy_reasons=(),
                broker_order_id="broker-secret",
                broker_status="filled",
                previous_receipt_hash="genesis",
                market_data=capability,
                paper_accounting=accounting,
            )
        }
    )
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        agent=StubAgent(result),
        agent_enabled=True,
        agent_control_token="control-secret",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agent/run-once", headers={"X-Agent-Control-Token": "control-secret"})
        response = await client.get("/v1/strategy/readiness")
        dashboard = await client.get("/dashboard")

    body = response.json()
    assert body["underlying_feed"] == "sip"
    assert body["options_feed"] == "opra"
    assert body["market_data_tier"] == "paper_executable"
    assert body["performance_eligible"] is False
    assert body["market_data_limitations"] == ["paper_simulation_not_live_equivalent"]
    assert "OPRA DATA" in dashboard.text
    assert "PERFORMANCE NOT VALIDATED" in dashboard.text
    assert "broker-secret" not in response.text


async def test_readiness_explains_conflict_as_abstention_without_fake_metrics(
    engine: AsyncEngine,
) -> None:
    result = AgentRunResult(
        state=AgentRunState.ABSTAINED,
        decision=decision("AI and quant direction conflict"),
        quant_snapshot=QuantSignalSnapshot(
            direction=ThesisDirection.BULLISH,
            momentum=Decimal("0.01"),
            realized_volatility=Decimal("0.20"),
            as_of=NOW,
            version="quant-v1",
        ),
    )
    app = create_app(
        engine=engine,
        broker=FakeBroker(),
        agent=StubAgent(result),
        agent_enabled=True,
        agent_control_token="control-secret",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agent/run-once", headers={"X-Agent-Control-Token": "control-secret"}
        )
        response = await client.get("/v1/strategy/readiness")

    body = response.json()
    assert body["status"] == "abstained"
    assert body["provider_thesis_status"] == "validated"
    assert body["ai_quant_agreement"] == "conflict"
    assert body["candidate_outcome"] == "abstained"
    assert body["selected_candidate"] is None
    assert body["max_loss"] is None
    assert body["expiry"] is None
    assert body["liquidity"] is None
    assert body["portfolio_risk_budget"] is None
    assert body["rejected_candidates"] == [
        {"reason": "AI and quant direction conflict"}
    ]
    assert body["abstention_reason"] == "AI and quant direction conflict"
