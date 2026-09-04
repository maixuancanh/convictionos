from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from convictionos.domain.market_data import MarketDataCapability
from convictionos.domain.trading import TradeIntent


class MarketDataPolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    reasons: tuple[str, ...] = ()


def evaluate_market_data_policy(
    capability: MarketDataCapability, intent: TradeIntent
) -> MarketDataPolicyDecision:
    if capability.execution_tier.value == "research_only":
        return MarketDataPolicyDecision(
            allowed=False, reasons=("market_data_research_only",)
        )
    quantity = intent.legs[0].quantity
    if quantity != quantity.to_integral_value():
        return MarketDataPolicyDecision(
            allowed=False, reasons=("market_data_invalid_spread_unit_quantity",)
        )
    if capability.max_spread_units is not None and quantity > Decimal(
        capability.max_spread_units
    ):
        return MarketDataPolicyDecision(
            allowed=False, reasons=("market_data_spread_unit_ceiling_exceeded",)
        )
    return MarketDataPolicyDecision(allowed=True)
