from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from convictionos.application.agent import AgentMarketFrame
from convictionos.domain.market_data import (
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)
from convictionos.infrastructure.alpaca_evidence import AlpacaEvidenceGateway


@pytest.mark.asyncio
@respx.mock
async def test_collects_canonical_point_in_time_news() -> None:
    route = respx.get("https://data.alpaca.markets/v1beta1/news").mock(
        return_value=httpx.Response(
            200,
            json={
                "news": [
                    {
                        "id": 42,
                        "headline": "Macro catalyst reprices risk assets",
                        "summary": "A timestamped market catalyst.",
                        "created_at": "2026-08-30T14:30:00Z",
                        "updated_at": "2026-08-30T14:35:00Z",
                        "symbols": ["SPY"],
                        "url": "https://example.test/news/42",
                    }
                ],
                "next_page_token": None,
            },
        )
    )
    gateway = AlpacaEvidenceGateway(api_key_id="key", api_secret_key="secret")
    items = await gateway.news(
        "SPY", observed_at=datetime(2026, 8, 30, 15, 0, tzinfo=UTC), lookback_hours=48
    )
    assert items[0].source_id == "alpaca-news:42"
    assert route.calls[0].request.url.params["symbols"] == "SPY"
    assert route.calls[0].request.url.params["include_content"] == "false"
    assert route.calls[0].request.headers["APCA-API-KEY-ID"] == "key"
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_discards_news_published_after_observation() -> None:
    respx.get("https://data.alpaca.markets/v1beta1/news").mock(
        return_value=httpx.Response(
            200,
            json={
                "news": [
                    {
                        "id": 99,
                        "headline": "Future item",
                        "summary": "Must be excluded.",
                        "created_at": "2026-08-30T15:01:00Z",
                        "updated_at": "2026-08-30T15:01:00Z",
                        "symbols": ["SPY"],
                        "url": "https://example.test/news/99",
                    }
                ]
            },
        )
    )
    gateway = AlpacaEvidenceGateway(api_key_id="key", api_secret_key="secret")
    items = await gateway.news(
        "SPY", observed_at=datetime(2026, 8, 30, 15, 0, tzinfo=UTC), lookback_hours=48
    )
    assert items == ()
    await gateway.aclose()


def test_build_bundle_always_includes_market_evidence() -> None:
    observed_at = datetime(2026, 8, 30, 15, 0, tzinfo=UTC)
    frame = AgentMarketFrame(
        underlying="SPY",
        price=Decimal("505"),
        previous_close=Decimal("500"),
        observed_at=observed_at,
        source="alpaca_market",
        market_data=build_market_data_capability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.INDICATIVE,
            assessed_at=observed_at,
        ),
        calls=(),
    )
    bundle = AlpacaEvidenceGateway.build_bundle(frame, ())
    assert bundle.items[0].source_id == "alpaca-market:SPY:2026-08-30T15:00:00+00:00"
    assert bundle.items[0].source_type.value == "market"
    assert bundle.price == frame.price
