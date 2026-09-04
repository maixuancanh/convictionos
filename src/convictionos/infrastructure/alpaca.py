from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from convictionos.infrastructure.brokers import (
    BrokerLookupUnavailable,
    BrokerOrder,
    BrokerOrderStatus,
    BrokerReplacementPending,
    UnknownSubmission,
)

STATUS_MAP = {
    "new": BrokerOrderStatus.NEW,
    "accepted": BrokerOrderStatus.NEW,
    "pending_new": BrokerOrderStatus.NEW,
    "accepted_for_bidding": BrokerOrderStatus.NEW,
    "pending_cancel": BrokerOrderStatus.NEW,
    "pending_replace": BrokerOrderStatus.NEW,
    "partially_filled": BrokerOrderStatus.PARTIALLY_FILLED,
    "filled": BrokerOrderStatus.FILLED,
    "canceled": BrokerOrderStatus.CANCELED,
    "expired": BrokerOrderStatus.CANCELED,
    "done_for_day": BrokerOrderStatus.CANCELED,
    "rejected": BrokerOrderStatus.REJECTED,
}


class AlpacaRestOrderReader:
    """Read-only Alpaca paper order adapter.

    An injected client must target the paper endpoint and carry its own authentication.
    It remains caller-owned and is never closed by this adapter.
    """

    def __init__(
        self,
        *,
        base_url: str,
        oauth_token: str = "",
        api_key_id: str = "",
        api_secret_key: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if normalized_base_url != "https://paper-api.alpaca.markets":
            raise ValueError("Alpaca paper base_url must be https://paper-api.alpaca.markets")
        if client is not None:
            if oauth_token or api_key_id or api_secret_key:
                raise ValueError("injected client must own Alpaca paper authentication")
            if str(client.base_url).rstrip("/") != normalized_base_url:
                raise ValueError("injected client must target the Alpaca paper base_url")
            self._client = client
            self._owns_client = False
            return
        if not oauth_token and not (api_key_id and api_secret_key):
            raise ValueError("Alpaca paper credentials are required")
        headers = (
            {"Authorization": f"Bearer {oauth_token}"}
            if oauth_token
            else {
                "APCA-API-KEY-ID": api_key_id,
                "APCA-API-SECRET-KEY": api_secret_key,
            }
        )
        self._client = httpx.AsyncClient(
            base_url=normalized_base_url, headers=headers, timeout=10.0
        )
        self._owns_client = True

    async def get_by_client_order_id(self, client_order_id: str) -> BrokerOrder | None:
        try:
            response = await self._client.get(
                "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id}
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise BrokerLookupUnavailable(client_order_id) from error
        if response.status_code == 404:
            return None
        if not response.is_success:
            raise BrokerLookupUnavailable(client_order_id)
        return self._map_order(response.json())

    @staticmethod
    def _map_order(payload: dict[str, Any]) -> BrokerOrder:
        alpaca_status = str(payload["status"])
        if alpaca_status == "replaced":
            raise BrokerReplacementPending("Alpaca order replacement requires reconciliation")
        status = STATUS_MAP.get(alpaca_status)
        if status is None:
            raise UnknownSubmission(f"unsupported Alpaca order status: {alpaca_status}")
        try:
            fill_facts = (
                _optional_decimal(payload.get("filled_avg_price")),
                _optional_decimal(payload.get("filled_qty")),
                _optional_timestamp(payload.get("submitted_at")),
                _optional_timestamp(payload.get("filled_at")),
            )
        except (InvalidOperation, TypeError, ValueError):
            fill_facts = (None, None, None, None)
        return BrokerOrder(
            order_id=str(payload["id"]),
            client_order_id=str(payload["client_order_id"]),
            status=status,
            filled_avg_price=fill_facts[0],
            filled_qty=fill_facts[1],
            submitted_at=fill_facts[2],
            filled_at=fill_facts[3],
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


AlpacaPaperBroker = AlpacaRestOrderReader


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("invalid optional decimal")
    return parsed


def _optional_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("optional timestamp must be timezone-aware")
    return parsed
