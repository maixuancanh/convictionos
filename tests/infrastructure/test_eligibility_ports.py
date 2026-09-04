from decimal import Decimal

import httpx

from convictionos.infrastructure.eligibility_ports import (
    AlpacaPaperAccountEligibilityPort,
)


async def test_alpaca_paper_account_eligibility_port_reads_account_safely() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        if request.url.path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "id": "paper-account-secret",
                    "status": "ACTIVE",
                    "equity": "100000.00",
                    "cash": "100000.00",
                    "options_trading_level": 3,
                },
            )
        if request.url.path == "/v2/positions":
            return httpx.Response(200, json=[])
        if request.url.path == "/v2/orders":
            assert request.url.params["status"] == "open"
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx.MockTransport(handler),
        headers={
            "APCA-API-KEY-ID": "paper-key",
            "APCA-API-SECRET-KEY": "paper-secret",
        },
    )
    port = AlpacaPaperAccountEligibilityPort(
        paper_base_url="https://paper-api.alpaca.markets",
        client=client,
    )

    snapshot = await port.inspect()

    assert snapshot.account_id == "paper-account-secret"
    assert snapshot.environment == "paper"
    assert snapshot.status == "ACTIVE"
    assert snapshot.equity == Decimal("100000.00")
    assert snapshot.cash == Decimal("100000.00")
    assert snapshot.open_position_count == 0
    assert snapshot.open_order_count == 0
    assert snapshot.options_level == 3
    assert snapshot.trading_api_read_ok is True
    assert all(header["APCA-API-KEY-ID"] == "paper-key" for header in seen_headers)
    await client.aclose()
