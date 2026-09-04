from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex


class BrokerActivityType(StrEnum):
    OPASN = "OPASN"
    OPEXP = "OPEXP"
    OPXRC = "OPXRC"
    OPTRD = "OPTRD"
    FEE = "FEE"


class SanitizedBrokerActivity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_id: str = Field(min_length=1)
    activity_type: BrokerActivityType
    event_time: datetime
    event_date: str | None = None
    symbol: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    net_amount: Decimal | None = None
    subtype: str | None = None
    content_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_content(self) -> "SanitizedBrokerActivity":
        if self.event_time.tzinfo is None or self.event_time.utcoffset() is None:
            raise ValueError("event_time must be timezone-aware")
        for name in ("quantity", "price", "net_amount"):
            value = getattr(self, name)
            if value is not None and not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.content_hash != self.computed_hash():
            raise ValueError("activity hash does not match sanitized payload")
        return self

    def computed_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="python", exclude={"content_hash"}))


class AlpacaActivityGateway:
    def __init__(
        self,
        *,
        api_key_id: str = "",
        api_secret_key: str = "",
        oauth_token: str = "",
        paper_base_url: str = "https://paper-api.alpaca.markets",
    ) -> None:
        if paper_base_url.rstrip("/") != "https://paper-api.alpaca.markets":
            raise ValueError("Alpaca activity gateway requires paper endpoint")
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
            base_url=paper_base_url.rstrip("/"), headers=headers, timeout=10.0
        )

    async def list_activities(
        self,
        activity_type: BrokerActivityType,
        *,
        after: datetime | None = None,
        until: datetime | None = None,
        page_token: str | None = None,
    ) -> tuple[tuple[SanitizedBrokerActivity, ...], str | None]:
        params: dict[str, str] = {"direction": "asc"}
        if after is not None:
            params["after"] = _iso(after)
        if until is not None:
            params["until"] = _iso(until)
        if page_token is not None:
            params["page_token"] = page_token
        response = await self._client.get(
            f"/v2/account/activities/{activity_type.value}", params=params
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Alpaca activity response is malformed")
        activities = tuple(self._sanitize(item, activity_type) for item in payload)
        return activities, response.headers.get("Next-Page-Token")

    @staticmethod
    def _sanitize(item: Any, activity_type: BrokerActivityType) -> SanitizedBrokerActivity:
        if not isinstance(item, dict):
            raise ValueError("Alpaca activity item is malformed")
        event_time = _parse_datetime(
            str(item.get("transaction_time") or item.get("date"))
        )
        values: dict[str, Any] = {
            "activity_id": str(item["id"]),
            "activity_type": activity_type,
            "event_time": event_time,
            "event_date": str(item["date"]) if item.get("date") is not None else None,
            "symbol": str(item["symbol"]) if item.get("symbol") is not None else None,
            "quantity": _decimal(item.get("qty")),
            "price": _decimal(item.get("price")),
            "net_amount": _decimal(item.get("net_amount")),
            "subtype": str(item["activity_type"]) if item.get("activity_type") else None,
        }
        provisional = SanitizedBrokerActivity.model_construct(content_hash="provisional", **values)
        return SanitizedBrokerActivity(content_hash=provisional.computed_hash(), **values)

    async def aclose(self) -> None:
        await self._client.aclose()


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("activity decimal must be finite")
    return result


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("activity date must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _parse_datetime(value.isoformat()).isoformat().replace("+00:00", "Z")
