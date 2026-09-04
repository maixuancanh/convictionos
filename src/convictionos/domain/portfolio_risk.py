from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.intelligence import ThesisDirection
from convictionos.domain.trading import Horizon


class HorizonRiskBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_dte: int = Field(ge=0)
    maximum_dte: int = Field(ge=0)
    per_trade_max_loss: Decimal = Field(gt=0)
    aggregate_capacity: Decimal = Field(gt=0)
    max_positions: int = Field(gt=0)
    confidence_threshold: Decimal = Field(ge=0, le=1)
    cooldown_seconds: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range_and_capacity(self) -> "HorizonRiskBudget":
        if self.maximum_dte < self.minimum_dte:
            raise ValueError("maximum_dte must be greater than or equal to minimum_dte")
        if self.aggregate_capacity < self.per_trade_max_loss:
            raise ValueError("aggregate_capacity must cover per_trade_max_loss")
        return self


class OpenPosition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: str = Field(min_length=1)
    horizon: Horizon
    direction: ThesisDirection
    catalyst: str = Field(min_length=1)
    max_loss: Decimal = Field(ge=0)


class ActiveRiskReservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon: Horizon
    max_loss: Decimal = Field(gt=0)
    underlying: str = ""
    catalyst: str = ""
    created_at: datetime | None = None


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: datetime
    open_positions: tuple[OpenPosition, ...]
    active_reservations: tuple[ActiveRiskReservation, ...]
    account_equity: Decimal = Field(gt=0)
    buying_power: Decimal = Field(ge=0)
    options_level: int = Field(ge=0)
    directional_exposure: dict[str, Decimal]
    last_trade_at: datetime | None = None

    @property
    def immutable_directional_exposure(self) -> MappingProxyType[str, Decimal]:
        return MappingProxyType(self.directional_exposure)
