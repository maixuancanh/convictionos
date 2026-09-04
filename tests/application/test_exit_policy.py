from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from convictionos.application.exit_policy import (
    EXIT_POLICY_VERSION,
    CloseQuoteObservation,
    ExitDecision,
    ExitEvaluationContext,
    ExitOutcome,
    ExitPolicyEngine,
    ExitSignalObservation,
    exit_policy_preset,
)
from convictionos.domain.intelligence import ThesisDirection
from convictionos.domain.strategy import OptionMarketQuote
from convictionos.domain.trading import Horizon
from tests.domain.test_positions import make_position

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def quote(
    symbol: str, *, bid: str, ask: str, as_of: datetime = NOW, tradable: bool = True
) -> OptionMarketQuote:
    return OptionMarketQuote(
        symbol=symbol,
        strike=Decimal("100"),
        expiration=date(2028, 1, 20),
        bid=Decimal(bid),
        ask=Decimal(ask),
        delta=Decimal("0.55"),
        implied_volatility=Decimal("0.25"),
        open_interest=100,
        volume=10,
        as_of=as_of,
        tradable=tradable,
    )


def observation(
    *,
    position=None,
    now: datetime = NOW,
    long_bid: str = "2.00",
    short_ask: str = "0.50",
    observed_at: datetime = NOW,
    snapshot_hash: str | None = None,
    valid: bool = True,
) -> ExitEvaluationContext:
    position = position or make_position()
    quote_values = {
        "underlying": "SPY",
        "long_quote": quote(
            position.long_leg.symbol, bid=long_bid, ask="2.20", as_of=observed_at, tradable=valid
        ),
        "short_quote": quote(
            position.short_leg.symbol, bid="0.10", ask=short_ask, as_of=observed_at, tradable=valid
        ),
        "observed_at": observed_at,
    }
    provisional = CloseQuoteObservation.model_construct(snapshot_hash="provisional", **quote_values)
    close_quotes = CloseQuoteObservation(
        snapshot_hash=snapshot_hash or provisional.content_hash(), **quote_values
    )
    return ExitEvaluationContext(
        position=position,
        now=now,
        opened_at=NOW - timedelta(days=1),
        close_quotes=close_quotes,
    )


def signal(
    *,
    snapshot_hash: str,
    observed_at: datetime = NOW,
    underlying: str = "SPY",
    ai_direction: ThesisDirection = ThesisDirection.BEARISH,
    quant_direction: ThesisDirection = ThesisDirection.BEARISH,
    ai_valid: bool = True,
    quant_valid: bool = True,
) -> ExitSignalObservation:
    values = {
        "underlying": underlying,
        "ai_direction": ai_direction,
        "quant_direction": quant_direction,
        "ai_valid": ai_valid,
        "quant_valid": quant_valid,
        "observed_at": observed_at,
        "snapshot_hash": snapshot_hash,
    }
    provisional = ExitSignalObservation.model_construct(
        signal_content_hash="provisional", **values
    )
    return ExitSignalObservation(
        signal_content_hash=provisional.content_hash(), **values
    )


@pytest.mark.parametrize(
    ("horizon", "take", "stop", "buffer", "hold"),
    [
        (Horizon.CATALYST, "0.40", "-0.35", 2, 10),
        (Horizon.SWING, "0.50", "-0.40", 7, 56),
        (Horizon.THEMATIC, "0.60", "-0.50", 21, 365),
    ],
)
def test_code_owned_exit_policy_v1_presets_are_exact(horizon, take, stop, buffer, hold) -> None:
    preset = exit_policy_preset(horizon)
    assert preset.version == EXIT_POLICY_VERSION == "exit-policy-v1"
    assert preset.take_profit_return == Decimal(take)
    assert preset.stop_loss_return == Decimal(stop)
    assert preset.expiry_buffer_dte == buffer
    assert preset.max_holding_days == hold


def test_preset_is_immutable() -> None:
    with pytest.raises(ValidationError):
        exit_policy_preset(Horizon.CATALYST).take_profit_return = Decimal("9")


def test_conservative_mark_is_long_bid_minus_short_ask_and_uses_broker_basis() -> None:
    result = ExitPolicyEngine().evaluate(
        observation(long_bid="1.30", short_ask="0.50"),
    )
    assert result.outcome is ExitOutcome.HOLD
    assert result.close_credit == Decimal("0.80")
    assert result.basis_source == "broker_fill"
    assert result.performance_eligible is False


