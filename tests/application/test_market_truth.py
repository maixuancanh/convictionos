from datetime import UTC, date, datetime
from decimal import Decimal

from convictionos.application.agent import AgentMarketFrame
from convictionos.application.market_truth import (
    MarketTruthOutcome,
    MarketTruthService,
)
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.domain.strategy import OptionMarketQuote
from convictionos.infrastructure.alpaca_market import PaperAccountState
from convictionos.infrastructure.alpaca_mcp import McpObservation

NOW = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)


def option_quote(
    symbol: str = "SPY261030P00510000",
    *,
    strike: str = "510",
    bid: str = "3.80",
    ask: str = "4.00",
    as_of: datetime = NOW,
) -> OptionMarketQuote:
    return OptionMarketQuote(
        symbol=symbol,
        option_type="put",
        strike=Decimal(strike),
        expiration=date(2026, 10, 30),
        bid=Decimal(bid),
        ask=Decimal(ask),
        delta=Decimal("-0.55"),
        implied_volatility=Decimal("0.25"),
        open_interest=1200,
        volume=300,
        as_of=as_of,
        tradable=True,
    )


def frame(*quotes: OptionMarketQuote) -> AgentMarketFrame:
    return AgentMarketFrame(
        underlying="SPY",
        price=Decimal("505"),
        previous_close=Decimal("500"),
        observed_at=NOW,
        source="alpaca-indicative",
        market_data=build_market_data_capability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.INDICATIVE,
            assessed_at=NOW,
        ),
        calls=(),
        option_quotes=quotes or (option_quote(),),
    )


def account(**updates: object) -> PaperAccountState:
    values = {
        "account_id": "paper-account-1",
        "status": "ACTIVE",
        "equity": Decimal("10000"),
        "buying_power": Decimal("5000"),
        "options_trading_level": 3,
    }
    values.update(updates)
    return PaperAccountState(**values)


def observation(
    tool_name: str,
    facts: dict[str, object],
    *,
    content_hash: str | None = None,
    observed_at: datetime = NOW,
    limitations: tuple[str, ...] = ("mcp_read_model",),
) -> McpObservation:
    return McpObservation(
        provider_version="alpaca-mcp-server==2.3.1",
        tool_name=tool_name,
        request_hash=f"request-{tool_name}",
        observed_at=observed_at,
        facts=facts,
        limitations=limitations,
        content_hash=content_hash or f"content-{tool_name}",
    )


def corroborating_observations() -> tuple[McpObservation, ...]:
    return (
        observation(
            "get_account",
            {
                "id": "paper-account-1",
                "status": "ACTIVE",
                "cash": "5000",
                "buying_power": "5000",
            },
        ),
        observation(
            "get_clock",
            {
                "timestamp": NOW.isoformat(),
                "is_open": True,
                "session_open": True,
            },
        ),
        observation(
            "get_option_chain",
            {
                "symbol": "SPY261030P00510000",
                "expiration_date": "2026-10-30",
                "strike_price": "510",
                "type": "put",
                "latest_quote_timestamp": NOW.isoformat(),
            },
        ),
        observation(
            "get_news",
            {"id": "news-1", "published_at": "2026-09-04T13:55:00+00:00"},
        ),
    )


def test_market_truth_packet_hash_changes_when_mcp_fact_changes() -> None:
    first = MarketTruthService(required_mcp=True).cross_check(
        frame=frame(),
        account=account(),
        observations=corroborating_observations(),
        observed_at=NOW,
    )
    changed_news = observation(
        "get_news",
        {"id": "news-1", "published_at": "2026-09-04T13:56:00+00:00"},
        content_hash="content-news-changed",
    )
    second = MarketTruthService(required_mcp=True).cross_check(
        frame=frame(),
        account=account(),
        observations=(*corroborating_observations()[:-1], changed_news),
        observed_at=NOW,
    )

    assert first.outcome is MarketTruthOutcome.CORROBORATED
    assert second.outcome is MarketTruthOutcome.CORROBORATED
    assert first.truth_hash != second.truth_hash
    assert first.mcp_observation_hashes == (
        "content-get_account",
        "content-get_clock",
        "content-get_option_chain",
        "content-get_news",
    )


def test_mcp_account_mismatch_conflicts_with_stable_reason() -> None:
    packet = MarketTruthService(required_mcp=True).cross_check(
        frame=frame(),
        account=account(),
        observations=(
            observation("get_account", {"id": "other-account", "status": "ACTIVE"}),
            *corroborating_observations()[1:],
        ),
        observed_at=NOW,
    )

    assert packet.outcome is MarketTruthOutcome.CONFLICTED
    assert "account_id_mismatch" in packet.reasons
    assert packet.rest_source_hash == frame().snapshot_hash()


def test_required_mcp_outage_does_not_label_rest_as_mcp() -> None:
    packet = MarketTruthService(required_mcp=True).cross_check(
        frame=frame(),
        account=account(),
        observations=(),
        observed_at=NOW,
    )

    assert packet.outcome is MarketTruthOutcome.MCP_UNAVAILABLE
    assert packet.reasons == ("required_mcp_unavailable",)
    assert packet.mcp_observation_hashes == ()


def test_option_metadata_and_quote_time_are_cross_checked() -> None:
    packet = MarketTruthService(required_mcp=True).cross_check(
        frame=frame(),
        account=account(),
        observations=(
            *corroborating_observations()[:2],
            observation(
                "get_option_chain",
                {
                    "symbol": "SPY261030P00510000",
                    "expiration_date": "2026-10-31",
                    "strike_price": "510",
                    "type": "put",
                    "latest_quote_timestamp": "2026-09-04T13:00:00+00:00",
                },
            ),
            corroborating_observations()[-1],
        ),
        observed_at=NOW,
    )

    assert packet.outcome is MarketTruthOutcome.CONFLICTED
    assert "option_expiration_mismatch:SPY261030P00510000" in packet.reasons
    assert "option_quote_timestamp_mismatch:SPY261030P00510000" in packet.reasons
