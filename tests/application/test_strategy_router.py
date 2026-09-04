from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from convictionos.application.agent import AgentMarketFrame
from convictionos.application.strategy_router import (
    QuantSignal,
    RouterOutcome,
    StrategyRouter,
)
from convictionos.domain.intelligence import GroundedThesis, ThesisDirection
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.strategy import OptionMarketQuote
from convictionos.domain.trading import Horizon

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def thesis(direction: ThesisDirection, horizon: Horizon = Horizon.SWING) -> GroundedThesis:
    return GroundedThesis(
        direction=direction,
        confidence=Decimal("0.8"),
        horizon=horizon,
        catalyst="earnings catalyst",
        causal_mechanism="revenue growth",
        beneficiaries=("SPY",),
        adversely_affected=(),
        invalidation="guidance cut",
        material_risks=(),
        source_ids=("source-1",),
        contradicting_source_ids=(),
    )


def quote(
    symbol: str,
    strike: str,
    option_type: str,
    *,
    expiration: date = date(2026, 10, 30),
    bid: str = "2.00",
    ask: str = "2.20",
    delta: str = "0.55",
    as_of: datetime = NOW,
    open_interest: int = 100,
    volume: int = 20,
    tradable: bool = True,
) -> OptionMarketQuote:
    return OptionMarketQuote(
        symbol=f"{symbol}{option_type}{strike}",
        strike=Decimal(strike),
        expiration=expiration,
        bid=Decimal(bid),
        ask=Decimal(ask),
        delta=Decimal(delta) if option_type == "C" else -Decimal(delta),
        implied_volatility=Decimal("0.25"),
        open_interest=open_interest,
        volume=volume,
        as_of=as_of,
        tradable=tradable,
    )


def frame(*quotes: OptionMarketQuote, price: str = "100") -> AgentMarketFrame:
    result = AgentMarketFrame(
        underlying="SPY",
        price=Decimal(price),
        previous_close=Decimal("99"),
        observed_at=NOW,
        source="fixture",
        market_data=build_market_data_capability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.INDICATIVE,
            assessed_at=NOW,
        ),
        calls=(),
    )
    object.__setattr__(result, "option_quotes", quotes)
    return result


def quant(direction: ThesisDirection) -> QuantSignal:
    return QuantSignal(
        direction=direction,
        momentum=Decimal("0.03"),
        realized_volatility=Decimal("0.20"),
        as_of=NOW,
        version="quant-v1",
    )


def test_routes_bullish_thesis_to_lower_strike_call_debit_spread() -> None:
    result = StrategyRouter().route(
        thesis(ThesisDirection.BULLISH),
        frame(quote("SPY", "95", "C", ask="4.00"), quote("SPY", "102", "C", bid="2.00")),
        quant(ThesisDirection.BULLISH),
    )

    assert result.outcome is RouterOutcome.SELECTED
    assert result.selected is not None
    assert result.selected.long_symbol.endswith("C95")
    assert result.selected.short_symbol.endswith("C102")
    assert result.selected.limit_debit == Decimal("2.00")
    assert result.selected.max_loss == Decimal("200.00")


def test_routes_bearish_thesis_to_higher_strike_put_debit_spread() -> None:
    result = StrategyRouter().route(
        thesis(ThesisDirection.BEARISH),
        frame(
            quote("SPY", "105", "P", delta="0.55", ask="4.00"),
            quote("SPY", "98", "P", delta="0.40", bid="2.00"),
        ),
        quant(ThesisDirection.BEARISH),
    )

    assert result.outcome is RouterOutcome.SELECTED
    assert result.selected is not None
    assert result.selected.long_symbol.endswith("P105")
    assert result.selected.short_symbol.endswith("P98")


def test_neutral_and_conflicting_directions_abstain() -> None:
    neutral = StrategyRouter().route(
        thesis(ThesisDirection.NEUTRAL), frame(), quant(ThesisDirection.NEUTRAL)
    )
    conflict = StrategyRouter().route(
        thesis(ThesisDirection.BULLISH), frame(), quant(ThesisDirection.BEARISH)
    )

    assert neutral.outcome is RouterOutcome.ABSTAIN
    assert "neutral" in neutral.reasons[0]
    assert conflict.outcome is RouterOutcome.ABSTAIN
    assert "conflict" in conflict.reasons[0]


def test_rejects_stale_crossed_untradable_and_illiquid_quotes_with_reasons() -> None:
    stale = NOW - timedelta(minutes=6)
    result = StrategyRouter().route(
        thesis(ThesisDirection.BULLISH),
        frame(
            quote("SPY", "95", "C", ask="6.00", as_of=stale),
            quote("SPY", "105", "C", bid="2.00", as_of=stale),
            quote("SPY", "96", "C", ask="0.10", bid="0.20", tradable=False),
            quote("SPY", "106", "C", bid="0.10", ask="0.20", open_interest=0, volume=0),
        ),
        quant(ThesisDirection.BULLISH),
    )

    assert result.outcome is RouterOutcome.ABSTAIN
    assert any("stale" in reason for reason in result.reasons)
    assert any("crossed" in reason or "tradable" in reason for reason in result.reasons)
    assert any("liquidity" in reason for reason in result.reasons)


def test_rejects_dte_delta_width_and_debit_gates() -> None:
    result = StrategyRouter(max_debit=Decimal("1.00")).route(
        thesis(ThesisDirection.BULLISH, Horizon.CATALYST),
        frame(
            quote("SPY", "80", "C", expiration=date(2026, 9, 1), delta="0.80", ask="5.00"),
            quote("SPY", "90", "C", expiration=date(2026, 9, 1), bid="1.00"),
            quote("SPY", "99", "C", expiration=date(2026, 9, 10), delta="0.50", ask="2.00"),
            quote("SPY", "100", "C", expiration=date(2026, 9, 10), bid="2.00"),
        ),
        quant(ThesisDirection.BULLISH),
    )

    assert result.outcome is RouterOutcome.ABSTAIN
    assert any("DTE" in reason for reason in result.reasons)
    assert any("delta" in reason for reason in result.reasons)
    assert any("width" in reason for reason in result.reasons)
    assert any("debit" in reason for reason in result.reasons)


def test_ranks_candidates_deterministically_and_preserves_alternatives() -> None:
    quotes = (
        quote("SPY", "95", "C", ask="4.00"),
        quote("SPY", "100", "C", bid="2.00"),
        quote("SPY", "96", "C", ask="3.50"),
        quote("SPY", "101", "C", bid="1.50"),
    )
    router = StrategyRouter()
    first = router.route(
        thesis(ThesisDirection.BULLISH), frame(*quotes), quant(ThesisDirection.BULLISH)
    )
    second = router.route(
        thesis(ThesisDirection.BULLISH), frame(*reversed(quotes)), quant(ThesisDirection.BULLISH)
    )

    assert first.selected is not None and second.selected is not None
    assert first.selected.strategy_id == second.selected.strategy_id
    assert first.selected.long_symbol == second.selected.long_symbol
    assert first.alternatives
    assert first.utility_version == "options-router-utility-v1"
