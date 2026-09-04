from datetime import UTC, datetime
from decimal import Decimal

from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.paper_accounting import AccountingState, PaperExecutionAccounting

ASSESSED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def test_open_fill_accounting_uses_conservative_opening_costs() -> None:
    capability = build_market_data_capability(
        UnderlyingFeed.IEX, OptionsFeed.INDICATIVE, ASSESSED_AT
    )

    accounting = PaperExecutionAccounting.from_open_fill(
        capability=capability,
        authorized_limit_price=Decimal("2.20"),
        spread_units=1,
        broker_reported_fill_price=Decimal("2.10"),
        broker_reported_filled_qty=Decimal("1"),
    )

    assert accounting.state is AccountingState.OPEN_FILL_ONLY
    assert accounting.authorized_cost_ceiling == Decimal("220.00")
    assert accounting.broker_reported_cost_basis == Decimal("210.00")
    assert accounting.conservative_realized_pnl is None
    assert accounting.performance_eligible is False
    assert accounting.limitations == capability.limitations


def test_try_from_open_fill_returns_none_when_fill_facts_are_missing() -> None:
    capability = build_market_data_capability(UnderlyingFeed.IEX, OptionsFeed.OPRA, ASSESSED_AT)

    assert PaperExecutionAccounting.try_from_open_fill(
        capability=capability,
        authorized_limit_price=Decimal("2.20"),
        spread_units=1,
        broker_reported_fill_price=None,
        broker_reported_filled_qty=Decimal("1"),
    ) is None


def test_try_from_open_fill_returns_none_for_zero_quantity() -> None:
    capability = build_market_data_capability(UnderlyingFeed.IEX, OptionsFeed.OPRA, ASSESSED_AT)

    assert PaperExecutionAccounting.try_from_open_fill(
        capability=capability,
        authorized_limit_price=Decimal("2.20"),
        spread_units=1,
        broker_reported_fill_price=Decimal("2.10"),
        broker_reported_filled_qty=Decimal("0"),
    ) is None


def test_try_from_open_fill_returns_none_for_unauthorized_facts() -> None:
    capability = build_market_data_capability(UnderlyingFeed.IEX, OptionsFeed.OPRA, ASSESSED_AT)

    assert PaperExecutionAccounting.try_from_open_fill(
        capability=capability,
        authorized_limit_price=Decimal("2.20"),
        spread_units=1,
        broker_reported_fill_price=Decimal("2.21"),
        broker_reported_filled_qty=Decimal("2"),
    ) is None
