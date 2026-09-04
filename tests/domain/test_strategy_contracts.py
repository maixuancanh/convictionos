from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from convictionos.domain.strategy import (
    CATALYST_DTE,
    SWING_DTE,
    THEMATIC_DTE,
    OptionMarketQuote,
    StrategyCandidate,
)
from convictionos.domain.trading import Horizon


def test_dte_bands_are_explicit_and_overlapping_at_boundaries() -> None:
    assert CATALYST_DTE == (7, 30)
    assert SWING_DTE == (30, 90)
    assert THEMATIC_DTE == (90, 450)


def test_option_market_quote_is_immutable_and_decimal_typed() -> None:
    quote = OptionMarketQuote(
        symbol="SPY260918C00500000",
        strike=Decimal("500"),
        expiration=date(2026, 9, 18),
        bid=Decimal("7.80"),
        ask=Decimal("8.00"),
        delta=Decimal("0.55"),
        implied_volatility=Decimal("0.22"),
        open_interest=1200,
        volume=300,
        as_of=datetime(2026, 8, 30, 15, 0, tzinfo=UTC),
        tradable=True,
    )

    assert quote.bid == Decimal("7.80")
    with pytest.raises(Exception, match="frozen"):
        quote.bid = Decimal("7.90")  # type: ignore[misc]


def test_strategy_candidate_is_immutable_and_preserves_rejection_reasons() -> None:
    candidate = StrategyCandidate(
        strategy_id="call-debit-spread-v1",
        direction="bullish",
        horizon=Horizon.SWING,
        long_symbol="SPY260918C00500000",
        short_symbol="SPY260918C00510000",
        limit_debit=Decimal("2.40"),
        max_loss=Decimal("240.00"),
        width=Decimal("10"),
        dte=30,
        long_delta=Decimal("0.55"),
        liquidity_score=Decimal("0.90"),
        slippage_estimate=Decimal("0.05"),
        utility_score=Decimal("0.72"),
        rejection_reasons=("none",),
    )

    assert candidate.rejection_reasons == ("none",)
    with pytest.raises(Exception, match="frozen"):
        candidate.dte = 31  # type: ignore[misc]