def test_zero_short_bid_cannot_authorize_close() -> None:
    result = ExitPolicyEngine().evaluate(
        observation(long_bid="2.00", short_ask="0.25").model_copy(
            update={
                "close_quotes": observation(long_bid="2.00", short_ask="0.25")
                .close_quotes.model_copy(
                    update={
                        "short_quote": quote(
                            make_position().short_leg.symbol,
                            bid="0",
                            ask="0.25",
                            as_of=NOW,
                        )
                    }
                )
            }
        )
    )
    assert result.outcome is ExitOutcome.HOLD
    assert result.primary_reason == "close_quote_unavailable"


def test_take_profit_closes_from_executable_credit_not_midpoint() -> None:
    position = make_position()
    context = observation(position=position, long_bid="2.00", short_ask="0.25")
    result = ExitPolicyEngine().evaluate(context)
    assert result.outcome is ExitOutcome.CLOSE
    assert result.primary_reason == "take_profit_reached"
    assert result.close_credit == Decimal("1.75")


def test_incomplete_broker_basis_falls_back_to_authorized_ceiling() -> None:
    position = make_position(
        entry_basis_provenance=make_position().entry_basis_provenance.model_copy(
            update={
                "broker_reported_fill_price": Decimal("0"),
                "broker_reported_cost_basis": Decimal("0"),
            }
        )
    )
    result = ExitPolicyEngine().evaluate(
        observation(position=position, long_bid="2.00", short_ask="0.25")
    )
    assert result.basis_source == "authorized_ceiling"
    assert result.entry_basis == Decimal("1.25")
    assert result.performance_eligible is False


@pytest.mark.parametrize("field", ["long_quote", "short_quote"])
def test_missing_or_invalid_quote_holds_without_authorizing_close(field: str) -> None:
    context = observation(long_bid="2.00", short_ask="0.25")
    quotes = context.close_quotes.model_copy(update={field: None})
    result = ExitPolicyEngine().evaluate(context.model_copy(update={"close_quotes": quotes}))
    assert result.outcome is ExitOutcome.HOLD
    assert result.primary_reason == "close_quote_unavailable"


def test_invalid_quote_inside_expiry_buffer_requires_review() -> None:
    position = make_position()
    context = observation(
        position=position,
        now=datetime(2028, 1, 19, 12, tzinfo=UTC),
        observed_at=datetime(2028, 1, 19, 12, tzinfo=UTC),
        valid=False,
    )
    result = ExitPolicyEngine().evaluate(context)
    assert result.outcome is ExitOutcome.REVIEW_REQUIRED
    assert result.primary_reason == "expiry_buffer_reached"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"broker_event": "assignment"}, "broker_event_or_position_mismatch"),
        ({"broker_position_match": False}, "broker_event_or_position_mismatch"),
        ({"thesis_invalidated": True}, "structured_thesis_invalidation"),
        ({"long_bid": "0.40", "short_ask": "0.25"}, "stop_loss_reached"),
        ({"now": NOW + timedelta(days=11)}, "max_holding_time_reached"),
    ],
)
def test_priority_is_stable_and_broker_event_wins(change, reason) -> None:
    kwargs = {key: value for key, value in change.items() if key in {"now"}}
    context = observation(**kwargs)
    updates = {key: value for key, value in change.items() if key not in {"now", "long_bid"}}
    if "long_bid" in change:
        context = observation(
            long_bid=change["long_bid"], short_ask="0.25", now=kwargs.get("now", NOW)
        )
    context = context.model_copy(update=updates)
    result = ExitPolicyEngine().evaluate(context)
    assert result.primary_reason == reason
    if reason == "broker_event_or_position_mismatch":
        assert result.outcome is ExitOutcome.REVIEW_REQUIRED


def test_priority_places_structured_invalidation_before_reversal_and_price_triggers() -> None:
    signal_value = signal(snapshot_hash=observation().close_quotes.snapshot_hash)
    context = observation(long_bid="2.00", short_ask="0.25").model_copy(
        update={"thesis_invalidated": True, "signals": signal_value}
    )
    result = ExitPolicyEngine().evaluate(context)
    assert result.primary_reason == "structured_thesis_invalidation"


