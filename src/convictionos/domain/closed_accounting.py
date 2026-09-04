from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.market_data import MarketDataCapability, MarketDataLimitation
from convictionos.domain.paper_accounting import OPTION_MULTIPLIER, PaperExecutionAccounting


class ClosedPositionAccounting(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    broker_reported_gross_pnl: Decimal
    conservative_gross_pnl: Decimal
    verified_fees: Decimal | None = Field(default=None, ge=0)
    verified_after_cost_pnl: Decimal | None = None
    basis_provenance: Literal["broker_fill_and_authorized_floor"] = (
        "broker_fill_and_authorized_floor"
    )
    capability: MarketDataCapability
    limitations: tuple[MarketDataLimitation, ...] = ()
    performance_eligible: Literal[False] = False
    version: Literal["closed-paper-accounting-v1"] = "closed-paper-accounting-v1"

    @model_validator(mode="after")
    def validate_values(self) -> "ClosedPositionAccounting":
        for name in (
            "broker_reported_gross_pnl",
            "conservative_gross_pnl",
        ):
            if not getattr(self, name).is_finite():
                raise ValueError(f"{name} must be finite")
        if self.verified_after_cost_pnl is not None and self.verified_fees is None:
            raise ValueError("after-cost P&L requires verified fees")
        if self.verified_fees is not None and not self.verified_fees.is_finite():
            raise ValueError("verified fees must be finite")
        if (
            self.verified_after_cost_pnl is not None
            and not self.verified_after_cost_pnl.is_finite()
        ):
            raise ValueError("after-cost P&L must be finite")
        if self.limitations != self.capability.limitations:
            raise ValueError("closed accounting limitations must match capability")
        return self


def close_position_accounting(
    *,
    opening: PaperExecutionAccounting | None,
    close_fill_credit: Decimal,
    close_filled_units: Decimal,
    authorized_close_credit_floor: Decimal,
    capability: MarketDataCapability,
    verified_fees: Decimal | None = None,
) -> ClosedPositionAccounting:
    if opening is None:
        raise ValueError("opening basis is required")
    values = (close_fill_credit, close_filled_units, authorized_close_credit_floor)
    if any(not value.is_finite() for value in values):
        raise ValueError("close accounting inputs must be finite")
    if close_fill_credit < 0 or authorized_close_credit_floor < 0:
        raise ValueError("close credits cannot be negative")
    if close_filled_units <= 0 or close_filled_units != opening.broker_reported_filled_qty:
        raise ValueError("close quantity must completely match opening quantity")
    if close_fill_credit < authorized_close_credit_floor:
        raise ValueError("close fill is below authorized credit floor")
    if opening.capability != capability:
        raise ValueError("opening and closing capabilities must match")
    if verified_fees is not None and (
        not verified_fees.is_finite() or verified_fees < 0
    ):
        raise ValueError("verified fees must be finite and non-negative")

    multiplier = OPTION_MULTIPLIER * close_filled_units
    broker_gross = (
        close_fill_credit - opening.broker_reported_fill_price
    ) * multiplier
    conservative_gross = (
        authorized_close_credit_floor - opening.authorized_limit_price
    ) * multiplier
    after_cost = broker_gross - verified_fees if verified_fees is not None else None
    return ClosedPositionAccounting(
        broker_reported_gross_pnl=broker_gross.quantize(Decimal("0.01")),
        conservative_gross_pnl=conservative_gross.quantize(Decimal("0.01")),
        verified_fees=verified_fees,
        verified_after_cost_pnl=(
            after_cost.quantize(Decimal("0.01")) if after_cost is not None else None
        ),
        capability=capability,
        limitations=capability.limitations,
    )
