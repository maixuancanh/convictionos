from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from convictionos.application.close_intents import (
    CloseIntentFactory,
    authorize_close,
)
from convictionos.application.exit_policy import ExitOutcome, ExitPolicyEngine
from convictionos.domain.trading import PositionIntent, Side
from tests.application.test_exit_policy import make_position, observation, signal

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def close_decision():
    position = make_position()
    quotes = observation(position=position)
    result = ExitPolicyEngine().evaluate(
        quotes.model_copy(
            update={
                "signals": signal(
                    snapshot_hash=quotes.close_quotes.snapshot_hash,
                    observed_at=NOW,
                )
            }
        )
    )
    assert result.outcome is ExitOutcome.CLOSE
    return position, result


def test_factory_reverses_exact_opening_vertical() -> None:
    position, decision = close_decision()
    intent = CloseIntentFactory.create(position, decision, NOW)

    assert intent.idempotency_key == "position-001-close-v1"
    assert intent.expires_at == NOW + timedelta(minutes=5)
    assert intent.limit_price == decision.close_credit
    assert intent.thesis_ref == position.opening_thesis_ref
    assert intent.position_id == position.position_id
    assert intent.exit_decision_hash == decision.decision_hash
    assert [(leg.symbol, leg.side, leg.position_intent, leg.quantity) for leg in intent.legs] == [
        (position.long_leg.symbol, Side.SELL, PositionIntent.SELL_TO_CLOSE, Decimal("1")),
        (position.short_leg.symbol, Side.BUY, PositionIntent.BUY_TO_CLOSE, Decimal("1")),
    ]
    assert authorize_close(position, intent, decision).authorized


@pytest.mark.parametrize(
    "mutate",
    [
        lambda decision: decision.model_copy(update={"outcome": ExitOutcome.HOLD}),
        lambda decision: decision.model_copy(update={"close_credit": None}),
    ],
)
def test_factory_rejects_non_close_or_missing_credit(mutate) -> None:
    position, decision = close_decision()
    with pytest.raises(ValueError):
        CloseIntentFactory.create(position, mutate(decision), NOW)


def test_authorization_rejects_changed_shape_and_hash() -> None:
    position, decision = close_decision()
    intent = CloseIntentFactory.create(position, decision, NOW)
    tampered = intent.model_copy(
        update={
            "legs": (intent.legs[0], intent.legs[1].model_copy(update={"quantity": Decimal("2")})),
            "position_state_hash": "tampered",
        }
    )
    result = authorize_close(position, tampered, decision)
    assert not result.authorized
    assert "shape_mismatch" in result.reasons
    assert "position_hash_mismatch" in result.reasons


def test_legacy_opening_hash_is_unchanged_when_close_fields_absent() -> None:
    position, decision = close_decision()
    intent = CloseIntentFactory.create(position, decision, NOW)
    legacy = intent.model_copy(
        update={
            "position_id": None,
            "exit_decision_hash": None,
            "position_state_hash": None,
        }
    )
    assert legacy.operation_payload() == {
        key: value
        for key, value in intent.model_dump(mode="python").items()
        if key not in {"position_id", "exit_decision_hash", "position_state_hash"}
    }
