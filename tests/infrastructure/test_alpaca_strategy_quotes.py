from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from convictionos.domain.market_data import OptionsFeed
from convictionos.infrastructure.alpaca_market import AlpacaPaperMarketGateway

NOW = "2026-08-30T15:00:00Z"
CHAIN_URL = "https://data.alpaca.markets/v1beta1/options/snapshots/SPY"
CONTRACTS_URL = "https://paper-api.alpaca.markets/v2/options/contracts"


def stock_payload() -> dict[str, object]:
    return {
        "latestTrade": {"p": 505.0, "t": NOW},
        "previousDailyBar": {"c": 500.0},
    }


def option_snapshot(
    symbol: str,
    *,
    bid: str = "2.00",
    ask: str = "2.20",
    delta: str = "0.55",
    implied_volatility: str = "0.25",
    open_interest: int = 1200,
    volume: int = 300,
    as_of: str = NOW,
) -> dict[str, object]:
    return {
        "latestQuote": {"bp": bid, "ap": ask, "t": as_of},
        "greeks": {"delta": delta},
        "impliedVolatility": implied_volatility,
        "openInterest": open_interest,
        "volume": volume,
    }


def contract(symbol: str, option_type: str, strike: str) -> dict[str, object]:
    return {
        "id": f"id-{symbol}",
        "symbol": symbol,
        "status": "active",
        "tradable": True,
        "expiration_date": "2026-10-30",
        "root_symbol": "SPY",
        "underlying_symbol": "SPY",
        "type": option_type,
        "style": "american",
        "strike_price": strike,
        "size": "100",
    }


