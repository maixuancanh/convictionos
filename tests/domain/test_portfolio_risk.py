from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from convictionos.domain.portfolio_risk import HorizonRiskBudget, PortfolioSnapshot


def test_horizon_budget_is_immutable_and_validates_its_range() -> None:
    budget = HorizonRiskBudget(
        minimum_dte=7,
        maximum_dte=30,
        per_trade_max_loss=Decimal("100"),
        aggregate_capacity=Decimal("300"),
        max_positions=2,
        confidence_threshold=Decimal("0.7"),
        cooldown_seconds=3600,
    )

    with pytest.raises(ValidationError):
        HorizonRiskBudget(
            minimum_dte=30,
            maximum_dte=7,
            per_trade_max_loss=Decimal("100"),
            aggregate_capacity=Decimal("300"),
            max_positions=2,
            confidence_threshold=Decimal("0.7"),
            cooldown_seconds=0,
        )
    with pytest.raises(ValidationError):
        budget.per_trade_max_loss = Decimal("1")


def test_portfolio_snapshot_normalizes_directional_exposure_and_is_immutable() -> None:
    snapshot = PortfolioSnapshot(
        observed_at=datetime(2026, 8, 30, 15, 0, tzinfo=UTC),
        open_positions=(),
        active_reservations=(),
        account_equity=Decimal("10000"),
        buying_power=Decimal("5000"),
        options_level=3,
        directional_exposure={"SPY": Decimal("250")},
    )

    assert snapshot.directional_exposure["SPY"] == Decimal("250")
    with pytest.raises(ValidationError):
        PortfolioSnapshot(
            observed_at=snapshot.observed_at,
            open_positions=(),
            active_reservations=(),
            account_equity=Decimal("0"),
            buying_power=Decimal("1"),
            options_level=3,
            directional_exposure={},
        )
