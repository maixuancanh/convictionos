import re
from datetime import date, time
from decimal import Decimal
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, model_validator

from convictionos.domain.trading import InstrumentKind, PositionIntent, Side, TradeIntent, TradeLeg

COMPETITION_MANDATE_VERSION: Literal["competition-defined-risk-v1"] = (
    "competition-defined-risk-v1"
)
_OCC_SYMBOL = re.compile(r"^[A-Z]{1,6}(?P<expiry>\d{6})(?P<option_type>[CP])(?P<strike>\d{8})$")
_COMPETITION_MANDATE_VALUES = {
    "version": COMPETITION_MANDATE_VERSION,
    "allowed_underlyings": ("QQQ", "SPY"),
    "max_spread_units": 1,
    "max_loss_per_intent": Decimal("500.00"),
    "max_loss_per_underlying": Decimal("1000.00"),
    "max_open_risk": Decimal("2000.00"),
    "daily_loss_breaker": Decimal("1000.00"),
    "max_open_positions": 4,
    "entry_cutoff": time(15, 15),
    "short_leg_exit_buffer_dte": 2,
}


class CompetitionMandate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["competition-defined-risk-v1"] = COMPETITION_MANDATE_VERSION
    allowed_underlyings: tuple[Literal["QQQ", "SPY"], ...] = ("QQQ", "SPY")
    max_spread_units: Literal[1] = 1
    max_loss_per_intent: Decimal = Decimal("500.00")
    max_loss_per_underlying: Decimal = Decimal("1000.00")
    max_open_risk: Decimal = Decimal("2000.00")
    daily_loss_breaker: Decimal = Decimal("1000.00")
    max_open_positions: Literal[4] = 4
    entry_cutoff: time = time(15, 15)
    short_leg_exit_buffer_dte: Literal[2] = 2

    @model_validator(mode="before")
    @classmethod
    def reject_altered_contract(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        for field_name, expected in _COMPETITION_MANDATE_VALUES.items():
            if field_name not in data:
                continue
            value = data[field_name]
            if _normalized_mandate_value(value, expected) != expected:
                raise ValueError(
                    "CompetitionMandate must remain fixed competition-defined-risk-v1"
                )
        return data


class _OptionContract(NamedTuple):
    underlying: str
    expiration: date
    option_type: str
    strike: int


def validate_competition_intent(
    intent: TradeIntent, mandate: CompetitionMandate
) -> TradeIntent:
    if intent.underlying not in mandate.allowed_underlyings:
        raise ValueError("competition intent must use allowed underlyings QQQ or SPY")
    if len(intent.legs) != 2 or any(
        leg.kind is not InstrumentKind.US_OPTION for leg in intent.legs
    ):
        raise ValueError("competition allows only two-leg vertical option spreads")

    long_leg, short_leg = _opening_vertical_legs(intent.legs)
    if long_leg.quantity != long_leg.quantity.to_integral_value():
        raise ValueError("competition spread quantity must be integral")
    if long_leg.quantity != Decimal(mandate.max_spread_units):
        raise ValueError("competition intent must use exactly one spread unit")
    if long_leg.ratio_quantity != 1 or short_leg.ratio_quantity != 1:
        raise ValueError("competition vertical option spreads must be 1:1")

    long_contract = _parse_occ_symbol(long_leg.symbol)
    short_contract = _parse_occ_symbol(short_leg.symbol)
    if long_contract.underlying != short_contract.underlying:
        raise ValueError("competition vertical option legs must share same underlying")
    if long_contract.underlying != intent.underlying:
        raise ValueError("competition intent underlying must match option legs")
    if long_contract.expiration != short_contract.expiration:
        raise ValueError("competition vertical option legs must share same expiration")
    if long_contract.option_type != short_contract.option_type:
        raise ValueError("competition vertical option legs must share same option type")
    if long_contract.strike == short_contract.strike:
        raise ValueError("competition vertical option spreads require ordered strikes")

    max_defined_risk = (
        Decimal(abs(short_contract.strike - long_contract.strike)) / Decimal("1000")
    ) * Decimal("100") * long_leg.quantity
    if intent.max_loss > max_defined_risk:
        raise ValueError("competition intent must have defined debit/credit risk")
    if intent.max_loss > mandate.max_loss_per_intent:
        raise ValueError("competition intent exceeds per-intent loss limit")
    return intent


def _opening_vertical_legs(legs: tuple[TradeLeg, ...]) -> tuple[TradeLeg, TradeLeg]:
    long_legs = [
        leg
        for leg in legs
        if leg.side is Side.BUY and leg.position_intent is PositionIntent.BUY_TO_OPEN
    ]
    short_legs = [
        leg
        for leg in legs
        if leg.side is Side.SELL and leg.position_intent is PositionIntent.SELL_TO_OPEN
    ]
    if len(long_legs) != 1 or len(short_legs) != 1:
        has_non_opening_short = any(
            leg.position_intent is not PositionIntent.SELL_TO_OPEN
            for leg in legs
            if leg.side is Side.SELL
        )
        if has_non_opening_short:
            raise ValueError("competition rejects uncovered short leg orders")
        raise ValueError("competition rejects rolls and requires opening vertical option spreads")
    return long_legs[0], short_legs[0]


def _parse_occ_symbol(symbol: str) -> _OptionContract:
    match = _OCC_SYMBOL.fullmatch(symbol)
    if match is None:
        raise ValueError("competition option legs must use valid OCC symbols")
    raw_expiration = match.group("expiry")
    try:
        expiration = date(
            2000 + int(raw_expiration[:2]),
            int(raw_expiration[2:4]),
            int(raw_expiration[4:6]),
        )
    except ValueError as exc:
        raise ValueError("competition OCC symbols must contain valid expiration dates") from exc
    return _OptionContract(
        underlying=symbol[: match.start("expiry")],
        expiration=expiration,
        option_type=match.group("option_type"),
        strike=int(match.group("strike")),
    )


def _normalized_mandate_value(value: object, expected: object) -> object:
    if isinstance(expected, Decimal):
        try:
            return Decimal(str(value))
        except Exception:
            return value
    if isinstance(expected, tuple) and not isinstance(value, str):
        try:
            return tuple(value)  # type: ignore[arg-type]
        except TypeError:
            return value
    return value
