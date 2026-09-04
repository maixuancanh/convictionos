from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from convictionos.application.eligibility import (
    AccountEligibilitySnapshot,
    CliEligibilitySnapshot,
    McpEligibilitySnapshot,
)
from convictionos.domain.market_data import (
    MarketDataCapability,
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)


class AlpacaPaperAccountEligibilityPort:
    """Inspect the configured Alpaca paper account without exposing private IDs."""

    def __init__(
        self,
        *,
        paper_base_url: str,
        oauth_token: str = "",
        api_key_id: str = "",
        api_secret_key: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_base_url = paper_base_url.rstrip("/")
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

    async def inspect(self) -> AccountEligibilitySnapshot:
        account_response = await self._client.get("/v2/account")
        account_response.raise_for_status()
        positions_response = await self._client.get("/v2/positions")
        positions_response.raise_for_status()
        orders_response = await self._client.get(
            "/v2/orders", params={"status": "open", "limit": 500}
        )
        orders_response.raise_for_status()
        account = account_response.json()
        positions = _json_list(positions_response.json(), "Alpaca positions response")
        orders = _json_list(orders_response.json(), "Alpaca orders response")
        return AccountEligibilitySnapshot(
            account_id=str(account["id"]),
            environment="paper",
            status=str(account["status"]),
            equity=Decimal(str(account["equity"])),
            cash=Decimal(str(account["cash"])),
            open_position_count=len(positions),
            open_order_count=len(orders),
            options_level=max(
                int(account.get("options_trading_level") or 0),
                int(account.get("options_approved_level") or 0),
            ),
            trading_api_read_ok=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


@dataclass(frozen=True)
class StaticCliEligibilityPort:
    version: str
    revision: str
    digest: str

    async def inspect(self) -> CliEligibilitySnapshot:
        return CliEligibilitySnapshot(
            version=self.version,
            revision=self.revision,
            digest=self.digest,
        )


@dataclass(frozen=True)
class StaticMcpEligibilityPort:
    version: str
    schema_hash: str

    async def inspect(self) -> McpEligibilitySnapshot:
        return McpEligibilitySnapshot(
            version=self.version,
            schema_hash=self.schema_hash,
        )


@dataclass(frozen=True)
class StaticMarketDataEligibilityPort:
    options_feed: OptionsFeed
    underlying_feed: UnderlyingFeed = UnderlyingFeed.IEX

    async def capability(self, now: datetime) -> MarketDataCapability:
        return build_market_data_capability(
            self.underlying_feed,
            self.options_feed,
            now,
        )


def _json_list(payload: Any, label: str) -> list[Any]:
    if not isinstance(payload, list):
        raise ValueError(f"{label} is malformed")
    return payload
