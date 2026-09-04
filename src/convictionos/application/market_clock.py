from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class MarketSession(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    is_open: bool
    observed_at: datetime
    next_open: datetime | None = None
    next_close: datetime | None = None
    source: str


class MarketClockPort(Protocol):
    async def current_session(self, *, now: datetime) -> MarketSession: ...
