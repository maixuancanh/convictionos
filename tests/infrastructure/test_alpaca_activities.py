from datetime import UTC, datetime

import httpx
import pytest
import respx

from convictionos.infrastructure.alpaca_activities import (
    AlpacaActivityGateway,
    BrokerActivityType,
)


@pytest.mark.asyncio
@respx.mock
async def test_activity_gateway_sanitizes_and_preserves_next_page_token() -> None:
    route = respx.get(
        "https://paper-api.alpaca.markets/v2/account/activities/OPTRD"
    ).mock(
        return_value=httpx.Response(
            200,
            headers={"Next-Page-Token": "page-2"},
            json=[
                {
                    "id": "activity-1",
                    "activity_type": "OPTRD",
                    "transaction_time": "2026-09-04T12:00:00Z",
                    "date": "2026-09-04",
                    "symbol": "SPY260918C00500000",
                    "qty": "1",
                    "price": "2.50",
                    "net_amount": "250.00",
                    "secret": "must-not-leak",
                }
            ],
        )
    )
    gateway = AlpacaActivityGateway(api_key_id="key", api_secret_key="secret")

    activities, token = await gateway.list_activities(
        BrokerActivityType.OPTRD,
        after=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
        page_token="page-1",
    )

    assert token == "page-2"
    assert activities[0].activity_id == "activity-1"
    assert activities[0].price == 2.5
    assert "secret" not in activities[0].model_dump_json()
    assert route.calls[0].request.headers["APCA-API-KEY-ID"] == "key"
    assert route.calls[0].request.url.params["page_token"] == "page-1"
    await gateway.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_activity_gateway_rejects_malformed_payload_and_missing_credentials() -> None:
    respx.get(
        "https://paper-api.alpaca.markets/v2/account/activities/FEE"
    ).mock(return_value=httpx.Response(200, json={"activities": []}))
    with pytest.raises(ValueError, match="credentials"):
        AlpacaActivityGateway()

    gateway = AlpacaActivityGateway(api_key_id="key", api_secret_key="secret")
    with pytest.raises(ValueError, match="malformed"):
        await gateway.list_activities(BrokerActivityType.FEE)
    await gateway.aclose()