def test_joint_reversal_requires_fresh_opposite_validated_signals() -> None:
    context = observation(long_bid="1.20", short_ask="0.50")
    context = context.model_copy(
        update={
            "signals": signal(snapshot_hash=context.close_quotes.snapshot_hash)
        }
    )
    result = ExitPolicyEngine().evaluate(context)
    assert result.outcome is ExitOutcome.CLOSE
    assert result.primary_reason == "joint_reversal_confirmed"

    for update in (
        {"ai_valid": False},
        {"quant_valid": False},
        {"ai_direction": ThesisDirection.BULLISH},
        {"quant_direction": ThesisDirection.NEUTRAL},
    ):
        updated_signal = context.signals.model_copy(update=update)
        held = ExitPolicyEngine().evaluate(
            context.model_copy(update={"signals": updated_signal})
        )
        assert held.outcome is ExitOutcome.HOLD


def test_joint_reversal_requires_same_snapshot_hash() -> None:
    context = observation(long_bid="1.20", short_ask="0.50")
    signal_value = signal(snapshot_hash="different-snapshot")
    result = ExitPolicyEngine().evaluate(context.model_copy(update={"signals": signal_value}))
    assert result.outcome is ExitOutcome.HOLD
    assert result.primary_reason == "no_exit_trigger"


@pytest.mark.parametrize(
    "signal_update",
    [
        {"observed_at": NOW - timedelta(minutes=6)},
        {"observed_at": NOW - timedelta(seconds=1), "underlying": "QQQ"},
        {"ai_direction": ThesisDirection.BULLISH, "quant_direction": ThesisDirection.BULLISH},
    ],
)
def test_reversal_requires_fresh_same_underlying_opposite_signal(signal_update) -> None:
    context = observation(long_bid="1.20", short_ask="0.50")
    signal_value = signal(snapshot_hash=context.close_quotes.snapshot_hash).model_copy(
        update=signal_update
    )
    result = ExitPolicyEngine().evaluate(context.model_copy(update={"signals": signal_value}))
    assert result.outcome is ExitOutcome.HOLD


@pytest.mark.parametrize("field", ["now", "opened_at"])
def test_naive_evaluation_datetimes_are_rejected(field: str) -> None:
    context = observation()
    values = context.model_dump()
    values[field] = datetime(2026, 9, 3, 12)
    with pytest.raises(ValidationError, match="timezone"):
        ExitEvaluationContext.model_validate(values)


def test_naive_quote_and_signal_datetimes_are_rejected() -> None:
    position = make_position()
    with pytest.raises(ValidationError, match="timezone"):
        CloseQuoteObservation(
            underlying="SPY",
            long_quote=quote(
                position.long_leg.symbol, bid="2", ask="2.2", as_of=datetime(2026, 9, 3, 12)
            ),
            short_quote=quote(position.short_leg.symbol, bid="0.1", ask="0.5", as_of=NOW),
            observed_at=NOW,
            snapshot_hash="snapshot-1",
        )
    context = observation()
    naive_signal = signal(snapshot_hash=context.close_quotes.snapshot_hash).model_copy(
        update={"observed_at": datetime(2026, 9, 3, 12)}
    )
    with pytest.raises(ValidationError, match="timezone"):
        ExitEvaluationContext(
            position=context.position,
            now=context.now,
            opened_at=context.opened_at,
            close_quotes=context.close_quotes,
            signals=naive_signal,
        )


def test_unknown_pinned_policy_requires_review() -> None:
    position = make_position(exit_policy_version="exit-policy-v2")
    result = ExitPolicyEngine().evaluate(observation(position=position))
    assert result.outcome is ExitOutcome.REVIEW_REQUIRED
    assert result.primary_reason == "unsupported_exit_policy_version"


def test_decision_hash_is_stable_and_binds_position_and_observation() -> None:
    result = ExitPolicyEngine().evaluate(observation())
    assert result.decision_hash == ExitDecision.compute_hash(result)
    assert result.position_state_hash
    assert result.observation_hash == observation().close_quotes.snapshot_hash
    assert result.model_copy().decision_hash == result.decision_hash


def test_snapshot_hash_is_canonical_for_decimal_scale_and_timezone() -> None:
    position = make_position()
    first = observation(position=position, observed_at=NOW)
    equivalent = observation(
        position=position,
        observed_at=datetime(2026, 9, 3, 19, 0, tzinfo=timezone(timedelta(hours=7))),
        long_bid="2.0",
        short_ask="0.50",
    )
    assert first.close_quotes.snapshot_hash == equivalent.close_quotes.snapshot_hash
