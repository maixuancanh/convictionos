from datetime import UTC, datetime
from decimal import Decimal

from convictionos.application.agent import AgentMarketFrame, AutonomousTradingAgent
from convictionos.application.quant_signals import compute_quant_signal
from convictionos.domain.market_data import (
    ExecutionTier,
    MarketDataCapability,
    MarketDataLimitation,
    OptionsFeed,
    UnderlyingFeed,
)


def test_extracted_quant_signal_matches_agent_path() -> None:
    observed_at = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    frame = AgentMarketFrame(
        underlying="SPY",
        price=Decimal("510"),
        previous_close=Decimal("500"),
        observed_at=observed_at,
        source="test",
        market_data=MarketDataCapability(
            underlying_feed=UnderlyingFeed.IEX,
            options_feed=OptionsFeed.INDICATIVE,
            execution_tier=ExecutionTier.PAPER_EXECUTABLE,
            max_spread_units=1,
            limitations=(
                MarketDataLimitation.INDICATIVE_OPTIONS_FEED,
                MarketDataLimitation.PAPER_SIMULATION_NOT_LIVE_EQUIVALENT,
            ),
            assessed_at=observed_at,
        ),
        calls=(),
    )
    extracted = compute_quant_signal(frame)
    legacy = AutonomousTradingAgent._compute_quant_signal(frame)
    assert extracted == legacy
