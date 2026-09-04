from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from convictionos.domain.market_data import (
    ExecutionTier,
    MarketDataCapability,
    MarketDataLimitation,
    OptionsFeed,
    UnderlyingFeed,
    build_market_data_capability,
)

ASSESSED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def test_indicative_feed_is_paper_executable_with_conservative_limits() -> None:
    capability = build_market_data_capability(
        UnderlyingFeed.IEX, OptionsFeed.INDICATIVE, ASSESSED_AT
    )

    assert capability.execution_tier is ExecutionTier.PAPER_EXECUTABLE
    assert capability.max_spread_units == 1
    assert capability.performance_eligible is False
    assert capability.limitations == (
        MarketDataLimitation.INDICATIVE_OPTIONS_FEED,
        MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,
    )


def test_opra_feed_is_paper_executable_without_spread_cap() -> None:
    capability = build_market_data_capability(UnderlyingFeed.SIP, OptionsFeed.OPRA, ASSESSED_AT)

    assert capability.execution_tier is ExecutionTier.PAPER_EXECUTABLE
    assert capability.max_spread_units is None
    assert capability.performance_eligible is False
    assert capability.limitations == (MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,)


def test_unknown_options_feed_is_research_only() -> None:
    capability = build_market_data_capability(UnderlyingFeed.IEX, OptionsFeed.UNKNOWN, ASSESSED_AT)

    assert capability.execution_tier is ExecutionTier.RESEARCH_ONLY
    assert capability.max_spread_units == 0
    assert MarketDataLimitation.UNSUPPORTED_OPTIONS_FEED in capability.limitations


def test_indicative_invariant_cannot_be_overridden_directly() -> None:
    with pytest.raises(ValidationError):
        MarketDataCapability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.INDICATIVE,
            execution_tier=ExecutionTier.RESEARCH_ONLY,
            max_spread_units=0,
            assessed_at=ASSESSED_AT,
            limitations=(
                MarketDataLimitation.INDICATIVE_OPTIONS_FEED,
                MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,
            ),
        )


def test_limitation_set_must_match_feed_invariant_exactly() -> None:
    with pytest.raises(ValidationError):
        MarketDataCapability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.OPRA,
            execution_tier=ExecutionTier.PAPER_EXECUTABLE,
            assessed_at=ASSESSED_AT,
            limitations=(),
        )
