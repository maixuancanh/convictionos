from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from convictionos.domain.market_data import (
    MarketDataCapability,
    MarketDataLimitation,
)

OPTION_MULTIPLIER = Decimal("100")
_CENTS = Decimal("0.01")


class AccountingState(StrEnum):
    OPEN_FILL_ONLY = "open_fill_only"


class PaperExecutionAccounting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal[AccountingState.OPEN_FILL_ONLY] = AccountingState.OPEN_FILL_ONLY
    capability: MarketDataCapability
    authorized_limit_price: Decimal = Field(gt=0)
    spread_units: int = Field(gt=0)
    authorized_cost_ceiling: Decimal = Field(gt=0)
    broker_reported_fill_price: Decimal = Field(ge=0)
    broker_reported_filled_qty: Decimal = Field(gt=0)
    broker_reported_cost_basis: Decimal = Field(ge=0)
    conservative_realized_pnl: None = None
    performance_eligible: Literal[False] = False
    limitations: tuple[MarketDataLimitation, ...]
    version: Literal["paper-accounting-v1"] = "paper-accounting-v1"

    @classmethod
    def from_open_fill(
        cls,
        *,
        capability: MarketDataCapability,
        authorized_limit_price: Decimal,
        spread_units: int,
        broker_reported_fill_price: Decimal,
        broker_reported_filled_qty: Decimal,
    ) -> "PaperExecutionAccounting":
        authorized_cost_ceiling = (
            authorized_limit_price * OPTION_MULTIPLIER * spread_units
        ).quantize(_CENTS)
        broker_reported_cost_basis = (
            broker_reported_fill_price * OPTION_MULTIPLIER * broker_reported_filled_qty
        ).quantize(_CENTS)
        return cls(
            capability=capability,
            authorized_limit_price=authorized_limit_price,
            spread_units=spread_units,
            authorized_cost_ceiling=authorized_cost_ceiling,
            broker_reported_fill_price=broker_reported_fill_price,
            broker_reported_filled_qty=broker_reported_filled_qty,
            broker_reported_cost_basis=broker_reported_cost_basis,
            limitations=capability.limitations,
        )

    @classmethod
    def try_from_open_fill(
        cls,
        *,
        capability: MarketDataCapability,
        authorized_limit_price: Decimal,
        spread_units: int,
        broker_reported_fill_price: Decimal | None,
        broker_reported_filled_qty: Decimal | None,
    ) -> "PaperExecutionAccounting | None":
        if broker_reported_fill_price is None or broker_reported_filled_qty is None:
            return None
        if (
            not broker_reported_fill_price.is_finite()
            or not broker_reported_filled_qty.is_finite()
            or broker_reported_fill_price < 0
            or broker_reported_filled_qty <= 0
            or broker_reported_filled_qty > Decimal(spread_units)
            or broker_reported_fill_price > authorized_limit_price
        ):
            return None
        return cls.from_open_fill(
            capability=capability,
            authorized_limit_price=authorized_limit_price,
            spread_units=spread_units,
            broker_reported_fill_price=broker_reported_fill_price,
            broker_reported_filled_qty=broker_reported_filled_qty,
        )
