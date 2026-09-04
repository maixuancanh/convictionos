from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from convictionos.domain.canonical import sha256_hex
from convictionos.domain.trading import Horizon


class EvidenceSourceType(StrEnum):
    NEWS = "news"
    MARKET = "market"


class ThesisDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    source_type: EvidenceSourceType
    published_at: datetime
    observed_at: datetime
    headline: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=4000)
    symbols: tuple[str, ...]
    url: str = ""

    def content_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying: str = Field(min_length=1)
    observed_at: datetime
    price: Decimal = Field(gt=0)
    previous_close: Decimal = Field(gt=0)
    items: tuple[EvidenceItem, ...]
    version: str = Field(min_length=1)
    market_truth_hash: str | None = None
    mcp_observation_hashes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_point_in_time(self) -> "EvidenceBundle":
        if any(item.published_at > self.observed_at for item in self.items):
            raise ValueError("future evidence is not allowed")
        if len({item.source_id for item in self.items}) != len(self.items):
            raise ValueError("duplicate evidence source_id")
        if self.version == "grounded-options-router-v2" and (
            not self.market_truth_hash or not self.mcp_observation_hashes
        ):
            raise ValueError("market truth hashes are required")
        return self

    def canonical_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        if self.market_truth_hash is None:
            payload.pop("market_truth_hash")
        if not self.mcp_observation_hashes:
            payload.pop("mcp_observation_hashes")
        payload["items"] = sorted(
            payload["items"], key=lambda value: value["source_id"]
        )
        return payload

    def snapshot_hash(self) -> str:
        return sha256_hex(self.canonical_payload())


class GroundedThesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    direction: ThesisDirection
    confidence: Decimal = Field(ge=0, le=1)
    horizon: Horizon
    catalyst: str = Field(min_length=1, max_length=1000)
    causal_mechanism: str = Field(min_length=1, max_length=2000)
    beneficiaries: tuple[str, ...]
    adversely_affected: tuple[str, ...]
    invalidation: str = Field(min_length=1, max_length=1000)
    material_risks: tuple[str, ...] = Field(max_length=8)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=10)
    contradicting_source_ids: tuple[str, ...] = Field(max_length=10)


class IntelligenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(min_length=1, max_length=120)
    identity_hash: str = Field(min_length=1, max_length=64)
    evidence_snapshot_hash: str = Field(min_length=1, max_length=64)
    thesis: GroundedThesis
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=120)
    schema_version: str = Field(min_length=1, max_length=120)
    generated_at: datetime
    valid: bool
    validation_reasons: tuple[str, ...]

    def artifact_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))
