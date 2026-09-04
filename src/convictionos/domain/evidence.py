from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.trading import Horizon


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    event_at: datetime
    observed_at: datetime
    ingested_at: datetime
    source_checksum: str = Field(pattern=r"^sha256:")

    @model_validator(mode="after")
    def validate_point_in_time_order(self) -> "EvidenceClaim":
        if not self.event_at <= self.observed_at <= self.ingested_at:
            raise ValueError("evidence timestamps are not point-in-time ordered")
        return self


class SyntheticNarrativeCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    synthetic: bool
    underlying: str
    horizon: Horizon
    thesis: str
    invalidation: str
    created_at: datetime
    expires_at: datetime
    evidence: tuple[EvidenceClaim, ...]

    def snapshot_hash(self) -> str:
        return f"sha256:{sha256_hex(self.model_dump(mode='python'))}"
