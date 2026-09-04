from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UnderlyingFeed(StrEnum):
    IEX = "iex"
    SIP = "sip"


class OptionsFeed(StrEnum):
    INDICATIVE = "indicative"
    OPRA = "opra"
    UNKNOWN = "unknown"


class ExecutionTier(StrEnum):
    RESEARCH_ONLY = "research_only"
    PAPER_EXECUTABLE = "paper_executable"


class MarketDataLimitation(StrEnum):
    INDICATIVE_OPTIONS_FEED = "indicative_options_feed"
    PAPER_SIMULATION_NOT_LIVE_EQUIVALENT = "paper_simulation_not_live_equivalent"
    UNSUPPORTED_OPTIONS_FEED = "unsupported_options_feed"
    MISSING_REQUIRED_ANALYTICS = "missing_required_analytics"


class MarketDataCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying_feed: UnderlyingFeed
    options_feed: OptionsFeed
    execution_tier: ExecutionTier
    max_spread_units: int | None = Field(default=None, ge=0)
    performance_eligible: Literal[False] = False
    limitations: tuple[MarketDataLimitation, ...]
    assessed_at: datetime
    version: Literal["market-data-capability-v1"] = "market-data-capability-v1"

    @model_validator(mode="after")
    def validate_invariants(self) -> "MarketDataCapability":
        if self.assessed_at.tzinfo is None or self.assessed_at.utcoffset() is None:
            raise ValueError("assessed_at must be timezone-aware")

        expected_tier, expected_max, expected_limitations = _expected_values(self.options_feed)
        if self.execution_tier is not expected_tier or self.max_spread_units != expected_max:
            raise ValueError("execution tier and max spread must match options feed")

        allowed_limitations = (
            expected_limitations,
            expected_limitations + (MarketDataLimitation.MISSING_REQUIRED_ANALYTICS,),
        )
        if self.limitations not in allowed_limitations:
            raise ValueError("limitations must match the options feed capabilities")
        return self


def _expected_values(
    options_feed: OptionsFeed,
) -> tuple[ExecutionTier, int | None, tuple[MarketDataLimitation, ...]]:
    if options_feed is OptionsFeed.INDICATIVE:
        return (
            ExecutionTier.PAPER_EXECUTABLE,
            1,
            (
                MarketDataLimitation.INDICATIVE_OPTIONS_FEED,
                MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,
            ),
        )
    if options_feed is OptionsFeed.OPRA:
        return (
            ExecutionTier.PAPER_EXECUTABLE,
            None,
            (MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,),
        )
    return (
        ExecutionTier.RESEARCH_ONLY,
        0,
        (
            MarketDataLimitation.UNSUPPORTED_OPTIONS_FEED,
            MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,
        ),
    )


def build_market_data_capability(
    underlying_feed: UnderlyingFeed,
    options_feed: OptionsFeed,
    assessed_at: datetime,
    missing_required_analytics: bool = False,
) -> MarketDataCapability:
    execution_tier, max_spread_units, base_limitations = _expected_values(options_feed)
    limitations = base_limitations + (
        (MarketDataLimitation.MISSING_REQUIRED_ANALYTICS,) if missing_required_analytics else ()
    )
    return MarketDataCapability(
        underlying_feed=underlying_feed,
        options_feed=options_feed,
        execution_tier=execution_tier,
        max_spread_units=max_spread_units,
        limitations=tuple(dict.fromkeys(limitations)),
        assessed_at=assessed_at,
    )
