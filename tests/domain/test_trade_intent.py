from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from convictionos.domain.canonical import canonical_json, sha256_hex
from convictionos.domain.trading import (
    Horizon,
    InstrumentKind,
    IntentState,
    OrderType,
    PositionIntent,
    Side,
    TradeIntent,
    TradeLeg,
)


def make_intent(quantity: Decimal = Decimal("1")) -> TradeIntent:
    now = datetime(2026, 8, 29, 14, 0, tzinfo=UTC)
    return TradeIntent(
        intent_id="intent-001",
        idempotency_key="intent-001-v1",
        account_id="paper-account",
        mandate_id="mandate-001",
        mandate_version=1,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        horizon=Horizon.CATALYST,
        underlying="SPY",
        thesis_ref="synthetic-catalyst-001",
        legs=(
            TradeLeg(
                symbol="SPY280120C00500000",
                kind=InstrumentKind.US_OPTION,
                side=Side.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
                quantity=quantity,
                ratio_quantity=1,
            ),
            TradeLeg(
                symbol="SPY280120C00510000",
                kind=InstrumentKind.US_OPTION,
                side=Side.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
                quantity=quantity,
                ratio_quantity=1,
            ),
        ),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1.25"),
        max_loss=Decimal("125"),
        exit_plan="Close before catalyst expiry or at 50% max loss.",
        data_snapshot_hash="sha256:evidence-001",
    )


def test_operation_hash_is_stable() -> None:
    intent = make_intent()
    assert intent.operation_hash() == intent.model_copy().operation_hash()


def test_operation_hash_changes_when_quantity_changes() -> None:
    assert make_intent().operation_hash() != make_intent(Decimal("2")).operation_hash()


def test_trade_intent_is_frozen() -> None:
    intent = make_intent()
    with pytest.raises(Exception, match="frozen"):
        intent.limit_price = Decimal("1.30")  # type: ignore[misc]


def test_timezone_equivalent_datetimes_have_same_hash() -> None:
    payload_a = {"when": datetime(2026, 8, 29, 14, 0, tzinfo=UTC)}
    payload_b = {"when": datetime(2026, 8, 29, 9, 0, tzinfo=timezone(timedelta(hours=-5)))}

    assert canonical_json(payload_a) == canonical_json(payload_b)
    assert sha256_hex(payload_a) == sha256_hex(payload_b)


def test_decimal_scale_equivalence_has_same_hash() -> None:
    payload_a = {"price": Decimal("1.2")}
    payload_b = {"price": Decimal("1.20")}

    assert canonical_json(payload_a) == canonical_json(payload_b)
    assert sha256_hex(payload_a) == sha256_hex(payload_b)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="naive datetime"):
        canonical_json({"when": datetime(2026, 8, 29, 14, 0)})


def test_ack_unknown_is_an_explicit_submission_state() -> None:
    assert "ack_unknown" in {state.value for state in IntentState}
