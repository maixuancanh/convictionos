from datetime import UTC, datetime, timedelta
from decimal import Decimal

from convictionos.application.portfolio_risk import (
    PortfolioRiskService,
    RiskOutcome,
)
from convictionos.domain.intelligence import GroundedThesis, ThesisDirection
from convictionos.domain.portfolio_risk import (
    ActiveRiskReservation,
    HorizonRiskBudget,
    OpenPosition,
    PortfolioSnapshot,
)
from convictionos.domain.strategy import StrategyCandidate
from convictionos.domain.trading import Horizon

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def thesis(horizon: Horizon, catalyst: str = "earnings") -> GroundedThesis:
    return GroundedThesis(
        direction=ThesisDirection.BULLISH,
        confidence=Decimal("0.85"),
        horizon=horizon,
        catalyst=catalyst,
        causal_mechanism="profits improve",
        beneficiaries=("SPY",),
        adversely_affected=(),
        invalidation="guidance cut",
        material_risks=(),
        source_ids=("source-1",),
        contradicting_source_ids=(),
    )


def candidate(horizon: Horizon = Horizon.SWING, loss: str = "100") -> StrategyCandidate:
    return StrategyCandidate(
        strategy_id="candidate-1",
        direction="bullish",
        horizon=horizon,
        long_symbol="SPY-C95",
        short_symbol="SPY-C105",
        limit_debit=Decimal(loss) / Decimal("100"),
        max_loss=Decimal(loss),
        width=Decimal("10"),
        dte=60 if horizon is Horizon.SWING else 20,
        long_delta=Decimal("0.55"),
        liquidity_score=Decimal("0.9"),
        slippage_estimate=Decimal("0.05"),
        utility_score=Decimal("0.8"),
        rejection_reasons=(),
    )


def snapshot(**changes: object) -> PortfolioSnapshot:
    values: dict[str, object] = {
        "observed_at": NOW,
        "open_positions": (),
        "active_reservations": (),
        "account_equity": Decimal("10000"),
        "buying_power": Decimal("5000"),
        "options_level": 3,
        "directional_exposure": {},
    }
    values.update(changes)
    return PortfolioSnapshot(**values)


def service() -> PortfolioRiskService:
    budget = HorizonRiskBudget(
        minimum_dte=30,
        maximum_dte=90,
        per_trade_max_loss=Decimal("200"),
        aggregate_capacity=Decimal("500"),
        max_positions=2,
        confidence_threshold=Decimal("0.7"),
        cooldown_seconds=3600,
    )
    return PortfolioRiskService(
        budgets={Horizon.SWING: budget},
        aggregate_max_loss=Decimal("600"),
        max_directional_exposure=Decimal("300"),
        minimum_options_level=3,
    )


def test_horizons_have_independent_capacity_and_aggregate_loss_is_enforced() -> None:
    decision = service().evaluate(candidate(), thesis(Horizon.SWING), snapshot())
    assert decision.outcome is RiskOutcome.ALLOW
    reserved = snapshot(
        active_reservations=(
            ActiveRiskReservation(horizon=Horizon.SWING, max_loss=Decimal("500")),
        )
    )
    denied = service().evaluate(candidate(loss="200"), thesis(Horizon.SWING), reserved)
    assert denied.outcome is RiskOutcome.DENY
    assert "horizon_capacity_exceeded" in denied.reasons
    assert "aggregate_max_loss_exceeded" in denied.reasons


def test_duplicate_catalyst_concentration_and_options_level_are_denied() -> None:
    existing = OpenPosition(
        underlying="SPY",
        horizon=Horizon.SWING,
        direction=ThesisDirection.BULLISH,
        catalyst="earnings",
        max_loss=Decimal("100"),
    )
    denied = service().evaluate(
        candidate(), thesis(Horizon.SWING), snapshot(open_positions=(existing,), options_level=2)
    )
    assert denied.outcome is RiskOutcome.DENY
    assert "duplicate_catalyst" in denied.reasons
    assert "options_level_insufficient" in denied.reasons


def test_opposite_direction_hedge_can_reduce_existing_exposure() -> None:
    bearish = thesis(Horizon.SWING).model_copy(update={"direction": ThesisDirection.BEARISH})
    hedge = candidate().model_copy(update={"direction": "bearish"})
    decision = service().evaluate(
        hedge,
        bearish,
        snapshot(directional_exposure={"SPY": Decimal("500")}),
    )
    assert decision.outcome is RiskOutcome.ALLOW
    assert decision.hedge is True


def test_cooldown_and_concurrent_reservation_count_are_fail_closed() -> None:
    budget = service()
    recent = snapshot(last_trade_at=NOW - timedelta(minutes=5))
    cooldown = budget.evaluate(candidate(), thesis(Horizon.SWING), recent)
    assert cooldown.outcome is RiskOutcome.ABSTAIN
    assert "cooldown_active" in cooldown.reasons

    reservations = tuple(
        ActiveRiskReservation(horizon=Horizon.SWING, max_loss=Decimal("1")) for _ in range(2)
    )
    full = budget.evaluate(
        candidate(), thesis(Horizon.SWING), snapshot(active_reservations=reservations)
    )
    assert full.outcome is RiskOutcome.DENY
    assert "max_positions_exceeded" in full.reasons
