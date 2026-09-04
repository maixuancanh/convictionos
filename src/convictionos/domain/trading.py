from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex


class Horizon(StrEnum):
    CATALYST = "catalyst"
    SWING = "swing"
    THEMATIC = "thematic"


class InstrumentKind(StrEnum):
    EQUITY = "equity"
    US_OPTION = "us_option"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PositionIntent(StrEnum):
    BUY_TO_OPEN = "buy_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_OPEN = "sell_to_open"
    SELL_TO_CLOSE = "sell_to_close"


class OrderType(StrEnum):
    LIMIT = "limit"


class IntentState(StrEnum):
    VALIDATED = "validated"
    POLICY_DENIED = "policy_denied"
    PENDING_APPROVAL = "pending_approval"
    AUTHORIZED = "authorized"
    SUBMITTING = "submitting"
    ACK_UNKNOWN = "ack_unknown"
    REPLACEMENT_PENDING = "replacement_pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELED = "canceled"
    RECONCILED = "reconciled"


class TradeLeg(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    kind: InstrumentKind
    side: Side
    position_intent: PositionIntent
    quantity: Decimal = Field(gt=0)
    ratio_quantity: int = Field(gt=0)


class TradeIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    mandate_id: str = Field(min_length=1)
    mandate_version: int = Field(gt=0)
    created_at: datetime
    expires_at: datetime
    horizon: Horizon
    underlying: str = Field(min_length=1)
    thesis_ref: str = Field(min_length=1)
    legs: tuple[TradeLeg, ...] = Field(min_length=1)
    order_type: OrderType
    limit_price: Decimal = Field(gt=0)
    max_loss: Decimal = Field(gt=0)
    exit_plan: str = Field(min_length=1)
    data_snapshot_hash: str = Field(min_length=1)
    position_id: str | None = Field(default=None, min_length=1)
    exit_decision_hash: str | None = Field(default=None, min_length=1)
    position_state_hash: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_times_and_quantities(self) -> "TradeIntent":
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        if len({leg.quantity for leg in self.legs}) != 1:
            raise ValueError("all legs in the first slice must share quantity")
        return self

    def operation_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="python")
        optional_fields = {"position_id", "exit_decision_hash", "position_state_hash"}
        return {
            key: value
            for key, value in payload.items()
            if key not in optional_fields or value is not None
        }

    def operation_hash(self) -> str:
        return sha256_hex(self.operation_payload())
