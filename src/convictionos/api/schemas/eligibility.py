from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PublicCompetitionReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["eligible", "ineligible", "unavailable"]
    paper_mode: Literal[True] = True
    options_incorporated: bool
    cli_revision: str | None
    mcp_version: str | None
    options_feed: str | None
    market_data_tier: str | None
    mandate_version: str | None
    deployed_sha: str | None
    verified_at: datetime | None
    public_reasons: tuple[str, ...]
