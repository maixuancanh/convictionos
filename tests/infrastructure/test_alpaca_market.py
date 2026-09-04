from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from convictionos.domain.market_data import OptionsFeed
from convictionos.infrastructure.alpaca_market import AlpacaPaperMarketGateway


@pytest.mark.asyncio
@respx.mock
async def test_observes_stock_and_filtered_call_chain() -> None:
    stock = respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "latestTrade": {"p": 505.0, "t": "2026-08-30T15:00:00Z"},
                "prevDailyBar": {"c": 500.0},
            },
        )
    )
    chain = respx.get(
        "https://data.alpaca.markets/v1beta1/options/snapshots/SPY"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "snapshots": {
                    "SPY260918C00500000": {
                        "latestQuote": {
                            "bp": 7.8,
                            "ap": 8.0,
                            "t": "2026-08-30T15:00:00Z",
                        }
                    },
                    "SPY260918C00510000": {
                        "latestQuote": {
                            "bp": 5.6,
                            "ap": 5.75,
                            "t": "2026-08-30T15:00:00Z",
                        }
                    },
                }
            },
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    frame = await gateway.observe(
        "SPY",
        expiration_gte=date(2026, 9, 11),
        expiration_lte=date(2026, 10, 30),
    )

    assert frame.price == Decimal("505.0")
    assert frame.previous_close == Decimal("500.0")
    assert [call.strike for call in frame.calls] == [Decimal("500"), Decimal("510")]
    assert stock.calls[0].request.url.params["feed"] == "iex"
    assert chain.calls[0].request.url.params["feed"] == "indicative"
    assert chain.calls[0].request.url.params["type"] == "call"
    assert chain.calls[0].request.headers["APCA-API-KEY-ID"] == "key"
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_reads_paper_account_without_exposing_credentials() -> None:
    account_route = respx.get("https://paper-api.alpaca.markets/v2/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "paper-account-1",
                "status": "ACTIVE",
                "equity": "100000.50",
                "buying_power": "200001.00",
                "options_trading_level": 3,
            },
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    account = await gateway.get_account()

    assert account.account_id == "paper-account-1"
    assert account.equity == Decimal("100000.50")
    assert account.options_trading_level == 3
    assert account_route.calls[0].request.headers["APCA-API-SECRET-KEY"] == "secret"
    assert "secret" not in account.model_dump_json()
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_current_session_uses_alpaca_clock_next_open_and_next_close() -> None:
    route = respx.get("https://paper-api.alpaca.markets/v2/clock").mock(
        return_value=httpx.Response(
            200,
            json={
                "timestamp": "2026-11-27T18:30:00Z",
                "is_open": False,
                "next_open": "2026-11-30T14:30:00Z",
                "next_close": "2026-11-30T21:00:00Z",
            },
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    session = await gateway.current_session(now=datetime(2026, 11, 27, 18, 0, tzinfo=UTC))

    assert session.is_open is False
    assert session.observed_at == datetime(2026, 11, 27, 18, 30, tzinfo=UTC)
    assert session.next_open == datetime(2026, 11, 30, 14, 30, tzinfo=UTC)
    assert session.next_close == datetime(2026, 11, 30, 21, tzinfo=UTC)
    assert session.source == "alpaca_clock"
    assert route.calls[0].request.headers["APCA-API-SECRET-KEY"] == "secret"
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_reads_open_paper_positions() -> None:
    respx.get("https://paper-api.alpaca.markets/v2/positions").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "symbol": "SPY260918C00500000",
                    "qty": "1",
                    "market_value": "800.00",
                    "unrealized_pl": "25.50",
                }
            ],
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    positions = await gateway.get_positions()

    assert positions[0].symbol == "SPY260918C00500000"
    assert positions[0].quantity == Decimal("1")
    assert positions[0].unrealized_pl == Decimal("25.50")
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_get_option_quotes_requires_exact_fresh_aligned_snapshot() -> None:
    route = respx.get("https://data.alpaca.markets/v1beta1/options/snapshots").mock(
        return_value=httpx.Response(
            200,
            json={
                "snapshots": {
                    "SPY260918C00500000": {
                        "latestQuote": {"bp": "2.0", "ap": "2.2", "t": "2026-09-03T12:00:00Z"},
                        "greeks": {"delta": "0.5"},
                        "impliedVolatility": "0.2",
                        "openInterest": 100,
                        "volume": 10,
                    },
                    "SPY260918C00510000": {
                        "latestQuote": {"bp": "1.0", "ap": "1.2", "t": "2026-09-03T12:00:00Z"},
                        "greeks": {"delta": "0.4"},
                        "impliedVolatility": "0.2",
                        "openInterest": 100,
                        "volume": 10,
                    },
                }
            },
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    snapshot = await gateway.get_option_quotes(
        ("SPY260918C00500000", "SPY260918C00510000")
    )

    assert snapshot.symbols == ("SPY260918C00500000", "SPY260918C00510000")
    assert snapshot.snapshot_hash == snapshot.content_hash()
    assert route.calls[0].request.url.params["symbols"] == (
        "SPY260918C00500000,SPY260918C00510000"
    )
    await gateway.aclose()


@pytest.mark.asyncio
async def test_rejects_non_paper_trading_endpoint() -> None:
    with pytest.raises(ValueError, match="paper"):
        AlpacaPaperMarketGateway(
            api_key_id="key",
            api_secret_key="secret",
            paper_base_url="https://api.alpaca.markets",
        )


@pytest.mark.asyncio
@respx.mock
async def test_incomplete_chain_fails_closed() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "latestTrade": {"p": 505.0, "t": "2026-08-30T15:00:00Z"},
                "prevDailyBar": {"c": 500.0},
            },
        )
    )
    respx.get("https://data.alpaca.markets/v1beta1/options/snapshots/SPY").mock(
        return_value=httpx.Response(
            200,
            json={"snapshots": {"SPY260918C00500000": {"latestQuote": {"bp": 0}}}},
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    frame = await gateway.observe(
        "SPY",
        expiration_gte=date(2026, 9, 11),
        expiration_lte=date(2026, 10, 30),
    )

    assert frame.calls == ()
    assert frame.observed_at == datetime(2026, 8, 30, 15, 0, tzinfo=UTC)
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_opra_feed_is_bound_to_frame_capability_and_request() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "latestTrade": {"p": 505.0, "t": "2026-08-30T15:00:00Z"},
                "prevDailyBar": {"c": 500.0},
            },
        )
    )
    chain = respx.get("https://data.alpaca.markets/v1beta1/options/snapshots/SPY").mock(
        return_value=httpx.Response(200, json={"snapshots": {}})
    )
    gateway = AlpacaPaperMarketGateway(
        api_key_id="key", api_secret_key="secret", option_data_feed=OptionsFeed.OPRA
    )

    frame = await gateway.observe(
        "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
    )

    assert chain.calls[0].request.url.params["feed"] == "opra"
    assert frame.source == "alpaca-opra"
    assert frame.market_data.options_feed is OptionsFeed.OPRA
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_opra_entitlement_failure_is_not_retried_with_indicative() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "latestTrade": {"p": 505.0, "t": "2026-08-30T15:00:00Z"},
                "prevDailyBar": {"c": 500.0},
            },
        )
    )
    chain = respx.get("https://data.alpaca.markets/v1beta1/options/snapshots/SPY").mock(
        return_value=httpx.Response(403, json={"message": "not entitled"})
    )
    gateway = AlpacaPaperMarketGateway(
        api_key_id="key", api_secret_key="secret", option_data_feed=OptionsFeed.OPRA
    )

    with pytest.raises(httpx.HTTPStatusError):
        await gateway.observe(
            "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
        )

    assert len(chain.calls) == 1
    assert chain.calls[0].request.url.params["feed"] == "opra"
    await gateway.aclose()
