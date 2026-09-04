from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from convictionos.infrastructure.alpaca import AlpacaPaperBroker
from convictionos.infrastructure.brokers import (
    BrokerLookupUnavailable,
    BrokerOrderStatus,
    BrokerReplacementPending,
    UnknownSubmission,
)


def test_rejects_live_alpaca_base_url() -> None:
    with pytest.raises(ValueError, match="paper"):
        AlpacaPaperBroker(base_url="https://api.alpaca.markets", oauth_token="token")


def test_rest_order_reader_has_no_submit_mutation_method() -> None:
    broker = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets", oauth_token="token")

    assert not hasattr(broker, "submit")


@pytest.mark.asyncio
@respx.mock
async def test_queries_unknown_submission_by_client_order_id() -> None:
    respx.get("https://paper-api.alpaca.markets/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "alpaca-order-1",
                "client_order_id": "intent-001-v1",
                "status": "filled",
            },
        )
    )
    broker = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets", oauth_token="token")
    order = await broker.get_by_client_order_id("intent-001-v1")
    assert order is not None
    assert order.status is BrokerOrderStatus.FILLED


@pytest.mark.asyncio
@respx.mock
async def test_returns_none_only_for_a_not_found_lookup() -> None:
    respx.get("https://paper-api.alpaca.markets/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(404)
    )
    broker = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets", oauth_token="token")

    assert await broker.get_by_client_order_id("intent-001-v1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
@respx.mock
async def test_uncertain_lookup_http_statuses_are_not_treated_as_missing(
    status_code: int,
) -> None:
    respx.get("https://paper-api.alpaca.markets/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(status_code, json={"message": "try again"})
    )
    broker = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets", oauth_token="token")

    with pytest.raises(BrokerLookupUnavailable, match="intent-001-v1"):
        await broker.get_by_client_order_id("intent-001-v1")


@pytest.mark.asyncio
@respx.mock
async def test_lookup_transport_failure_is_not_treated_as_missing() -> None:
    respx.get("https://paper-api.alpaca.markets/v2/orders:by_client_order_id").mock(
        side_effect=httpx.ReadTimeout("lookup timed out")
    )
    broker = AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets", oauth_token="token")

    with pytest.raises(BrokerLookupUnavailable, match="intent-001-v1"):
        await broker.get_by_client_order_id("intent-001-v1")


@pytest.mark.parametrize(
    ("alpaca_status", "expected_status"),
    [
        ("pending_new", BrokerOrderStatus.NEW),
        ("accepted_for_bidding", BrokerOrderStatus.NEW),
        ("pending_cancel", BrokerOrderStatus.NEW),
        ("pending_replace", BrokerOrderStatus.NEW),
        ("done_for_day", BrokerOrderStatus.CANCELED),
    ],
)
def test_maps_safe_in_flight_and_terminal_alpaca_statuses(
    alpaca_status: str, expected_status: BrokerOrderStatus
) -> None:
    order = AlpacaPaperBroker._map_order(
        {
            "id": "alpaca-order-1",
            "client_order_id": "intent-001-v1",
            "status": alpaca_status,
        }
    )

    assert order.status is expected_status


def test_maps_filled_order_facts() -> None:
    order = AlpacaPaperBroker._map_order(
        {
            "id": "alpaca-order-1",
            "client_order_id": "intent-001-v1",
            "status": "filled",
            "filled_avg_price": "1.25",
            "filled_qty": "2",
            "submitted_at": "2026-08-30T15:00:00Z",
            "filled_at": "2026-08-30T15:01:00+00:00",
        }
    )

    assert order.filled_avg_price == Decimal("1.25")
    assert order.filled_qty == Decimal("2")
    assert order.submitted_at == datetime(2026, 8, 30, 15, 0, tzinfo=UTC)
    assert order.filled_at == datetime(2026, 8, 30, 15, 1, tzinfo=UTC)


def test_malformed_order_facts_are_cleared_atomically() -> None:
    order = AlpacaPaperBroker._map_order(
        {
            "id": "alpaca-order-1",
            "client_order_id": "intent-001-v1",
            "status": "filled",
            "filled_avg_price": "not-a-price",
            "filled_qty": "2",
            "submitted_at": "2026-08-30T15:00:00Z",
            "filled_at": "2026-08-30T15:01:00Z",
            "private": "do-not-leak",
        }
    )

    assert order.status is BrokerOrderStatus.FILLED
    assert order.order_id == "alpaca-order-1"
    assert order.client_order_id == "intent-001-v1"
    assert order.filled_avg_price is None
    assert order.filled_qty is None
    assert order.submitted_at is None
    assert order.filled_at is None


def test_replaced_alpaca_status_requires_explicit_reconciliation() -> None:
    with pytest.raises(BrokerReplacementPending, match="replacement"):
        AlpacaPaperBroker._map_order(
            {
                "id": "alpaca-order-1",
                "client_order_id": "intent-001-v1",
                "status": "replaced",
            }
        )


def test_unsupported_alpaca_status_is_an_unknown_submission() -> None:
    with pytest.raises(UnknownSubmission, match="unsupported Alpaca order status"):
        AlpacaPaperBroker._map_order(
            {
                "id": "alpaca-order-1",
                "client_order_id": "intent-001-v1",
                "status": "mystery_status",
            }
        )


@pytest.mark.asyncio
async def test_aclose_closes_only_an_internally_owned_client() -> None:
    owned_broker = AlpacaPaperBroker(
        base_url="https://paper-api.alpaca.markets", oauth_token="token"
    )
    await owned_broker.aclose()
    assert owned_broker._client.is_closed

    injected_client = httpx.AsyncClient(base_url="https://paper-api.alpaca.markets")
    try:
        injected_broker = AlpacaPaperBroker(
            base_url="https://paper-api.alpaca.markets", client=injected_client
        )
        await injected_broker.aclose()
        assert not injected_client.is_closed
    finally:
        await injected_client.aclose()


@pytest.mark.asyncio
async def test_injected_client_must_own_authentication() -> None:
    injected_client = httpx.AsyncClient(base_url="https://paper-api.alpaca.markets")
    try:
        with pytest.raises(ValueError, match="injected client"):
            AlpacaPaperBroker(
                base_url="https://paper-api.alpaca.markets",
                oauth_token="token",
                client=injected_client,
            )
    finally:
        await injected_client.aclose()


@pytest.mark.asyncio
async def test_rejects_an_injected_live_client() -> None:
    injected_client = httpx.AsyncClient(base_url="https://api.alpaca.markets")
    try:
        with pytest.raises(ValueError, match="paper"):
            AlpacaPaperBroker(base_url="https://paper-api.alpaca.markets", client=injected_client)
    finally:
        await injected_client.aclose()
