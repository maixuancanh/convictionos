from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from convictionos.application.agent import (
    AgentAction,
    AgentMarketFrame,
    DefinedRiskCallSpreadStrategy,
    OptionQuote,
)
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def quote(symbol: str, strike: str, bid: str, ask: str) -> OptionQuote:
    return OptionQuote(
        symbol=symbol,
        strike=Decimal(strike),
        expiration=date(2026, 9, 18),
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        as_of=NOW - timedelta(seconds=20),
    )


def frame(*, observed_at: datetime = NOW) -> AgentMarketFrame:
    return AgentMarketFrame(
        underlying="SPY",
        price=Decimal("505"),
        previous_close=Decimal("500"),
        observed_at=observed_at,
        source="alpaca-indicative",
        market_data=build_market_data_capability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.INDICATIVE,
            assessed_at=observed_at,
        ),
        calls=(
            quote("SPY260918C00500000", "500", "7.80", "8.00"),
            quote("SPY260918C00510000", "510", "5.60", "5.75"),
            quote("SPY260918C00520000", "520", "3.90", "4.10"),
        ),
    )


def test_selects_defined_risk_call_spread_from_fresh_bullish_frame() -> None:
    decision = DefinedRiskCallSpreadStrategy().decide(frame(), now=NOW)

    assert decision.action is AgentAction.TRADE
    assert decision.long_symbol == "SPY260918C00500000"
    assert decision.short_symbol == "SPY260918C00510000"
    assert decision.limit_price == Decimal("2.40")
    assert decision.max_loss == Decimal("240.00")
    assert decision.snapshot_hash == frame().snapshot_hash()


def test_stale_market_frame_abstains() -> None:
    decision = DefinedRiskCallSpreadStrategy().decide(
        frame(observed_at=NOW - timedelta(minutes=6)), now=NOW
    )

    assert decision.action is AgentAction.ABSTAIN
    assert "stale" in decision.reasons[0]


def test_incomplete_option_quotes_abstain() -> None:
    incomplete = frame().model_copy(update={"calls": frame().calls[:1]})

    decision = DefinedRiskCallSpreadStrategy().decide(incomplete, now=NOW)

    assert decision.action is AgentAction.ABSTAIN
    assert decision.reasons == ("fewer than two usable call contracts",)


def test_same_snapshot_produces_same_decision_identity() -> None:
    strategy = DefinedRiskCallSpreadStrategy()

    first = strategy.decide(frame(), now=NOW)
    second = strategy.decide(frame(), now=NOW)

    assert first.decision_id == second.decision_id
