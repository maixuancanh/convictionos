from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from convictionos.domain.trading import TradeIntent


class BrokerOrderStatus(StrEnum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELED = "canceled"


class BrokerOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    client_order_id: str
    status: BrokerOrderStatus
    filled_avg_price: Decimal | None = Field(default=None, ge=0)
    filled_qty: Decimal | None = Field(default=None, ge=0)
    submitted_at: datetime | None = None
    filled_at: datetime | None = None


class UnknownSubmission(RuntimeError):
    pass


class BrokerReplacementPending(UnknownSubmission):
    pass


class BrokerLookupUnavailable(UnknownSubmission):
    pass


class BrokerRejected(RuntimeError):
    pass


class BrokerOrderSubmitter(Protocol):
    async def submit(self, intent: TradeIntent) -> BrokerOrder: ...


class BrokerOrderReader(Protocol):
    async def get_by_client_order_id(self, client_order_id: str) -> BrokerOrder | None: ...


class BrokerPort(BrokerOrderSubmitter, BrokerOrderReader, Protocol):
    pass


class CompositeBroker:
    def __init__(self, *, submitter: BrokerOrderSubmitter, reader: BrokerOrderReader) -> None:
        self._submitter = submitter
        self._reader = reader

    async def submit(self, intent: TradeIntent) -> BrokerOrder:
        return await self._submitter.submit(intent)

    async def get_by_client_order_id(self, client_order_id: str) -> BrokerOrder | None:
        return await self._reader.get_by_client_order_id(client_order_id)


class FakeBroker:
    def __init__(self, *, raise_after_accept: bool = False, reject: bool = False) -> None:
        self._orders: dict[str, BrokerOrder] = {}
        self._raise_after_accept = raise_after_accept
        self._reject = reject
        self.submit_calls = 0

    async def submit(self, intent: TradeIntent) -> BrokerOrder:
        existing = self._orders.get(intent.idempotency_key)
        if existing is not None:
            return existing
        self.submit_calls += 1
        if self._reject:
            raise BrokerRejected("synthetic broker rejection")
        order = BrokerOrder(
            order_id=f"fake-{intent.intent_id}",
            client_order_id=intent.idempotency_key,
            status=BrokerOrderStatus.FILLED,
        )
        self._orders[intent.idempotency_key] = order
        if self._raise_after_accept:
            self._raise_after_accept = False
            raise UnknownSubmission(intent.idempotency_key)
        return order

    async def get_by_client_order_id(self, client_order_id: str) -> BrokerOrder | None:
        return self._orders.get(client_order_id)
