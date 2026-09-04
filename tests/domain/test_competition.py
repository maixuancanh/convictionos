from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from convictionos.domain.competition import CompetitionMandate, validate_competition_intent
from convictionos.domain.trading import (
    Horizon,
    InstrumentKind,
    OrderType,
    PositionIntent,
    Side,
    TradeIntent,
    TradeLeg,
)


def option_leg(
    symbol: str,
    side: Side,
    position_intent: PositionIntent,
    *,
    quantity: Decimal = Decimal("1"),
    ratio_quantity: int = 1,
) -> TradeLeg:
    return TradeLeg(
        symbol=symbol,
        kind=InstrumentKind.US_OPTION,
        side=side,
        position_intent=position_intent,
        quantity=quantity,
        ratio_quantity=ratio_quantity,
    )


def make_intent(
    *,
    underlying: str = "SPY",
    legs: tuple[TradeLeg, ...] | None = None,
    quantity: Decimal = Decimal("1"),
    max_loss: Decimal = Decimal("125.00"),
) -> TradeIntent:
    now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
    return TradeIntent(
        intent_id="intent-competition-001",
        idempotency_key="intent-competition-001-v1",
        account_id="paper-account",
        mandate_id="competition-defined-risk",
        mandate_version=1,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        horizon=Horizon.CATALYST,
        underlying=underlying,
        thesis_ref="synthetic-catalyst-001",
        legs=legs
        or (
            option_leg(
                f"{underlying}260918C00500000",
                Side.BUY,
                PositionIntent.BUY_TO_OPEN,
                quantity=quantity,
            ),
            option_leg(
                f"{underlying}260918C00510000",
                Side.SELL,
                PositionIntent.SELL_TO_OPEN,
                quantity=quantity,
            ),
        ),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1.25"),
        max_loss=max_loss,
        exit_plan="Close before catalyst expiry or at 50% max loss.",
        data_snapshot_hash="sha256:evidence-001",
    )


def test_competition_mandate_freezes_exact_runtime_limits() -> None:
    mandate = CompetitionMandate()

    assert mandate.version == "competition-defined-risk-v1"
    assert mandate.allowed_underlyings == ("QQQ", "SPY")
    assert mandate.max_spread_units == 1
    assert mandate.max_loss_per_intent == Decimal("500.00")
    assert mandate.max_loss_per_underlying == Decimal("1000.00")
    assert mandate.max_open_risk == Decimal("2000.00")
    assert mandate.daily_loss_breaker == Decimal("1000.00")
    assert mandate.max_open_positions == 4
    assert mandate.entry_cutoff == time(15, 15)
    assert mandate.short_leg_exit_buffer_dte == 2
    with pytest.raises(ValidationError, match="frozen"):
        mandate.max_open_positions = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    "altered_value",
    [
        {"allowed_underlyings": ("SPY",)},
        {"max_spread_units": 2},
        {"max_loss_per_intent": Decimal("999999.00")},
        {"max_loss_per_underlying": Decimal("999999.00")},
        {"max_open_risk": Decimal("999999.00")},
        {"daily_loss_breaker": Decimal("999999.00")},
        {"max_open_positions": 99},
        {"entry_cutoff": time(23, 59)},
        {"short_leg_exit_buffer_dte": 99},
    ],
)
def test_competition_mandate_rejects_altered_runtime_limits(
    altered_value: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="fixed competition-defined-risk-v1"):
        CompetitionMandate(**altered_value)


@pytest.mark.parametrize("underlying", ["SPY", "QQQ"])
def test_allows_one_unit_vertical_spreads_for_allowed_underlyings(underlying: str) -> None:
    intent = make_intent(underlying=underlying)

    assert validate_competition_intent(intent, CompetitionMandate()) is intent


@pytest.mark.parametrize(
    "legs",
    [
        (
            option_leg("SPY260918C00520000", Side.BUY, PositionIntent.BUY_TO_OPEN),
            option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
        ),
        (
            option_leg("SPY260918P00490000", Side.BUY, PositionIntent.BUY_TO_OPEN),
            option_leg("SPY260918P00500000", Side.SELL, PositionIntent.SELL_TO_OPEN),
        ),
    ],
)
def test_allows_defined_risk_credit_vertical_spreads(legs: tuple[TradeLeg, ...]) -> None:
    intent = make_intent(legs=legs, max_loss=Decimal("400.00"))

    assert validate_competition_intent(intent, CompetitionMandate()) is intent


@pytest.mark.parametrize(
    ("legs", "match"),
    [
        (
            (
                TradeLeg(
                    symbol="SPY",
                    kind=InstrumentKind.EQUITY,
                    side=Side.BUY,
                    position_intent=PositionIntent.BUY_TO_OPEN,
                    quantity=Decimal("1"),
                    ratio_quantity=1,
                ),
            ),
            "two-leg vertical option spreads",
        ),
        (
            (option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),),
            "two-leg vertical option spreads",
        ),
        (
            (option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),),
            "two-leg vertical option spreads|uncovered short",
        ),
        (
            (
                TradeLeg.model_construct(
                    symbol="BTCUSD",
                    kind="crypto",
                    side=Side.BUY,
                    position_intent=PositionIntent.BUY_TO_OPEN,
                    quantity=Decimal("1"),
                    ratio_quantity=1,
                ),
                option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "two-leg vertical option spreads",
        ),
        (
            (
                option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
                option_leg("SPY260918C00520000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "two-leg vertical option spreads",
        ),
        (
            (
                option_leg(
                    "SPY260918C00500000",
                    Side.BUY,
                    PositionIntent.BUY_TO_OPEN,
                    ratio_quantity=2,
                ),
                option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "1:1",
        ),
        (
            (
                option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                option_leg("SPY261016C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "same expiration",
        ),
        (
            (
                option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                option_leg("SPY260918P00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "same option type",
        ),
        (
            (
                option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                option_leg("QQQ260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "same underlying",
        ),
        (
            (
                option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                option_leg("SPY260918C00500000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "ordered strikes",
        ),
        (
            (
                option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_CLOSE),
            ),
            "uncovered short leg",
        ),
        (
            (
                option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_CLOSE),
                option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "rolls",
        ),
        (
            (
                option_leg("SPY260918C0050000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                option_leg("SPY260918C00510000", Side.SELL, PositionIntent.SELL_TO_OPEN),
            ),
            "OCC",
        ),
    ],
)
def test_rejects_ineligible_competition_orders(
    legs: tuple[TradeLeg, ...], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        validate_competition_intent(make_intent(legs=legs), CompetitionMandate())


@pytest.mark.parametrize(
    ("intent", "match"),
    [
        (make_intent(underlying="IWM"), "allowed underlyings"),
        (make_intent(quantity=Decimal("2")), "one spread unit"),
        (make_intent(quantity=Decimal("1.5")), "integral"),
        (
            make_intent(
                legs=(
                    option_leg("SPY260918C00500000", Side.BUY, PositionIntent.BUY_TO_OPEN),
                    option_leg("SPY260918C00501000", Side.SELL, PositionIntent.SELL_TO_OPEN),
                ),
                max_loss=Decimal("125.00"),
            ),
            "defined debit/credit risk",
        ),
        (make_intent(max_loss=Decimal("500.01")), "per-intent loss"),
    ],
)
def test_rejects_competition_mandate_limit_violations(
    intent: TradeIntent, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        validate_competition_intent(intent, CompetitionMandate())
