from datetime import UTC, datetime
from decimal import Decimal

import pytest

from convictionos.domain.closed_accounting import close_position_accounting
from convictionos.domain.market_data import (
    ExecutionTier,
    MarketDataCapability,
    MarketDataLimitation,
    OptionsFeed,
    UnderlyingFeed,
)
from convictionos.domain.paper_accounting import PaperExecutionAccounting

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def capability() -> MarketDataCapability:
    return MarketDataCapability(
        underlying_feed=UnderlyingFeed.IEX,
        options_feed=OptionsFeed.OPRA,
        execution_tier=ExecutionTier.PAPER_EXECUTABLE,
        limitations=(MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,),
        assessed_at=NOW,
    )


def opening() -> PaperExecutionAccounting:
    return PaperExecutionAccounting(
        capability=capability(),
        authorized_limit_price=Decimal("1.25"),
        spread_units=2,
        authorized_cost_ceiling=Decimal("250"),
        broker_reported_fill_price=Decimal("1.20"),
        broker_reported_filled_qty=Decimal("2"),
        broker_reported_cost_basis=Decimal("240"),
        limitations=capability().limitations,
    )


def test_complete_close_uses_broker_and_conservative_basis() -> None:
    result = close_position_accounting(
        opening=opening(),
        close_fill_credit=Decimal("1.80"),
        close_filled_units=Decimal("2"),
        authorized_close_credit_floor=Decimal("1.50"),
        capability=capability(),
    )
    assert result.broker_reported_gross_pnl == Decimal("120.00")
    assert result.conservative_gross_pnl == Decimal("50.00")
    assert result.verified_fees is None
    assert result.verified_after_cost_pnl is None


def test_verified_fees_enable_after_cost_pnl() -> None:
    result = close_position_accounting(
        opening=opening(),
        close_fill_credit=Decimal("1.80"),
        close_filled_units=Decimal("2"),
        authorized_close_credit_floor=Decimal("1.50"),
        capability=capability(),
        verified_fees=Decimal("3.25"),
    )
    assert result.verified_after_cost_pnl == Decimal("116.75")


@pytest.mark.parametrize(
    ("field", "value"),
    [("close_filled_units", Decimal("1")), ("close_fill_credit", Decimal("NaN"))],
)
def test_close_fails_closed(field, value) -> None:
    values = {
        "opening": opening(),
        "close_fill_credit": Decimal("1.80"),
        "close_filled_units": Decimal("2"),
        "authorized_close_credit_floor": Decimal("1.50"),
        "capability": capability(),
    }
    values[field] = value
    with pytest.raises(ValueError):
        close_position_accounting(**values)


def test_close_below_floor_or_mixed_capability_is_rejected() -> None:
    with pytest.raises(ValueError, match="below authorized"):
        close_position_accounting(
            opening=opening(), close_fill_credit=Decimal("1.49"),
            close_filled_units=Decimal("2"), authorized_close_credit_floor=Decimal("1.50"),
            capability=capability(),
        )
    indicative = capability().model_copy(
        update={
            "options_feed": OptionsFeed.INDICATIVE,
            "execution_tier": ExecutionTier.PAPER_EXECUTABLE,
            "max_spread_units": 1,
            "limitations": (
                MarketDataLimitation.INDICATIVE_OPTIONS_FEED,
                MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,
            ),
        }
    )
    with pytest.raises(ValueError, match="capabilities"):
        close_position_accounting(
            opening=opening(), close_fill_credit=Decimal("1.80"),
            close_filled_units=Decimal("2"), authorized_close_credit_floor=Decimal("1.50"),
            capability=indicative,
        )
