from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from convictionos.domain.trading import Horizon

# Inclusive DTE bands. Adjacent horizons intentionally share their boundary day.
CATALYST_DTE: tuple[int, int] = (7, 30)
SWING_DTE: tuple[int, int] = (30, 90)
THEMATIC_DTE: tuple[int, int] = (90, 450)


class OptionMarketQuote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    option_type: Literal["call", "put"] | None = None
    strike: Decimal = Field(gt=0)
    expiration: date
    bid: Decimal = Field(ge=0)
    ask: Decimal = Field(ge=0)
    delta: Decimal
    implied_volatility: Decimal = Field(ge=0)
    open_interest: int = Field(ge=0)
    volume: int = Field(ge=0)
    as_of: datetime
    tradable: bool


class StrategyCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    horizon: Horizon
    long_symbol: str = Field(min_length=1)
    short_symbol: str = Field(min_length=1)
    limit_debit: Decimal = Field(ge=0)
    max_loss: Decimal = Field(ge=0)
    width: Decimal = Field(gt=0)
    dte: int = Field(ge=0)
    long_delta: Decimal
    liquidity_score: Decimal = Field(ge=0)
    slippage_estimate: Decimal = Field(ge=0)
    utility_score: Decimal
    rejection_reasons: tuple[str, ...]