@pytest.mark.asyncio
@respx.mock
async def test_observe_exposes_enriched_calls_and_puts_with_alpaca_metadata() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(200, json=stock_payload())
    )
    chain_route = respx.get(CHAIN_URL).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "snapshots": {
                    (
                        "SPY261030C00500000"
                        if request.url.params["type"] == "call"
                        else "SPY261030P00500000"
                    ): option_snapshot(
                        "SPY261030C00500000"
                        if request.url.params["type"] == "call"
                        else "SPY261030P00500000",
                        delta=("0.55" if request.url.params["type"] == "call" else "-0.55"),
                    )
                }
            },
        )
    )
    contracts_route = respx.get(CONTRACTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "option_contracts": [
                    contract("SPY261030C00500000", "call", "500"),
                    contract("SPY261030P00500000", "put", "500"),
                ],
                "page_token": None,
            },
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    frame = await gateway.observe(
        "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
    )

    assert [quote.symbol for quote in frame.calls] == ["SPY261030C00500000"]
    assert [quote.option_type for quote in frame.option_quotes] == ["call", "put"]
    assert frame.option_quotes[0].strike == Decimal("500")
    assert frame.option_quotes[1].delta == Decimal("-0.55")
    assert frame.option_quotes[0].bid == Decimal("2.00")
    assert frame.option_quotes[0].ask == Decimal("2.20")
    assert frame.option_quotes[0].implied_volatility == Decimal("0.25")
    assert frame.option_quotes[0].open_interest == 1200
    assert frame.option_quotes[0].volume == 300
    assert frame.option_quotes[0].as_of.isoformat() == "2026-08-30T15:00:00+00:00"
    assert frame.option_quotes[0].tradable is True

    assert {call.request.url.params["type"] for call in chain_route.calls} == {"call", "put"}
    first_chain_request = chain_route.calls[0].request
    assert first_chain_request.url.params["feed"] == "indicative"
    assert first_chain_request.url.params["expiration_date_gte"] == "2026-09-11"
    assert first_chain_request.url.params["expiration_date_lte"] == "2026-10-30"
    contract_request = contracts_route.calls[0].request
    assert contract_request.url.params["underlying_symbols"] == "SPY"
    assert contract_request.url.params["status"] == "active"
    assert contract_request.url.params["expiration_date_gte"] == "2026-09-11"
    assert contract_request.url.params["expiration_date_lte"] == "2026-10-30"
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_observe_follows_chain_and_contract_pagination() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(200, json=stock_payload())
    )
    chain_route = respx.get(CHAIN_URL).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=(
                {
                    "snapshots": {
                        "SPY261030C00500000": option_snapshot(
                            "SPY261030C00500000", delta="0.55"
                        )
                    },
                    "next_page_token": "call-next",
                }
                if request.url.params["type"] == "call"
                and "page_token" not in request.url.params
                else {
                    "snapshots": {
                        "SPY261030C00510000": option_snapshot(
                            "SPY261030C00510000", delta="0.50"
                        )
                    }
                }
                if request.url.params["type"] == "call"
                else {
                    "snapshots": {
                        "SPY261030P00500000": option_snapshot(
                            "SPY261030P00500000", delta="-0.55"
                        )
                    },
                    "next_page_token": "put-next",
                }
                if "page_token" not in request.url.params
                else {
                    "snapshots": {
                        "SPY261030P00510000": option_snapshot(
                            "SPY261030P00510000", delta="-0.50"
                        )
                    }
                }
            ),
        )
    )
    contracts_route = respx.get(CONTRACTS_URL).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=(
                {
                    "option_contracts": [
                        contract("SPY261030C00500000", "call", "500"),
                        contract("SPY261030P00500000", "put", "500"),
                    ],
                    "page_token": "contract-next",
                }
                if "page_token" not in request.url.params
                else {
                    "option_contracts": [
                        contract("SPY261030C00510000", "call", "510"),
                        contract("SPY261030P00510000", "put", "510"),
                    ],
                    "page_token": None,
                }
            ),
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    frame = await gateway.observe(
        "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
    )

    assert len(frame.option_quotes) == 4
    assert len(chain_route.calls) == 4
    assert len(contracts_route.calls) == 2
    assert any(
        call.request.url.params.get("page_token") == "call-next" for call in chain_route.calls
    )
    assert any(
        call.request.url.params.get("page_token") == "put-next" for call in chain_route.calls
    )
    assert contracts_route.calls[1].request.url.params["page_token"] == "contract-next"
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_observe_excludes_stale_malformed_and_non_executable_snapshots() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(200, json=stock_payload())
    )
    snapshots = {
        "SPY261030C00500000": option_snapshot("SPY261030C00500000"),
        "SPY261030P00500000": option_snapshot(
            "SPY261030P00500000", delta="-0.55", as_of="2026-08-30T14:54:00Z"
        ),
        "SPY261030C00510000": option_snapshot(
            "SPY261030C00510000", delta="not-a-number"
        ),
        "SPY261030P00510000": option_snapshot(
            "SPY261030P00510000", delta="-0.50", open_interest=0
        ),
        "SPY261030C00520000": {"latestQuote": {"bp": "2", "ap": "2.2", "t": NOW}},
        "SPY261030P00520000": option_snapshot(
            "SPY261030P00520000", bid="2.3", ask="2.2", delta="-0.50"
        ),
    }
    respx.get(CHAIN_URL).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={"snapshots": {symbol: value for symbol, value in snapshots.items() if
                (request.url.params["type"] == "call" and "C" in symbol) or
                (request.url.params["type"] == "put" and "P" in symbol)}},
        )
    )
    respx.get(CONTRACTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "option_contracts": [
                    contract("SPY261030C00500000", "call", "500"),
                    contract("SPY261030P00500000", "put", "500"),
                    contract("SPY261030C00510000", "call", "510"),
                    contract("SPY261030P00510000", "put", "510"),
                    contract("SPY261030C00520000", "call", "520"),
                    contract("SPY261030P00520000", "put", "520"),
                ]
            },
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    frame = await gateway.observe(
        "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
    )

    assert [quote.symbol for quote in frame.option_quotes] == ["SPY261030C00500000"]
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_chain_http_failure_is_fail_closed() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(200, json=stock_payload())
    )
    respx.get(CHAIN_URL).mock(return_value=httpx.Response(503, json={"message": "unavailable"}))
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    with pytest.raises(httpx.HTTPStatusError):
        await gateway.observe(
            "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
        )

    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_malformed_snapshot_records_rejection_and_capability_limitation() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(200, json=stock_payload())
    )
    respx.get(CHAIN_URL).mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "snapshots": {
                    "SPY261030C00500000": {
                        "latestQuote": {"bp": "2.00", "ap": "2.20", "t": NOW}
                    }
                }
            },
        )
    )
    respx.get(CONTRACTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"option_contracts": [contract("SPY261030C00500000", "call", "500")]},
        )
    )
    gateway = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")

    frame = await gateway.observe(
        "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
    )

    assert frame.option_quotes == ()
    assert frame.quote_rejections == ("missing_required_analytics",)
    assert frame.market_data.options_feed is OptionsFeed.INDICATIVE
    assert "missing_required_analytics" in frame.market_data.limitations
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_feed_binding_changes_snapshot_hash_for_same_payload() -> None:
    respx.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot").mock(
        return_value=httpx.Response(200, json=stock_payload())
    )
    respx.get(CHAIN_URL).mock(
        return_value=httpx.Response(200, json={"snapshots": {}})
    )
    indicative = AlpacaPaperMarketGateway(api_key_id="key", api_secret_key="secret")
    opra = AlpacaPaperMarketGateway(
        api_key_id="key", api_secret_key="secret", option_data_feed=OptionsFeed.OPRA
    )

    indicative_frame = await indicative.observe(
        "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
    )
    opra_frame = await opra.observe(
        "SPY", expiration_gte=date(2026, 9, 11), expiration_lte=date(2026, 10, 30)
    )

    assert indicative_frame.snapshot_hash() != opra_frame.snapshot_hash()
    await indicative.aclose()
    await opra.aclose()
