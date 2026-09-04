from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from convictionos.domain.intelligence import IntelligenceArtifact, ThesisDirection
from convictionos.domain.positions import ManagedOptionPosition

if TYPE_CHECKING:
    from convictionos.application.agent import AgentMarketFrame, QuantSignalSnapshot
    from convictionos.application.exit_policy import ExitSignalObservation


def compute_quant_signal(
    frame: AgentMarketFrame,
) -> tuple[QuantSignalSnapshot, Any]:
    """Compute the versioned deterministic frame-return signal used by entry and exit."""
    from convictionos.application.agent import QuantSignalSnapshot
    from convictionos.application.strategy_router import QuantSignal

    momentum = (frame.price - frame.previous_close) / frame.previous_close
    direction = (
        ThesisDirection.BULLISH
        if momentum > 0
        else ThesisDirection.BEARISH
        if momentum < 0
        else ThesisDirection.NEUTRAL
    )
    snapshot = QuantSignalSnapshot(
        direction=direction,
        momentum=momentum,
        realized_volatility=abs(momentum),
        as_of=frame.observed_at,
        version="frame-return-quant-v1",
    )
    return (
        snapshot,
        QuantSignal(
            direction=direction,
            momentum=momentum,
            realized_volatility=abs(momentum),
            as_of=frame.observed_at,
            version="frame-return-quant-v1",
        ),
    )


def build_exit_signal(
    position: ManagedOptionPosition,
    grounded_result: IntelligenceArtifact,
    quant_snapshot: QuantSignalSnapshot,
    frame: AgentMarketFrame,
) -> ExitSignalObservation:
    """Build independent AI/quant exit evidence; this function never authorizes an order."""
    from convictionos.application.exit_policy import ExitSignalObservation

    fresh = (
        frame.observed_at.tzinfo is not None
        and frame.observed_at <= quant_snapshot.as_of
        and quant_snapshot.as_of - frame.observed_at <= timedelta(minutes=5)
    )
    opposite = (
        ThesisDirection.BEARISH
        if position.direction is ThesisDirection.BULLISH
        else ThesisDirection.BULLISH
    )
    ai_valid = (
        grounded_result.valid
        and grounded_result.thesis.horizon is position.horizon
        and grounded_result.thesis.direction is opposite
        and bool(grounded_result.thesis.source_ids)
    )
    quant_valid = (
        fresh
        and quant_snapshot.version == "frame-return-quant-v1"
        and quant_snapshot.direction is opposite
    )
    underlying = frame.underlying
    ai_direction = grounded_result.thesis.direction
    quant_direction = quant_snapshot.direction
    observed_at = quant_snapshot.as_of
    snapshot_hash = frame.snapshot_hash()
    provisional = ExitSignalObservation.model_construct(
        signal_content_hash="provisional",
        underlying=underlying,
        ai_direction=ai_direction,
        quant_direction=quant_direction,
        ai_valid=ai_valid,
        quant_valid=quant_valid,
        observed_at=observed_at,
        snapshot_hash=snapshot_hash,
    )
    return ExitSignalObservation(
        signal_content_hash=provisional.content_hash(),
        underlying=underlying,
        ai_direction=ai_direction,
        quant_direction=quant_direction,
        ai_valid=ai_valid,
        quant_valid=quant_valid,
        observed_at=observed_at,
        snapshot_hash=snapshot_hash,
    )
